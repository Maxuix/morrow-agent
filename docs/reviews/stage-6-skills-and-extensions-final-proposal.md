# Stage 6 Skills 与扩展生命周期最终方案

> 状态：最终设计基线，尚未实施
> 事实基线：本地 `main`，`479270b`，2026-08-24
> 上位约束：`docs/roadmap/stage-6-skills-and-extensions.md`
> 实施入口：`.agent/PLAN.md`

## 1. 结论

这份方案可以作为 Stage 6 的最终设计基线。上一版的总体方向成立，但不能原样实施；
Review 中与当前代码相符的修订已经纳入本方案，主要包括：

- Provider、Model、RunPolicy、SkillSet 和 MCP ToolSet 必须按新 AgentRun 装配并冻结；
- Skill 的“可发现版本”和“某个作用域实际启用什么”必须分离；
- Skill 自带的 `trust_level` 只能是非权威提示，实际 Trust 由 Morrow 本地生命周期产生；
- Skill Script 不能直接复用工作空间根绑定的 `ProcessExecutionService`；
- MCP 必须分别评估 Server 启动风险和工具语义风险，再按 deny 优先合并；
- MCP 调用不自动重试；崩溃、超时或结果丢失统一进入现有恢复语义；
- 动态 JSON Schema、恢复声明、MCP 结果、身份冲突、版本路径、TOCTOU、备份和依赖门禁
  必须在实现前成为明确契约；
- AgentRun 主快照只存引用和摘要，完整 Skill 上下文进入专用运行证据表；
- Operational Store 迁移按职责拆分，Backup 明确升级为 bundle v2，旧 v1 保持可验证。

以下两点没有按 Review 建议机械照搬：

1. 不新增第二套 MCP 权限引擎。MCP 的本地审核信息只是事实证据，最终仍由
   `PermissionSnapshot + CapabilityPolicy + ApprovalService` 决策。
2. 现在不修改 `docs/ARCHITECTURE.md` 去描述尚未存在的结构。实现完成后，每个子计划只把
   已落地事实同步进去，最终收口时再更新完整架构。

## 2. 当前事实与需要补齐的边界

当前代码已经提供可复用底座，但没有 Stage 6 所需的运行时装配边界：

- `AgentLoop.__init__()` 固定保存 Provider、Model、ContextBuilder、RunPolicy 和 ToolExecutor；
  同一进程内切换 `active_model` 不会天然影响下一次 AgentRun。
- `TurnSubmissionCoordinator.submit_user()` 在读取新运行配置前先处理 receipt；这适合幂等，
  但需要把“新请求准备”和“已有 receipt 重放/恢复”明确分流，避免重放读取当前扩展状态。
- `AgentRunSnapshot` 有 64 KiB 总预算，不能把完整 Skill 正文、MCP Catalog 或 Tool Schema
  直接塞入主快照。
- `AdapterRegistry` 只记录工具协议与多工具调用，低于 Roadmap 要求的能力描述。
- `RegisteredTool` 只能用静态 Pydantic 参数模型；MCP 的运行时 JSON Schema 需要通用验证口。
- Tool 恢复声明依赖静态工具名知识；动态 MCP 工具必须把恢复声明随注册项冻结。
- `ProcessExecutionService` 的 cwd 被工作空间根限制；Skill Script 需要 Skill 根语义和只读输入。
- `CapabilityPolicy` 默认拒绝 network、credential、outside-workspace、destructive 和
  `EXTERNAL_EFFECT`；普通“只读工具”声明不能证明 MCP Server 可以安全启动。
- `PermissionSnapshot` 当前只支持 `UNCONFINED_HOST_PROCESS` 提升证据，不包含 MCP Server
  的受审配置和工具 allowlist。
- Operational Store 当前为 v13，迁移注册文件和 Journal 已很大；Stage 6 不应继续把全部
  扩展表堆进一个迁移或一个 Journal 文件。
- 现有 Operational Backup 主要覆盖 SQLite 与 Artifact，不包含 Stage 6 的 YAML/Skill 包。
- 项目当前没有 `mcp` 或 `jsonschema` 依赖，新增第三方依赖必须先完成 Spike 并再次得到用户
  对实际依赖变更的批准。

## 3. 范围和非目标

### 3.1 Stage 6 包含

- 兼容 Agent Skills 目录格式的发现、校验、安装、启用、pin、回滚、停用和移除；
- SkillCandidate 到 generated Draft 的受控转化、检查、Diff、批准和版本化；
- AgentRun 级 Skill 选择、低权限上下文注入、资源读取和使用记录；
- Skill Script 专用执行服务；
- 首个离线可测的 MCP stdio Client、Catalog、动态工具适配和安全执行；
- Provider/Model 控制面与完整能力快照；
- 扩展状态的 doctor、backup v2、restore 验证和 acceptance。

### 3.2 Stage 6 不包含

- Skill 市场、远程自动更新、`uvx`/`npx` 作为默认安全安装或启动方式；
- 后台守护 MCP Server、无人值守脚本执行或自动批准；
- 学习型 Skill 路由、自动模型路由、模型故障切换或成本优化器；
- Stage 7 的 AgentDefinition/Workflow；
- Skill 之间的依赖图、递归 Skill 注入或通用插件框架；
- 将 Skill 正文、脚本、MCP 原始结果或凭据写进公共事件；
- 重新设计公开 AgentEvent 生命周期或放宽 bundled capability-policy 默认值。

## 4. 目标架构

```text
CLI / REPL / future GUI
        |
        v
Application services
  Skills: Catalog | Lifecycle | Draft | Selection | Script | Query
  MCP:    Definition | Catalog | Run bridge | Query
  Model:  Provider/Model configuration | Capability resolution
        |
        v
AgentRunPreparationService
  probe receipt -> prepare new run OR rehydrate existing run
  freeze provider/model/policy/skills/MCP/tools and evidence refs
        |
        v
AgentLoop.run_task(prepared_run)
        |
        v
existing ContextBuilder + ToolExecutor + CapabilityPolicy + Approval + Journal
        |
        +--> filesystem packages / versioned YAML desired state
        +--> SQLite operational evidence, catalogs, snapshots and usage
```

`AgentRunPreparationService` 是唯一新增的跨域装配点。它协调已有端口，但不承担 Skill 解析、
MCP 进程管理、Provider 配置写入或 Tool 执行。各领域仍由小型服务负责，避免形成新的 god file。

## 5. AgentRun 装配与重放

### 5.1 两类对象

运行准备分成可持久化事实与进程内对象：

```python
@dataclass(frozen=True)
class PreparedAgentRunSpec:
    provider_snapshot: ProviderRuntimeSnapshot
    adapter_capabilities: AdapterCapabilities
    model_capabilities: ModelCapabilities
    run_policy: RunPolicy
    skill_selection_id: str | None
    skill_context_id: str | None
    mcp_run_snapshot_ids: tuple[str, ...]
    tool_set_digest: str
    source_revisions: tuple[SourceRevisionRef, ...]

@dataclass
class PreparedAgentRunRuntime:
    provider: ModelProvider
    context_builder: ContextBuilder
    tool_executor: ToolExecutor | None
    mcp_pool: LazyMcpRunPool | None
```

二者由一个薄的 `PreparedAgentRun` 组合。Spec 可被摘要后写入 AgentRun/专用表；Runtime 只在进程内
存在，结束或取消时统一关闭。

`ProviderRuntimeSnapshot` 必须足以在不读取当前 `config.yaml` 的情况下恢复同一调用目标：
provider/adapter ID、ModelRef、API model ID、经过秘密检查的 endpoint、CredentialRef 名称/版本、
Adapter/Model capabilities、配置 revision/digest。它绝不保存 credential value；CredentialRef 已不可用
时恢复应显示稳定的 unavailable/recovery 状态，而不是改用当前 active model。该紧凑快照可在
Subplan 64 的预算测试后作为向后兼容的 AgentRun 字段持久化。

### 5.2 准备顺序

新提交必须按以下顺序处理：

1. `TurnSubmissionCoordinator.probe()` 只读取 receipt、Session lifecycle 和 recovery 状态；
2. `closed_replay` 直接返回历史结果，不读取当前 Provider、Skill 或 MCP 配置；
3. `recovery` 使用已持久化 AgentRun 证据调用 `rehydrate()`，不重新选择 Skill 或工具；
4. 只有真正的新请求才调用 `prepare_new()`；
5. `prepare_new()` 读取当前 Provider/Model、Preference、SkillBinding 和 MCP desired state，创建
   冻结的 selection/context/catalog/tool/permission 证据；
6. 进入 `submit_user()` 的事务后再次检查 receipt，解决并发重复提交；
7. 若事务内发现另一提交已获胜，丢弃刚准备的进程内 Runtime，并按已有 receipt 分支返回。

Provider 构造、MCP 握手和文件读取不能发生在 SQLite 写事务中。

### 5.3 AgentLoop 改动边界

`AgentLoop` 保留 Task/Turn、模型循环、ToolCycle 和 ConversationLog 所有权，但不再把一次启动时的
Provider/Model/RunPolicy/ToolExecutor 当作永久运行配置。`run_task()` 接收 PreparedAgentRun，
每次只从该运行对象创建 runner/tool cycle。保留的 `run_turn()` 继续薄委托同一循环。

这不是把装配逻辑搬进 AgentLoop；AgentLoop 不读取 YAML、SQLite、Skill 目录或 MCP 配置。

## 6. Skill 领域设计

### 6.1 身份、版本与启用状态

使用四个明确概念：

```text
SkillDefinition   稳定身份、名称、来源、作用域、来源 locator
SkillVersion      不可变内容版本、树摘要、manifest、验证/批准结果
SkillBinding      global/workspace 对某个 Skill 的 enabled/pinned desired state
SkillSelection    某次 AgentRun 实际采用的 skill_id/version/activation_reason
```

`SkillVersion` 不保存 `active | disabled`。同一版本可以在全局停用、在 Workspace 启用，或被某次
AgentRun 选中；不能用一列状态表达这三种事实。

Binding 的第一版字段保持最小：

```text
skill_id
scope: global | workspace
scope_id: null | ws_...
enabled
pinned_version_id | null
selection_mode: explicit | workspace_default | description_match
revision
```

第一版不做学习型自动路由。用户在输入中显式指定的 Skill 和 Workspace 默认 Skill进入候选；描述
匹配只作为保守、数量受限、可关闭的 fallback，且必须记录 activation reason。

### 6.2 来源与身份冲突

来源优先级不用于静默覆盖。Catalog 使用复合身份 `(scope, source_kind, skill_id)`，并应用以下规则：

- 同一 `skill_id`、相同树摘要：折叠为同一可见内容并展示全部来源；
- 同一 `skill_id`、不同树摘要：标记 `identity_conflict`，两者都不可自动启用；
- 不同 `skill_id`、同名：标记 `name_conflict`，CLI 必须用 ID 或来源限定；
- generated/imported 与 user/builtin 冲突：不能靠目录顺序获胜；
- 工作空间来源只对该 Workspace 可见，跨 Workspace 查询失败关闭。

### 6.3 安全版本路径

外部 manifest 中的 `version` 是展示值，不能直接成为目录名。Morrow 为每个已管理版本分配固定格式
`skv_<opaque-id>`，目录只使用该 ID：

```text
skills/<source-kind>/<skill-id>/<skv-id>/package/...
skills/<source-kind>/<skill-id>/<skv-id>/managed-version.json
```

`managed-version.json` 由 Morrow 写入，包含展示版本、来源、安装时间、逐文件摘要、树摘要和批准
证据引用。导入时拒绝路径分隔符、`.`/`..`、NUL、大小写/Unicode 归一化碰撞和保留名称。

### 6.4 Trust 权威

`SKILL.md` 或 `morrow.yaml` 可以声明 `morrow.requested_trust`，但这只是提示。有效 Trust 只来自：

- builtin：随 Morrow 发布并由发行清单证明；
- user_authored：用户管理目录与本地 provenance；
- generated：受控 Draft/批准记录；
- imported：安装来源、树摘要和本地审核结果。

有效值由 Catalog/Lifecycle 计算并写入本地管理 envelope 或 SQLite 证据。Skill 包不能通过修改
sidecar 提高自己的 Trust，Trust 也不能绕过 CapabilityPolicy。

### 6.5 完整性与 TOCTOU

发现阶段计算 canonical package tree：只包含普通文件，路径归一化后排序，记录每个文件的类型、
与执行有关的 mode、长度和 SHA-256。默认拒绝 symlink、hardlink、device、socket、FIFO 和逃逸引用。

选择时冻结 `version_id + tree_digest`；加载正文、reference、asset 或 script 时从同一次安全打开
得到的字节计算摘要，并直接使用该批字节生成上下文/Artifact/临时副本，避免 `stat/check` 后再次按
路径读取。任何 drift 都使该 Skill 对新运行变为 `degraded/unavailable`；已开始运行也不能改读新
内容。执行 Script 时使用只读的版本快照或由已验证字节生成的临时副本。

### 6.6 Manifest 与 Context

核心兼容标准 `SKILL.md`。Morrow 扩展字段使用 `morrow.*` 或 `morrow.yaml`，但结构化权限只是
需求声明，不是授权。

Catalog 只保留摘要。选中后才读取正文；正文经过长度、编码、秘密/注入提示扫描和预算选择，作为
低于系统安全边界的专用 `skill_context` block 注入。

完整渲染上下文不放进 64 KiB `AgentRunSnapshot`。SQLite 使用一张专用、受预算约束的
`agent_run_skill_contexts` 表保存冻结正文及 digest；AgentRun 主快照只存 context ID、digest、
selected count、omitted count 和来源 revision。Context 上限由实施时的预算测试定值，必须同时满足
单 Skill、总字符数和 AgentRun 总请求预算，不能用“正文 24 KiB”推断主快照一定合法。

资源读取通过 `SkillResourceService`，只接收冻结 selection/version/path，不开放任意主机路径。Asset
默认作为 Artifact 或 bounded bytes 交付，不进入系统提示。

### 6.7 生命周期与历史版本

```text
discover/validate
→ install disabled
→ enable or pin through SkillBinding
→ select per AgentRun
→ optional new Draft version
→ validate + diff + user approve
→ update binding or retain pin
→ rollback/disable/remove
```

- Active 后内容不可原地修改，变化产生新 `SkillVersion`；
- user/builtin 永不由 Agent 改写；imported 已安装版本也不可原地覆盖；
- `remove` 先解除当前 scope 的 Binding；包删除只允许 generated/imported；
- 被 AgentRun、Task、Usage、Draft、回滚点或 backup 引用的历史版本不可删除；
- 无引用、非当前、非 pinned 的 managed 版本可在用户显式确认后清理；
- 第一版不做自动垃圾回收。

### 6.8 Draft 与 Usage

`SkillCandidate` 只能通过 `SkillDraftService` 创建 generated Draft。服务读取已接受 Candidate 的
bounded Evidence，创建隔离目录，执行静态校验，生成 Diff 和验证报告；不直接启用。接受 Draft 只
生成不可变版本，是否更新 Binding 是第二个显式决定。

`SkillUsage` 只记录 ID、版本、AgentRun/Task、激活原因、结果、纠正标记、bounded 指标和 Artifact
引用，不保存完整用户消息、脚本输出或 Provider reasoning。Stage 6 只积累评估数据，不自动改写。

## 7. Skill Script 执行

新增 `SkillScriptExecutionService`，不直接调用面向 Workspace cwd 的
`ProcessExecutionService`。二者只共享更低层的 `ProcessAdapter`、取消/超时原语、`SecretRedactor`
和结果投影工具。

服务职责保持单一：

1. 接收冻结的 `SkillSelection`、script relative path 和结构化 argv；
2. 校验版本/树/文件摘要与平台约束；
3. 构造只读 Skill 根、隔离临时输出目录和最小环境；
4. 生成 `OperationIntent`，声明 process/network/credential/outside-workspace 风险；
5. 经现有 `CapabilityPolicy + ApprovalService + ToolExecutor` 后执行；
6. 将输出脱敏、限长，产物导入 ArtifactStore；
7. 写入普通 ToolExecution 审计。

第一版只支持 argv，不支持任意 shell string；脚本默认无网络、无凭据、不能写真实 Skill 包或项目。
需要的输入通过只读资源/Artifact 显式挂载，输出只写隔离目录。未批准、无沙箱或平台不兼容时拒绝，
不能降级为无隔离 Host 运行。

## 8. 动态 Tool 通用接缝

为了让 MCP 不在 ToolExecutor 中形成特殊分支，先增加两个小扩展点：

```text
ToolArgumentsValidator
  validate_json(raw_arguments) -> validated mapping/model

ToolRecoveryDeclaration
  effect_class
  missing_completion_policy
  reconciliation strategy id | null
```

`RegisteredTool` 同时携带 validator 和 recovery declaration。本地工具继续使用
`PydanticArgumentsValidator`；MCP 使用 `JsonSchemaArgumentsValidator`。恢复分类只读取冻结在
ToolExecution/AgentRun 证据中的声明，不按动态工具名查静态表。

JSON Schema 验证必须读取 `$schema`：缺失时按 MCP 协议约定使用 2020-12；第一版只实现 Spike
确认过的 dialect，遇到不支持的 dialect 隔离该工具并报告稳定错误，不能静默按另一方言解释。

## 9. MCP 设计

### 9.1 第一版边界

- 只支持 stdio；
- 默认示例是仓库内 Fake Server 或受管理的绝对 executable + 内容摘要；
- 不以 `uvx`、`npx` 或未固定包版本的 runner 作为安全默认；
- Server 按 AgentRun lazy 启动，运行结束统一关闭；不做 daemon 或跨运行池化；
- 真实网络/凭据路径必须显式配置、显式审批，离线门禁使用 Fake Server。

### 9.2 配置、Catalog 与快照

MCP desired state 进入 global/workspace Extension YAML，秘密只保存 CredentialRef 名称。SQLite 保存：

- 本地 Server 定义投影与 config revision/digest；
- 最近一次 catalog revision、工具 schema/annotation 摘要和 degraded 原因；
- 每次 AgentRun 的 `McpLaunchSnapshot` 与 `McpToolSnapshot`；
- ToolExecution 仍由现有 Journal 所有，不复制一套 MCP 调用日志。

关键启动证据至少包含：server ID、transport、冻结 argv、argv digest、解析 executable、executable
identity digest、cwd policy、confinement、network/credential/outside-workspace 事实、配置 revision、
catalog revision 和 allowlisted remote tool names。

工具快照包含：稳定且兼容当前 Provider 工具名约束的本地名
`mcp__<server-slug>__<tool-slug>`、远端原名、input/output schema dialect 与 digest、result
contract version、本地 ToolEffect/OperationIntent 映射、recovery declaration 和 catalog revision。
Slug 规范化后若超出 64 字符或发生碰撞，使用截断前内容的 digest 后缀确定化消歧；映射随
Catalog revision 冻结。

### 9.3 权限：复合判断，不建立第二权威

每次调用产生两个独立 Intent：

```text
Launch Intent：启动什么、隔离方式、网络/凭据/工作空间外风险
Tool Intent：本次远端工具是 read/write/destructive/external_effect 中的哪一种
```

两者都由现有 `CapabilityPolicy` 评估，结果用纯函数合并：

```text
DENY > REQUIRE_APPROVAL > ALLOW
```

Stage 6 对 `PermissionSnapshot` 增加受 server/config/catalog/toolset digest 约束的 MCP 审核证据。
它证明“本地界面审核过哪些风险事实”，不是授权本身。证据不匹配、配置变化、executable drift、工具
不在 allowlist 或 catalog revision 漂移时必须拒绝并要求新 AgentRun/重新审核。

当前 CapabilityPolicy 对 network、credential、loopback 和 external effect 是硬拒绝。Stage 6 只增加
一个窄的 MCP 例外路径：当 `McpReviewEvidence` 由本地界面产生、与本次 PermissionSnapshot 和两个
Intent 完全匹配时，这四类风险可以从硬拒绝提升为 `REQUIRE_APPROVAL`，不能直接变成 `ALLOW`；证据
缺失/不匹配仍拒绝。Destructive、outside-workspace、privilege escalation 和 git write 在 MCP v1
继续硬拒绝。该逻辑仍位于同一个 CapabilityPolicy，且不修改 bundled profile 默认值。

工具自己的“read-only”注解只作为输入；本地配置不确认时按更高风险处理。Network、Credential、
External effect 不因一次 Server 启动批准而永久放行；每次 ToolExecution 仍按冻结语义决定审批。
Destructive、outside-workspace 和未隔离脚本默认拒绝。

### 9.4 生命周期、失败与重试

`McpRunBridge` 只负责一个 AgentRun 内的 connect/list/call/close 和协议归一化；Server 配置 CRUD、
Catalog 持久化和审批不放进该类。

- connect、initialize、list tools、call tool、close 分别有上限；
- stdout 是协议通道，stderr 只进入 bounded、脱敏诊断；
- Server 崩溃仅使相关工具/Server degraded，不污染 Session 或其他工具；
- 任何 MCP 调用都不自动重试，包括标记为 read 的工具；
- 在 handler entry 前失败可以按现有 `never_started` 处理；entry 后结果丢失一律
  `outcome_unknown` 或 `requires_reconciliation`，由用户通过现有 recovery 表面处理；
- Stage 6 不增加 linked retry，也不把“只读”当作安全重放证明。

### 9.5 MCP Result 契约

协议结果先归一化为内部 DTO，再由现有 ToolExecutor 生成 bounded envelope：

```text
McpNormalizedResult
- is_error
- text_parts[]
- image_refs[]
- audio_refs[]
- resource_links[]
- embedded_resource_refs[]
- structured_content | null
- omitted_count / truncation metadata
```

- Text 经过总字符预算；
- image/audio/embedded resource 以受限 bytes 导入 ArtifactStore，模型上下文只得到引用与摘要；
- resource link 默认只是非权威 locator，不自动拉取；
- structured content 可以是任意 JSON 值，经深度、成员数、字节和秘密扫描；存在 output schema
  时按其受支持 dialect 验证；
- 原始 SDK 对象、stderr、credential、完整二进制和超限字段不持久化；
- `is_error=true` 映射为稳定工具错误，但保留安全的 bounded 诊断文本。

## 10. Provider 与 Model 控制面

### 10.1 能力模型

Adapter 级能力和 Model 级能力分开，Model 可对 Adapter 默认值做更窄的覆盖：

```text
streaming_text
tool_protocol
multiple_tool_calls
structured_output
safe_request_chars / context_limit
input_types: text | image | audio | document
cost_metadata: source, currency, input/output units, updated_at
```

未知能力不是“支持”；Model/Adapter 未声明时按保守路径处理。能力与精确 ModelRef 一起冻结进
PreparedAgentRunSpec，RunPolicy 从该快照计算，不在运行中读取新配置。

### 10.2 控制面

提供 Roadmap 中的 provider/model add/remove/sync/show/use 命令，但遵守：

- YAML 是 Provider/Model desired state 权威；CredentialStore 仍是秘密权威；
- 配置写使用现有 revision/OCC、备份和审批；
- `active_model` 是唯一普通运行默认，不新增 default provider；
- remove 被 active model 或历史不可恢复引用阻挡时给出明确原因；
- model sync 只更新该 Provider 的模型投影，不自动切换 active model；
- 无静默 fallback；新 Adapter 通过 Registry 注册，不在 AgentLoop/Session/Task 写分支。

## 11. 持久化、迁移与备份

### 11.1 权威分配

| 数据 | 权威 |
|---|---|
| Provider/Model、SkillBinding、MCP desired state | versioned YAML |
| Credential secret | CredentialStore |
| Skill 包内容 | managed filesystem package + immutable envelope |
| Catalog、Draft、Usage、运行选择/上下文/MCP 快照 | SQLite operational store |
| Tool 调用、审批、恢复 | 现有 Tool Journal |
| AgentRun 主事实 | 现有 AgentRun + 专用 Stage 6 evidence refs |

SQLite Catalog 是派生/审计投影，不与 YAML 或包目录竞争 Active 权威。启动和 doctor 可以从权威源
重建 Catalog；历史 AgentRun 只依赖自己的冻结证据和不可变包版本。

### 11.2 迁移切分

实施默认使用三个版本，若 Spike 证明某一版本无表变更可在激活前合并，但不能把职责混在一张巨型
迁移中：

- v14：Skill definition/version/catalog/operation、AgentRun selection/context；
- v15：Draft/validation/usage；
- v16：MCP definition/catalog/run snapshot/result artifact links。

每版由独立 `migrations_vNN_*.py` 注册；Journal 按 Skills/MCP 拆成适配器，现有
`adapters/state/journal.py` 只做薄组合。迁移必须幂等、checksum 固定、future schema 拒绝，并有从
v13 升级、失败回滚、备份恢复和 doctor 篡改测试。

所有可能作用于 global 或 workspace 的 operation/receipt 使用 `scope + scope_id`：global 的
`scope_id` 为 null，workspace 使用精确 `ws_` ID。不得用一个必填 `workspace_id` 假装表示 global。

### 11.3 Backup bundle v2

现有 bundle v1 的含义保持不变。Stage 6 新建显式 v2：

- 在线 SQLite backup；
- Artifact 清单与内容；
- global/workspace Extension YAML 和相关配置文档；
- 被引用的 managed Skill versions/envelopes；
- canonical manifest、逐文件 digest、schema versions 和引用关系。

默认不包含 CredentialStore、秘密、用户未管理的外部 Skill 源目录、私有脚本明文诊断或 MCP 原始
结果。创建时获取有界 extension maintenance lock，记录 YAML revisions 和包 digests，复制 immutable
包并执行 SQLite online backup，随后再次校验 revisions/digests，最后写 manifest 并原子发布。Restore
到隔离根后先验证 manifest、路径、digest、SQLite integrity 和跨存储引用，再允许激活。v1 verifier
继续可验证旧包；v2 verifier 不把缺少 Stage 6 文件的 v1 误报为损坏。

## 12. 功能表面

### 12.1 Skills

```text
morrow skill list [--all-sources]
morrow skill show <skill-id> [--source ...] [--version ...]
morrow skill validate <path-or-id>
morrow skill install <local-path>          # 安装后默认 disabled
morrow skill enable|disable <skill-id> [--workspace]
morrow skill pin <skill-id>@<version> [--workspace]
morrow skill rollback <skill-id> [--workspace]
morrow skill remove <skill-id> [--workspace] [--version ...]
morrow skill draft create|show|diff|accept|reject ...
morrow skill usage <skill-id>
```

含歧义 ID/名称、identity conflict、缺失依赖、drift、未来 schema 和越界路径都给稳定错误，不靠
隐式优先级猜测。

### 12.2 MCP

```text
morrow mcp add|show|list|enable|disable|remove
morrow mcp inspect <server-id>              # 显示启动事实和工具风险映射
morrow mcp refresh <server-id>              # 显式握手/Catalog 更新
morrow mcp status <server-id>
```

`add` 不等于 enable；enable 不等于批准所有调用。Inspect 不显示 credential value、完整环境或原始
stderr。

### 12.3 Provider/Model

```text
morrow provider add|show|list|remove|test
morrow model add|show|list|sync|use|remove
```

所有接口调用应用服务；CLI/REPL 不直接写 YAML、SQLite 或包目录。

## 13. 模块边界与防 god file 规则

目标模块按职责拆分：

| 领域 | 模块族 | 不承担 |
|---|---|---|
| Skill core | `core/skills/*.py` | 文件 IO、SQLite、CLI |
| Skill package | `adapters/skills/*.py` | AgentRun 装配、审批 |
| Skill app | `application/skills/*.py` | MCP/Provider CRUD、Tool loop |
| MCP core | `core/mcp/*.py` | SDK 对象、进程管理 |
| MCP adapter | `adapters/mcp/*.py` | YAML 权威、CLI |
| MCP app | `application/mcp/*.py` | AgentLoop、ConversationLog |
| Run prep | `application/agent_runs/*.py` | 领域解析/持久化实现 |
| Provider | `application/providers/*.py`, Registry | Session/Task 修改 |
| Persistence | `skill_journal.py`, `mcp_journal.py`, 独立 migrations | 全部 Stage 6 SQL 堆叠 |
| Interfaces | `skills_cli.py`, `mcp_cli.py`, `providers_cli.py` | 直接 IO/策略判断 |

约束：

- 不继续扩张 700+ 行的 `bootstrap.py`、`turn_lifecycle.py`、`runtime/tools.py` 和
  `runtime/agent.py`；这些文件只接受薄协议接线，必要时先抽出现有职责；
- 新生产模块通常控制在约 300 行以内，超过时按领域职责拆分，不用 helper 压缩掩盖复杂度；
- 一个应用服务只有一个写入权威；跨 YAML/FS/SQLite 的动作使用明确 prepare/apply/finalize saga；
- 不建立通用“ExtensionManager”或“Plugin”基类；Skills、MCP、Provider 共享端口和安全底座，但保留
  各自领域语义；
- 不建立通用依赖图、事件总线或后台调度器来解决 Stage 6 第一版不需要的问题。

## 14. 验收条件

### 14.1 运行冻结与重放

- 同一 REPL 修改 active model 后，下一新 AgentRun 使用新 Provider/Model/RunPolicy；当前运行不变；
- closed replay 不构造新 Provider、不扫描 Skill、不连接 MCP；
- recovery 只用历史快照重建，配置漂移不改变历史 ToolSet/SkillSet；
- 并发同 client message 只落一个 Turn/AgentRun，输掉的 Prepared Runtime 被关闭。

### 14.2 Skills

- 手写 Skill 只在指定 Workspace 启用并按预算注入；
- generated Draft 未批准时不进入任何 AgentRun；批准版本仍需显式 Binding；
- 同 ID 异内容和同名异 ID 都不会静默覆盖；
- manifest 自报高 Trust 不改变本地有效 Trust；
- package drift、symlink、路径逃逸、Unicode 碰撞和摘要篡改失败关闭；
- pin/rollback 后只有新 AgentRun 变化，历史运行仍引用原版本；
- 被历史证据引用的版本不能删除。

### 14.3 Script 与 MCP

- Script 只读 Skill 根、隔离输出、无 shell string、无默认网络/凭据；
- Fake stdio MCP 完成握手、发现、调用、取消、超时、崩溃和关闭测试；
- Launch/tool 任何一方 DENY 即拒绝，任一方需审批即只生成一次合并审批；
- MCP read 调用结果丢失不自动重试；
- schema dialect 不支持只隔离该工具；动态工具恢复不依赖静态名字表；
- text/image/audio/resource/embedded/structured result 均按预算、安全投影和 Artifact 引用处理；
- Server 崩溃不损坏 Session、ConversationLog、SQLite 或其他工具。

### 14.4 Provider、存储与治理

- 能力快照覆盖 Roadmap 的全部字段，未知能力保守处理；
- 新 Fake/第二 Adapter 不修改 AgentLoop、Session、TaskStore；
- v13→v14→v15→v16 迁移、future schema、失败回滚和 doctor 篡改测试通过；
- backup v1 仍可验证；v2 可在隔离根恢复 Extension YAML、受引用 Skill 包、SQLite 和 Artifact；
- 全部输出不泄露秘密、reasoning、SDK 对象、traceback、完整脚本或原始 MCP payload。

## 15. 依赖门禁

实施的第一个子计划只做只读/临时 Spike，比较：

- 官方 MCP Python SDK 对 stdio、生命周期、取消、结果类型和 Python 3.12 的支持；
- SDK 的直接/传递依赖、license、包体和离线 Fake 能力；
- 是否需要独立 `jsonschema`，以及支持哪些 `$schema` dialect；
- 不使用新依赖时的维护成本和协议风险。

Spike 不修改 `pyproject.toml`/`uv.lock`。结论必须形成 ADR；若建议增加依赖，实施相应子计划前再向
用户请求一次明确批准。未获批准时 Stage 6 可继续完成 Skills、运行装配和 Provider 控制面，但 MCP
实现保持 blocked，不能用自制不完整协议客户端冒充完成。

## 16. 完成定义

本方案的实现以 `.agent/PLAN.md` 的 Stage 6 子计划为唯一执行计划。所有子计划逐个验证、提交、合并
并退休；最终离线门禁、doctor、backup/restore、示例与 acceptance 全部通过后，才将当前事实同步到
`docs/ARCHITECTURE.md` 并把 Roadmap 标为完成。真实 Provider/MCP 网络测试只在显式授权与兼容凭据
存在时运行，不是默认离线完成门禁。
