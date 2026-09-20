# 状态与恢复

[架构总览](../ARCHITECTURE.md) · [运行时](runtime.md) · [扩展](extensions.md)

## 权威来源与写入者

| 状态 | 权威来源 | 写入边界 |
| --- | --- | --- |
| Provider 配置、全局 Preference、运行策略覆盖 | `config.yaml` | 对应服务在共享文档锁内保留其他字段；仅存 credential ref |
| 凭据 | CredentialStore / 显式环境变量 | 专用凭据入口；不进入 YAML、普通投影或备份 |
| 工作空间身份与最近使用 | `workspace-index.yaml` | WorkspaceIndexStore / Workspace 服务 |
| Workspace Profile、Preference | 工作空间状态目录内的 YAML | ConfigPatchService、PreferenceWriter；revision/OCC 与原子替换 |
| Agent/Workflow desired source | `agent-definitions.yaml`、`workflow-definitions.yaml` | definition YAML owner；发布服务生成不可变版本 |
| Skill Binding、MCP desired state、编排策略 | 扩展 YAML | Extension store 与领域服务；修改共享同一 revision |
| Session、Turn、Task、AgentRun、执行与审批 | `store/operational.sqlite` | Application/coordinator 经分域 Journal 共享事务 |
| 对话正文 | SQLite conversation records | AgentLoop 经 Session-owned ConversationLog 追加；Session.messages 只读 |
| 草稿、Workflow/NodeRun、控制输入、暂停安全点 | SQLite 分域表 | 对应 Draft、admission、Scheduler、control/pause owner |
| 聊天轨迹索引与安全活动投影 | SQLite timeline/content 表 | TimelineIndexService；引用原始事实，不成为聊天日志 writer |
| Artifact | 文件字节 + SQLite 元数据、引用和状态 | ArtifactService 的预留、受控发布、引用与隔离清理 |
| Learning/Memory/Preference 审计 | SQLite | Review/Inbox/Writer/Promotion/Memory 服务；Active 配置仍归 YAML |

YAML 使用版本化信封、revision、文件锁、临时文件、fsync 和原子替换；`state: cleared` 是合法
状态。Profile 损坏或未来版本阻止相关配置写入；Preference 损坏按作用域隔离。
旧 `handoff.yaml(.bak)` 不属于当前状态 API，生产代码不读取、迁移或清理它。

## Workflow 文件交付

Workflow 节点通过 `submit_node_result` 提交协议登记交付文件：v2 请求按**已声明输出槽位**给出
工作区相对路径与可选 label，服务端读取字节后发布 `ArtifactKind.DELIVERABLE` 快照，并把
name/path/MIME/sha256/字节数写入该 NodeRun 的 `node_submission_artifact_id` marker（JSON，
最后写入）。普通文件不占用节点输出的 `producer_node_run_id`/`output_slot` 唯一列，槽位归属
只由 marker 表达；提交它的 ToolExecution 持有 marker 与全部快照（含 HTML 静态依赖）的持久引用，
因此 Doctor/Cleanup 看到的是真实引用而不是 JSON ID。修订冻结 `submission_protocol_version`：
旧记录缺字段按 v1 处理，并保留 pre-field 内容哈希，旧运行恢复时重建原提交工具 schema。
结果读取沿「修订 required_outputs → 有效输出 → 实际 producer 的 marker」解析，历史 HTML 预览
只用已登记快照字节，不读取当前工作区。结果条目优先显示登记 label，下载使用真实文件名
（清理过的 ASCII 回退加 RFC 5987 的 filename* 参数），历史下载不读当前 path。

## Operational Store

Operational Store 当前 schema 为 **v49**。版本常量见
[core/store.py](../../src/morrow/core/store.py)，期望态和历史 v44 catalog 集中在
[adapters/state/schema.py](../../src/morrow/adapters/state/schema.py)。v49 新库为 83 张表、0 个
业务 trigger；v48 的 94 张表是迁移中间态，v44 兼容库仍可通过 v45–v49 数据迁移收敛。状态/类别列由 Pydantic、StrEnum 和
journal 校验，DDL 只保留主键、外键、长度/数值预算、NULL 形态和查询索引。

打开写库时，42–48 版本依次执行有界、幂等迁移；当前版本缺少普通表、列或索引时，
[schema_reconcile.py](../../src/morrow/adapters/state/schema_reconcile.py) 在维护锁和短事务内做
additive reconciliation。reconciliation 不猜测改名、删除或约束变化，无法安全补齐的变化仍需
编号数据迁移。日常补表/补列不强制备份；结构重建和编号数据迁移各自在执行前保留在线备份，失败
由事务回滚并保留原文件。

SQLite header 的 `application_id` 用于防止误开其他数据库，`user_version` 是当前版本事实。
`store_identity` 身份表和 `schema_migrations` 存在性门控已删除；历史库迁移前仍兼容读取旧身份行。
未来版本可用 `READ_ONLY`/`DIAGNOSE` 打开并标记 `FUTURE_SCHEMA`，写入仍拒绝；损坏、空文件和身份
不匹配保持原文件并进入诊断路径。新库中的命令回执统一为 `command_receipts`，Session/AgentRun
的 1:1 状态写入父表 JSON/列，Workflow NodeRun 直接保存归属引用。
附件 Artifact 关系、规划输入、候选/知识证据、MCP 结果和 Agent 技能绑定也写入拥有记录的
bounded JSON；Review/Preference 的 owner 字段可直接推导对应关系，不再维护单独的关系表。

`memory_search_terms` 是可丢弃的派生索引；doctor 发现 `memory_terms_rebuild` 时，可运行
`morrow memory rebuild-index`。该命令在单一事务中按页清空并重建指定工作区的全部 Knowledge
词条，源记录异常会整体回滚。

```text
{data_root}/                       # 默认 ~/.morrow，可用 --state-root 指定
  store/operational.sqlite         # SQLite；WAL/SHM 使用相同私有权限
  artifacts/
    tmp/                          # staging 字节；目录本身不是 orphan
  backups/operational/
  locks/operational-store.lock     # 数据根维护锁
```

`SqliteOperationalJournal` 是门面，SQL 分属领域 repository。它们共享一个
[SqliteJournalBackend](../../src/morrow/adapters/state/transaction.py) 的事务、时间戳、
replayability 与 touched Session 集合，不能通过拆分 repository 拆散跨域提交。
命令回执与相关状态在领域规定的事务边界提交；YAML/文件发布用已有 saga/receipt 恢复，
不能声称 SQLite 事务能回滚整个文件系统。

## 聊天历史与展示索引

Session-owned [ConversationLog](../../src/morrow/runtime/conversation.py) 校验并追加合法记录；
持久化恢复加载原始记录，fork 从父 Session 的 immutable prefix 投影。进程内 log 是当前写入对象，
SQLite 是跨重启事实，不能互相描述成两个独立 writer。

[TimelineService](../../src/morrow/application/chat_timeline.py) 与
[TimelineIndexService](../../src/morrow/application/timeline_index.py) 组合根会话的普通消息、
规划、控制输入、节点进度和结果引用。v42 的 `timeline_position` 是展示顺序，与
`conversation_position`、runtime event sequence、application cursor 和瞬时 activity sequence
分别属于不同命名空间。
`chat_activity_content` 只保存有界、已安全处理的展示内容；完整工具结果和 Artifact 仍由原 owner
受控读取。索引更新不允许倒推、覆盖或复制原始聊天正文为另一套历史。

## Artifact、Checkpoint 与 Fork

Artifact 发布经过预留元数据、私有临时字节、hash/size 校验、文件与目录 fsync、原子 rename、
available 状态提交。missing/corrupt/staging/orphan 保持显式可见。运行观测不能把原始流、
完整参数或凭据写成 Artifact。附件的显式用户输入入口有自身安全合同，不能放宽普通审计发布。

确定性 ContextCheckpoint 保存来源范围、record IDs、计数/任务投影和引用；压缩 checkpoint
保存摘要及其来源证据。两者都不修改原 ConversationLog，未闭合 ToolCycle 交由恢复处理。
Fork 只接受合法闭合前缀，子 Session 保存 parent/cut/checkpoint lineage，不复制 TaskRun、
Preferences、审批或 grant；子会话可创建自己的新任务。共享 Artifact 使用引用，工作区文件不会回退。

## 恢复、诊断、备份和清理

普通工作要求 Session active 且 health OK。恢复先检查回执，随后在事务内重新验证报告仍 OPEN
及当前健康状态；旧 report 不能清除后来的隔离状态。Host/sandbox 缺少可信 handler completion
时保留 outcome_unknown，禁止自动重放；中断 ToolMessage 仍由原恢复/log 边界追加。

[OperationalDoctor](../../src/morrow/application/doctor.py) 使用诊断连接检查 Schema、SQLite/FK、
对话语法、领域引用和 Artifact，输出安全摘要而不自动修复历史。
`OperationalBackupService` 委托 [BackupService](../../src/morrow/application/backup_service.py)
只生成当前完整 bundle，
组合 SQLite online backup、受管 Artifact、脱敏 YAML、引用的 managed Skill 版本和完整性 manifest。
restore 只写不存在的新目标，凭据永不进入 bundle。

[cleanup.py](../../src/morrow/application/cleanup.py) 默认 dry-run。引用权威是数据根内所有
工作空间的元数据与引用并集，不能只按当前工作区判定 orphan。Apply 在安全目录链和事务内
重验类型/权限/身份，合格文件原子移动到私有 quarantine，保留原字节；无法证明安全则拒绝。
数据根维护与活动工作、排队输入、附件解析和 writer locks 协调，不能删除活跃文件。

YAML 文档同样只接受当前 schema，
解析器不会把旧字段转换为当前字段。
