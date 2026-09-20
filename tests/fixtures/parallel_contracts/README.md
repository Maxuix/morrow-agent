# 冻结契约（P01，四线共享）

> 迁移注记（2026-09-13）：本目录原位于 `.agent/parallel/contracts/`，随
> `f90f24c` 的清理从该处移除后迁入 `tests/fixtures/parallel_contracts/`，
> 作为历史快照供契约测试自包含使用；所有权归属等叙述仍按原文保留。

契约版本：**1.0.0**（`contract_version: "1.0.0"`，两侧代码内常量 `CONTRACT_VERSION`）。

基线 C0：本目录随 P01 一起提交；C0 是包含本目录的确切提交 SHA，由总控在
`../COORDINATION.md` 登记，并以四份启动词中的完整 SHA 为准。拿到 C0 后任何字段变更都必须：
总控提升契约版本（1.1.0/2.0.0）→ 更新本目录与两侧类型 → 发布兼容说明与新基线。口头约定不替代版本化契约。

## 消费方式

| 文件 | Python 侧 | TypeScript 侧 |
| --- | --- | --- |
| `wire-fixtures.json` | `tests/parallel_contracts/test_wire_fixtures.py` 往返校验 | `gui/src/api/contracts.test.ts` 结构校验 |
| `ownership.json` | `tests/parallel_contracts/test_ownership.py`（存在文件必须真实存在） | — |
| 类型定义 | `src/morrow/core/contracts.py`（coordinator 独占） | `gui/src/api/contracts.ts`（coordinator 独占） |

类型模块只冻结跨线边界；各线内部类型留在各自 owner 文件里。`core/contracts.py` 全部模型
继承 `ProtocolModel`（`extra="forbid"`、frozen），未知字段即契约违反。

## 冻结接口

### 1. 中断信号与本轮结果（A 实现，D 消费展示）

- `TurnInterruptOutcome`：本轮被用户暂停中断后的 typed 终态。
  `finish_reason="interrupted"` 与 `stop_code="user_pause"` 是冻结的新字面值；实施线在
  `core/models.py` 的 `FinishReason`/`AgentStopCode` 中新增同名字面值，不得复用
  `cancelled` 冒充暂停。`committed_position` 是最后一个 durable conversation position；
  `partial_text` 为有界展示片段（≤8192 字符），不得拼接成完整回复。
- 暂停意图先持久接纳（`PauseIntentFact`），再进入 `requested → quiescing → suspended →
  resumed` 生命周期；cancel 是独立事实，不得覆写为 `resumed`。
- 同一轮内模型等待/工具准入/工具结果提交后都要按 `control_generation` 检查暂停意图；
  迟到事件（旧段）不得驱动新段。

### 2. 执行段身份与当前段查询（A 实现存储，C/D 消费身份）

- `ExecutionSegmentIdentity`：append-only 段；`(workflow_run_id, node_run_id, ordinal)` 唯一，
  节点同一时刻至多一个 `status="active"` 段。旧 `NodeRun.agent_run_id` 保持可读并映射为
  首段；当前执行者一律经 `SegmentDirectoryPort.current_segment(node_run_id)` 查询。
- `SegmentDirectoryPort`：A 的 segment repository 提供真实实现；C/D 与测试可用
  `morrow.testing` 的 fake。

### 3. 规划控制命令、回执与请求结果（B 实现）

- 控制回执复用现有 `core/control_receipts.ControlReceipt`（v39 表）；先持久接纳，同
  `command_id` 同 payload 重放返回原回执，同 ID 不同 payload 冲突；合法 replay 检查先于
  版本（`expected_run_row_version`/revision）检查。
- `PlanningRequestOutcome`：三层结果（`model_request` / `candidate_validation` /
  `planning_operation`），每层允许 outcome 见模块内 `*_OUTCOMES` 常量；持久事实必须含
  `started_at`/`ended_at`/`revision`。request 收束先于 operation terminal 提交。
- 请求 sequence 与 invalid-plan 重试次数分开：`attempt` 是 1-based 操作重试展示，
  `request_sequence` 是每次真实模型调用的序号；暂停后恢复用新 `request_sequence`，
  不重置校验预算、不覆盖旧请求。

### 4. 同事务 timeline sink 与提交后通知（C 实现真实，A/B 调用）

- `TimelineSinkPort.record(identity)`：调用方在自己持有的事务内记录展示条目；实现不得
  自行提交，条目随源事实同事务生效，回滚则一并消失。source identity
  `(workspace_id, root_session_id, source_kind, source_id)` 全局唯一去重。
- `PostCommitNotifierPort.notify_after_commit(entry)`：提交后广播；事务回滚不得广播。
  当前 EventHub 的 after-commit 扇出是进程内投影，持久 outbox 由 C 在同事务内补扫兜底。
- `TimelineEntryIdentity` 的 `kind` 冻结集合：`user_message`、`assistant_message`、
  `turn_status`、`interruption`、`tool_activity`、`planning_input`、`plan_version`、
  `control_input`、`node_progress`、`result`、`artifact_ref`。新增 kind 需要契约版本提升。
- 展示索引独立于 `conversation_position`：`timeline_position`/`revision` 归 sink/索引
  owner（C）分配；调用方不写根 Session 模型上下文，不新增第二条 user_message。

### 5. 安全内容 offset（C 存储正文，A/B 产生）

- `SafeContentRef`：`committed_offset` 语义冻结为"客户端只收到已提交 offset"；有乐观未
  保存尾部时 `availability="pending"`/`"unsaved"`，不得声称重启后逐字恢复。已有
  ToolResult/Artifact 正文不复制，用 `content_ref` 受控引用。

### 6. snapshot → subscribe 高水位、cursor reset

- `TimelineCursor`：`schema_version` 冻结为 1；`high_water` 是快照已含的最大
  `timeline_position`；订阅起点 = `high_water`。新条目不移动旧页边界。
- `TimelineSnapshotPage.reset=true` 表示旧 cursor 无法增量续读，客户端必须重取快照；
  不得把 `conversation_position` 当新 cursor。
- 快照与订阅之间的提交必须可回放：SSE 丢包/重复/乱序/epoch 更换都不丢不重；epoch 只
  代表传输实例。

### 7. GUI ApiClient 请求（D 消费，扩展须向总控申请）

- 控制输入：`POST .../control`，body = `GuiControlRequest`
  （`command_id`、`session_id`、`text`、可选 `client_message_id`）；响应 =
  `ControlReceipt` 的 JSON（字段见 `core/control_receipts.py`）。
- 暂停：`GuiPauseRequest`（`command_id`、`session_id`、可选 `expected_run_row_version`），
  主界面 pause 与 cancel 是两个独立动作；文字控制继续走 control 端点原文接纳。
- execution/control 投影沿用现有 `ExecutionView`（`gui/src/api/chat.ts`）；状态机新增
  `pausing/paused` 已存在，`allowed_actions` 必须由服务端投影给出，D 不得本地另推一套。

## 错误、OCC 与幂等规则（全接口通用）

1. 所有控制命令携带 `command_id`（≤128）与准确 owner identity；重放同 ID 同 payload
   返回原结果，同 ID 不同 payload 报冲突；先 replay 检查后 OCC 版本检查。
2. OCC 用持久 `row_version`/`expected_*` 与 `control_generation`；旧代次确认不得覆盖新
   代次意图；重复 continue 不产生第二个 driver。
3. 失败不静默：索引/内容写入失败必须可诊断、可补偿（同事务 outbox 补扫），不得
   `except: return` 后宣称已保存。
4. 身份：`workflow_run_id/node_run_id/segment_id/item_id/tool_execution_id` 等一旦提交
   不可复用；被中断请求的新调用使用新 identity，关联旧段而非覆盖。

## 时序与所有权

- 暂停：接纳意图（持久）→ 收束模型等待/工具 → 段进入 `suspended` → 发布 paused 投影；
  恢复：新 Turn/AgentRun/`request_sequence` 接纳 → 校验权限/预算/deadline → 继续原节点。
- 提交顺序：业务事实 + timeline 索引同事务 → 提交后广播 → 前端按 `item_id` 幂等合并。
- 文件 owner、worktree、分支与迁移注册责任见 `ownership.json` 与
  未列明且可能共用的文件先向总控申请。
