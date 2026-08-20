# Stage 4：Task、Session、Artifact 与持久化

> 状态：生产实现、验收、边界重构与 Subplan 47 真实用户测试修复已完成
> 阶段结果：前台 Session、TaskRun、ToolCycle、授权与关键产物可在进程退出后恢复，未完成副作用可被安全解释和对账
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 3：本地 Code Agent 与安全闭环](stage-3-local-tools-and-safety.md)
> 下一阶段：[Stage 5：可审查学习与长期记忆](stage-5-reviewable-learning-and-memory.md)
> 当前执行方案：[`.agent/PLAN.md`](../../.agent/PLAN.md)

## 一、阶段目标

Stage 4 把 Morrow 从进程内 Code Agent 变成可长期日用、可诊断、可备份的本地前台 Agent：

```text
进入工作空间
→ 创建或恢复 Session
→ 在一个 TaskRun 中接受多个 Turn
→ 冻结 AgentRun 的非敏感配置与权限证据
→ 在副作用前持久化消息、审批与 ToolExecution intent
→ 闭合或对账 ToolCycle
→ 生成 Artifact、ContextCheckpoint 与版本化 TaskOutcome
→ 正常退出或崩溃
→ 以安全健康状态重新打开
→ 继续、对账、Fork、归档或接受
```

完成后，系统必须能准确回答：

- 当前工作空间有哪些 Session，当前目标属于哪个 TaskRun？
- 哪些 Turn、AgentRun、ToolExecution 和审批已经提交？
- 哪些副作用确定完成、确定未开始、可安全重试、需要对账或结果未知？
- 恢复时为什么继续、阻止、重试或重新询问用户？
- 上下文中哪些原始记录仍保留，哪些只通过 checkpoint 或 Artifact 引用？
- 当时实际冻结了什么配置、工具 Schema、权限和 CapabilityGrant？

本阶段不是一次性 Demo，也不建设分布式企业调度平台。目标是可靠的单机、单用户、前台个人
Agent：具备事务、迁移、备份、损坏降级、故障注入和清晰产品语义，但不提前引入后台 Worker、
分布式租约、多设备同步或组织权限系统。

## 二、边界

Stage 4 负责：

- 单一 Operational Store 中的 Session、TaskRun、Turn、AgentRun、消息、ToolExecution、Approval、
  TaskOutcome、Artifact 元数据、ContextCheckpoint、CapabilityGrant 与必要查询投影。
- ConversationLog 的同步持久化边界与唯一消息语法。
- 有副作用工具的 intent-before-effect、崩溃分类和用户可理解的恢复对账。
- 有界、脱敏、完整性可验证的 Artifact。
- 确定性上下文压缩、原始记录来源和 conversation/session Fork。
- 统一 Command/Query 边界、CLI/REPL、只读 doctor、在线备份与恢复验证。
- 用户显式、可撤销、按 AgentRun 冻结的 CapabilityGrant 与 Full Access Manual。

Stage 4 不负责：

- 从历史自动学习 Preference、Knowledge 或 Skill；
- LLM 摘要作为完成门禁、向量数据库、Embedding 或 FTS5；
- Multi-Agent Workflow、in-flight steering、后台运行、队列、周期任务或通知；
- 事件投递 Outbox/Worker 或分布式 RunClaim/租约；
- 自动修复业务历史、静默重建数据库或把不确定副作用改写为成功；
- 工作空间/代码 rewind、恢复或删除用户文件；
- Controlled Full Access Auto、raw auto 或任意宿主命令免审批；
- 完整 GUI、多设备同步、团队共享和 Stage 10 的完整导出/彻底删除体验。

> **Stage 4 可靠记录“发生了什么”；Stage 5 才评审“什么值得长期学习”。**

## 三、当前基线与进入条件

Stage 3 已在当前声明的 macOS 平台完成真实 Code Agent 闭环：工作空间读搜、冲突安全变更、审批后
Host 命令、原生快照沙箱、变更推广与只读 Git 均经过验收。Stage 4 必须保持以下基线：

- 普通聊天只走 `AgentLoop.run_task()`；保留的 `run_turn()` 委托到同一条 loop，生产组合可能带有冻结的
  ToolExecutor，并不是独立 no-tools 状态机。
- Session-owned `ConversationLog` 是唯一聊天历史写入者和 ToolCycle 语法权威。
- Profile、Preferences 与 Provider 非敏感配置继续由现有 YAML Store 管理；凭据继续由
  CredentialStore/环境变量管理。
- 公开事件生命周期和随包 `agent-policy.toml` 默认值在明确 hold point 前不改变。
- Host 命令在 Stage 3 Manual/Auto Safe 中需要审批且没有 OS 隔离；只有 Auto Sandboxed 声明原生隔离。
- 生产 ToolSet 中每个工具必须继续使用同一 ToolExecutor/ToolCycle 协议，不能在 AgentLoop 中按名称
  增加业务分支。

Subplan 35 的 SQLite、并发、迁移、恢复、Payload、权限和来源治理 ADR 门禁已经通过；Subplans 36–46
已完成 Operational Store、Session/Task、ToolExecution/Recovery、Artifact、Context/Fork、API/Doctor/
Backup、CapabilityGrant 实现、全链路验收与边界重构。Subplan 47 只收敛真实用户报告的
RUT-001～RUT-008，不新增能力范围；实现、聚焦回归、完整 offline 与质量门禁已通过。验收证据见
[`docs/acceptance/stage-4-durable-agent-evidence.md`](../acceptance/stage-4-durable-agent-evidence.md)。

## 四、领域语义与所有权

### 4.1 Session

Session 是工作空间隔离的可恢复交互容器。Store 健康、Session 生命周期与 Session 健康必须分开：

```text
store_health:      ok | read_only | needs_repair | future_schema
session_lifecycle: active | archived | deleted
session_health:    ok | needs_recovery | quarantined | read_only
```

`deleted` 是 tombstone；物理删除留到 Stage 10。Quarantine 只能改变 session_health，不能把用户选择的
active/archived/deleted 改成另一种业务状态。一个 Session
可以顺序承载多个 TaskRun，默认只有一个前台 current TaskRun。

普通前台 Turn 或 TaskRun 只能在 `lifecycle=active` 且 `health=ok` 时开始或恢复。
Archive 要求 current TaskRun 为空，不自动改写任务历史；非 active Session 不能持有活跃
current TaskRun。`needs_recovery` 只走显式恢复边界，`quarantined`/`read_only` 以稳定错误拒绝普通工作。

`updated_at` 是 Session 的乐观 stale token。Task、Turn、conversation、lifecycle、health 和 recovery
等所有可观察 mutation 都推进它；一个外层事务共享一个注入时间戳，跨事务的
整秒值严格单调。

### 4.2 TaskRun

TaskRun 表示一个用户目标，不等于一次模型请求。锁定的首版语义是：

- Session 没有 current TaskRun 时，第一条普通输入创建一个 `open` TaskRun。
- Subplan 37 只保存 current TaskRun 指针；完整状态机由 Subplan 40 实现。
- 最终 Assistant 回答在 Subplan 40 后把 TaskRun 移到非终态 `ready_for_acceptance`，不代表用户接受、
  也不是 Stage 5 学习触发。
- `ready_for_acceptance` 后的普通输入默认让同一 TaskRun 回到 `open`，并记录继续/纠正证据。
- `/accept` 记录显式接受；`/task new` 创建新 TaskRun；`/new` 创建新 Session。
- failed/cancelled/interrupted 保留已发生副作用，显式 resume/retry 创建可关联的新尝试，不能伪装回滚。

Subplan 40 的持久化状态为 `open`、`ready_for_acceptance`、`accepted`、`cancelled`、`failed`、
`abandoned`。合法转移是：`open → ready_for_acceptance`、`ready_for_acceptance → open | accepted |
cancelled | abandoned`、`open → cancelled | failed | abandoned` 和 `failed → open`。其中
`accepted/cancelled/abandoned` 是终态；`ready_for_acceptance` 不是接受，`failed` 允许显式 resume/retry
进入下一次尝试。状态转移、乐观 `row_version`、转移审计和 Session 的 current 指针在同一个 SQLite
事务内提交；终态会清除 current 指针。

持久化启用后，`/new` 创建并选择新 Session，不 reset/delete/自动 archive 旧 Session；旧 Session 可恢复，
session-scoped Preferences 不继承。存在 needs_recovery 的工作时先要求用户解决或真实关闭。`/exit` 不再询问
“丢弃已保存对话”，无法证明的进行中效果被记录为 needs_recovery。

TaskRun 的合法状态和精确转换由 Stage 4 ADR 固定；任何 CLI/REPL/未来 GUI 都调用同一个
Application Service，不能自行解释状态。

### 4.3 Turn 与 AgentRun

Turn 表示一次被接纳的用户输入及其闭合结果。`client_message_id` 是 turn-submit 命令字段而不是
UserMessage 字段。它保证同一 Session 中相同键只接纳一个 Turn/UserMessage：Turn 已闭合时重复提交返回
已提交结果；Turn open/interrupted 时返回原 receipt 和恢复状态，显式恢复可以在同一 Turn 创建新
AgentRun；相同键但不同 Payload 返回冲突。

一次 Turn 可以因崩溃恢复包含多个 AgentRun。没有新用户输入的 crash resume 在同一 open Turn 中创建新
AgentRun；有新输入时创建新 Turn。

AgentRun 冻结：

- Provider/Model 引用与非敏感解析结果；
- 有界的已解析 Profile、Preferences、配置值及其来源 revision/hash；
- RunPolicy、ToolSet/Schema digest、Runtime instance；
- Subplan 37 的基础 AccessScope、ApprovalMode、ProcessIsolation 与 digest；完整 PermissionSnapshot 与
  grant 引用由 Subplan 44 添加；
- 开始/结束、状态、消息范围、调用计数、停止原因和错误分类。

快照是不可变运行证据，不是新的 Profile/Preferences 权威；密钥、完整环境变量、Provider reasoning 和
SDK 对象不得进入快照。

### 4.4 ConversationLog

- ConversationLog 继续拥有 User/Assistant/ToolMessage 的合法顺序、完整 ToolCycle 与 Turn 闭合规则。
- AgentLoop 通过 durable ConversationLog append 提交普通聊天；TaskService 可以协调事务和幂等，但不能
  直接写聊天记录。
- 唯一提交顺序是：构造 candidate → ConversationLog validate → 一次 SQLite transaction → COMMIT →
  从已提交行更新投影；失败时不能先改内存再异步 flush。
- 恢复只能调用窄化 API，为已记录的未闭合 call 顺序补 interrupted/error ToolMessage 和非成功终结；
  不能补 User/Assistant 或成功 ToolMessage。
- `conversation_position`、公开 `runtime_event_sequence` 与 durable `application_event_cursor` 是三个独立
  命名空间。
- Subplan 37 必须先提交 Turn/UserMessage，再发保持原类型/基数的 `turn.started`，然后调用 Provider。

### 4.5 TaskOutcome

TaskOutcome 是从持久事实确定性生成、不可变且可版本化的任务结果，至少引用：

- user goal 与 TaskRun 状态；
- result summary；
- changed paths；
- validation facts/results；
- 已知、未知和未解决的副作用；
- Artifact 与原始证据引用；
- completion basis 与显式用户反馈。

Outcome 不因每次最终 Assistant 自动生成；只在显式接受、显式 outcome snapshot 或终止性关闭时生成。
纠正不会改写旧 Outcome，而是生成新的 superseding version。Stage 5 可以读取 Outcome 作为学习证据，
但不能借此获得 Stage 4 历史写权限。

Subplan 40 的 v5 `TaskOutcome` 只保存有界的 summary、首个 Turn 的 user-goal 引用、changed paths、
validation facts、side effects、unresolved items、completion basis、显式 feedback，以及对 Turn、
ToolExecution 和 TaskRun transition 的类型化引用；不会保存 Provider reasoning、完整参数/结果或秘密。普通最终回答只推进到
`ready_for_acceptance`，不自动创建 Outcome；重复命令通过命令回执返回原结果，冲突请求不会改写历史。

## 五、存储架构

### 5.1 权威来源

```text
现有 YAML / CredentialStore
- Global / Workspace / Session-resolved Preferences authority
- Workspace Profile authority
- Provider non-secret config and credential refs
- credentials

SQLite Operational Store（一个 data root）
- Session / TaskRun / Turn / AgentRun
- Conversation records / ToolExecution / Approval
- TaskOutcome versions / ContextCheckpoint
- Artifact metadata / CapabilityGrant / PermissionSnapshot
- retry-sensitive CommandReceipt / sanitized application_events

Filesystem Artifact Store（同一 data root 下的受管目录）
- bounded redacted command output
- patch / diff / test and diagnostic reports
- deterministic summary/checkpoint payloads when too large for rows
```

每类状态只有一个权威。AgentRun 中保存已解析非敏感值是历史证据，不把 SQLite 变成配置写入源。

### 5.2 SQLite 最低合同

- 首版使用 Python 标准库 `sqlite3`，不默认增加 ORM。
- 显式 application/schema identity、顺序迁移、future-schema refusal、外键和完整性检查。
- WAL/`synchronous=FULL`/250ms busy/`BEGIN IMMEDIATE`/最多 8 次可注入退避已由
  [Operational Store ADR](../decisions/stage-4-operational-store.md) 和
  `tests/test_stage4_operational_store_spike.py` 锁定。
- 上述 spike 没有证明迁移、WAL/SHM `0600`、thread affinity、全部错误过滤或迁移与普通 writer 竞争；
  这些是 Subplan 36 的明确门禁。Schema v1–v9 的所有者已在 Operational Store ADR 预留。
- 写事务短小，不跨模型请求、用户审批、文件 IO、子进程或网络调用。
- bounded busy retry/typed contention；失败不能无限等待或丢写。
- 一个全局 Operational Store maintenance lock（`locks/operational-store.lock`）负责初始化、迁移、
  备份与 diagnose/quarantine-mode 转换；现有 workspace-scoped `WorkspaceWriterLock` 不足以承担该职责。
- 日常 read-write open 只做 header/identity/version/连接设置校验；完整 `integrity_check` 在创建、迁移、
  backup 与 doctor 执行，不能让增长中的数据库每次启动全表扫描。
- SQLite 只在 owning event-loop thread 同步访问；handler、`asyncio.to_thread` 与子进程不持有连接。
- 迁移前预检和备份，失败保持原数据；未来版本或损坏数据不得静默删除/重建。
- doctor 默认只读；业务历史没有自动修复路径。
- 数据根下的保留路径：`store/operational.sqlite`、`artifacts/`、`backups/operational/`。

不要建立一个包含所有表方法的 `OperationalStore` God Protocol。Core 使用按 Session/Task、Conversation、
Execution/Approval、Artifact、Grant、Receipt、Query/Event 划分的窄 Port；一个 SQLite Adapter 可以共享
连接和事务基础设施。

### 5.3 Artifact Store

Artifact 使用 opaque ID 管理路径并用 SHA-256（或 ADR 锁定的等价算法）校验内容；首版不要求内容寻址
去重。发布顺序是：受管临时文件写入 → file fsync → atomic rename → parent fsync → metadata commit，
每个故障点都有确定的 orphan/缺失状态。

Orphan 判定以同一 data root 中全部 workspace 的 Artifact metadata、普通 reference 和
checkpoint reference 并集为权威。Cleanup 默认 dry-run；显式 apply 也不删除字节，而是经
`O_NOFOLLOW` dirfd 目录链、类型/权限/单链接和事务内全局权威复查后，原子 rename 到
随机 0700 私有 quarantine。成功报告为 `removed=0`/`quarantined=1`；路径不调用
`unlink`/`truncate`/`ftruncate`，无法证明安全时保留 quarantine 并 fail closed。

首版可以持久化现有 8 KiB 上限内、完成整块脱敏的 command result。只有未来要保留 full/raw stream 时才
必须先证明流式 redactor；未证明前不写盘。聊天与 TaskOutcome 只保存 Artifact 引用和有界 excerpt，
不复制大内容。

## 六、ToolExecution、Approval 与恢复

### 6.1 intent-before-effect

```text
Assistant ToolCall 通过 ConversationLog 校验
→ 消息、ordered ToolExecution intent、AgentRun 状态在同一事务提交
→ 必要时创建/解决 Approval
→ Approval consume 与 executing 状态原子提交
→ handler 执行
→ handler_completed 保存有界脱敏结果证据
→ ConversationLog 追加 ToolMessage 并闭合 ToolCycle
```

任何有副作用 handler 都不能在 intent 事务成功前运行。`handler_completed` 与聊天中 ToolMessage 已持久化
是两个不同事实，崩溃恢复必须能区分。

### 6.2 Approval

Approval 至少绑定：opaque approval ID、intent hash、Tool Schema digest、Subplan 38 的基础 permission-
context digest、请求/授予子集、row version、过期时间、解决结果和 consumed_at；Subplan 44 再加入完整
PermissionSnapshot FK。解决/consume 只能发生一次，且与 `executing` 转换原子提交。单机首版不需要额外
approval nonce。

### 6.3 恢复分类

当前 `ToolEffect` 只服务运行期策略，不能决定 crash replay。每个生产工具必须声明独立、持久的
EffectClass/RecoveryPolicy，至少能得到：

```text
never_started | safe_to_retry | requires_reconciliation | outcome_unknown | completed
```

- 只读工具只有在声明且前置条件满足时才可重试。
- 结构化幂等操作使用幂等键和已提交结果。
- 文件变更对账使用 before hash、expected-after hash、expected size 与父目录/辅助条件，不用包含 mtime 的
  完整 FileRevision 做唯一真值。
- 所有 Host process 都可能产生外部副作用；无法证明完成时标为 outcome_unknown，绝不自动重放。
- Stage 4 v1 没有 durable PID/PGID/temp-root/snapshot 证据，因此 Sandbox process 与 Host process 一样：
  缺少 `handler_completed` 时一律 outcome_unknown，禁止自动重放。推广只按文件 expected-after 对账。

恢复输出 RecoveryReport，让用户选择继续、接受已知结果、取消或保持 quarantine。安全重试仍由分类器
保留为后续 linked-attempt 扩展的判定依据；Stage 4 v1 在真正的 linked retry 原子路径实现前不暴露重试
操作。恢复是分类和对账，不是自动改写事实。

Recovery command 的幂等与状态终态同时受守卫：同 command receipt 只返回 replay；
非 `OPEN` RecoveryReport 的新 command 稳定拒绝，并在同一写事务内再读 durable status。
已关闭的旧 report 不能清除后来的 `quarantined`/`read_only` health，不能重复创建
AgentRun。恢复决策完成后的 `resume_recovery()` 只在 Session 仍为 ACTIVE + health OK 时启动。

## 七、上下文与 Fork

ContextCheckpoint 是对不可变记录的确定性投影，记录 source record ID/range、算法/version、预算事实、
Artifact 引用和创建来源。它不复制一份新的 `retained_tail_json` 作为第二聊天权威。

压缩顺序优先：

1. 用 Artifact 引用替代可重新读取的大输出；
2. 对旧工具输出和 Diff 生成确定性摘要；
3. 按完整 Turn/ToolCycle 生成 checkpoint；
4. 始终保留当前 Task 目标、约束、未解决项、最近失败、open Approval/Recovery 与当前 Turn。

LLM 摘要可以在未来作为带来源的附加投影，但不是 Stage 4 完成条件，也不能成为项目事实源。

Conversation/session Fork 从合法 Turn 边界或 checkpoint 创建新 Session，保存 parent provenance，共享不可变
Artifact 引用，后续历史互不反写。Child 创建时必须没有继承的 current TaskRun，但持久化后可
正常创建和拥有自己的 TaskRun、Turn 与 child-local records。Fork 不回退、恢复或删除工作空间文件。

## 八、Command、Query、Event、Doctor 与 Backup

CLI、REPL 和未来客户端必须调用同一 Command/Query Application Service。需要幂等 receipt 的是 turn
submit、approval/recovery resolve、grant create/revoke 和 Session/Task 等重试敏感 mutation；普通 Query
不需要泛化 exactly-once 设施。

业务事务从 Subplan 43 开始才可在同一 SQLite transaction 追加 versioned、脱敏、有界的
`application_events`，并按单调 cursor 查询/重放。公开 runtime events 继续服务 Terminal 流式 UI，
application events 只做 Session/Task/Recovery/Grant 审计，不能重建 ConversationLog。Stage 4 不建设
delivery outbox、ack 或 worker。

现有公开 `turn.started`/`tool.status`/`turn.completed` 生命周期若要改变，必须在 Subplan 43 到达显式 hold
point 后再次授权，并原子更新全部消费者和测试。

Doctor 只读检查当时已存在的 schema/integrity/foreign key、消息/ToolExecution、Artifact 和引用一致性；
Grant 检查由 Subplan 44 添加。Doctor 可生成报告、
建议 quarantine 和识别确定性 orphan 候选，不能修复业务历史。Backup 使用 SQLite online backup 与经 hash
验证的 Artifact manifest/copy，在独立目标执行恢复验证，且不读取/复制 CredentialStore 密钥。

Doctor 在候选遍历前验证 data-root/Artifact/tmp 目录链，并区分
managed-unreferenced、unmanaged-removable 和 unsafe-refused；正常受管 `tmp/` 不是 orphan。
Doctor health 非 OK 时 CLI exit 2。Session/Task/Artifact list 的文本输出显示 `next_cursor`，
三者的 `--json` 都保留 `{items, next_cursor}` page metadata。

## 九、CapabilityGrant 与 Full Access Manual

CapabilityGrant 是通过本地 CLI/REPL/未来 GUI 的显式应用命令创建的授权记录；Morrow 当前没有本地用户
认证系统。模型、Tool、Profile、Preferences、Memory、Skill、项目
文件、导入历史和恢复流程都不能创建、延长或提升授权。

首版合同：

- grant 显式列出 capability/operation 子集，绑定 workspace、TaskRun、AgentRun、原因、策略版本、创建/
  过期/撤销时间；
- AgentRun 开始时解析并冻结 PermissionSnapshot；缺失、过期、撤销或无法证明的授权在重启后 fail closed；
- crash resume 创建新 AgentRun，新 Run 不继承旧 Run 的 grant；需要 elevated capability 时必须重新授权；
- 默认只对一个前台 AgentRun 生效，不能保存为全局/工作空间默认；
- grant 不是审批，Full Access Manual 中每个 elevated side effect 仍需 intent-bound Approval；
- 结构化直接工具继续执行其 protected-resource 规则；
- approved opaque Host command 明确标记为 `unconfined_host`：它没有 OS 隔离，可能访问用户文件、网络、
  凭据和 Morrow 状态。命令分类只能帮助预览，不能宣称提供 confinement；
- Full Access Manual 只激活 ADR 明确枚举的 `unconfined_host_process`；不为扩展名称而新增通用外部文件、
  Browser、MCP、Git-write 或网络专用工具；
- `full_access + auto`、raw auto 和任意宿主命令免审批在 Stage 4 返回 unsupported。

Controlled Full Access Auto 只有在未来存在足够有用、可结构化约束的 elevated tools 时才重新评审；当前不为
满足路线文字而给不透明 Shell 自动授权。

## 十、实施顺序与门禁

当前执行细节以 `.agent/PLAN.md` 和一个活动 subplan 为准：

| Subplan | 结果门禁 |
|---|---|
| 35 | ADR、来源锁、故障矩阵与计划一致性；无生产行为变化 |
| 36 | SQLite、迁移、全局 maintenance lock、健康模式与 online backup 基础 |
| 37 | 无工具多轮 Session 可持久化、幂等提交并无损恢复 |
| 38 | Tool intent 在副作用前提交，Approval 一次性消费，ToolCycle 可解释 |
| 39 | 真实 Stage 3 工具在崩溃后分类/对账，不盲目重放 |
| 40 | TaskRun 继续/纠正/接受与版本化 TaskOutcome 跨重启一致 |
| 41 | Artifact 原子发布、脱敏、完整性、保留与故障状态可靠 |
| 42 | 确定性 checkpoint 保留完整 Cycle/来源，Fork 与父历史隔离 |
| 43 | 统一 API/CLI/REPL、cursor events、只读 doctor 与备份恢复可用 |
| 44 | CapabilityGrant 与 Full Access Manual 可撤销、可审计、无 Auto 路径 |
| 45 | 全链路、故障矩阵、迁移、包安装、当前平台安全与文档验收 |
| 46 | Recovery 单一写者、领域协作者、窄 journal port 与 runtime/CLI 边界收敛 |
| 47 | RUT-001～RUT-008 数据安全、Fork/lifecycle、诊断和 CLI 修复与回归 |

一次只执行一个 Subplan，下一项不得提前把生产行为混入当前切片。

## 十一、测试与故障注入

至少覆盖：

- 每个消息、Task/Run 状态和 ToolExecution 事务的提交前后；
- 审批创建、解决、consume、handler start、handler completed、ToolMessage close 前后；
- 文件已写但 result 未提交、Host command 结果未知、Sandbox process/promotion 中断；
- SQLite contention、future schema、迁移失败、只读/写失败和损坏；
- Artifact publish 各阶段、文件缺失/hash 不匹配、引用与 orphan；
- checkpoint/fork 中断、预算边界与父子隔离；
- grant 创建、冻结、过期、撤销、重启和跨 scope 复用；
- 模型/配置/项目/历史尝试提升权限；
- backup 时并发有界写入和隔离 restore verification。

测试使用注入时钟、barrier、pipe、脚本化 Provider/Runner、逻辑 fault point 和 subprocess `os._exit`。
不得用 wall-clock sleep 断言时序，不使用真实凭据或默认联网测试。

## 十二、完成标准

1. 重启后可按工作空间列出、恢复、归档和 Fork Session。
2. 消息顺序、完整 ToolCycle 与 AgentRun 非敏感快照一致；幂等 turn submit 只接纳一个
   Turn/UserMessage，恢复可在同一 Turn 创建关联的新 AgentRun。
3. 所有 side-effecting ToolCall 在 handler 前已可靠持久化。
4. 崩溃后不会自动重放 outcome_unknown 的写入、推广、Host 或 Sandbox 命令；Stage 4 v1 缺少完成证据的
   Host/Sandbox 一律 unknown。
5. Session lifecycle 与 health/quarantine 相互独立；普通工作只在 ACTIVE + health OK 时启动；
   archive 不会留下活跃 current task；future/corrupt 状态不被静默覆盖。
6. TaskRun 可跨多个 Turn 继续、纠正、取消和显式接受，并生成不可变版本化 TaskOutcome。
7. Artifact 有界、脱敏、完整性可验证、来源明确，缺失/损坏可见；cleanup 使用
   data-root 全局权威且只保留字节地隔离，不按路径销毁字节。
8. 长上下文通过确定性 checkpoint 继续，所有摘要/引用可追溯到原始记录。
9. Fork 不修改父历史，创建时不继承父 TaskRun，但 child 可后续创建自己的任务；
   Fork 不回退或删除工作空间文件。
10. Command/Query/CLI/REPL 共享同一业务实现；application events 有序重放且无后台 Outbox。
11. Doctor、online backup、restore、迁移、contention、损坏和故障矩阵具有可复现证据。
12. CapabilityGrant 只能由本地界面显式命令创建，按 AgentRun 冻结、过期和撤销；crash resume 的新
    AgentRun 不继承旧 grant。
13. Full Access Manual 的所有 elevated effects 都逐次审批，并诚实展示 unconfined Host 风险；
    Controlled Full Access Auto 仍不可用。
14. Stage 3 产品故事、安全门禁、完整 offline suite 和安装包恢复验收继续通过。

## 十三、进入 Stage 5 前必须确认

- TaskOutcome 是否提供足够且不过度的学习证据。
- accepted/corrected/abandoned 等显式信号如何映射为 LearningReview 输入。
- 哪些 Artifact 可被学习评审读取，哪些因敏感性必须拒绝。
- Profile、Preference、Project Knowledge、Episodic Summary 与原始运行记录的事实边界。
- 是否有真实证据需要 LLM 摘要或语义检索；没有证据时继续使用确定性路径。
