# Stage 4：Task、Session、Artifact 与持久化

> 状态：实施规划已激活（Subplan 35；生产实现尚未开始）
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

- 普通聊天只走 `AgentLoop.run_task()`；保留的 `run_turn()` 仍是薄的 no-tools 委托。
- Session-owned `ConversationLog` 是唯一聊天历史写入者和 ToolCycle 语法权威。
- Profile、Preferences 与 Provider 非敏感配置继续由现有 YAML Store 管理；凭据继续由
  CredentialStore/环境变量管理。
- 公开事件生命周期和随包 `agent-policy.toml` 默认值在明确 hold point 前不改变。
- Host 命令在 Stage 3 Manual/Auto Safe 中需要审批且没有 OS 隔离；只有 Auto Sandboxed 声明原生隔离。
- 生产 ToolSet 中每个工具必须继续使用同一 ToolExecutor/ToolCycle 协议，不能在 AgentLoop 中按名称
  增加业务分支。

进入生产实现前，Subplan 35 必须通过 SQLite、并发、迁移、恢复、Payload、权限和来源治理 ADR 门禁。

## 四、领域语义与所有权

### 4.1 Session

Session 是工作空间隔离的可恢复交互容器。生命周期与存储健康必须分开：

```text
lifecycle: active | archived | deleted
health:     ok | needs_recovery | needs_repair | read_only
```

Quarantine 只能改变 health，不能把用户选择的 active/archived/deleted 改成另一种业务状态。一个 Session
可以顺序承载多个 TaskRun，默认只有一个前台 current TaskRun。

### 4.2 TaskRun

TaskRun 表示一个用户目标，不等于一次模型请求。锁定的首版语义是：

- Session 没有 current TaskRun 时，第一条普通输入创建一个 TaskRun。
- 最终 Assistant 回答使 TaskRun 进入 `completed`，但不代表用户已经接受。
- `completed` 后的普通输入默认属于同一个 TaskRun 的继续或纠正。
- `/accept` 记录显式接受；`/task new` 创建新 TaskRun；`/new` 创建新 Session。
- failed/cancelled/interrupted 保留已发生副作用，显式 resume/retry 创建可关联的新尝试，不能伪装回滚。

TaskRun 的合法状态和精确转换由 Stage 4 ADR 固定；任何 CLI/REPL/未来 GUI 都调用同一个
Application Service，不能自行解释状态。

### 4.3 Turn 与 AgentRun

Turn 表示一次被接纳的用户输入及其闭合结果。`client_message_id` 是 turn submit 的必填幂等键：同一
Session 中相同键和相同 Payload 返回已提交结果，不产生第二次模型运行；相同键但不同 Payload 返回冲突。

一次 Turn 可以因崩溃恢复包含多个 AgentRun。没有新用户输入的 crash resume 在同一 open Turn 中创建新
AgentRun；有新输入时创建新 Turn。

AgentRun 冻结：

- Provider/Model 引用与非敏感解析结果；
- 有界的已解析 Profile、Preferences、配置值及其来源 revision/hash；
- RunPolicy、ToolSet/Schema digest、Runtime instance；
- AccessScope、ApprovalMode、ProcessIsolation、PermissionSnapshot 与 grant 引用；
- 开始/结束、状态、消息范围、调用计数、停止原因和错误分类。

快照是不可变运行证据，不是新的 Profile/Preferences 权威；密钥、完整环境变量、Provider reasoning 和
SDK 对象不得进入快照。

### 4.4 ConversationLog

- ConversationLog 继续拥有 User/Assistant/ToolMessage 的合法顺序、完整 ToolCycle 与 Turn 闭合规则。
- AgentLoop 通过 durable ConversationLog append 提交普通聊天；TaskService 可以协调事务和幂等，但不能
  直接写聊天记录。
- 恢复只能调用一个窄化的 ConversationLog recovery API，校验并闭合已记录的中断 ToolCycle；不能合成
  成功 ToolMessage。
- 数据库 sequence 是恢复顺序权威；持久记录与内存投影之间不得在副作用前存在未提交双写窗口。

### 4.5 TaskOutcome

TaskOutcome 是从持久事实确定性生成、不可变且可版本化的任务结果，至少引用：

- user goal 与 TaskRun 状态；
- result summary；
- changed paths；
- validation facts/results；
- 已知、未知和未解决的副作用；
- Artifact 与原始证据引用；
- completion basis 与显式用户反馈。

纠正不会改写旧 Outcome，而是生成新的 superseding version。Stage 5 可以读取 Outcome 作为学习证据，
但不能借此获得 Stage 4 历史写权限。

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
- 写事务短小，不跨模型请求、用户审批、文件 IO、子进程或网络调用。
- bounded busy retry/typed contention；失败不能无限等待或丢写。
- 一个全局 Operational Store maintenance lock（`locks/operational-store.lock`）负责初始化、迁移、
  备份与 repair-mode 转换；现有 workspace-scoped `WorkspaceWriterLock` 不足以承担该职责。
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

首版只持久化经过流式/完整红线测试的有界脱敏输出。没有被证明安全的 full/raw command stream 不写盘。
聊天与 TaskOutcome 只保存 Artifact 引用和有界 excerpt，不复制大内容。

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

Approval 至少绑定：opaque approval ID、intent hash、Tool Schema digest、PermissionSnapshot digest、
请求/授予子集、row version、过期时间、解决结果和 consumed_at。解决/consume 只能发生一次，且与
`executing` 转换原子提交。单机首版不需要额外 approval nonce。

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
- Sandbox process 只有在旧进程已终止、无推广和无外部效果得到证明时才可能安全重试。

恢复输出 RecoveryReport，让用户选择继续、显式重试、接受未知、取消或保持 quarantine。恢复是分类和
对账，不是自动改写事实。

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
Artifact 引用，后续历史互不反写。Fork 不回退、恢复或删除工作空间文件。

## 八、Command、Query、Event、Doctor 与 Backup

CLI、REPL 和未来客户端必须调用同一 Command/Query Application Service。需要幂等 receipt 的是 turn
submit、approval/recovery resolve、grant create/revoke 和 Session/Task 等重试敏感 mutation；普通 Query
不需要泛化 exactly-once 设施。

业务事务可在同一 SQLite transaction 追加 versioned、脱敏、有界的 `application_events`，并按单调 cursor
查询/重放。Stage 4 不建设 delivery outbox、ack 或 worker。

现有公开 `turn.started`/`tool.status`/`turn.completed` 生命周期若要改变，必须在 Subplan 43 到达显式 hold
point 后再次授权，并原子更新全部消费者和测试。

Doctor 只读检查 schema/integrity/foreign key、消息/ToolExecution、Artifact、Grant 和引用一致性；可生成报告、
建议 quarantine 和识别确定性 orphan 候选，不能修复业务历史。Backup 使用 SQLite online backup 与经 hash
验证的 Artifact manifest/copy，在独立目标执行恢复验证，且不读取/复制 CredentialStore 密钥。

## 九、CapabilityGrant 与 Full Access Manual

CapabilityGrant 是用户显式创建的本地授权记录。模型、Tool、Profile、Preferences、Memory、Skill、项目
文件、导入历史和恢复流程都不能创建、延长或提升授权。

首版合同：

- grant 显式列出 capability/operation 子集，绑定 workspace、TaskRun、AgentRun、原因、策略版本、创建/
  过期/撤销时间；
- AgentRun 开始时解析并冻结 PermissionSnapshot；缺失、过期、撤销或无法证明的授权在重启后 fail closed；
- 默认只对一个前台 AgentRun 生效，不能保存为全局/工作空间默认；
- grant 不是审批，Full Access Manual 中每个 elevated side effect 仍需 intent-bound Approval；
- 结构化直接工具继续执行其 protected-resource 规则；
- approved opaque Host command 明确标记为 `unconfined_host`：它没有 OS 隔离，可能访问用户文件、网络、
  凭据和 Morrow 状态。命令分类只能帮助预览，不能宣称提供 confinement；
- Full Access Manual 只激活 ADR 明确枚举且真实实现的 elevated capabilities；
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
2. 消息顺序、完整 ToolCycle、AgentRun 非敏感快照与幂等 turn submit 完全一致。
3. 所有 side-effecting ToolCall 在 handler 前已可靠持久化。
4. 崩溃后不会自动重放 outcome_unknown 的写入、推广或 Host 命令。
5. Session lifecycle 与 health/quarantine 相互独立；future/corrupt 状态不被静默覆盖。
6. TaskRun 可跨多个 Turn 继续、纠正、取消和显式接受，并生成不可变版本化 TaskOutcome。
7. Artifact 有界、脱敏、完整性可验证、来源明确，缺失/损坏可见。
8. 长上下文通过确定性 checkpoint 继续，所有摘要/引用可追溯到原始记录。
9. Fork 不修改父历史，也不回退或删除工作空间文件。
10. Command/Query/CLI/REPL 共享同一业务实现；application events 有序重放且无后台 Outbox。
11. Doctor、online backup、restore、迁移、contention、损坏和故障矩阵具有可复现证据。
12. CapabilityGrant 只能由用户显式创建，按 AgentRun 冻结、过期和撤销，重启不静默提权。
13. Full Access Manual 的所有 elevated effects 都逐次审批，并诚实展示 unconfined Host 风险；
    Controlled Full Access Auto 仍不可用。
14. Stage 3 产品故事、安全门禁、完整 offline suite 和安装包恢复验收继续通过。

## 十三、进入 Stage 5 前必须确认

- TaskOutcome 是否提供足够且不过度的学习证据。
- accepted/corrected/abandoned 等显式信号如何映射为 LearningReview 输入。
- 哪些 Artifact 可被学习评审读取，哪些因敏感性必须拒绝。
- Profile、Preference、Project Knowledge、Episodic Summary 与原始运行记录的事实边界。
- 是否有真实证据需要 LLM 摘要或语义检索；没有证据时继续使用确定性路径。
