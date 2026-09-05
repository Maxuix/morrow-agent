# Stage 8 Subplans 8–12 代码 Review

日期：2026-09-05。基线：`e320f51..429d812`，在当前 `main` 的实现上检查。

结论：发现 7 项问题，分为 2 项 P1、4 项 P2、1 项 P3。其中前 5 项已通过隔离的
scripted Provider / Core API 场景复现，后 2 项通过前后端数据流静态确认。
原始 Review 阶段未修复应用代码、修改执行计划或运行 Live 测试。
用户随后授权修复；修复结果见文末。

## 1. [P1] 通用 TextResult 审查依赖可以绕过 Replan 升级审批

涉及 Subplans 8、9。

位置：`src/morrow/application/workflows/patch_preview.py:66–72,194–195`；自动应用入口为
`src/morrow/application/workflows/replan.py` 的 `propose()` / `decide()`。

`_report_binding_sources()` 仅将 `ReviewReport` / `TestReport` 类型的输入视为报告依赖。
但 Subplan 8 生成的 `builtin_reviewer` 节点使用通用 `TextResult` 输出。因此删除下游
Synthesizer 对 Reviewer 结果的输入绑定、保留控制边，不会产生任何风险原因；Compiler
也接受这个图。在 `allow_low_risk` 策略下，这类修改可自动应用，使下游不再收到审查结果。

复现证据：

- 发布全部内置角色，生成含 Reviewer → Synthesizer 的复杂任务 Draft。
- 删除 Synthesizer 的 `node_output` 输入绑定，保留边及 required outputs。
- 实际输出：`reviewer_definition=builtin_reviewer`、`reviewer_output=TextResult`、
  `compiled=true`、`risk=low`、`reasons=[]`。
- 另用真实暂停的两节点 Workflow 验证同类 TextResult 输入删除：提案 `applied`，
  父运行 `superseded`，无需显式批准。

建议：不能仅依靠旧的结构化 Artifact kind 识别受保护的依赖。对无法证明可安全移除的
输入绑定变更升级审批，或由冻结的节点用途/依赖契约提供证据。增加通用 Reviewer 输出的
自动 Replan 回归场景。

## 2. [P1] Server 并行失败收尾会遗留未闭合的 leaf Task / Turn

涉及 Subplan 12。

位置：`src/morrow/application/workflows/scheduler.py:402–405,707–709`，恢复分支中也有同样的
`cancelled_is_user` 传递。

Core Server 以 `cancelled_is_user=False` 驱动 Scheduler。当一个并行节点正常报告不可重试
错误时，Scheduler 对其他节点调用 `task.cancel()`，但传给 AgentLoop 的判定仍然是 false。
AgentLoop 将这次由 Scheduler 主动发起的取消当成进程驱动丢失，保留 leaf Task 和 Turn。
随后 `_drive_frontier()` 却把整个 Workflow 和 NodeRun 标为失败。

复现：两个已准入的只读节点用 Event 同步，第一个返回 Provider AUTH 错误，第二个仍等待
响应。相同场景分别用前台和 Server 参数运行：

| 路径 | Workflow | 被取消 NodeRun | 对应 leaf Task | open Turn |
|---|---|---|---|---|
| 前台 `True` | failed | failed | cancelled | false |
| Server `False` | failed | failed | open | true |

运行已经终态，正常 Workflow 驱动不再接管这些未闭合记录，破坏每个已准入节点恰好一次收尾的
约定。若取消发生在工具执行阶段，还可能引入额外的恢复状态。

建议：区分同组节点失败引发的受控取消与外部驱动丢失；前者必须闭合已准入 leaf 的生命周期，
后者才保留恢复事实。覆盖生产 Server 参数下的失败、撤销与并行取消场景。

## 3. [P2] 并行证明不足时的串行回退会把 Replan 信号变成运行失败

涉及 Subplans 9、12。

位置：`src/morrow/application/workflows/scheduler.py:459–464`，以及
`src/morrow/application/workflows/parallel.py` 的串行回退分支。

并行候选没有完整只读证明时，各 leaf 在 ReadFrontier 内顺序运行。第一个节点提交 Replan
信号后正常闭合；第二个节点在准入事务中因未消费信号被拒绝并保持 queued，这是正确的。
但 frontier 最终只识别 `pause_requested`，没有处理尚待 Coordinator 消费的信号，因而把
“completed + queued”判成失败。Coordinator 在外层稍后才处理信号，此时运行已经终态。

复现：声明两个只读并行节点，使用缺少运行时证明的 scripted 工具触发串行回退，第一个节点
通过 `submit_node_result` 请求修改 summary。结果为：

```text
Workflow: failed
reader_0: completed
reader_1: cancelled
summary: cancelled
ReplanProposal: pending
```

预期是暂停并保留后续 queued 节点。实际留下的 pending 提案也无法对这个终态父运行执行交接。

建议：回退路径复用普通串行调度的信号/暂停语义，在失败映射之前处理未消费信号；覆盖
“证明不足 → 顺序执行 → 节点信号 → Pause → continuation”的完整流程。

## 4. [P2] 修改 Profile 约束后，GraphPlanner 仍使用进程启动时的旧约束

涉及 Subplans 8、10。

位置：`src/morrow/bootstrap.py:1305–1310`；
`src/morrow/application/workflows/graph_planner.py:110` 附近的特征提取。

GraphPlanner 在启动时收到 `tuple(session.profile.constraints)`，之后始终复用这个元组。
GUI/CLI 的 Profile 写入只更新实际 Profile authority，没有刷新规划器的副本。新生成的
Draft 因而可能遗漏用户刚设置的约束，也可能在约束已经删除后继续错误限制任务。

复现：通过真实管理 API 在现有 Profile r1 上新增 `Do not modify files; read only`，
写入返回 200，查询显示 Profile r2 已包含该约束；同一 Host 内随后生成复杂实现任务，
结果仍为 `features.workspace_constraints=[]`、`writing_nodes=[coder]`。

建议：每次规划从当前 Profile authority 读取约束，并在模型分类 await 之后、保存 Draft
之前检查修订变化，重新计算相关特征或拒绝陈旧准备结果。

## 5. [P2] 角色回退导致反馈学习和 Reviewer 评估识别错误

涉及 Subplans 8、11。

位置：`src/morrow/application/workflows/feedback.py:35–45`；规划器回退选择位于
`src/morrow/application/workflows/graph_planner.py:379–395`。

GraphPlanner 允许用其他已发布 AgentDefinition 承担 Planner/Reviewer/Coder 的任务角色。
反馈模块却优先把底层内置 AgentDefinition 的来源当成角色，忽略规划器赋予该节点的用途。
这两个模块对同一图的角色判断不一致。

复现：仅发布 `builtin_direct` 和 `builtin_explorer`，生成大型重构 Draft，实际识别为：

```text
coder -> direct
explorer -> explorer
planner -> explorer
reviewer -> explorer
```

后果：删除 Planner 不形成 `removed_planner` 证据；Reviewer 输出不进入评估汇总；
`reviewer_useful` / `reviewer_not_useful` 被 `submit()` 以“没有 Reviewer”拒绝；按角色学习
模型偏好也可能写到错误角色上。

建议：为任务角色建立一致且冻结的语义来源，供规划、反馈和评估共同使用。增加仅发布部分
内置定义时的“生成 → 编辑 → 运行 → 反馈”集成测试。

## 6. [P2] Learning 翻页遗漏编排候选的 next_cursor

涉及 Subplans 10、11；静态确认。

位置：`src/morrow/application/management.py:69–76`、`gui/src/views/LearningManager.tsx:132`。

后端把编排候选的分页信息放在 `orchestration.next_cursor`，顶层 `next_cursor` 仍只反映
旧 Learning Candidate / Preference Proposal 列表。前端 Pager 只读取顶层字段。因此当
编排候选超过 50 条、另外两种候选已经没有下一页时，Learning 的下一页按钮被禁用，后续
编排候选及历史无法从此入口访问。Evaluation 页面正确合并了多来源 cursor，但 Learning
页面没有相同处理。

建议：合并三个来源的分页可用性，或提供独立分页；增加只有编排候选超过一页的用例。

## 7. [P3] 策略设置页把“没有已推广类型”写成固定文案

涉及 Subplans 8、11；静态确认。

位置：`gui/src/views/OrchestrationSettings.tsx:56–57`。

Subplan 11 已能通过配对证据推广任务类型、开放类型级低风险 Replan，但设置页仍无条件显示
“当前没有已推广的任务类型”和“仅所有任务策略可生效”。即使 API / Evaluation 页面显示已
满足条件，这里也给出相反状态。

建议：文案依据真实的 promotion / eligibility 查询生成，并区分证据满足与显式策略授权。

## 验证范围

本次实际运行：

- 五个子计划的 Python 测试文件：118 passed（56.17 秒）。
- `pnpm --dir gui test`：15 个测试文件、98 tests passed。
- `pnpm --dir gui typecheck`：通过。
- `uv run --offline ruff check .`：通过。
- `uv run --offline ruff format --check .`：633 files already formatted。
- 隔离复现使用现有 DagFixture / ServerFixture、scripted Providers 和 asyncio Event，未使用
  Live Provider、真实凭据或真实网络测试。临时脚本在 `/private/tmp/morrow_review_probe.py`。

测试使用 `UV_CACHE_DIR=/private/tmp/morrow-review-uv`，因为默认 uv cache 在当前沙箱中不可写。
没有重新运行全仓库 pytest、GUI build 或浏览器验收；上述通过结果不能视为这些更广检查通过。
现有测试主要覆盖各功能正常路径，未覆盖此次发现的角色回退、通用审查结果和 Server 并行
失败之间的组合。

## 修复处置（2026-09-05）

用户明确授权修复后，以 Subplan 13 完成以下变更。原始发现和复现结果保留作为历史证据。

| Issue | 修复 | 回归证据 |
|---|---|---|
| 1 / P1 | 删除或替换任意输入证据须升级审批，添加输入仍可保持低风险 | TextResult 删除进入 pending，显式批准才应用；添加输入自动应用 |
| 2 / P1 | 将同组失败的受控取消与驱动丢失区分 | 前台/Server 模式已准入叶 Task 恰好一次收尾，无 open Turn；现有驱动丢失恢复用例保留 |
| 3 / P2 | frontier 收尾识别未消费 Replan 信号，保留 queued 工作并请求暂停 | 缺少只读证明 → 串行回退 → 信号 → Pause → 批准子运行 → 完成剩余节点 |
| 4 / P2 | 规划时读取当前 Profile，并在保存前重算约束与分类融合 | 分类前/分类期间添加只读约束，以及删除、清空 Profile 后的新 Draft |
| 5 / P2 | 使用冻结的 canonical node ID 表示任务角色，非角色 ID 保留原定义回退 | 仅发布 Direct/Explorer 后的生成、删除 Planner、运行 Reviewer、反馈和评估 |
| 6 / P2 | Learning 顶层分页合并编排候选的剩余页 | 只有编排候选时的 50/105 条边界，全部候选及历史可遍历 |
| 7 / P3 | 服务投影分别提供推广状态和已保存策略的实际资格，GUI 动态展示 | 证据不足、推广、无收益撤回、未授权、通配授权的 API 与组件用例 |

实现提交：`072fd27`、`64cb4d8`、`1d5a4af`、`6a27dc2`。
最终验收与集成状态：[Subplan 13 验收](../acceptance/stage-8-subplan-13-review-remediation.md)。
