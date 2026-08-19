# Morrow Stage 4 成熟 Agent 设计对照与复用评审

> 评审日期：2026-08-19  
> 对照项目：Pi、Hermes Agent、OpenAI Codex、Claude Code  
> 目的：为 Morrow Stage 4 的 Session、TaskRun、ToolCycle、Artifact、恢复、审批、权限和上下文压缩设计查漏补缺  
> 结论：**不引入某个现成 Agent 作为 Morrow 的运行时依赖；采用“协议借鉴 + 算法语义移植 + 小型代码选择性复用 + 上游故障回归化”的组合策略。**

---

## 1. 总体判断

这四个项目并不是同一种架构的四个实现：

- **Pi** 是最值得研究的轻量 Session/Context Harness，优势是历史树、分支、压缩条目和扩展状态与模型上下文分离。
- **Hermes Agent** 是最接近 Morrow Python 技术栈的 SQLite Session Store 参考，优势是迁移、WAL、全文检索、Session lineage 和多进程写竞争处理。
- **Codex** 最值得借鉴的是稳定的 Thread/Turn/Item 应用协议、客户端幂等 ID、乐观并发保护、权限请求与生命周期事件。
- **Claude Code** 的实现本身不可复用，但其 Checkpoint/Rewind、权限运行时强制、Session Hook 生命周期和高风险模式隔离具有很高的行为设计参考价值。

Morrow 不应把它们拼装成一个“大框架”。Stage 4 是 Morrow 最核心的执行一致性层，必须保持现有不变量：

```text
ConversationLog 仍是消息语法权威
AgentLoop 仍是正常聊天历史唯一写入者
ToolExecutor 仍是工具安全执行入口
SQLite 负责运行状态
Artifact Store 负责大型字节
YAML / CredentialStore 保持各自权威
恢复不得盲目重放副作用
权限不得由模型或项目内容提升
```

推荐的采用比例：

```text
Morrow 原生领域设计          60%
成熟项目协议/算法语义借鉴     30%
经许可审查后的直接代码复用     10% 以下
```

直接代码比例应该很低。原因不是这些项目不成熟，而是语言、状态模型、安全边界和发布节奏不同：Pi 为 TypeScript，Codex 为 Rust，Claude Code 不提供可复用实现；Hermes 虽为 Python，但其 Session/message 模型与 Morrow 的 `TaskRun + AgentRun + ToolExecution + Approval` 正规化模型不同。

---

## 2. 采用级别

### 2.1 Level A：直接代码复用

只用于满足以下全部条件的小型、隔离、稳定组件：

- 上游许可证允许。
- 代码与 Morrow 的领域边界相容。
- 不需要把上游运行时或全局状态一起引入。
- 能写独立单元测试。
- 能记录原仓库、固定 commit、原文件、许可证、修改说明。
- 后续能由 Morrow 自己维护，而不是依赖上游内部 API。

候选例子：

- SQLite retry/backoff 的小型 helper。
- Session tree/common-ancestor 的纯算法。
- 明确版本的迁移测试 fixture。
- 结构化摘要模板中的字段设计。

不应直接复制：

- 完整 Session Manager。
- 完整 Agent Loop。
- 完整权限解析器。
- 上游 JSONL/SQLite Schema 全表。
- 上游 Provider SDK 对象序列化。
- 未稳定的内部模块。

### 2.2 Level B：语义移植

这是 Stage 4 的主要采用方式：理解上游合同和失败模式，再用 Python 和 Morrow 领域对象重新实现。

适合：

- Pi 的树形 lineage、self-contained compaction checkpoint、common-ancestor branch summary。
- Hermes 的 WAL/transaction/retry/migration 纪律。
- Codex 的客户端幂等 ID、`expectedTurnId` 类乐观并发保护、Item 生命周期。
- Claude Code 的“代码恢复与会话恢复分离”的 Checkpoint UX。

### 2.3 Level C：行为参考

适用于 Claude Code 等没有可复用源代码，或上游实现与 Morrow 差异过大的部分。

方法：

- 只记录可公开观察的行为合同。
- 不反编译、不复制非公开实现。
- 用 Morrow 自己的领域模型实现等价或更严格的行为。

### 2.4 Level D：故障模式复用

这是收益最高、风险最低的一层：把上游真实问题转成 Morrow 的自动化验收场景。

例如：

- 最终 flush 错误被吞掉导致消息丢失。
- 同一用户输入因重试重复持久化。
- 确认文本在重启后被再次消费。
- recorder shutdown race 丢失尾部记录。
- Session 文件因重复压缩和原始工具输出膨胀到数百 MB/GB。
- 恢复时工作目录、Sandbox 或 Approval Policy 漂移。
- 文件和索引数据库成为两个权威并发生分叉。

---

## 3. 项目逐项评审

## 3.1 Pi

### 最成熟的设计

Pi 的 Session 采用 JSONL Entry，并通过 `id` / `parentId` 形成历史树。当前叶子只是树中的一个位置，回到旧 Entry 后可以直接产生新分支。其 Session 格式还把普通消息、模型切换、压缩、分支摘要、扩展状态等表示为显式 Entry。

尤其值得采用的设计：

1. **不可变历史 + parent linkage**
   - 分支不会覆盖旧历史。
   - 可以计算 root-to-leaf 上下文。
   - 可以找到两个分支的最深公共祖先。

2. **CompactionEntry 是显式对象**
   - 不把摘要偷偷覆盖进旧消息。
   - 新版 `retainedTail` 让压缩条目成为自包含 Checkpoint。
   - Context Builder 可以从 Checkpoint 和后续 Entry 重建模型上下文。

3. **运行记录与模型上下文分离**
   - `CustomEntry` 可持久化扩展状态，但不进入 LLM Context。
   - `CustomMessageEntry` 才明确进入 Context。
   - 这说明“持久化了”不等于“应该发给模型”。

4. **压缩前选择切点，而不是简单删最旧消息**
   - 识别 Turn 边界。
   - 对超长单 Turn 支持 prefix summary。
   - 累积 `readFiles` / `modifiedFiles` 等工程事实。

5. **分支摘要基于公共祖先**
   - 只总结离开主路径的分支部分。
   - 不重复总结共同历史。

### Morrow 应采用

```text
Pi 语义                     → Morrow 实现
Entry parentId              → Session fork lineage + origin sequence
root-to-leaf context        → PromptAssembler 从 fork prefix/checkpoint/local records 投影
CompactionEntry             → ContextCheckpoint
retainedTail                → retained_record_ids / retained_tail_json
CustomEntry 不进模型         → Operational records 与 prompt-visible records 分离
common ancestor             → Fork/branch summary 算法
fileOps ledger              → ContextCheckpoint.file_operations
versioned format migration  → schema + payload codec 迁移测试
```

### Morrow 不应照搬

- 不用单个 JSONL 文件作为 Stage 4 的运行状态权威。
- 不在同一 Session 文件内完成所有分支；Morrow 保留“Fork 创建 child Session”的产品语义。
- 不把 ToolExecution、Approval、TaskOutcome 压回通用 message entry。
- 不让 Compaction Summary 替代原始证据。

### 可复用程度

- 许可证：MIT。
- 建议：算法语义移植为主；common-ancestor、cut-point 等纯函数可在固定 commit 后评估小范围移植。
- 不建议：把 Pi SessionManager 直接改写成 Python。

---

## 3.2 Hermes Agent

### 最成熟的设计

Hermes 使用 SQLite `state.db` 保存 Session 与 Message，采用 WAL、显式 Schema Version、FTS5、Session lineage，并针对多个 Hermes 进程共享数据库设计了短 busy timeout、应用层随机退避、`BEGIN IMMEDIATE` 和周期 WAL checkpoint。

值得采用：

1. **SQLite 作为结构化 Session 权威**。
2. **Schema migration 显式版本化**。
3. **WAL + bounded retry + BEGIN IMMEDIATE**，让锁竞争在事务开头暴露。
4. **FTS5 作为可选 Session 搜索能力**，并考虑 CJK/substring。
5. **Session lineage**，可用递归 CTE 查询祖先和后代。
6. **展示内容与 API 重放内容分离**的思路，避免 UI 文本和 Provider replay payload 混为一谈。
7. **跨进程压缩锁/Turn lease 的问题意识**。

### Morrow 应采用

- `sqlite3` 连接工厂统一配置 foreign keys、WAL、busy timeout 和 synchronous policy。
- 短、有限、可观测的 write retry，随机抖动可注入测试随机源。
- 所有写事务默认在需要抢写锁时使用 `BEGIN IMMEDIATE`。
- 迁移前使用 SQLite online backup API，而不是复制活动中的 WAL 文件。
- `state doctor` 检查 integrity、FK、Conversation grammar、Artifact 引用和未终止 Run。
- Session 搜索先做能力探测；FTS5 不可用时安全降级，不让可选搜索阻塞 Stage 4 核心完成。
- Provider replay payload 采用 Morrow 的 canonical payload codec，不保存 SDK 私有对象。

### 上游问题转化出的硬要求

Hermes 的公开问题展示了几类 Stage 4 必须防止的故障：

- 持久化错误不能仅记录 warning 后继续；副作用链路必须 fail closed。
- 相同平台消息/用户输入必须有稳定幂等键。
- 压缩前必须完成当前 durable flush/finalize。
- plain-text confirmation 不能跨重启重复授权危险动作。
- DB 中存在不合法 ToolCycle 时，不能把整个 Session 直接喂给 Provider；需要隔离、诊断与修复模式。

### Morrow 不应照搬

- 不采用“一个 messages 表承载所有对象”的模型。
- 不把完整 system prompt、原始 reasoning 或无界 ToolResult 默认写入数据库。
- 不把 Session lineage 与 context compression 强制绑定；Morrow 的 Checkpoint 和 Fork 是独立对象。
- 不引入 Gateway/平台路由相关 Schema。

### 可复用程度

- 许可证：MIT。
- 建议：SQLite helper、migration discipline 和测试模式可选择性复用或重写。
- 不建议：导入 Hermes SessionDB 作为依赖；它的生命周期和 Schema 变化速度会把 Morrow 核心绑定到外部项目。

---

## 3.3 OpenAI Codex

### 最成熟的设计

Codex app-server 对 Thread、Turn、Item、Queue、Approval、Compaction、Review 和 Shell command 采用显式 RPC 合同。对 Stage 4 最有价值的不是其 JSONL rollout，而是应用协议：

1. **Thread / Turn / Item 生命周期**
   - `turn/started`
   - `item/started`
   - output delta
   - `item/completed`
   - `turn/completed`

2. **稳定客户端 ID**
   - `clientUserMessageId` 可随 user message 回显。
   - Queue 同时保留客户端 ID 与服务端稳定 ID。

3. **乐观并发保护**
   - `turn/steer` 要求 `expectedTurnId`。
   - 客户端不能把输入误送给已经切换的活跃 Turn。

4. **分页与游标**
   - 长 Session/Queue 不要求一次性全部加载。

5. **权限请求返回 granted subset**
   - 用户可以只授予请求权限的一个子集。
   - 授权作用域是显式字段。

6. **Compaction 也是可观察的生命周期操作**
   - 不是静默改写历史。
   - 通过标准 Item 事件反馈进度。

### Morrow 应采用

```text
Codex                   → Morrow
clientUserMessageId     → client_message_id + UNIQUE(session_id, client_message_id)
request id              → command_id + CommandReceipt
expectedTurnId          → expected_session_revision / expected_agent_run_id
Thread/Turn/Item event  → Session/Turn/ToolExecution ApplicationEvent
cursor pagination       → QueryPage[cursor, limit, next_cursor]
granted subset          → CapabilityGrant / Approval 只保存实际授予集合
compaction lifecycle    → context.compaction.started/completed/failed
```

### 上游问题转化出的硬要求

Codex 的公开问题提供了非常有价值的压力测试：

- shutdown 时 recorder 的旧 clone 不能在 writer 关闭后继续排队，尾部记录不能丢。
- 不使用无界 JSONL 保存重复 replacement history、raw tool output、reasoning 和 token events。
- 每行/每条记录设置明确尺寸预算，超限内容转 Artifact；迁移不能静默丢弃大记录。
- 状态目录和数据库使用用户私有权限。
- 恢复时不能让 cwd、Sandbox、Approval Policy 或 Full Access 状态漂移。
- 不建立“Rollout 文件是真相、SQLite 只是索引”这种双权威，除非存在确定性 repair/reindex。

### Morrow 不应照搬

- 不把 append-only rollout JSONL 作为唯一持久化形式。
- 不复制 Rust Session Runtime。
- 不在 Stage 4 引入完整 Queue/Goal Scheduler。
- 不把用户主动 `!` Shell 与 Agent Tool 权限混成同一执行入口。
- 不让 UI 标签替代实际有效权限的计算与展示。

### 可复用程度

- 许可证：Apache-2.0。
- 建议：协议和 Schema 语义借鉴；直接 Rust→Python 代码复用价值很低。
- 若复制代码片段，必须保留 Apache-2.0 要求的版权/NOTICE 信息。

---

## 3.4 Claude Code

### 最成熟的设计

Claude Code 的可参考部分主要来自公开文档：

1. **Checkpoint 在每次用户 Prompt 前建立**。
2. **会话恢复和代码恢复可以分开选择**：
   - 恢复代码和会话；
   - 只恢复会话；
   - 只恢复代码；
   - 从某点开始摘要；
   - 摘要到某点。
3. **摘要不删除原始 Transcript**，只是改变活动上下文投影。
4. **只承诺恢复自身文件编辑工具跟踪的改动**；Bash、外部进程、并发 Session、symlink/hardlink 有明确限制。
5. **权限由 Runtime 强制，不由 Prompt/CLAUDE.md 强制**。
6. **危险的 bypass 模式只建议在隔离环境中使用，并支持管理员禁用**。
7. **Hook 生命周期**覆盖 SessionStart、UserPromptSubmit、PreToolUse、PermissionRequest、PostToolUse、PostCompact、SessionEnd 等阶段。
8. **SessionEnd 是通知/清理点，不能阻塞终止**。

### Morrow 应采用

- 在每个被接受的 User Turn 前创建轻量逻辑 `WorkspaceCheckpoint` marker，不扫描整个项目。
- Checkpoint 先记录 Git HEAD/工作空间身份；受管 File Tool 第一次修改某路径前，再惰性捕获该路径的 before revision 和 before-image/reversible-patch Artifact。
- 用户恢复操作明确区分：
  - `fork conversation`；
  - `restore managed files`；
  - `fork + restore`；
  - `summarize before/after checkpoint`。
- 原始 ConversationRecord 保持不可变；“rewind conversation”通过 Fork 实现，不物理删除历史。
- 只对 Morrow 受管 File Tool 的修改承诺恢复；Shell、外部变化、链接文件返回 limitations/conflict。
- 先定义内部 lifecycle event；任意用户 Hook 执行机制不进入 Stage 4 核心。
- 高风险 Full Access/Bypass 状态不随 Resume 自动继承。

### Morrow 不应照搬

- Claude Code 实现不是开源依赖，不能复制内部代码。
- 不把“100 个 checkpoint”当成 Morrow 固定常量；应由 retention policy 和引用关系决定。
- 不宣称 Checkpoint 能替代 Git。
- 不采用纯 prompt boundary 作为硬安全规则；硬限制进入 PermissionSnapshot/Policy。
- 不在 Stage 4 实现背景 classifier 自动审批；Morrow 的 Controlled Auto 只针对结构化、可对账 Tool。

### 可复用程度

- 只做行为/协议参考。
- 文档中的术语可以在来源说明中引用，但 Morrow 对象和命令保持自己的命名。

---

## 4. 跨项目综合采用矩阵

| 能力 | 主参考 | 辅助参考 | Morrow 最终决策 |
|---|---|---|---|
| Session operational store | Hermes | Codex | SQLite 当前状态权威；不采用 JSONL 双权威 |
| Conversation lineage | Pi | Hermes | child Session + origin sequence/checkpoint；父历史不可变 |
| Context compaction | Pi | Claude Code | 自包含 ContextCheckpoint；原始记录保留；活动上下文是投影 |
| Branch summary | Pi | Claude Code | 从公共祖先计算差异；不重复公共历史 |
| Application protocol | Codex | Claude hooks | typed Command/Query/Event + started/completed/failed lifecycle |
| Input idempotency | Codex | Hermes failures | `command_id` + `client_message_id` + unique constraint |
| Optimistic concurrency | Codex | — | mutation command 带 expected revision/run id |
| Tool recovery | Morrow 原生 | Hermes/Codex failures | ToolExecution Journal + Recovery Contract；不复制通用 replay |
| Approval | Codex | Claude Code | granted subset；一次性 nonce；绑定 intent hash；运行时强制 |
| Permission resume | Morrow 原生 | Codex/Claude Code | 每个 AgentRun 全量冻结；Resume 创建新 Snapshot；高风险不继承 |
| Checkpoint/Rewind | Claude Code | Pi | 会话 Fork 与受管文件恢复分离 |
| Large output | Morrow Artifact | Codex failures | 严格 inline budget；大内容 hash-addressed Artifact |
| SQLite hardening | Hermes | Codex failures | WAL、BEGIN IMMEDIATE、bounded retry、online backup、doctor |
| Search | Hermes | Codex pagination | FTS5 可选能力；所有 Query 分页 |
| Hook/event lifecycle | Claude Code | Codex | Stage 4 只提供内部事件与 outbox；任意 Hook 延后 |
| License/provenance | Pi/Hermes/Codex licenses | — | 固定 commit、NOTICE、来源清单；Claude 仅行为参考 |

---

## 5. 对原 Stage 4 方案必须增加的设计

## 5.1 CommandReceipt 与输入幂等

新增：

```text
CommandReceipt
- workspace_id
- command_id
- command_type
- request_hash
- status
- result_ref / error_code
- created_at / completed_at
```

规则：

- 每个外部写命令必须带 `command_id`。
- User Turn 还必须带 `client_message_id`。
- 相同 ID + 相同 request hash 返回已保存结果。
- 相同 ID + 不同 request hash 返回 idempotency conflict。
- Provider retry 不得重复追加 UserMessage。

建议索引：

```sql
CREATE UNIQUE INDEX uq_command_receipt
  ON command_receipts(workspace_id, command_id);
CREATE UNIQUE INDEX uq_client_message
  ON turns(session_id, client_message_id)
  WHERE client_message_id IS NOT NULL;
```

## 5.2 乐观并发保护

以下命令必须带预期状态：

```text
turn.submit(expected_session_revision)
approval.resolve(expected_tool_execution_revision)
recovery.resolve(expected_recovery_report_revision)
grant.revoke(expected_grant_revision)
agent.steer(expected_agent_run_id)
```

防止 UI、CLI 或两个进程把用户输入/审批应用到已经变化的 Run。

## 5.3 一次性 Approval Intent

Approval 不再只是 yes/no：

```text
Approval
- approval_id
- tool_execution_id
- intent_hash
- input_schema_digest
- permission_snapshot_hash
- requested_capabilities
- granted_capabilities
- nonce_hash
- expires_at
- consumed_at
- decision_actor = user
- decision
```

执行前必须在同一小事务中把 Approval 标记 consumed，并把 ToolExecution 推进到 `executing`。旧文本、旧按钮或重放 RPC 不能再次消费。

## 5.4 全量 PermissionSnapshot

每个 AgentRun 保存完整、不可变的有效权限快照：

```text
PermissionSnapshot
- workspace identity
- canonical cwd / repo root
- sandbox mode
- allowed roots
- network policy
- tool capability map
- hard denies
- grant ids
- policy version
- config/toolset hashes
```

切换模式时执行 **replace**，不执行 partial merge。Resume 总是创建新 AgentRun 和新 Snapshot；旧高风险模式不自动继承。

## 5.5 Operational truth 与 Prompt projection 分离

持久化记录分三类：

```text
authoritative operational record  — 状态与证据权威
prompt-visible message            — 可进入模型上下文
display-only/event record         — UI/审计，不进入模型
```

不得因为一条记录存在于 DB 就自动发送给模型。Provider replay payload 使用版本化 canonical codec；UI 文本与 replay payload 分开。

## 5.6 自包含 ContextCheckpoint

Checkpoint 至少包含：

```text
source sequence range + source hash
summary Artifact
retained tail record ids / canonical messages
active task goal and unresolved items
file operation ledger
artifact refs
prompt builder version
model summary metadata（若使用）
```

重建上下文不依赖“旧摘要旁边刚好还有若干未被清理的行”。

## 5.7 Managed WorkspaceCheckpoint 与 Rewind

每个 User Turn 接受前只创建一个**轻量逻辑 Checkpoint marker**，不扫描或复制整个项目：

```text
WorkspaceCheckpoint
- session_id / task_run_id / before_turn_id
- workspace identity
- git HEAD（若存在）
- coarse dirty fingerprint
- created_at
```

当该 Turn 第一次通过 Morrow 受管 File Tool 修改某个路径时，再在副作用前惰性附加：

```text
checkpoint_file
- canonical path
- before revision/hash
- before-image or reversible-patch Artifact ref
- snapshot policy / sensitivity class
```

同一路径在一个 Checkpoint 内只捕获一次 before-state。禁止保存的敏感路径不创建 before-image，并明确标记为 `not_checkpointable`。恢复只覆盖这些受管且可验证的文件，并进行 current revision 校验；Shell、外部进程、symlink/hardlink、并发改动默认不可自动恢复。

## 5.8 Shutdown Drain 与 Process Ownership

不引入分布式 Lease 系统，但必须有：

- OS WorkspaceWriterLock 作为本机强制单写边界。
- `process_instance_id` 和 `executor_instance_id` 记录到 AgentRun，供恢复诊断。
- Transactional run claim，避免两个入口同时开始同一 AgentRun。
- 明确 shutdown 状态机：

```text
stop accepting commands
→ cancel/interrupt provider and handler where possible
→ finalize or mark interrupted
→ flush durable Conversation/Tool state
→ drain transactional event outbox
→ optional passive WAL checkpoint
→ close connections
→ release writer lock
```

关闭开始后，任何 stale handle 写入都必须失败并暴露错误，不允许静默丢尾部记录。

## 5.9 Storage Budget

建议首版默认值（通过 ADR 和配置可调整）：

```text
inline text field       32 KiB
single JSON payload     128 KiB
ApplicationEvent body   32 KiB
query page default      100 records
query page hard max     500 records
single Artifact         64 MiB
```

超限时：

- 转 Artifact；
- 或截断并记录 `original_size`、`stored_size`、`truncated=true`；
- 绝不静默丢弃；
- 绝不把完整 reasoning/token delta 作为 durable transcript。

## 5.10 State Doctor、Repair 与 Online Backup

`morrow state doctor`：

- SQLite integrity/FK/schema/checksum。
- Conversation grammar。
- ToolExecution 与 ToolMessage 闭合关系。
- Artifact hash/manifest/refcount。
- 非终止 AgentRun 和 run claim。
- orphan blob / orphan metadata。
- workspace identity。
- private permission。

`morrow state repair --dry-run` 先生成 RepairPlan；默认不修改。允许的自动修复必须是确定性的，例如补建可重建索引、隔离损坏记录、回收无引用 temp blob。原始 operational records 不静默删除。

Backup 使用 SQLite online backup API + Artifact manifest，不能在活动 writer 下直接复制 `.db`/`-wal`/`-shm` 文件组合。

## 5.11 Event Outbox

业务状态与 durable ApplicationEvent 在同一 SQLite 事务写入：

```text
state mutation
+ application_event
+ outbox delivery state
COMMIT
```

UI 流式 delta 可以非持久；最终 Item/Turn 状态必须可从数据库查询。Stage 4 不引入外部消息队列。

## 5.12 Loader Validation 与 Quarantine

加载 Session 时必须验证：

- schema/payload version；
- sequence 连续性；
- role/tool-call/tool-result grammar；
- ToolCall ID 唯一与顺序；
- Artifact 引用；
- canonical provider payload 可编码。

发现单条损坏记录时：

- Session 标记 `needs_repair`；
- 生成诊断，不把非法历史发送给 Provider；
- 支持只读导出和隔离修复；
- 其他健康 Session 仍可列出和恢复。

---

## 6. 对实施切片的修订

| Subplan | 新增内容 |
|---|---|
| 35 | 成熟项目采用 ADR、reference lock、许可证/NOTICE、failure corpus |
| 36 | command receipts、run claims、private permissions、bounded retry、online backup |
| 37 | `client_message_id`、expected revision、final assistant transaction、loader quarantine |
| 38 | intent hash、schema digest、one-time approval nonce、structured effect disposition |
| 39 | 上游故障回归、shutdown drain、malformed cycle、duplicate command、cwd drift |
| 41 | strict inline budget、content-addressed blob、orphan recovery、quota |
| 42 | self-contained retained tail、file-op ledger、single-turn prefix summary |
| 43 | common-ancestor Fork、managed WorkspaceCheckpoint/Rewind、限制声明 |
| 44 | CommandReceipt、optimistic concurrency、cursor pagination、event outbox |
| 45 | state doctor/repair、online backup、permission audit、active-write backup test |
| 46 | immutable full PermissionSnapshot、granted subset、effective permission query |
| 47 | 只允许结构化 intent；禁止基于任意 Shell prefix 的 raw auto |
| 48 | 将 Pi/Hermes/Codex/Claude 暴露的故障模式全部纳入 E2E acceptance |

---

## 7. 上游故障回归集

Stage 4 Closeout 至少包含以下测试：

1. **Persistence error fail-closed**：disk full/locked/schema error 后有副作用 handler 未启动。
2. **Duplicate inbound retry**：同一 `client_message_id` 重试 10 次只产生一个 UserMessage。
3. **One-time approval**：审批已 consumed 后，即使重启并重放相同请求也不能再次执行。
4. **Finalize before compact**：Assistant final 尚未提交时不能开始生成 authoritative Checkpoint。
5. **Tail write vs shutdown**：shutdown 与最后一条 durable append 并发时，要么 append 完成，要么调用方得到明确失败；不能成功返回但数据丢失。
6. **Oversized tool output**：MB 级输出进入 Artifact，Session row 和 Prompt 保持有界。
7. **Repeated compaction**：多次压缩不复制完整旧 history，不产生指数/线性大规模重复。
8. **Malformed ToolCycle isolation**：一个损坏 Session 不导致 Session 列表或其他 Session 失败。
9. **Resume permission drift**：恢复后创建新 Snapshot；旧 Full Access/Auto 不继承。
10. **Resume workspace drift**：canonical repo root/cwd 不一致时阻止自动继续。
11. **Dual writer**：第二个进程不能认领同一 AgentRun。
12. **Backup during write**：online backup 可恢复到一致事务点。
13. **Artifact/metadata split**：rename 后 metadata 失败产生可回收 orphan，不产生虚假引用。
14. **Fork common ancestor**：分支摘要不重复共同前缀，父 Session 不变。
15. **Managed rewind conflict**：文件已被外部修改时不覆盖，返回 conflict。
16. **Private storage**：新建 state/artifact 目录权限符合当前 OS 的私有用户语义。
17. **Pagination**：十万级记录查询保持分页，不一次 hydrate 全部 payload。
18. **Event outbox**：业务状态提交后即使 UI 断开，最终事件可重放；token delta 不进入 durable outbox。

---

## 8. 许可证与来源治理

当前参考项目许可证：

- Pi：MIT。
- Hermes Agent：MIT。
- OpenAI Codex：Apache-2.0。
- Claude Code：只做公开文档层面的行为参考，不复制实现。

Morrow 在复制任何代码前应先明确自身项目许可证，并新增：

```text
THIRD_PARTY_NOTICES.md
docs/references/stage4-reference-lock.yaml
docs/references/stage4-adoption-log.md
```

`stage4-reference-lock.yaml` 示例：

```yaml
reviewed_at: 2026-08-19
references:
  - project: pi
    repository: https://github.com/earendil-works/pi
    commit: <pin-before-implementation>
    license: MIT
    files:
      - packages/coding-agent/src/core/session-manager.ts
      - packages/coding-agent/src/core/compaction/compaction.ts
    adoption: semantic_port
    morrow_targets:
      - src/morrow/core/context_checkpoint.py
      - src/morrow/services/fork_service.py

  - project: hermes-agent
    repository: https://github.com/NousResearch/hermes-agent
    commit: <pin-before-implementation>
    license: MIT
    adoption: pattern_and_test_reference

  - project: codex
    repository: https://github.com/openai/codex
    commit: <pin-before-implementation>
    license: Apache-2.0
    adoption: protocol_reference

  - project: claude-code
    source: public_documentation
    checked_at: 2026-08-19
    adoption: behavioral_reference_only
```

每一次直接代码采用必须记录：

- 原始 commit 和 path。
- 原始许可证。
- Morrow 目标文件。
- 是否修改。
- 测试覆盖。
- 后续上游同步策略（通常为“不自动同步”）。

---

## 9. 推荐的最终组合

```text
Session / SQLite foundation       ← Hermes 模式，Morrow 正规化 Schema
Conversation lineage / compaction ← Pi 算法语义
Application protocol              ← Codex 的稳定 ID、并发保护、Item 生命周期
Checkpoint / rewind UX            ← Claude Code 的分离恢复语义
Tool side-effect recovery          ← Morrow 自己的 Journal + Recovery Contract
Permission safety                 ← Codex granted subset + Claude runtime enforcement
                                  + Morrow run-bound immutable snapshot
Large output / evidence            ← Morrow Artifact Store，吸收 Codex 膨胀教训
Validation                         ← 四者的真实故障模式回归化
```

最重要的判断是：**Morrow 的差异化不应是重新发明 Session 保存，而应是把成熟 Session/Context 设计与更严格的“工具副作用可恢复协议”结合起来。** 目前四个参考项目中，没有一个可以直接替代 Morrow 所规划的 `ToolExecution Journal + intent persistence + reconciliation + outcome_unknown`；这部分仍应成为 Morrow Stage 4 的核心原创边界。

---

## 10. 主要参考资料

### Pi

- [Session file format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md)
- [Compaction design](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)
- [Session manager source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)
- [Compaction source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/compaction.ts)
- [Branch summarization source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/branch-summarization.ts)
- [MIT License](https://github.com/earendil-works/pi/blob/main/LICENSE)

### Hermes Agent

- [Session storage developer guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/session-storage.md)
- [Persistence error issue #8038](https://github.com/NousResearch/hermes-agent/issues/8038)
- [Duplicate user turn issue #47237](https://github.com/NousResearch/Hermes-Agent/issues/47237)
- [Dangerous confirmation replay issue #59607](https://github.com/NousResearch/hermes-agent/issues/59607)
- [MIT License](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE)

### OpenAI Codex

- [App-server protocol](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- [Rollout shutdown race issue #16300](https://github.com/openai/codex/issues/16300)
- [Session log growth issue #24948](https://github.com/openai/codex/issues/24948)
- [Resume permission drift issue #28296](https://github.com/openai/codex/issues/28296)
- [Rollout/state DB divergence issue #31433](https://github.com/openai/codex/issues/31433)
- [Apache-2.0 License](https://github.com/openai/codex/blob/main/LICENSE)

### Claude Code

- [Checkpointing](https://code.claude.com/docs/en/checkpointing)
- [Permissions](https://code.claude.com/docs/en/permissions)
- [Permission modes](https://code.claude.com/docs/en/permission-modes)
- [Hooks](https://code.claude.com/docs/en/hooks)
