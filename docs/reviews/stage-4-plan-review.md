# Stage 4 计划可行性 Review

> 对象：[`.agent/PLAN.md`](../../.agent/PLAN.md)、[`.agent/subplans/35-stage4-contract-activation.md`](../../.agent/subplans/35-stage4-contract-activation.md) 至 [`45-stage4-acceptance.md`](../../.agent/subplans/45-stage4-acceptance.md)、[`docs/roadmap/stage-4-task-session-and-persistence.md`](../roadmap/stage-4-task-session-and-persistence.md)、[`docs/decisions/stage-4-operational-store.md`](../decisions/stage-4-operational-store.md)
> 对照：当前代码、[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)、[`docs/ROADMAP.md`](../ROADMAP.md)、三份 `docs/research/morrow-stage4-*.md`、spike `tests/test_stage4_operational_store_spike.py`
> 日期：2026-08-19
> 代码基线声明：`003dbdaab652520ca5cadf451ebca7a13bcba36d`（生产适配器尚未开始）
> 结论：**方向正确，锁定路线比研究稿更可落地；但按原文进入 Subplan 36 会在状态机、幂等、ConversationLog 提交边界和恢复证据上撞墙。有条件批准。先在 Subplan 35 的剩余 ADR 里关掉阻塞项，再激活 36。**

---

## 0. 一句话判断

这份 Stage 4 计划把“给聊天加个 SQLite”正确地拒绝了，改成建设一层前台 Durable Execution Substrate：单一 Operational Store、ConversationLog 仍是消息语法权威、副作用前持久化、崩溃只分类不对账改写、不建设 Outbox/后台 Worker/代码 rewind/Full Access Auto。这个产品判断是对的，也已经比三份研究稿更克制。

问题不在方向，而在三件事：

1. **锁定合同内部有几处互相否定**，尤其是 `client_message_id` 只跑一次模型，和崩溃恢复必须新建 AgentRun。
2. **后继 subplan 把尚未写完的 ADR 当成已经锁死的实现规格**，37/38/40/43 之间有所有权泄漏。
3. **当前代码是进程内 Turn 机，不是半成品持久化层。** 计划里若干句子把现状说成已经具备接入点，实际上是新协议。

**建议：保留锁定路线、切片顺序和安全不变量。不要按研究稿把 Auto、Rewind、Outbox、RunClaim、approval nonce 拉回来。不要激活 Subplan 36，直到 S4.35.3–S4.35.8 把下面的 P0 写成 ADR，并让 36–45 不再与 ADR 打架。**

---

## 1. 总体结论

| 问题 | 判断 |
|---|---|
| Stage 4 要不要按当前路线做 | 要。先可恢复前台 Session/Task，再学习、Skills、Workflow。 |
| 锁定路线是否符合 roadmap | **符合。** 目标闭环、排除项、Full Access Manual only、无 rewind、无 Auto 都对齐。 |
| 研究稿能否当实施合同 | **不能。** 研究 V2 仍写 Auto、WorkspaceCheckpoint/rewind、Outbox、RunClaim、nonce；锁定计划已否决。 |
| 计划能不能原文成为实施合同 | **还不能。** P0 未关之前，36 之后的实现者必须猜。 |
| 是否存在路线越界 | 锁定计划没有越界。风险是研究稿和未完成 ADR 把越界项再带回来。 |
| 是否存在范围不足 | 有：进程身份、expected-after、幂等 in-flight、schema 版本地图、系统提示、`/new` 产品语义。 |
| 是否过度设计 | 11 个串行 subplan 偏长，但刻意串行是对的。真正偏重的是把 CLI 全部堆到 43，以及 37 提前锁死 40 才该锁的 TaskRun 语义。 |
| SQLite 基础是否可行 | **可行。** ADR + spike 已经证明 WAL/`BEGIN IMMEDIATE`/崩溃提交/维护锁/在线备份的核心路径。未证明的是迁移和应用层提交协议。 |
| 崩溃恢复是否可行 | **分类可行，自动重放不可行。** Host/沙箱在现有代码里没有可跨进程证明的 PID/快照。39 必须按“证据不足即 unknown”写，不能按研究稿承诺 sandbox retry。 |
| 能否按原文做完并验收 | 无工具多轮恢复、文件 hash 对账、备份、Manual grant 能做。若 37 把“每个 client_message_id 只跑一次模型”写成硬测试，崩溃恢复会被自己的门禁卡死。 |

对应实施决策：

- **不批准将当前 36–45 原文直接开工。**
- **批准继续 Subplan 35**：把 P0/P1 写进 ADR，改后继 subplan 的锁定合同，给研究稿盖“决策输入、非实施规格”的章。
- 现行权威仍是 `.agent/PLAN.md` + 已接受的 Operational Store ADR + `docs/roadmap/stage-4-task-session-and-persistence.md`。研究稿只提供语义参考。

---

## 2. 审阅范围与方法

审阅覆盖：

- 主计划、11 个子计划、Stage 4 路线图、Operational Store ADR、spike 测试。
- 三份研究文档与锁定计划的分歧。
- 当前 `Session`、`ConversationLog`、`AgentLoop`、`ToolExecutor`、审批、文件变更、Host/沙箱、`ContextBuilder`、CLI/CommandService、`DataRoot`、公开事件。

未做：生产实现、新 spike、改计划。本文件是计划审查，不是执行状态更新。

---

## 3. 计划做对了的部分

这些应保留，不要在 ADR 里放松。

| 锁定项 | 为什么对 |
|---|---|
| 一个 data-root SQLite + 文件系统 Artifact，不是每仓库一个库 | 与现有 `~/.morrow` 布局一致；工作空间隔离用 `workspace_id`。 |
| YAML / CredentialStore 不迁入 SQLite | 避免第二配置权威。 |
| 标准库 `sqlite3`，无 ORM / 无 daemon / 无 FTS5 | 事务和崩溃语义可审查。 |
| `BEGIN IMMEDIATE` + WAL + `synchronous=FULL` + 有界 busy retry | spike 已证明提交/回滚/竞争的核心事实。 |
| 全局 `operational-store.lock` 只管维护，不管普通写 | 现有 `WorkspaceWriterLock` 是 per-workspace REPL/YAML 锁，管不了共享库。 |
| 未来/损坏/外来文件拒绝且不改写 | 与 Stage 1/3 的 YAML 损坏合同一致。 |
| ConversationLog 仍是消息语法权威；AgentLoop 走 `run_task()` | 保住 Stage 2/3 最硬的不变量。 |
| 独立 `EffectClass`，不拿 `ToolEffect` 做崩溃重放 | 当前 `ToolEffect` 只有 `none/session_write/persistent_write`，且 Host 被标成 `NONE`。 |
| 不自动重放 Host；沙箱不因“看起来像沙箱”就重试 | 现有进程适配器不持久化 PID，父死后子进程可残留。 |
| 文件对账用 hash/size，不用 `FileRevision.mtime_ns` | 现行冲突检查已经只用 sha256；mtime 只是元数据。 |
| 无 Outbox、无 RunClaim/租约、无后台 Worker | 单机前台产品不需要。Cursor replay 够用。 |
| 无 workspace/code rewind | 现有变更工具不能诚实恢复 Shell/外部/链接变化。 |
| Full Access Manual only；`full_access + auto` unsupported | 与当前 `CapabilityPolicy` fail-closed 兼容。 |
| 公开事件生命周期是 43 的 hold point | `lifecycle_is_valid` 被大量测试锁死。 |
| 一次只激活一个 subplan | 防止 37 还没站稳就写工具表。 |
| 研究稿降为决策输入 | 正确。问题是研究稿自己还自称实施主计划。 |

---

## 4. 当前代码基线（计划必须正视的事实）

计划多处把现状写成“很好的接入点”。接入点存在，但**持久化协议几乎要从零建**。

### 4.1 现在实际是什么

```text
bootstrap 每次新建 Session(ses_…)
→ REPL 持有 WorkspaceWriterLock(workspace_id)
→ 斜杠走 CommandService
→ 普通输入走 runtime.run_turn → AgentLoop.run_task
→ ConversationLog 在内存里分配 sequence 并立即 append
→ 工具：先 append Assistant(calls)，再执行 handler，再 append ToolMessage
→ 退出即丢失
```

关键事实：

- `Session` 在进程启动时创建，不是第一条用户输入时创建。`dirty` / `read_only` 不是 lifecycle/health。
- `turn_id` 与 `ToolRunContext.run_id` 是同一个字符串。没有 TaskRun / AgentRun。
- `ConversationLog._next_sequence()` 是唯一顺序权威。记录没有 `record_id`、`client_message_id`、所属 Turn/Run。
- `run_turn()` 是 `run_task` 的薄别名，**生产路径带着 ToolExecutor**。计划/AGENTS 写的“thin no-tools delegate”是过期句子。
- 审批只有 `approved: bool`。没有 approval_id、intent hash、consume、expiry。
- `ChangeSet`、`ToolFact`、`CommandPlan`、沙箱 `SandboxChangeSet` 全部进程内。`Session.retain_run_facts` 明确写着 never persist。
- Host 适配器 `start_new_session=True`，不记录 PID；沙箱快照在 `finally` 里删除。崩溃后不能证明进程已死，也不能重入快照。
- `run_command` 的 `OperationIntent.effect` 被写成 `ToolEffect.NONE`。这是合同谎言，不能遗传给 `EffectClass`。
- 系统提示仍说：“聊天记录只存在于当前进程，不能声称可跨进程恢复。”
- `/new` 在 dirty 时是“确认丢弃进程内对话”；没有 `/accept`、`/task`、`/continue`。Handoff 已从产品面删除。
- `test_plain_chat_turn_persists_no_state_document` 断言一轮聊天不得增删改任何状态文件。37 一旦写 SQLite，这条测试必须改语义，不能假装聊天仍无状态。

### 4.2 对计划句子的直接纠正

| 计划/AGENTS 用语 | 代码事实 |
|---|---|
| `run_turn()` 是 thin no-tools delegate | 薄委托，但是同一条带工具的 `run_task` |
| ConversationLog 是唯一写入者 | 唯一**追加语法**；`/new` 会 `reset()` 清空历史 |
| SQLite 是 sequence 权威 | 现在是内存 `_sequence` |
| 第一条普通输入创建 TaskRun | 第一条输入只 `begin_turn`；Session 早已存在 |
| 最终 Assistant 表示 TaskRun `completed` | 只关闭一个 ConversationLog public turn |
| persist-before-effect | 工具路径是 effect-before-any-durable-record |
| `client_message_id` | 不存在 |
| Session lifecycle vs health | 只有 `dirty` / `read_only` |
| `/new` 创建新 Session | 换一个 `ses_` 并清空内存；没有 durable 行 |

---

## 5. 阻塞项（P0）

这些不在 ADR 里关掉，后继 subplan 会做出互斥实现。

### P0-1. `client_message_id` 一次模型运行 vs 崩溃恢复新建 AgentRun

**冲突双方：**

- Subplan 37 完成门禁：“exactly one model execution per accepted client message”。
- 主计划锁定语义：无新用户输入的 crash resume，在**同一 open Turn** 里创建**新 AgentRun**。

一次崩溃恢复必然产生第二次模型运行，却仍是同一个 `client_message_id`。按 37 的字面测试，39 的恢复路径非法。

更糟的是 in-flight 窗口：

```text
提交 → 持久化 UserMessage + CommandReceipt
→ 发出 turn.started / 调用模型
→ 进程在 Assistant append 前死亡
```

若 receipt 视为已提交，重放同一 `client_message_id` 不得再跑模型；若没有恢复入口，用户被卡住：既不能重试，也没有回答。

**ADR 35.3 必须写成：**

| 提交状态 | 重复同一 key + 同一 payload | 同一 key + 不同 payload |
|---|---|---|
| 未落盘 | 当作首次提交 | — |
| 已接受、Turn 仍 open / AgentRun interrupted | 返回已有 receipt，并进入恢复/继续同一 Turn；**允许**新 AgentRun | 冲突 |
| 已接受、Turn 已闭合 | 返回已提交结果，零新模型运行 | 冲突 |

“每个 client_message_id 只产生一次 UserMessage / 一次 Turn 接纳”，不是“整个宇宙只跑一次模型”。37 的测试句子要改。

不要把 `client_message_id` 塞进 `UserMessage`。它是命令字段，不是聊天记录字段。

### P0-2. TaskRun `completed` 不是终态，但 37 已把它当产品语义锁死

锁定规则同时说：

- 最终 Assistant 使 TaskRun 进入 `completed`，但不等于用户接受。
- `completed` 之后的普通输入默认仍是同一 TaskRun 的继续/纠正。

研究稿有显式转移：`completed → corrected → active`。锁定计划把这条状态机推给“35 可以 refine names”，37 却已经要求“final assistant yields a completed-but-not-accepted TaskRun”，40 才拥有完整状态机。

若 37 把 `completed` 写成不可再写的终态，40 只能破坏 37 的测试。若 37 把每次成功回答都标 `completed`，查询“当前任务是否完成”会对每一轮普通闲聊都为真。

**ADR 35.3 必须画出合法状态图。** 建议不要让 `completed` 同时表示“这一轮说完了”和“用户目标结束了”。更干净的拆法：

```text
active
  → waiting_acceptance   # 最终 Assistant 已闭合 Turn，等待 /accept 或继续
  → accepted             # /accept
  → cancelled / failed / abandoned

waiting_acceptance + 普通输入
  → active（记 continuation/correction，旧 Outcome 不被改写）
```

若坚持保留 `completed` 这个词，必须写明它可重入，并给出 `completed → corrected → active`。37 只应持久化 **current TaskRun 指针**，不要提前实现 40 的终态语义。

### P0-3. ConversationLog 提交边界 vs 现有语法：恢复必须补写 ToolMessage

计划要求：

- 先校验，再原子提交，再从已提交记录更新内存投影。
- 恢复只能走窄 API，不能合成成功结果。
- 副作用前不得存在内存/数据库双写窗口。

当前对象**就是** sequence 权威：`append_*` 先分配序号再写入 `_records`。这不是加一个 wrapper，是重写最热路径。

同时，现有语法禁止带着未解决 call 去 `finish_turn`。今天 `AgentLoop._close_unresolved` 会为剩余 call **合成错误 ToolMessage**。崩溃后若 intent 已提交、ToolMessage 未提交，恢复要闭合 ToolCycle，就只能走同一条路。

**ADR 必须锁：**

1. 唯一提交顺序：`ConversationLog.validate(candidate) → BEGIN IMMEDIATE → 写 conversation + 伴随行 → COMMIT → 用已提交行替换投影`。禁止先改 `_records` 再异步 flush。
2. 恢复允许追加的**仅有**一类记录：标记为 interrupted/error 的 ToolMessage（或等价的合法闭合信封）。禁止成功信封，禁止补 User/Assistant。
3. `reset()` / fork / restore 是 ConversationLog 的显式方法，不是 SQL helper。
4. 内存 `sequence` 与公开事件 `AgentEvent.sequence`、未来 `application_events` cursor 必须换名或分命名空间。三者现在都叫 sequence，不是同一个计数器。

### P0-4. 研究稿仍自称实施主计划，且含已否决能力

锁定计划写得很清楚：研究文档是决策输入。但研究稿自己写的是：

- `docs/research/morrow-stage4-complete-implementation-plan-v2.md`：“可作为新的 `.agent/PLAN.md` 基线”，范围包含受控 Full Access Auto、WorkspaceCheckpoint/rewind、EventOutbox、RunClaim、approval nonce。
- `docs/research/morrow-stage4-agent-plan-v2.md`：指向不存在的 `docs/implementation/stage-4-complete-plan.md` 和 `docs/research/stage-4-mature-agent-reference-review.md`。
- 参考评审里的 subplan 编号（38 nonce、43 rewind、47 Auto）与现行 35–45 不是同一套。

这不是文风问题。后续实现者（包括以后的 agent）会打开研究稿并按 Auto/Rewind/Outbox 施工。

**S4.35.7/35.8 必须：** 在三份研究稿顶部盖“已被 `.agent/PLAN.md` 取代；下列能力已明确延期”的横幅，并列出否决项。缺页链接删掉或改成真实路径。

### P0-5. 恢复证据今天几乎不存在；39 若按“可证明”来写会假安全

计划要求沙箱仅在“旧进程已终止、无推广、无外部效果”时可安全重试。当前事实：

- 不持久化 PID/pgid。
- Host/沙箱都 `start_new_session`；macOS 没有 die-with-parent。
- 沙箱目录是 `/private/tmp/morrow-sandbox-*`，调用结束就删；崩溃后只剩无法关联的孤儿目录。
- `expected_after` hash 只存在于写盘之后的内存 `after_revision`。写盘前的 `desired_raw` 可以 hash，但现在没做。
- `promote_sandbox_changes` 依赖进程内 `SandboxChangeSet`；快照已删，重启后无法再推广。
- `update_configuration` 连 ToolFact 都没有。

**可行合同：**

- Host：intent 已提交且无 `handler_completed` → 永远 `outcome_unknown`，禁止自动重放。不要为了“更强”去猜 PID。
- 文件变更：38 必须在 handler 前持久化 `before_sha256`、`expected_after_sha256`、`expected_size`、父目录条件。没有这些字段就不要声称可对账。
- 沙箱：首版与 Host 同等对待，除非 38/39 新增 durable `temp_root` + pid/pgid + 未 cleanup 标记。否则“可安全重试沙箱”是假能力。
- 推广：按文件变更对账，不按沙箱重试。

---

## 6. 高优先级问题（P1）

### P1-1. Subplan 所有权泄漏，串行路线被提前锁死

| 泄漏 | 风险 |
|---|---|
| 37 实现 TaskRun `completed-but-not-accepted` | 抢 40 的状态机 |
| 37 AgentRun 快照含 PermissionSnapshot | 44 才会有 grant；schema 必改 |
| 38 同一事务写 application events | 43 才拥有事件投影 |
| 40 TaskOutcome 引用 Artifact | 41 才有 Artifact |
| 43 doctor 检查 grant invariants | 44 才有 grant |
| 43 才出现全部 CLI | 37–42 的产品语义没有操作面，回归只能打服务层；43 会变成巨型接线 PR |

**改法：** 37 只做 Session + 单调 conversation + Turn 接纳 + **最小** current TaskRun 指针（`open` 即可）。38 只写 journal/approval 表。application_events、PermissionSnapshot 完整字段、Artifact 引用、grant doctor 全部改成“预留列/后续 subplan 填充”，不要写进完成门禁。

### P1-2. Schema 版本地图缺失

ADR 说首次创建是 schema 1。36 明确不建业务表。37–44 各自加表。没有 `schema_version → 表集合 → 负责 subplan` 的地图，会出现：

- 36 把 identity 叫 v1，37 也叫 v1；
- 两个 subplan 抢同一迁移文件名；
- 45 的“至少一个先前 Stage 4 schema fixture”无处取材。

**S4.35.2 的后续或 36 开工第一件事：** 预分配版本号，例如 v1 identity-only，v2 lifecycle+conversation，v3 tool/approval，v4 outcome，v5 artifact，v6 checkpoint/fork，v7 events/receipts 补全，v8 grants。允许空迁移，不允许事后重编号。

### P1-3. `turn.started` 先于 `begin_turn`，与“无双写窗口”打架

当前循环先 `yield turn.started`，再 `begin_turn`。公开事件 hold point 禁止改生命周期基数。

可行而不碰 hold point 的做法：先提交 UserMessage/Turn，再发 `turn.started`。事件类型和“恰好一次 started/completed”不变，只是 started 的含义变成“已接纳并已落盘”。43 必须把这句写进 hold-point 备忘，避免有人把它当成生命周期变更。

若先发事件再落盘，客户端会看见一个数据库里不存在的 Turn。这是 P0 级双写，不能选。

### P1-4. `/new`、dirty、`/exit` 确认语义未重定义

今天 dirty = 有未保存的进程内对话；`/new` 确认后 `Session.reset()`，连 session Preferences 一起清掉。Stage 4 之后对话默认已保存，再问“丢弃未保存对话”是假问题。

主计划只说 `/new` 创建新 Session。没说：

- 旧 Session 自动 archive 还是保持 active；
- 未闭合 Turn / 未接受 Task 要不要先恢复；
- session-scoped Preferences 跟不跟着走；
- `/exit` 还要不要确认。

不锁死，43 和现有 terminal 测试会互相拆。建议：`/new` = 归档或挂起当前 Session 并创建新 Session，不再 reset 同一对象；未闭合工作先走恢复，不提供“丢弃已持久化历史”。

### P1-5. 系统提示与边界测试会在 37 变成谎言

`render_system_boundary()` 固定写聊天不可跨进程恢复。37 一旦能 resume，这条必须改，否则模型会被指令要求否认产品能力。

同类必须同期改写的门禁：

- `test_plain_chat_turn_persists_no_state_document`
- `test_session_construction_and_restart_do_not_restore_conversation_log`
- README / ARCHITECTURE 里“退出即丢失”的现在时表述（37 之后不再是现在时）

### P1-6. 公开事件 vs `application_events` 是两套流

公开运行时事件：`turn.started` / `text.delta` / `tool.status` / `turn.completed`，按任务内 `sequence`，且 `lifecycle_is_valid` 极严。

计划中的 `application_events`：同事务、可 cursor 重放、不含 token delta。

43 若把两套流混在一个消费者上，现有测试会炸。ADR 应写：Terminal/REPL 继续消费公开运行时事件；Session/Task/Recovery/Grant 的可重放审计走 application_events；禁止用 application_events 重建 ConversationLog。

### P1-7. 异步运行时 + `check_same_thread=True` + `asyncio.to_thread`

`AgentLoop` 全异步。文件变更经 `_blocking_mutation` → `asyncio.to_thread`。ADR 要求单连接、`check_same_thread=True`、REPL 生命周期复用。

合同应写死：**SQLite 只在事件循环线程同步访问**；handler / 文件 IO / 子进程不得拿到 connection。backup/migrate 用维护连接。spike 没有设置或断言 `check_same_thread`。

另外，Python `sqlite3.connect(timeout=…)` 和 `PRAGMA busy_timeout` 叠在一起，再套 8 次应用重试，最坏会变成数秒墙钟等待。测试必须按 ADR 注入 `busy_timeout=0`。

### P1-8. 上下文预算要到 42 才有 checkpoint，耐久 Session 会先把自己撑死

当前 ContextBuilder 只在内存里省略 tool body、丢旧 Turn。没有 Artifact 引用，没有 checkpoint。37–41 让历史真正变长之后，长任务会先撞 `ContextBudgetError`，而计划的完成标准 8 要到 42 才成立。

这不一定要重排 subplan，但 37 完成门禁不能暗示“任意长多轮都能继续”。应写：37 只保证短脚本会话；超预算仍失败，直到 42。

### P1-9. Grant 绑在 AgentRun 上，崩溃恢复会静默丢权

44 锁：默认寿命一个 AgentRun；重启 fail closed。39 锁：恢复创建新 AgentRun。

结果：Full Access Manual 在崩溃后必须重新授予。这是正确的安全行为，但是产品惊讶点。44/39 必须在 UX 和测试里写成显式规则，不能在恢复流程里“为了方便”继承旧 snapshot。

另外，44 写“authenticated local user”。Morrow 没有认证。应改成：**只有 CLI/REPL/未来 GUI 的应用命令可以建 grant；工具、模型、项目文件、恢复记录都不能。**

### P1-10. Host `ToolEffect.NONE` 不得变成 EffectClass

`ProcessExecutionService.intent()` 把 Host 标成 `effect=ToolEffect.NONE`，同时预览承认它可以改工作区外文件和网络。38 的分类表如果从现有 `ToolEffect` 推导，Host 会被标成可重试只读。

38 必须为每个生产工具**手写**独立声明，并以 Host = 外部副作用 / 默认 `outcome_unknown` 为准。

生产工具清单（都必须有声明，否则拒绝组合）：

`update_configuration`、`list_directory`、`read_file`、`find_files`、`search_text`、`apply_patch`、`write_file`、`show_changes`、`run_command`、`git_status`、`git_diff`、以及能力探测通过时的 `promote_sandbox_changes`。

### P1-11. 故障注入点出现太晚

39 要在每个已提交故障点做逻辑异常和 `os._exit`。38 若没有先留下可注入的 persist/handler 边界，39 只能回头打孔。35.4 / 38 应定义一个测试专用 fault injector（按名字触发一次），生产默认空实现。

### P1-12. Operational Store ADR 仍有未证明主张

Spike 已证明的：路径权限（库文件/目录）、identity、WAL+FULL、COMMIT/`_exit`、未 COMMIT/`_exit`、WAL 读快照、`BEGIN IMMEDIATE` 竞争、8 次注入退避、维护锁互斥、死进程释放锁、未来/外来/空文件拒绝、在线 backup 且不拷 WAL。

**未证明、但 ADR 已经写成合同的：**

- 有序 `schema_migrations`、迁移前备份、失败回滚；
- `user_version` 与 `store_identity.schema_version` 不一致 → `needs_repair`（有逻辑，无测试）；
- header 合法但 `integrity_check` 失败；
- WAL/`-shm` sidecar 也是 `0600`；
- `check_same_thread=True`；
- 重试只针对 BUSY/LOCKED，不重试约束/磁盘错误；
- 两个工作空间进程同时普通写，由 SQLite 串行；
- 迁移中普通写看到 `busy`/`unavailable` 且文件不被改写。

这些应进 36，不要假装 35.2 已经证明“迁移锁”等于“迁移本身”。

### P1-13. `integrity_check` 放在每次 startup open

ADR 允许小库时在 open 时检查。库变大后，每次打开 REPL 做全库 integrity 会卡死。36 应改成：首次创建/迁移/backup/doctor 必查；日常 `read_write` 只验 identity + 快速 header，完整 check 留给 doctor。

### P1-14. 命令层今天不是“一个 Application API”

`CommandService` 只解析斜杠并返回 `action` 字符串；真正的 `/new` reset 和 dirty 确认在 `interfaces/terminal.py`。CLI 还直接碰 `global_store` / workspace / provider 服务。

43 若只是给现有 CommandService 加方法，会把 SQL 和 Session 生命周期塞进斜杠解析器。应新增 Session/Task/Recovery/Grant 应用服务；现有 CommandService 继续做斜杠分发，或降级为薄适配器。

### P1-15. 变更证据与 TaskOutcome 的来源未写清

40 要求 changed paths、validation facts、known/unknown side effects。这些今天在进程内 `ChangeSetService` / `ToolFact`。38 若只存“有界脱敏结果信封”，40 会没有结构化路径列表。

38 应持久化有界的 `ChangeToolFact` 等价物（路径、operation、before/after sha256、truncated 标记），而不是完整 diff。大 diff 等到 41 再进 Artifact。

---

## 7. 建议与需澄清项（P2）

1. **Session `deleted` vs Stage 10 完整删除。** 写成 tombstone 即可；物理清理、导出、安全删除留 Stage 10。查询默认排除 tombstone。
2. **Store health 与 Session health 同名。** `ok/needs_repair/read_only` 两边都有。建议 store 用 `store_health`，Session 用 `session_health`，`needs_recovery` 只属于 Session。
3. **Repair-mode。** 36 有 repair-mode transition，同时禁止自动修复业务历史。应改名为 diagnose/quarantine mode。
4. **保留策略。** “raw records remain available under retention rules” 没有规则。Stage 4 默认：不自动删 conversation；Artifact 只报告 orphan 候选；archive 不影响引用。
5. **Fork 切点。** 从 Turn 边界 fork 与从 checkpoint fork 不是同一段历史。必须写清 fork 复制哪些 record ID，以及子 Session 是否只读引用父 Artifact。
6. **Checkpoint 与当前 ContextBuilder。** 42 应扩展选择器（checkpoint + 最近完整 cycle），不要把今天的 `OMITTED_TOOL_RESULT` 持久化成第二历史。
7. **生产 Clock。** 协议有，bootstrap 没注入。过期 grant/approval 测试需要注入钟；生产也走同一端口。
8. **Id 前缀。** 现有 `ws` / `ses` / `turn` / `evt`。预留 `task` / `arun` / `tex` / `apr` / `art` / `cmd` / `chk` / `grt`。继续用 `RandomIdSource`（已足够唯一），不要用计数器当全局主键。
9. **`FileRevision.size` 上限 8 MiB。** 更大的文件没有 revision。38 对超限文件只能 `outcome_unknown` 或拒绝变更，不要假装能 hash 对账。
10. **Mutation 被 `asyncio.shield`。** 取消不会打断写盘。崩溃分类必须区分“executing 且无 after hash”和“handler_completed”。
11. **YAML 原子发布不得用于 sqlite 文件。** 现有 `os.replace` + fsync 会撕裂 WAL。36 文档里应显式禁止。
12. **`waiting_approval` 不要做成 TaskRun 状态。** 它是 ToolExecution/Approval 状态。放到 TaskRun 会让 40 的状态机和 38 的 journal 双重记账。
13. **研究稿 7.4“通常一个 Turn 一个 AgentRun”。** 与崩溃恢复多 AgentRun 冲突。锁定计划已选后者，研究稿那句作废。
14. **旧 Stage 4 文件。** `stage-4-sessions-context-and-memory.md` 已是兼容入口。`docs/acceptance/handoff-removal-evidence.md` 仍把 memory 算进 Stage 4，应改口。
15. **串行 11 刀是否可微平行。** 不建议打乱 35→36→37→38→39。可考虑在 37 之后加一个极窄的内部 `session resume` 测试入口，避免 43 才第一次从 CLI 打开库。不要为此提前做完整 CLI。
16. **Payload 预算。** 研究稿给了 32 KiB / 128 KiB / 64 MiB 等数字。锁定计划只说“explicit limits”。35.4/35.5 必须给出数字，否则 38/41 会各写各的。
17. **`trusted_schema=OFF` + 不使用 FTS** 保持。不要因为“以后搜索 Session”提前开 FTS5。
18. **备份包。** 36 只备份 sqlite；43 加 Artifact manifest。必须声明备份瞬间 Artifact 与元数据可能短暂不一致，restore 验证以“元数据引用缺失 = 可见失败”为准，而不是静默补文件。

---

## 8. 按子计划审查

### 35 合同激活（进行中）— 可行，必须先做完

范围正确：只允许 ADR 和一次性 spike，禁止生产适配器。35.2 已落地且方向对。缺口就是本审查的 P0/P1。

35.8 写“对照 ADR 审查每个后继 subplan”。这项现在还没做；本文件可以当作预审。正式 35.8 应在 ADR 落地后**改 36–45 正文**，不是只在 LOG 里记一句“已审查”。

### 36 Operational Store — 可行，前提是补迁移地图

目标合适。注意：

- 不要在 36 建业务表，但要锁死版本号分配。
- 把 ADR 未证明项变成 36 的测试清单（见 P1-12）。
- 日常 open 不要全量 `integrity_check`。
- `DataRoot` 增加 `store_path` / `artifacts_path` / `backups_path` / `operational_lock_path`，创建时校验 0700/0600，并在 SQLite 创建 sidecar 后补 chmod。
- 错误码已列；不要把 SQL 或绝对路径漏进异常文本。

### 37 无工具耐久 Session — 可行，但完成门禁过宽

重写 ConversationLog 追加边界是本阶段最危险的代码改动。建议 37 只交付：

- Session 行 + workspace 隔离查询；
- 耐久 ConversationLog（User / 无工具 Assistant / Turn terminal）；
- `client_message_id` 接纳（含 P0-1 的 in-flight/interrupted 语义）；
- 干净退出后 resume；
- current TaskRun **指针**，状态保持 `open`。

拿掉：TaskRun `completed`、PermissionSnapshot 全字段、任何“任意长多轮”。

崩溃测试只覆盖：user append 前/后、assistant append 前/后、turn close 前/后。不要在 37 承诺工具崩溃。

### 38 工具日志与审批 — 可行，且是恢复能否成立的真正地基

必须新增、现在没有的东西：

- 独立 EffectClass/RecoveryPolicy；
- 预检/校验之后、handler 之前的 intent 提交；
- 审批创建与（resolve+consume+executing）原子提交；
- `handler_completed` 与 ToolMessage 分开；
- 写盘前的 `expected_after_sha256`。

不要做：恢复决策、Full Access、公开事件改动、application_events 完整投影。

审批 UX：38 就要改 `TerminalApprovalPort` 去 consume durable `approval_id`。不能等到 43。这不是公开事件生命周期变更。

过期审批：注入钟；终端等待期间到期 → 拒绝执行，不得 handler。

### 39 恢复 — 分类可行，证明进程已死目前不可行

完成门禁里“sandbox retry when process proved terminated”应按 P0-5 降级。首版验收矩阵应为：

| 中断点 | 期望分类 |
|---|---|
| intent 未提交 | 无执行行；可安全重新接纳（新 command 或同一 in-flight receipt → 恢复） |
| intent 已提交，handler 未进 | `never_started` / 声明允许则 `safe_to_retry` |
| 文件写中，无 after hash | `requires_reconciliation`（hash 比对）或 `outcome_unknown` |
| 文件写完，ToolMessage 未写 | `handler_completed` 未闭合；对账后补 interrupted/error ToolMessage 或确认完成 |
| Host 无 completion | `outcome_unknown`，禁止重放 |
| 沙箱无 PID/快照证据 | 与 Host 相同，`outcome_unknown` |
| 推广中 | 按文件对账 |

恢复关闭 ConversationLog 后，ContextBuilder 才能再次 `build`（它拒绝 open ToolCycle）。这个顺序要写进 39。

### 40 TaskOutcome — 可行，必须在 35.3 状态图之后

不要让普通闲聊的每一句最终回答都变成不可变 Outcome 版本爆炸。应对 `waiting_acceptance → 继续` 定义：是新 Outcome 版本，还是只追加 continuation 证据、在下次真正完成时再投影。

禁止 LLM 写 Outcome。禁止 Stage 5 获得历史写权。这两条已经写对。

### 41 Artifact — 可行，且应利用现有 8 KiB 命令输出上限

当前命令输出已经有界并整缓冲脱敏。Stage 4 不需要先做 streaming redactor 才能存**现有**命令输出。Streaming redactor 仍是“完整/原始流”的门禁，保持。

发布顺序 temp → fsync → rename → parent fsync → metadata 与现有 YAML 发布同族，适合字节文件，不要用于 sqlite。

禁止内容寻址去重，正确。路径逃逸/symlink 测试必须有。

### 42 Checkpoint / Fork — 可行，且必须保持 conversation-only

不要把研究稿的 WorkspaceCheckpoint/rewind 塞回来。Fork 不得碰工作区文件。Checkpoint 必须带 source range + method/version + Artifact 引用。当前任务目标、未决项、open approval/recovery、完整最近 ToolCycle 不得被压掉。

LLM 摘要保持非完成条件。

### 43 API / CLI / doctor / backup — 可行，但是最大的接线风险

hold point 写得对。建议 43 开工先做一件事：证明现有公开事件可以原样保留（37–42 没有改基数）。只有证明失败才申请改生命周期。

Doctor 只读。Quarantine 只改 health。Backup 用 `Connection.backup()` + Artifact manifest；凭据不得入包。孤立清理只针对已证明无引用的托管 tmp/orphan。

Grant doctor 检查应标成 44 之后启用，或 43 只留接口。

### 44 CapabilityGrant / Full Access Manual — 可行，且应保持窄

当前 `FULL_ACCESS` 一律 deny。44 只打开 ADR **枚举且真实实现** 的 elevated capabilities。不要为了让 Full Access 看起来更大而加网络/浏览器/MCP 工具。

`unconfined_host` 应成为存储的隔离标签，不只是文案。预览必须每次 elevated 不透明 Host 都出现。

撤销：阻止新副作用、请求取消进行中的相关工具、保留已完成/unknown 事实。正确。

### 45 验收 — 可行，但“验收叙事不够就不得加能力”要求 35 现在就把洞补上

产品故事 1–6 覆盖面合适。必须把 P0-1、P0-2、P0-5 的降级写进证据文档，否则 45 会为了叙事去实现 39/44 明确禁止的自动重放或权限继承。

升级 fixture：从“无 operational store 的 Stage 3 数据根”创建库；再准备一个开发中的旧 Stage 4 schema。这两份夹具要在 36/37 就开始留，不要等到 45。

---

## 9. 与研究稿、路线图、架构的对齐

### 9.1 锁定计划 vs 研究 V2

| 研究 V2 | 锁定计划 | 本审查 |
|---|---|---|
| 受控 Full Access Auto 在 Stage 4 | 延期 | 同意锁定计划 |
| WorkspaceCheckpoint / rewind | 明确不做 | 同意锁定计划 |
| Event outbox + ack | 不做；只要同事务 cursor 事件 | 同意锁定计划 |
| RunClaim / 租约 | 不做 | 同意锁定计划 |
| Approval nonce | 单机首版不要 | 同意；command receipt + row_version 够用 |
| 关闭时 drain outbox | 无 outbox | 改为：显式 shutdown flush 已提交写，失败则可见错误 |
| 详细 TaskRun 状态图 | 推给 ADR | **必须在 35.3 采用一版**，建议吸收研究稿的 corrected 转移，而不是整份 V2 |

研究稿仍有价值的部分：幂等/冲突表、operational truth vs prompt projection、成熟项目故障转回归（吞掉 flush、重复 User、旧审批重放、shutdown 丢尾、双权威分叉）。这些应进故障矩阵，不要进 schema 范围。

### 9.2 与长期路线图

北极星里的 WorkflowRun / LearningReview 不得出现在 Stage 4 表里当假 API。TaskRun 不要预留 `workflow_run_id` 生产列。

ROADMAP 4.1 写 TaskRun 在“用户目标完成”时结束；Stage 4 的 `completed` 不是用户接受。35.3 应用 Stage 4 的词，并在路线图 4.2 加一句：Stage 4 的 completed ≠ accepted ≠ 学习触发。

### 9.3 与架构基线

分层方向正确：Core 端口，适配器在 `adapters/state/`，不要把 sqlite3 放进 `core/`，不要把 Session 方法加进 `ProjectStateYamlStore`。

已有的轻微分层倒挂（`runtime/agent.py` 引 `application.context.ContextBudgetError`）不要再加重。Durable ConversationLog 端口应留在 runtime/application，SQLite 适配器不得被 ContextBuilder 或 AgentLoop 直接 import。

---

## 10. 建议的 ADR 关闭清单（S4.35.3–35.8）

### 35.3 领域/所有权

- [ ] 标识符所有者与前缀
- [ ] Session lifecycle ⊥ health ⊥ store_health
- [ ] TaskRun 状态图（含继续/纠正；37 不得实现终态）
- [ ] Turn vs AgentRun；崩溃恢复 = 新 AgentRun + `resume_of_run_id`
- [ ] ConversationLog：validate → 单提交 → 投影；恢复只能补 interrupted ToolMessage
- [ ] `client_message_id` 状态表（P0-1）；字段在命令上，不在 `UserMessage` 上
- [ ] 哪些命令要 receipt：turn submit、approval/recovery resolve、grant create/revoke、Session/Task 变更
- [ ] `/new` / dirty / `/exit` 的产品语义
- [ ] 三个 sequence 命名空间

### 35.4 耐久执行

- [ ] 每个生产工具的 EffectClass/RecoveryPolicy 表；Host 不得从 `ToolEffect.NONE` 推导
- [ ] 预检后、handler 前的事务内容
- [ ] Approval 字段；无 nonce；consume+executing 原子
- [ ] `handler_completed` ≠ ToolMessage
- [ ] 文件 `expected_after_*` 必须在写盘前落盘
- [ ] 进程证据：首版不依赖 PID；沙箱默认 unknown
- [ ] 数字预算
- [ ] fault injector 端口

### 35.5 Artifact / checkpoint / fork

- [ ] 发布协议与 orphan 状态
- [ ] 首批 kind 与预算
- [ ] checkpoint 出处字段
- [ ] fork 切点与父/子隔离
- [ ] 明确无 workspace rewind

### 35.6 权限

- [ ] 用户/应用边界，不写“authenticated”
- [ ] AgentRun 冻结；崩溃后新 Run fail closed
- [ ] Full Access Manual 枚举能力清单（可以很短）
- [ ] `unconfined_host` 标签与必示预警
- [ ] Auto 返回 unsupported

### 35.7 / 35.8

- [ ] 研究稿降级横幅与否决清单
- [ ] 无代码复用则不强制 `THIRD_PARTY_NOTICES.md`
- [ ] schema 版本地图写入 36
- [ ] 按本审查改 36–45 的锁定合同/完成门禁
- [ ] 故障矩阵覆盖 Stage 4 DoD 14 条

---

## 11. 不建议做的事

- 不要为了“一次把持久化做完”合并 36–39。
- 不要引入 SQLAlchemy/Alembic/异步驱动。
- 不要把 ConversationLog 降成 sqlite 行的薄包装而丢掉语法校验。
- 不要用 JSONL transcript 当第二权威。
- 不要把 `WorkspaceWriterLock` 当库级锁。
- 不要在 37 实现工具表“以免以后麻烦”。
- 不要复活 Handoff / `/continue` 作为恢复入口。
- 不要把研究稿的 Auto/Rewind/Outbox 当作遗漏项补回 Stage 4。
- 不要在没有 expected-after 和进程证据时宣传“安全重试”。

---

## 12. 最终裁决

**CHANGES REQUIRED。**

Stage 4 的产品方向、排除项和切片顺序可以成为实施合同。Operational Store 选择已经有 spike 证据，不应推翻。

不能原文激活 Subplan 36。先完成 Subplan 35 剩余 ADR，并按本审查修改 36–45 中与 ADR 冲突的完成门禁，尤其是：

1. 幂等与崩溃恢复共存（P0-1）
2. TaskRun 状态图与 37/40 边界（P0-2）
3. ConversationLog 单提交边界与恢复补写规则（P0-3）
4. 研究稿降权（P0-4）
5. 恢复证据的诚实降级（P0-5）

关掉这五项之后，Stage 4 是可实施的：工作量大，但没有架构级死路。
