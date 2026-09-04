# Stage 8 Subplan 5：通用 Workflow 基础真实用户模拟

## 1. Verdict

**请求中的两项运行时纠偏通过，适合已执行的用户目标。** 内置多 Agent 起点不再依赖角色专用
Artifact 转接；用户可从同一普通图克隆、插入 Web Developer、删减到单节点并运行。请求 cap 与
admission timeout 缺省为 `None`，但 request/usage 继续持久化计量。

场景计数：**PASS 8、FAIL 2、BLOCKED 0、NOT RUN 1、INCONCLUSIVE 0**。两个 FAIL 都是中间态：
首次实现被隐藏 oracle 找到结果质量缺口；第一次纠正运行遇到真实 Provider `invalid_response`。
公开 `task resume → workflow rerun` 仅重跑失败节点后，最终用户目标和全部隐藏 oracle 通过。请求
范围内没有 release-blocking 产品缺陷；Provider 单样本可靠性和成功终态中的历史失败项展示另列为
非阻塞风险。

## 2. Test basis

- 被测实现：`c4b1c4ef1f64eec470211e8858d45e7ad0d750cb`；真实运行发生在该提交前的等同行为工作树，
  之后未再修改运行时代码。
- 基线：`main@c4fd7314f78325deec444655ea9288c7b8b8721a`；topic branch
  `refactor/general-workflow-runtime`。
- 时间：2026-09-04 17:45–18:11（Asia/Shanghai）。
- 平台：Darwin 25.6.0 arm64；Python 3.13.0；公开 `uv run morrow ...` CLI。
- 隔离：state root `/private/tmp/morrow-generic-e2e.vymgvv/state`，工程
  `/private/tmp/morrow-generic-e2e.vymgvv/project`；两者均可丢弃，真实仓库不是模型写入目标。
- Provider-backed 测试由用户明确授权并实际执行。目标为已配置的
  `openai-compatible` / `opencode-go/deepseek-v4-flash`；只复用 Keychain 引用，未读取、复制或记录
  credential 值。一次性 state 只复制非敏感 Provider/Model 配置。
- 公开接口：Provider list/show/test，交互式 Workspace 登记，Agent list/create/validate/publish，
  Workflow list/clone/edit/validate/publish/run/status/rerun/resume，Task new/show/resume，State
  doctor/backup/verify-backup。
- 外部对照只用于校准设计，而非代替本项目证据：OpenAI Agents SDK 的 `max_turns=None` 可关闭
  turn limit，usage 仍单独跟踪；Claude Code 的 `--max-turns` 是可选非交互参数；OpenCode 把
  `steps` 与 doom-loop 权限分别配置。参见 [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)、
  [OpenAI usage](https://openai.github.io/openai-agents-python/usage/)、
  [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code/cli-usage) 与
  [OpenCode agents](https://opencode.ai/docs/agents)。这支持“有限请求数应由用户/调用方选择，loop
  控制不等同于固定任务预算”的方向，但不声称所有框架实现完全相同。

## 3. User Surface Inventory

| Surface / capability | Implementation evidence | Reachable states or modes | Scenario IDs | Coverage status |
|---|---|---|---|---|
| 内置起点发现 | `workflow list/show`；`builtins.py` | Direct、最小 EIV；未发布/已发布 | S01 | PASS |
| 通用 result 链 | `TextResult@1/result` binding；Compiler v2 | 3 节点、插入节点、删减为 1 节点 | S01、S03、S07 | PASS |
| Clone 与 desired-source OCC | `workflow clone/edit` | stale、create、repeat clone、edit | S02、S07 | PASS |
| 自定义 Agent/节点 | `agent create/validate/publish` | user Agent、write node、串行多 Writer warning | S03 | PASS |
| 可选执行 guardrail | nullable Workflow/Node fields；Scheduler/admission | 无上限、显式有限上限、51 请求 | S01、S05、S10 | PASS |
| Provider-backed Workflow | `workflow run/status` | completed、failed、rerun completed | S05–S08 | PASS（含中间失败） |
| 结果与 Artifact | 通用 TextResult effective outputs | task input、每节点 result、最终 result | S05、S08 | PASS |
| 用户恢复控制 | `task resume`、`workflow rerun/resume` | failed → open → rerun running → completed | S08 | PASS |
| 用户主动 Pause/Stop | `workflow pause`、前台 Ctrl+C；既有离线测试 | queued/running/paused/cancelled | S11 | NOT RUN（本轮未故意中止额外 Live Run） |
| 状态治理 | `state doctor/backup/verify-backup` | healthy、完整 bundle、credential exclusion | S09 | PASS |

GUI 编辑器尚属后续 Subplan 6；GraphPlanner、并行执行和其他 Provider/平台未伪装成本轮覆盖。

## 4. Scenario results

| ID | Persona and real task | Preconditions | User actions | Expected | Actual and evidence | Status |
|---|---|---|---|---|---|---|
| S01 | 用户发现最小起点 | 已配置模型 | `agent/workflow list` | 只见 Direct/EIV；链路和限制通用 | 仅 2 个起点；所有 output 为 TextResult；三项限制均 null | PASS |
| S02 | 用户从建议开始自定义 | desired revision 0 | 先用 stale revision clone，再用正确 revision | stale 无写入，正确 clone 成 user source | stale exit 2；随后 source revision 1、`origin=user` | PASS |
| S03 | 全栈用户插入 Web Developer | EIV 已 clone | 创建/发布 Agent；在 Coder 与 Reviewer 间插入普通节点；edit/validate/publish | 同一 Compiler 接受任意角色节点 | 4 节点 3 边通过；仅给出串行多 Writer/可选工具 warning | PASS |
| S04 | 发布者检查无猜测限制 | 自定义图已发布 | 检查 candidate/revision | Workflow、每节点 cap/deadline 均为空 | revision 与所有 NodeRun 均为 null；Compiler v2 | PASS |
| S05 | 用户用真实多 Agent 完成 Python + Web 功能 | 一次性项目有 6 个公开测试失败 | 运行四节点自定义图 | 通用 handoff、真实写入、公开测试通过 | `wrun_K_YCFwqNWLXoJRxg` completed/succeeded；32 请求；4/4 测试通过 | PASS |
| S06 | 独立验收者执行隐藏边界 | S05 terminal 后才创建 oracle | Python/JS hidden oracle + test-file byte compare | 跨语言 half-up 一致；空输入拒绝；测试未改 | 测试未改，但 Python banker’s rounding 与空 Web 输入失败 | FAIL |
| S07 | 用户删减为单节点纠正 | clone 自定义图后删到 Web Developer | validate/publish/run | 单节点完成两项纠正 | 图合法；首个 Run 7 请求后 Provider `invalid_response`，无源文件落盘 | FAIL |
| S08 | 用户从 Provider 失败恢复 | failed Run 与 root Task 保留 | `task resume` → `workflow rerun` → `workflow resume` | 只重跑失败节点并达成目标 | 新 accounting root completed/succeeded；13 请求；公开 4/4、隐藏 Python 2/2、JS oracle、byte compare 全过 | PASS |
| S09 | 运维者验证持久状态 | 成功/失败/rerun 历史共存 | doctor、backup、verify-backup | 引用完整，凭据排除 | `health=ok`；bundle 全项 true；`credentials_excluded=True` | PASS |
| S10 | 维护者验证不再受 12/48 阻塞 | scripted Provider | 跑 49 个工具轮次 + 2 个终态请求 | 同一 Workflow 超过旧 48 后仍完成 | 51 次 durable agent request，Run completed，Node cap 全为 null | PASS |
| S11 | 用户主动暂停或 Ctrl+C | 需要额外进行中的 Run | 未执行额外 Live 中止 | 可由用户决定暂停/停止 | 完整离线套件覆盖既有控制；本轮公开 Live 路径未抽样 | NOT RUN |

S05 的节点请求数为 Explorer 5、Coder 6、Web Developer 15、Reviewer 6。Web Developer 已超过旧
12 次默认值却成功完成，这是真实 Provider 对本次纠偏的直接证据。S08 的单节点又以 13 次请求
成功；S10 则确定性证明同一 Run 超过旧 48 次总上限。

## 5. Complex journeys

### J1 — 从只读建议到任意四节点图

用户先遇到 stale OCC 拒绝，再使用正确 revision clone EIV；随后创建全新的 Web Developer Agent，
把它插入 Coder/Reviewer 之间并重新绑定边与 `previous_result`。状态跨 Agent publication、desired
source revision 和 immutable Workflow revision 保存。最终普通 Compiler 接受该图，没有引入
Web-specific runner、EvidenceBundle 或角色专用转接层。

### J2 — 真实 Provider 多节点实现与隐藏验收

一次性项目从公开 Python 测试失败和 Web placeholder 开始。四个真实 Agent 通过同一种 TextResult
链执行探索、实现、Web 验证和 Review；Run 在 32 次请求后成功，其中一个节点用了 15 次请求。
外部 oracle 没有采信最终文字，而是在 Run 结束后验证真实文件，发现跨语言 rounding 和空输入两个
边界缺口。这证明 runtime 目标通过，但首次生成结果不能仅凭绿色 DAG 判定质量。

### J3 — 自由删图、Provider 失败与精确恢复

用户把已 clone 的四节点图删减为一个 Web Developer 节点。第一次真实运行的 13/15 个工具调用
成功，但第 7 次模型请求收到 `invalid_response`，Run 以 failed 保存且没有半成品源修改。用户恢复
同一个 root Task，并从失败 Run 创建新 accounting root；rerun 只执行失败节点，13 次请求后完成。
最终公开测试、隐藏 Python/JS oracle 和测试文件 byte compare 全通过。

### J4 — 运行历史到可验证备份

成功四节点 Run、失败单节点 Run 和成功 rerun 同时存在。Doctor 验证 schema 26、52 个 AgentRun
模型请求、100 个工具执行和所有引用均健康；完整备份随后验证 manifest、SQLite、外键、YAML、
Artifacts、references 与凭据排除。这覆盖了跨进程查询和持久恢复，而非只检查内存终态。

## 6. Findings

### P2 — 单次 Provider `invalid_response` 需要用户显式 rerun（非本次产品回归归因）

- 影响：纠正任务第一次没有完成，用户需执行恢复；该次 7 个请求已被如实计量。
- 最短公开复现：运行 `custom_web_fix`；本样本 `wrun_VwcHAHaMGBvHSIig` 在第 7 次请求终止。
- 期望：Provider 返回合法 tool/final response，或在明确 retry policy 内恢复。
- 实际：13/15 个工具调用成功、两次 edit 失败，最终 `stop_code=invalid_response`；文件保持未改。
- 可复现性：初次 1/1 失败，显式 rerun 1/1 成功，证据不足以归因于 Adapter 或模型；因此不把它
  记成本次 Workflow 重构缺陷。
- Workaround：公开 `task resume → workflow rerun → workflow resume` 成功，只重跑失败节点。

### P3 — 成功 outcome 仍展示已恢复的历史工具失败

- 影响：成功 Run 的 `unresolved_items` 可包含早先已被后续成功调用覆盖的 `bash/write failed`，可能
  让观察者误认为最终结果仍未解决。
- 最短公开复现：查看成功的 `wrun_owIao2cUlvCRbQ8o` 状态。
- 期望：历史失败保留在 validation facts，但 unresolved 只列当前仍需处理的项。
- 实际：Run completed/succeeded 且全部 oracle 通过，仍有两项历史失败被列入 unresolved。
- 可复现性：本轮成功四节点与成功 rerun 均出现；这是既有 outcome projection 行为，不由可选 cap
  或通用 result 链引入。
- Workaround：当前以 Run/Node terminal status、required output 和外部 oracle 判断最终完成；建议
  后续单独修正展示语义。

## 7. Coverage and gaps

- 本次纠偏发现的公开能力 10 类，执行 9 类：**90%（9/10）**；主动 Pause/Stop 未走公开 Live
  场景，但既有离线控制测试包含在 1609 项全量门禁中。
- 计划场景 11 个，执行 10 个：**90.9%（10/11）**；8 PASS、2 中间 FAIL、1 NOT RUN。
- 覆盖状态：first-use、stale OCC、create/edit/publish、completed、failed、Task resume、failed-node
  rerun、新 accounting root、跨进程 status、healthy backup。
- 复杂 journey：4 个，分别覆盖定义自定义、真实 Provider 生成、失败恢复和持久治理。
- Provider-backed：连接检查 1 个、Workflow 场景 3 个（成功、失败、rerun 成功）；无 credential
  blocker。
- 未覆盖：另一个已配置模型 `mimo-v2.5`、Linux/native sandbox、网络中断、rate limit、高并发、
  GUI 编辑器（未实现）、GraphPlanner（未实现）、只读并行（未实现）。这些样本不能外推为跨模型
  质量或长期稳定性结论。
- 结构化 `EvidenceBundle/ReviewReport` 的历史兼容由完整离线回归覆盖；本轮 Live 特意不使用它们，
  因为目标正是验证通用 result 链。

## 8. Provider evidence

- Adapter/Model：`openai-compatible` / `opencode-go/deepseek-v4-flash`。
- 真实请求：1 次 Provider connection check；3 个 Workflow Run 共 **52 次** durable agent
  generation request（32 completed + 7 failed + 13 successful rerun）。
- 真实工具：Doctor 汇总 100 次 tool execution；四节点任务自身 61 次；失败纠正 15 次；成功 rerun
  24 次。
- 约耗时：四节点 222 秒、失败纠正 167 秒、成功 rerun 75 秒；连接检查约 19 秒。
- Usage：成功 Run 可用；失败 Run 最后一条 usage 不可用，因此不汇总成伪精确总 Token。Provider
  未提供可用 cost 数据。
- 结果：真实通用四节点执行成功；隐藏 oracle 驱动的单节点纠正先失败后恢复成功；没有把 fake/
  scripted 51-request 测试当作真实模型质量证据。

## 9. Recommended next actions

1. 在后续运行控制 GUI 中把 `无上限` 与实时 request/usage 分开展示，并让 Stop/Pause 始终可见；
   不重新引入模版默认请求数。
2. 单独修复成功 outcome 的 `unresolved_items` 投影，使已恢复工具失败保留为历史但不冒充待处理项。
3. 对 `invalid_response` 建立按错误类型的统计与显式 retry/recovery UX；不要用固定总请求预算替代
   Provider 错误处理。
4. 若要推广到默认自动编排，再获得授权后抽样其他模型、网络错误、rate limit、长任务与主动
   Pause/Stop；当前证据只支持本次 runtime 纠偏和一个真实 Provider 样本。

## Validation gates

| Gate | Result |
|---|---|
| Workflow focused matrix | 158 passed |
| Full offline pytest | 1609 passed, 2 deselected in 176.61s |
| Ruff format/check | 596 files formatted；All checks passed |
| Compileall / CLI help / git diff check | PASS |
| GUI | typecheck PASS；31/31 Vitest；build and bundle budget PASS |
| Live Provider final oracle | checked Python 4/4；hidden Python 2/2；hidden JS PASS；test byte compare PASS |
| State integrity | Doctor health ok；backup verify all true；credentials excluded |
