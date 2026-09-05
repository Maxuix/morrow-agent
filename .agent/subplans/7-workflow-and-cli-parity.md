# 子计划 7 — Workflow 融合与 CLI 功能闭环

> 主计划：[Chat 工作台补全](../PLAN.md)
> 状态：[ ] 待开始。
> 分支：feat/chat-cli-parity
> 依赖：子计划 1–6 的合同、Chat、工作区、设置和附件已集成。
> 对应要求：C12–C14；全量操作等价。
> 主要旅程：A10–A12。

## 目标

把现有 Workflow 能力融入 Chat，并为所有剩余 CLI 产品功能提供可完成操作的 GUI。
逐命令覆盖清单中每个业务操作都必须映射到服务、API、界面与测试，不能只增加总入口。

## 修改归属

- application 的现有业务服务与共享命令/交互 facade：仅补齐缺少的类型化动作。
- server 的受控管理查询/命令与产品结果投影。
- GUI：WorkflowPanel/EditorShell/GraphPlanner/Replan/Evaluation，MCP/Skill/Learning/Memory/
  Preferences/Agent/State/Recovery 完整管理页和命令菜单。
- 各功能的定向 CLI/API/GUI 等价测试及覆盖证据。
- 不增加第二 Compiler、权限解析器、Artifact writer 或全局 Replan writer。

## 顺序任务

- [ ] 7.1 重新发现当前 CLI/REPL/manage 操作，与子计划 1 清单做差异。
  标记 2–6 的已验证项，剩余项逐一分组并在当前 TODO 保持一个逻辑任务在做。
- [ ] 7.2 实现输入区的普通 Agent/编排建议/指定 Workflow 入口。
  普通 Chat 沿用原路径；Draft 预检/冻结/自动推广及用户选择沿用现有服务。
- [ ] 7.3 在中心显示任务请求、计划、进度、审批、TaskOutcome/产物和追问入口。
  Workflow 结果是有来源的卡片，叶子完整对话不并入用户 log。
- [ ] 7.4 衔接右侧节点、Agent、文件/Diff/Artifact 详情、Pause/Resume/Cancel/Rerun，
  future-only 编辑与 continuation；全屏编辑后回到原 Chat 和草稿。
- [ ] 7.5 补齐 Agent/Workflow Definition 的创建、编辑、克隆、校验、发布、启停/撤销，
  Replan/Patch/Policy、评估与证据推广全部 CLI 等价。
- [ ] 7.6 补齐 Preferences、Profile、Inbox jobs/review/批量操作、Learning 模式/候选/
  promotion/undo，以及 Knowledge/Memory selection 的查询与生命周期。
- [ ] 7.7 补齐 Skill 的安装/校验/启停/版本 pin/rollback/remove/usage 和全部 Draft 生命周期。
  保持 Draft 审阅/发布/启用三者区别，管理内容不能自行扩大运行权限。
- [ ] 7.8 增加 MCP 完整管理：add/list/show/inspect/status/enable/disable/remove/refresh，
  支持已有 scope/传输和安全凭据配置；离线 fixture 不连接真实 MCP 服务。
- [ ] 7.9 补齐 Task/Artifact/AgentRun/Grant/Recovery：创建与状态操作、pin/release、
  明确来源的重试、恢复报告与决定；不把未知结果重新执行当作恢复成功。
- [ ] 7.10 补齐 State doctor/events/backup/verify-backup/cleanup，遵守维护锁/静止状态要求；
  备份通过受控文件入口提供，清理保留预览和现有确认语义。
- [ ] 7.11 补齐 REPL /new、/status、/workspace、/compact、/task、/accept、/grant、
  /recovery、/learn、/memory、/preferences 及旧别名/迁移提示。新增快捷命令走类型化目录。
- [ ] 7.12 执行每组 CLI/API/GUI 等价回归和浏览器管理旅程，补齐每项覆盖证据。
  完成整合离线/GUI 检查，消除占位按钮和无入口操作。

## 命令等价要求

| 类型 | 交付 |
|---|---|
| 用户常用操作 | 可理解表单/按钮/结果和错误恢复，不要求用户知道 command_id |
| 同一业务命令不同作用域 | 展示实际 scope；既有 revision/来源/权限一致 |
| 修订冲突与重复调用 | OCC/幂等和 CLI 一致，不能通过刷新覆盖用户未提交编辑 |
| 长耗时操作 | 可见提交/执行/失败状态，不占住停止/审批队列 |
| manage 的通用入口 | 每个业务类型有对应功能页；高级结构化请求只作辅助 |
| headless JSONL/TTY | 行为在 Chat 等价，输出载体保留 CLI；明确记录适配 |
| /exit、serve、gui | 离开交互/连接状态与启动器语义，不关闭其他 Session 的 Core |

现有业务操作不可用“请到终端运行这条命令”作为完成方式。需要维护停机的操作要由 Core
提供安全可执行的流程与实际占用说明，不伪装为普通无影响按钮。

## 验证用例

- 同一 scripted 任务在普通 Chat、CLI 接入与显式 Workflow 的状态、权限和产物归属一致。
- 编排建议/手工 Draft/符合既有推广条件的自动执行，均使用现有 Compiler/Policy。
- 活动运行的 Pause/Drain、Future 修改、continuation、blocked/unknown recovery 语义不变。
- 节点输出/任务结果无叶子 log 泄露；回到 Chat 可继续追问。
- 每条业务操作有成功、适用的 stale/重复/权限失败证据，页数不能只覆盖第一页。
- Skill/MCP/Knowledge 的来源、revision、启用和运行解析与 CLI 一致。
- state 维护遇到活动工作时正确协调，backup/verify/cleanup 不破坏附件引用。
- 斜杠命令已识别则交互执行，未知命令明确报错，不变成 Shell 或模型的假命令。

## 验证与退出

按 7.5–7.11 每个逻辑任务先跑其对应定向测试，再提交已验证的小改动；
切片结束运行完整离线 pytest、GUI typecheck/test/build、Ruff format/check、compileall、
CLI help 与 git diff --check，并完成真实 scripted 浏览器功能组旅程。

退出时操作覆盖表必须有实际服务/API/GUI/测试和适配依据，不留未归属业务操作。
对“已有 GUI”也需回归，不能直接沿用旧通过标记。下一子计划为 8。
