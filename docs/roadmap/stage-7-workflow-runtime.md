# Stage 7：Agent Definition 与静态 Workflow Runtime

> 状态：进行中；生产总计划已激活并于 2026-08-31 按条件通过的计划评审完成修订（九个子计划
> 已重排，只读并行移至 Stage 8，validate/publish、工具需求声明、disable/revoke、结构化结果
> 提交协议与 needs_revision 语义已闭合）；Subplans 1–2 的 AgentDefinition 与 Workflow 领域/
> 持久化契约已完成并集成；Subplan 3（Workflow Compiler）已就绪但尚未激活
> 阶段结果：Morrow 可以把多个可配置 Agent 作为模块，按经过编译、版本化和可恢复的静态 Workflow 协作执行任务
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 6：Skills 与扩展生命周期](stage-6-skills-and-extensions.md)
> 下一阶段：[Stage 8：自适应编排与 GUI 控制面](stage-8-adaptive-orchestration-and-gui.md)

## 一、阶段目标

Stage 7 首次引入 Multi-Agent，但目标不是让多个聊天机器人自由讨论，而是建立可靠的 Workflow Runtime：

```text
用户选择一个静态 WorkflowDefinition
→ WorkflowCompiler 校验图、输入输出、权限与预算结构
→ 冻结 WorkflowRevision
→ 创建 WorkflowRun
→ Scheduler 按依赖启动 NodeRun
→ AgentFactory 由 AgentDefinition 构造 AgentRun
→ 每个 AgentRun 复用单 Agent AgentLoop
→ 节点通过类型化 Artifact 交换结果
→ 汇总、验证并完成 TaskRun
```

本阶段只要求“手写或预置的 Workflow 能可靠运行”。模型自动生成和 GUI 拖拽编辑属于 Stage 8。
具体实施顺序、比例性规则和当前执行状态以 `.agent/PLAN.md` 及其当前子计划为准。

## 二、核心修正

### 2.1 AgentLoop 不变成多 Agent 控制器

```text
WorkflowRuntime
    ↓ creates
AgentRun
    ↓ executes
AgentLoop
```

禁止：

```text
AgentLoop 内部按角色 spawn_agent()
AgentLoop 直接维护 DAG
ToolExecutor 识别 planner/reviewer/coder
ConversationLog 混写所有 Agent 对话
```

### 2.2 Multi-Agent 以 Artifact 协作为主

节点输出：

- `PlanArtifact`
- `EvidenceBundle`
- `ImplementationPatch`
- `TestReport`
- `ReviewReport`
- `SynthesisReport`

后续节点读取这些 Artifact 的结构化摘要和按需内容，而不是默认继承前一个 Agent 的完整聊天历史。

### 2.3 Workflow 是版本化 DAG

- Definition 可编辑。
- Revision 不可变。
- Run 永远引用一个冻结 Revision。
- 运行中修改只在 Stage 8 引入，并产生新 Revision。
- Stage 7 只增加稳定 node ID、attempt、Artifact producer 和可空 parent Revision 这类低成本
  lineage；不提前实现 GraphPatch、diff、失效传播或 continuation-run 协议。

### 2.4 Direct 也是 Workflow

单 Agent 基线可以显式表示为一个单节点 Direct Workflow，用于统一比较
WorkflowRun/NodeRun/Artifact 概念。Stage 7 只提供 opt-in Direct Workflow，不迁移所有普通任务；
现有 ordinary Direct 仍是默认路径，其 Task/Turn 语义保持不变。Direct 的 `invoking_session`
形状是同一套 Scheduler 上的一种 Session 绑定策略，在串行路径稳定之后（子计划 7）才接入；
Stage 7 不存在独立的 Direct runner 或第二套状态机，首个执行闭环全部使用 isolated Session。

### 2.5 安全、校验与可运行性的比例原则

新增 hard gate 必须同时满足：它保护当前切片正在使用的不变量；失败会导致越权、持久状态
损坏、重复/未知副作用、历史漂移或执行不确定；现有 AgentRun/Tool/Artifact/Session owner 尚未
处理；并且能提供一个确定性拒绝测试和一个最接近的合法接受测试。

否则应选择 warning、只影响目标 Run 的 blocked/failure、串行或 Direct fallback，或者明确延期：

- 图、必需合同、不可变引用和权限越界属于编译/准入错误。
- 临时 Provider/MCP 不可用属于目标 NodeRun 的运行事实，不做编译期联网门禁。
- cost/usage 不可用、模型质量和并行收益属于效果事实，不伪装成安全失败。
- 一个坏 Definition/Run 不得阻止应用启动、普通 Direct 或其他合法 Workflow。
- 校验只在权威输入边界执行一次，不再创建同义 Policy/Validator 层。
- Stage 7 全部文本边界复用现有 redaction/refusal owner 的同一个 value-sensitive mode：普通
  `password`/`authorization` 等词是合法数据；high-confidence credential token 或显式非 placeholder
  secret value 才是不安全值。Definition/TaskContract 在 publish/Start 前局部拒绝，输出侧只脱敏该值并
  标记 incomplete；Workflow root TaskOutcome projection 也使用该模式，不能因
  `password_validation.py` 或 `authorization test passed` 阻止终态闭合。不增加 prompt scanner，也不
  改变 legacy non-Workflow caller。
- 没有当前消费者的通用 DSL、Registry、Converter、分布式锁、追踪平台和扩展框架不进入
  Stage 7。

## 三、进入条件

- `[x]` Stage 4 的 TaskRun、AgentRun、Artifact、恢复和事件边界稳定。
- `[x]` Stage 5 的 Preference/Knowledge 可以按相关性最小注入。
- `[x]` Stage 6 的 Skill、Provider、MCP 和 Tool capability 可被版本化引用。
- `[x]` Stage 3 的文件/Shell写入冲突和已有用户改动能够识别。
- `[x]` 已建立足以准入的单 Agent 成功/返工基线；费用和模型质量证据不完整，因此只限制
  Multi-Agent 的默认推广，不阻止静态 Runtime 开工。

## 四、核心领域模型

### 4.1 AgentDefinitionSource / Version / Head

```text
AgentDefinitionSource（editable YAML）
- agent_definition_id
- name
- description
- role_prompt
- model_selection: exact ModelRef | invoking_active
- skill_version_ids[]
- access_mode_ceiling: read | write
- tool_requirements[]
  - name
  - requirement: required | optional | forbidden
- max_agent_generation_requests（可空正数；Definition 的唯一 Stage 7 run-budget ceiling）
- origin: builtin | user
```

`tool_requirements` 是工具必选性的唯一声明方：required/optional 名字构成期望工具集
（allow-list 语义），`forbidden` 是显式拒绝且优先级最高。合并规则固定：forbidden 与
required 冲突、required 被 task policy/permission/access ceiling 拒绝、required 在 catalog 中
不存在，都是 compile/publication error；optional 被拒/缺失/运行时不可用则从冻结证据中移除并
产生 diagnostic；required 的运行时 backend 不可用只失败目标 Node preparation。WorkflowNode 只能
叠加 restriction-only 的 `tool_requirements`（进一步拒绝或声明本节点机制必需），不得引入
Definition 集合之外的工具。Stage 7 的 SkillVersion 没有工具声明字段，Skill 带入的工具仍受
Definition 声明集合与 task policy 约束。

```text
AgentDefinitionSourceLoadMetadata（adapter/document envelope，不是 body 字段）
- source_revision（OCC document revision）
- source_hash（按 definition ID 从该 canonical normalized body 计算）

AgentDefinitionVersion（immutable Operational Store row）
- agent_definition_version_id
- agent_definition_id
- version
- source_revision / source_hash
- 完整 normalized Definition 字段
- content_hash / created_at

AgentDefinitionHead（Operational Store row）
- agent_definition_id
- current_agent_definition_version_id
- published_source_revision / published_source_hash
- enabled（operational admission gate）

AgentDefinitionRevocation（additive Operational Store row）
- agent_definition_version_id（exact immutable 目标）
- reason / revoked_at / command 证据（单向，不可撤销；用新 Version 取代）
```

用户编辑的是 source 中的 `role_prompt`，不是完整系统提示。Stage 7 不新增 ModelPolicy、
CapabilityPolicy 或 ContextPolicy registry/DSL：`model_selection` 只能是现有 exact `ModelRef` 或字面量
`invoking_active`。`invoking_active` 有唯一的解析时点契约：它在消费方制品自己的冻结边界上解析一次
并永久冻结——Workflow 在 publish 时解析并冻结为 Revision node 的 exact `resolved_model_ref`
（publish-time freeze；之后切换 active model 必须显式重新 publish 才生效），standalone AgentRun
在自己的 admission boundary 解析。同一 Revision 不做 per-run 重新解析，否则同一 Revision 的多次
运行不可重放。
发布服务把有效 source 固化为完整不可变
Version，并在同一个 SQLite transaction 中推进 Head。YAML source 不保存 published pointer 或
enabled gate；编辑后若尚未发布，只表现为 desired ahead of published。publish 新 Version 保留已有
Head 的 enabled 值；首次 publish 默认 enabled，除非该 publish command 明确要求 disabled。
desired-ahead 比较使用该 definition 的 body hash，document revision 只用于 OCC/audit；编辑同一文件中的
无关 definition 不会让所有 Head 假性 stale。
普通 disable 与紧急 revoke 是两种机制。enable/disable 只 OCC 更新 Head，不生成新 Version，只门禁
新准入：Head disabled 阻止新 standalone AgentRun 与新 WorkflowRun Start（Start 校验所有引用的
Agent Head enabled），但绝不阻止已被准入 WorkflowRun 的未启动节点、运行中 AgentRun 或基于冻结证据
的恢复。紧急 revoke 以 exact immutable Version 为目标写入 additive、带审计（reason/时间/命令证据）、
单向的 revocation 记录；它在 Start、每个未启动节点准入和 recovery resume 处检查，被撤销版本的
受影响 Run 以 `cancelled(reason=policy_revoked)` 收口并记录证据，不被记为普通执行失败。
`validate` 永远纯只读：不创建 Version、不推进 Head、不对内置定义 lazy publish；只有显式
`publish`（或 `run --ensure-published`）写库。
固定安全边界由 Morrow 组装并始终具有更高优先级。
Definition 文本 validate/publish 使用上述 value-sensitive owner；安全相关普通词不构成拒绝理由，实际
识别到的 credential 只拒绝该 Definition publication。
workspace YAML 只保存 `origin=user` desired source。`origin=builtin` 是 package 内只读 source object，
通过同一 typed contract 发布/显示，但不能被 `edit` 原地覆盖；Stage 7 返回“使用新 ID 创建 user
definition”的可执行提示，不先增加 copy/fork UX。builtin Head 的 operational enable/disable 仍合法。

### 4.2 AgentRun

```text
AgentRun
- agent_run_id
- agent_definition_id
- agent_definition_version
- workflow_run_id / node_run_id
- task_run_id
- model_ref_snapshot
- skill_snapshot[]
- toolset_snapshot
- preference_snapshot_refs[]
- context_snapshot_refs[]
- run_policy_snapshot
- status
- input_artifacts[]
- output_artifacts[]
- conversation_session_id / conversation_log_ref（只读引用；所有权仍属于 Session）
- usage / stop_code / error
```

AgentRun 不能在运行中因用户修改 Definition 而漂移。Workflow leaf preparation 必须消费 Revision node
冻结的 exact `resolved_model_ref`，不得再次解析 Definition selector 或读取此时的 active model；只有
standalone non-Workflow AgentFactory proof 在自己的 admission boundary 解析 `invoking_active`。

### 4.3 WorkflowDefinitionSource / Head

```text
WorkflowDefinitionSource（editable YAML）
- workflow_definition_id
- name
- description
- origin: builtin | user
- input_contract
- required_outputs[]（绑定 exact node_id.slot）
- tags[]
- default_budget
  - max_agent_generation_requests
  - default_node_max_agent_generation_requests
  - admission_timeout_seconds
  - max_concurrency
- nodes[]（4.5 的 source fields；不含 compiled-only declared cap）
- edges[]
  - from_node_id / to_node_id

WorkflowDefinitionSourceLoadMetadata（adapter/document envelope，不是 body 字段）
- source_revision（OCC document revision）
- source_hash（按 definition ID 从该 canonical normalized body 计算）

WorkflowDefinitionHead（Operational Store row）
- workflow_definition_id
- current_workflow_revision_id
- published_source_revision / published_source_hash
- enabled（operational admission gate）
```

YAML 只持有 editable source。纯 Compiler 读取一个精确 source revision/hash 并返回 candidate；
`WorkflowCompilationService` 在一个 SQLite transaction 中发布 immutable WorkflowRevision 并推进
Head。若该 definition body 随后又被编辑，Query 以 per-definition source hash 显示 desired ahead of
published；同文件无关 entry 的编辑只推进 document revision，不把其他 Head 标成 stale。不假装存在跨 YAML/SQLite
原子事务，也不引入 saga。publish 保留已有 Head 的 enabled；enable/disable 只改变 Head。Workflow
Head disabled 只拒绝新 WorkflowRun，历史/现有 Run 仍可观察并按既有恢复规则处理。

两类用户 source 分别位于 workspace 的 `agent-definitions.yaml` 与
`workflow-definitions.yaml`。它们和 SQLite 中的 Version/Revision/Head 一样进入现有 current-format
backup inventory、verify、restore 与 doctor；必须覆盖 `desired ahead of published`，避免未发布编辑在
恢复时丢失。现有 parsed `BackupYamlEntry` 不能真实表达 malformed source，因此只增加一个
`DEFINITION_SOURCE` file kind/reference，限制为两个 workspace definition-source 路径，仅持有普通
path/hash/size，不带 schema/revision；这不是第二套 backup 格式。两个 YAML 在 backup/verify/restore
中按 bounded raw bytes + path/hash 原样处理，不以解析
成功作为复制或恢复前置条件；malformed desired draft 由 validate/doctor 报告，但不能阻塞 backup、
restore、应用启动、最后一个有效 published head 或 ordinary Direct。这里只扩展现有 backup owner，不
创建 Workflow 专用备份系统。可在不解析 YAML 的情况下复用现有 high-confidence raw credential-literal
拒绝；普通敏感词/字段名和 syntax error 不是 backup gate，实际检测到的 credential 才以 scoped error
拒绝该次 backup，且不影响 runtime 可用性。published head/DB 完整时，malformed unpublished desired
只产生 definition-local doctor warning，整体 health 保持 OK；只有 authoritative published ref/hash/
integrity 损坏才是 error/needs-repair。

因此 editable source 本身拥有图：source-form nodes、unconditional edges、input contract、exact
required `node_id.slot` outputs 与 finite default budget。`entry_nodes`、`terminal_nodes`、normalized budget
和每个 node 的 `declared_node_max_agent_generation_requests` 都是 Compiler 派生并写入 Revision 的事实，
不要求用户在 YAML 重复维护。
Stage 7 v1 的 `input_contract` 只接受 exact `TaskContract@1`，因为 Start 只拥有这一种 bounded Workflow
input producer；其他 kind/version 是 scoped compile error。多入口或替代 input contract 等真实消费者
出现后再扩展。

### 4.4 WorkflowRevision

```text
WorkflowRevision
- workflow_revision_id
- workflow_definition_id
- revision（Definition 内单调展示序号）
- parent_workflow_revision_id（可空；仅记录定义谱系）
- name / description / tags / origin（完整 normalized source metadata）
- input_contract
- required_outputs[]（exact node_id.slot）
- nodes[]
- edges[]
- entry_nodes[]
- terminal_nodes[]
- budget
  - max_agent_generation_requests
  - default_node_max_agent_generation_requests
  - admission_timeout_seconds（正数相对时长）
  - max_concurrency
- compiler_version
- content_hash
- created_by
- created_at
```

### 4.5 NodeDefinition

```text
NodeDefinition
- node_id
- agent_definition_ref（exact immutable AgentDefinitionVersion，不是 mutable Head selector）
- resolved_model_ref（Compiler 在 publish 时从 Definition selector 解析并冻结的 exact ModelRef）
- task_contract
- input_bindings[]
  - input_name（node 内唯一）
  - accepts.kind / accepts.version（exact ContractRef）
  - source（严格二选一）
    - workflow_input: literal `task`
    - node_output: exact node_id / output_slot
- output_contracts[]
  - slot（node 内唯一稳定名）
  - kind / version
  - required_for_node_completion
- access_mode: read | write
- conversation_scope: isolated（invoking_session 随子计划 7 的 Direct adapter 引入）
- tool_requirements[]（可空；只能收窄/禁止 Definition 已声明集合，或声明节点机制必需）
- budget_override.max_agent_generation_requests（可空）
- declared_node_max_agent_generation_requests（Compiler 冻结，正数）
```

Stage 7 v1 只实现 Agent 节点。并行 fan-in 由 Synthesizer Agent 消费多个 Artifact，不先创建
通用 deterministic/merge Runtime。condition、approval、Converter、递归、通用 retry/failure
policy 和 concurrency-group DSL 在出现真实消费者前延期。

每个 binding 的 exact `accepts` 必须等于其 source contract。唯一 Workflow input source 是 literal
`task: TaskContract@1`；binding 不能同时携带两种 source 字段。输出必要性是两个独立事实而不是一个
重载字段：slot 的 `required_for_node_completion` 决定成功节点是否必须物化它，Revision 的
`required_outputs[]` 是独立的导出清单（投影进 root Outcome 的 exact `node_id.slot` 引用）。跨节点输入
绑定与导出 output 都只允许引用 `required_for_node_completion=true` 的 slot，因此任何被下游非可空
binding 消费的输出在生产者完成时必然存在；被绑定但不导出是正常 fan-in 形态。
`required_for_node_completion=false` slot 只是可检查的观察产物：产生时被物化，缺失不失败节点，
不参与 readiness 也不可导出。Stage 7 不增加 binding 级 `on_missing`、optional input、fallback 或
skip 语义——生产者的完成门禁是唯一保障机制，等真实消费者出现再扩展。Artifact identity 固定为
`(node_run_id, output_slot)`。因此 Coder 的 ImplementationPatch 与 TestReport 是两个明确输出，不塞进
一个无类型 composite blob。所有 success/needs-revision root Outcome 必带的导出 result refs 必须适配现有 TaskOutcome
`artifact_refs` 上限（当前 64）；TaskContract 使用单独的 `goal_reference`，不重复占用该 tuple。Compiler
在上限处接受、超一条拒绝。可选叶子明细可带确定性 omission fact 做 bounded projection，但导出 ref
不能静默截断或塞入无类型 manifest。

Workflow 结果只由当前 Revision `required_outputs` 导出清单中 exact 指向、contract kind 为
`ReviewReport` 的 slots 驱动：全部 declared nodes completed 后，其中任一 blocking verdict 得到
`completed/needs_revision`，否则得到 `completed/succeeded`。其他 ReviewReport 只保留为 evidence；不按
“最新报告”猜测，也不新增 result-role schema。

每个跨节点 input binding 必须同时存在同方向的显式 producer→consumer edge；Compiler 对缺边直接报错，
不猜测或自动补边。没有 Artifact binding 的 edge 合法，表示纯控制依赖。cycle/topological check 与
Scheduler 前置完成条件都以声明 edge graph 为准，binding 只叠加 Artifact 可用条件，从而保证 GUI 可见
DAG 与实际依赖图一致。workflow input binding 不属于跨节点 binding，不要求伪造入口 edge。

### 4.6 WorkflowRun / NodeRun

```text
WorkflowRun
- workflow_run_id
- root_task_run_id
- workflow_definition_id
- workflow_revision_id
- status
- result_status: succeeded | needs_revision（仅 completed 时）
- budget_snapshot（Revision normalized budget 的不可变副本）
- admission_deadline_at
- pending_terminal_intent: null | user_cancel
- input_artifacts[]（至少绑定一个 bounded TaskContract）
- started_at / completed_at
- result_artifacts[]

NodeRun
- node_run_id
- workflow_run_id
- node_id
- attempt
- status
- conversation_session_id（queued 时可空）
- leaf_task_run_id（queued 时可空）
- agent_run_id（queued 时可空）
- effective_node_generation_request_cap（queued 时可空，admission 后冻结）
- input_artifacts[]
- output_artifacts[]
- error
- started_at / completed_at
```

建议状态：

```text
queued
running
completed
failed
cancelled
blocked（仅恢复存在未知副作用时）
```

`ready` 由冻结图、依赖终态和 Artifact 绑定查询派生，不同时保存第二份 durable 状态。

`blocked` 只在 Morrow-owned handler 已返回/释放其 live handle 后提交，或由 restart Recovery 在原进程
handle 已不存在时从 durable facts 分类得到；外部 effect 本身仍可 unknown。缺少 Tool/Agent terminal
row 是 unknown evidence，不是“本进程仍能控制 live handler”的证明。

`(workflow_run_id, node_id, attempt)` 唯一，每个执行记录另有唯一 `node_run_id`。Stage 7 只创建
attempt 1；自动 retry 和节点级 rerun 都不进入本阶段。用户显式重跑时创建引用指定不可变 Revision
的新 WorkflowRun。纯 Compiler 是唯一 normalization/validation/hash 路径且不做 IO；
`WorkflowCompilationService` 是唯一 publication 路径，在同一 SQLite transaction 中保存 Revision
并以 OCC 推进 WorkflowDefinitionHead。失败不留下 Revision，也不推进 Head。同一 command replay
在任何重新解析前返回原结果。新 command 必须先按当前 immutable catalogs/config 生成完整 candidate，
再比较当前 published Revision 的 canonical compiled `content_hash`；该 hash 包含完整 normalized source
metadata（name/description/tags/origin）、所有 execution-affecting compiled fields、normalized graph/
contracts/budget、全部 exact immutable refs、exact `resolved_model_ref` 和 compiler version；排除 allocated
ID/display revision、timestamps、parent lineage、source OCC metadata 与 Head operational state。只有相等
才 no-op。`source_hash` 只用于
desired/published evidence，不能因相等而跳过 `invoking_active` 的重新解析。YAML source 不参与该事务，
较新的 source 只显示为 desired ahead of published。

### 4.7 Root Task 与内部叶子 Task

现有 Turn 持久化要求 Turn 和 TaskRun 属于同一个 Session，因此隔离叶子不能把新 Session 直接挂到
根 TaskRun。Stage 7 增加最小 `TaskRunPurpose: user | workflow_node`：

- Stage 7 首版所有 Workflow 节点都使用 `isolated`：创建新的 standalone Session 和匹配的内部
  `workflow_node` TaskRun。`invoking_session` 由子计划 7 的 Direct adapter 引入，只用于恰好一个
  节点且无 edge 的图，复用用户 root Session/root TaskRun；多节点图若包含它，Compiler 必须拒绝，
  避免首个 STOP 提前把 root 置 ready。由于 `needs_revision` 同样把 root 收口为
  `READY_FOR_ACCEPTANCE`（见 8.2），不再需要"invoking-session 图不得导出 result-driving
  ReviewReport"的特殊门禁——adapter 子计划直接用真实合同证明该形态。
- WorkflowRun 保存 `root_task_run_id`；NodeRun 保存 leaf Session/TaskRun 引用。
- 内部 leaf TaskRun 可把现有 `ready_for_acceptance` 作为叶子成功证据，但不会出现在普通用户 Task
  列表，也不能触发 LearningReview。所有普通用户 Task mutation（accept/snapshot/resume/cancel/
  fail/abandon/new）和普通 Turn admission 都拒绝 `workflow_node`。
- Scheduler/Recovery 通过一个明确的内部 leaf-lifecycle application boundary 驱动叶子，不因知道
  内部 ID 就绕过权限；Workflow Query 可只读显示 leaf Session/Task 引用。
- 不增加 SessionPurpose；`Session.current_task_run_id -> TaskRunPurpose` 足以完成最小门禁。
- 只有 root TaskRun 产生用户可接受的权威 TaskOutcome；叶子结果通过 NodeRun 和 Artifact 汇入它。

后文的 root lifecycle owner 按 conversation scope 判定，而不是按 node count：所有全 `isolated` 图
（包括单节点）都由 Scheduler 使用 Workflow/root 原子终态路径；子计划 7 接入的唯一
`invoking_session` shape 称为 Direct 并由 root TurnLifecycle 收口。Multi-Agent 只是产品/拓扑描述，
不是代码分支条件。
- Workflow 启动时把提交任务固化为一个 bounded `TaskContract` Artifact；所有叶子都从显式绑定读取，
  恢复不依赖进程内 prompt，也不复制 root transcript。
- 一个 `purpose=user` root Task 同时最多关联一个非终态 WorkflowRun。关联期间，ordinary Task
  mutation/replacement 与 ordinary Turn admission 拒绝并提示使用 owning foreground cancellation/
  recovery/abandon；
  Workflow-owned root transition 和 exact bound Direct Turn 走明确 application boundary。Workflow
  terminal 后恢复普通行为。这防止 `task new` 在运行中静默 abandon root，或 Direct Turn 竞态绑定到
  另一个 current Task。

这只增加一个用途字段和必要引用，不增加第二套 Task 状态机。

## 五、Prompt 组装

### 5.1 分层顺序

```text
1. 不可覆盖的 Morrow System/Safety Boundary
2. AgentDefinition Role Prompt
3. 当前 Node 的 Task Contract
4. Workspace Profile 与相关 Project Knowledge
5. 与节点相关的 Preferences
6. 已冻结 Skill 指令
7. 输入 Artifact 摘要与按需内容
8. 当前 AgentRun ConversationLog
9. Tool Definitions
```

### 5.2 Context selection（不新增 Definition policy）

Stage 7 的 AgentDefinition 不配置 ContextPolicy。最终可见内容继续由现有 ContextBuilder、
Preference/Knowledge selection 与节点显式 Artifact binding 决定；AgentDefinition 只新增 role prompt、
exact Skill versions 和 Compiler 已冻结的模型选择。实际 prompt/context/permission evidence 继续冻结在
AgentRun。没有第二个当前消费者，不创建 context-selection DSL、registry 或另一套隐私 policy。

默认最小权限：

- Explorer 不需要所有个人偏好。
- Reviewer 不默认读取 Coder 的自我解释，只读取 Patch、测试结果和任务合同。
- Coder 不默认获得全局敏感知识。
- 子 Agent 不获得与节点无关的历史 Session。

### 5.3 独立叶子 Conversation Scope

Direct AgentRun 使用调用方 root Session/TaskRun；每个 Multi-Agent 叶子使用新的 standalone
Session 和匹配的内部 leaf TaskRun。底层仍由对应 Session-owned `ConversationLog` 保存，Workflow
通过 NodeRun、Artifact 和 root TaskRun 索引关联这些叶子，不把所有角色消息混入一个聊天历史。

现有 Session fork 会继承父 transcript，因此不用于叶子隔离。每个叶子仍只能由它自己的 AgentLoop
通过所属 Session 的 ConversationLog 追加消息；Task/Workflow Store 只保存 Session/Task/log 引用、
状态和 Artifact 绑定，不能直接拼接或复制 Agent 对话，也不创建第二套聊天历史系统。

## 六、Artifact Contract

### 6.1 类型化而非仅 Markdown

Artifact 可以包含人类可读 Markdown，但必须有结构化元数据和 schema/version。

以下类型是按模板需要逐步加入的目标集合，不要求在第一个领域子计划中一次性实现；只有出现真实
生产者和消费者的 contract 才进入代码。

首批必需合同是 bounded `TaskContract` 与 role-neutral `TextResult`。前者保存 Workflow 提交的目标、
scope/constraints 和来源引用；后者保存 durable final-Assistant reference/digest、bounded redacted excerpt
与 `content_complete`，但不成为第二个 chat authority。TaskContract 是运行输入，不授予权限，也不是
整段 root Session 历史的副本。

示例：

```text
PlanArtifact
- objective
- scope
- steps[]
- affected_paths[]
- validation_plan[]
- risks[]
- open_questions[]
```

```text
EvidenceBundle
- findings[]
- source_refs[]
- relevant_paths[]
- uncertainties[]
```

```text
ImplementationPatch
- base_revision
- changed_paths[]
- patch_artifact_ref
- rationale
- validation_requested[]
```

```text
ReviewReport
- verdict
- findings[]
- severity
- evidence_refs[]
- required_changes[]
- optional_notes[]
```

### 6.2 Schema 与版本

- Node 输出必须通过 schema 校验后才标记 completed。
- 下游输入绑定声明可接受的 Artifact kind/version。
- 不兼容时由 Compiler 阻止；Stage 7 不实现自动 Converter 或通用 Schema Registry。
- 自由文本不能伪装成已验证结构化 Artifact。

### 6.3 确定性输出提交

Workflow leaf 使用一个可选、role-neutral 的 `NodeResultCommitter`，组合在现有 TurnLifecycle terminal
transaction，而不修改 AgentLoop。同一次模型调用的 final Assistant 先由 owning Session 的
ConversationLog 持久化；在 Turn terminal/root Task 转移前，committer 校验每个
`required_for_node_completion` slot 都已由 durable 事实满足，按
`(node_run_id, output_slot)` 派生可重放 Artifact identity，通过现有 ArtifactService publish/finalize，
再把 available Artifact binding 加入该 terminal transaction。所有必需 binding 成功后才能提交 Turn
terminal；NodeRun completed 仍以实际 AgentRun terminal 为依据。普通 Direct 不注入该 committer。

slot 满足只有两种形态，按 contract kind 区分。自由文本 `TextResult` 直接包装已提交的 final
Assistant 消息本身（整条消息即负载：reference/digest + bounded redacted excerpt），天然免疫 fence
解析、多候选、字段缺失等失败模式。所有结构化合同（EvidenceBundle、ReviewReport、PlanArtifact、
SynthesisReport 及以后的结构化负载）只允许通过一个权威提交：由 leaf composition 注入的内部
`submit_node_result` 机制工具。它不由 definition/prompt/Skill/Artifact 授予，不接受 definition 工具
声明或审批提示，从不出现在 ordinary Direct；唯一效应是经现有 ArtifactService 校验并
staging/publish 声明的输出负载，并在自己的 durable ToolExecution 行上记录 submission fact。规则：

```text
submit_node_result(schema_version, outputs{slot: payload}, summary, evidence_refs[])
1. final message 只是 transcript，绝不从中解析结构化数据；
2. 按冻结 Revision 的 output contract 校验每个 slot 负载；未声明 slot、kind/version 不符、schema
   违反都以 in-loop tool error 返回，模型可修正；
3. 每个 NodeRun 只有一次有效提交：相同重放 no-op 复用，冲突的第二次提交被拒绝且不覆盖；
4. Artifact 以 deterministic (node_run_id, output_slot) identity staging/publish，崩溃可重放；
5. evidence_refs 只能引用本节点作用域内已 durable 的 Artifact/ToolExecution；
6. 提交是普通 in-loop tool round，不引入第二次 structured-completion 或 repair Provider 请求。
```

proposed successful STOP 缺少必需结构化提交时，映射为一个 bounded application error，由既有
AgentLoop error 路径把节点关闭为 `failed(reason=output_contract_unsatisfied)`；error/cancel terminal
不要求输出，避免失败闭合被自身阻塞。崩溃恢复从 durable submission fact 与确定性 identity 完成
commit，不重跑节点、不重解析消息。

committer 只为 proposed successful STOP 强制 required outputs；确定性 parse 或已知 Artifact failure
映射为 bounded application error，随后复用
AgentLoop 既有 error terminal 路径，不能递归再次执行 output commit。

Definition、TaskContract、TextResult、change/test output、metadata/excerpt 与 Workflow 生成的 root
TaskOutcome projection 都复用现有 Artifact/redaction owner 的同一个 value-sensitive mode，不增加第二个
关键词 scanner。普通叙述、路径或验证事实包含 `password`、`credential`、`authorization` 等词必须仍能
发布；Definition/TaskContract 中实际识别到的 credential 在 publish/Start 前局部拒绝；输出或 root
projection 遇到实际 secret 或无法安全完整持久化的
内容时，保存 bounded redacted manifest/reference 与 `content_complete=false`（只有声明 schema 确实要求
精确 unsafe bytes 时才失败）。原始 secret 不落盘，保守词命中也不能把合法 terminal 变成全局可用性
故障。TaskOutcome 本身不新增 `content_complete` 字段：Workflow projection 先省略/替换不安全值，并在
现有 `completion_basis` 加入固定事实 `workflow_evidence_redacted=true`，再交给共享 Outcome owner。
当前 Artifact/TaskOutcome model 在构造与 rehydrate 都会再次验证，因此只增加一个持久化内部
`TextSafetyProfile: legacy_strict | workflow_value_sensitive` discriminator。历史行、普通 TaskOutcome 与通用
Artifact API 默认并保持 `legacy_strict`；只有 typed Workflow Artifact/Outcome application seam 可选择
`workflow_value_sensitive`。YAML、prompt 和 public CLI 不能选择该值；两个 profile 均由现有 redaction/
refusal owner 实现，不创建第二个 scanner 或 policy engine。

因此 Artifact budget、秘密拒绝、filesystem/integrity、SQLite staging 或 bind 失败都不能留下一个
“root 已 ready、必需输出却不存在”的假成功。安全可重放的 staging/bind 间隙复用相同 identity 完成；
结果不明时沿用现有 Artifact/Agent recovery，只 blocked 受影响 Workflow。任何路径都不发起第二次
Provider/repair 请求。

Writer leaf 还在现有 durable tool `handler_completed` 边界启用一个窄的
`ChangeArtifactCapture`。它必须在 process-local `MutationResult`/ChangeSet 被丢弃前，把可表示的完整
unified diff 发布为现有 Diff/Patch Artifact；二进制、移动或无法完整表示的变更发布精确结构清单，
含 operation、path、before/after hash/size 与 `content_complete=false`。现有 Artifact bounds/redaction
仍是唯一内容 owner；无法完整且安全发布的内容降级为结构清单，不为此阻止合法 mutation。同一 handler-completion
boundary 还把识别出的 ValidationFact 物化为小型 TestReport，并引用而不复制既有 command-output
Artifact。现有 command-output publication 是 best-effort；若引用缺失，TestReport 仍保留权威 exit/
ValidationFact，并写 `output_ref=null`、`content_complete=false` 与 bounded omission reason，不把“没有
复制文本”误作“没有验证结果”。Artifact identity 从 `(tool_execution_id, role, schema_version)` 派生并绑定回该 durable
execution。后续 `ImplementationPatch`/aggregate `TestReport` 只组合这些引用；同一次 final Assistant
只能补 rationale/notes，不能覆盖 change/test facts。不得从当前 workspace 猜测历史 diff，也不得
吞掉必需 capture 失败。非 Workflow tool 保持现状，不因该 capture 增加阻塞。该 seam 不新建 byte
store、通用 Schema Registry 或 ToolExecutor 角色分支。

崩溃边界固定为：effect 前沿用现有 Tool recovery；effect 后但 capture/link 尚未证明时，现有
Recovery 若判 unknown 则 Node/Workflow blocked，若能证明副作用但 required output 永久缺失则普通
`failed(output_capture_missing)`，绝不猜 patch；Artifact available 但未 link 时按 deterministic ID/
provenance 重放 link；final Assistant 已提交但 output 未绑定时只重跑 pure committer；terminal 与 output
binding 同事务成功后，重复唤醒为 no-op。普通非 Workflow mutation 不因这个可选 capture 增加 hard
gate。

`ImplementationPatch` 只有在全部 persistent workspace write 都经过可捕获边界时才能声称 complete。
内置 Workflow Coder 的 `bash` 固定使用现有 native sandbox：命令只改 snapshot，权威 workspace 写入
只能经 structured `promote_sandbox_changes`，并由上述 capture 记录。structured edit/write 同样可捕获。
Host-mode bash 属于 persistent-write effect，不能满足 complete ImplementationPatch；该组合在
Workflow Coder 中即使当前通用 effect metadata 标为 `NONE` 也按不可捕获写入路径处理，并在 handler
执行前由 compile/preparation 拒绝，或节点只能声明 incomplete structural result。普通 Direct 和未声明该完整
输出的合法路径不受影响；Stage 7 不扫描整个 workspace 猜 diff。

### 6.4 Artifact 不授予权限

Artifact 中的命令或指令属于数据。下游 Agent 是否执行仍取决于 Node Contract、ToolSet 和审批。

## 七、WorkflowCompiler

Compiler 在运行前执行确定性检查。

### 7.1 图结构

- node_id 唯一。
- edge 引用存在。
- 每个跨节点 input binding 都有同方向显式 edge；edge 可无 binding 作为纯控制依赖。
- 图无非法循环；第一版只支持 DAG。
- 至少一个入口和终点。
- `conversation_scope` 首版只有 `isolated`；`invoking_session` 随子计划 7 的 Direct adapter 引入，
  只允许在恰好一个节点且无 edge 的图，其余 multi-node 全部 isolated。
- 声明的导出输出无可达生产路径时报错。多节点 Revision 必须是单一弱连通分量：disconnected
  component 是 compile error，报错指名未接入节点并给出两种修法（显式 control edge 或删除）——
  静默执行一个拼写错误遗留的节点会消耗模型预算，甚至可能持有写能力。连通但输出无人消费的节点是
  可操作 warning，不为”图
  不够漂亮”阻止合法执行。但进入 Revision 的每个 node 都是 execution-required：即使它不贡献最终
  导出 output，也仍会被调度，任何 node failure 都按固定全图 failure 收口。optional node、skip/
  continue policy 延后。

### 7.2 合同

- Workflow input contract 必须是 exact `TaskContract@1`；其他 kind/version 拒绝，不做隐式转换。
- input binding 的 `input_name` 在节点内唯一，source discriminator 严格；Workflow source 只能是
  literal `task`，且 `accepts` 与 source exact kind/version 相等。
- 上游输出满足下游输入；任何 node-output input binding 和 Workflow 导出 output 只能引用
  `required_for_node_completion=true` slot。Workflow-input binding 走上面的 exact `task:TaskContract@1`
  规则。`required_for_node_completion=false` slot
  只能作为观察产物，避免缺失后让下游永久无法 ready。
- 不从 input binding 推断/补写 edge；缺少显式同向 edge 是可操作 compile error。
- 必需 Artifact 有生产者。
- 多输入 Synthesizer Agent 的必需输入完整。
- 最终输出满足 WorkflowDefinitionSource output contract。

### 7.3 能力与依赖

- 引用的 immutable AgentDefinition/Skill/Provider/Model/Tool 配置与能力证据存在。mutable Head/
  admission gate 的 disabled 状态只产生 warning，不让可变开关决定 Revision 结构有效性；Workflow
  Head enabled 在 Start 检查，Agent Head/其他 runtime gate 在 leaf preparation/admission 检查。
- Workspace Scope 可见。
- Provider 声明支持节点必需能力，或存在当前已经实现的降级路径。
- Provider/MCP 当前在线状态不做编译期联网探测；配置/引用合法即可，临时不可用在目标
  NodeRun 启动时报告。

### 7.4 权限

- 节点请求的能力不超过 AgentDefinition 和任务 Policy。
- Role Prompt/Skill 不能提升权限。
- `access_mode=read` 是可执行 ceiling：移除 Host `bash`、write/edit/config/promotion、unknown/opaque
  和 undeclared MCP effects。只有 preparation 冻结为现有 native sandbox、没有 promotion、既有
  policy 禁止 external effect 且 snapshot mutation 丢弃时，才允许 bash 用于 read/test；不增加
  command parser 或 path predictor。Compiler 只检查声明/config evidence，不探测 backend；native
  backend 在 leaf preparation 时不可用时，`tool_requirements` 声明为 optional 的 bash 被移除、声明为
  required 的只失败目标 Node，不能靠
  串行运行掩盖 read 合同违规，也不让 runtime availability 使 Revision invalid。内置 Explorer/Reviewer 定义为
  只读，Writer 显式标记。
- Runtime ToolSet/effect 与 compile evidence drift 时只失败目标 Node preparation，不静默扩大权限。
- Runtime 仍以现有 PermissionSnapshot、CapabilityPolicy 和 ToolExecutor 为执行权威；Compiler
  不创建第二套安全引擎。

### 7.5 预算

- Workflow 只对 Morrow 可权威准入的维度设置硬上限：primary
  `agent_generation_request_count`（现有 durable `purpose=agent` request rows）、正数相对
  `admission_timeout_seconds` 和 concurrency。Revision 不保存绝对 wall-clock deadline；Start 使用
  injected clock 冻结 `WorkflowRun.admission_deadline_at = started_at + duration`。静态 DAG 与唯一
  NodeRun rows 已经限制 Node admission，不另建 counter。
- Source 的 finite Workflow budget 由 Compiler 固化为 Revision 的
  `max_agent_generation_requests`、`default_node_max_agent_generation_requests`、
  `admission_timeout_seconds` 与 `max_concurrency`。每个 node 的
  `declared_node_max_agent_generation_requests = min(node override（若有，否则 Workflow node
  default）, AgentDefinition run ceiling（若有，否则不额外限制）)`，且必须为正；这四个 aggregate
  字段和一个 node override 是固定 v1 schema，不是预算 DSL。
- 串行节点准入时冻结
  `effective_node_generation_request_cap = min(declared_node_max_agent_generation_requests,
  workflow_remaining_agent_generation_requests)`；只要大于 0 就允许运行，并通过现有 durable
  agent-request admission seam 执行。只有 remaining=0 才停止。
- Stage 7 串行 admission 每次只准入一个节点；评审已否决"并行 batch 为每个叶子预留完整最大额度"
  的设计（过度保守、恢复账本复杂）。Stage 8 的只读并行条目改为按请求原子 claim：在 frozen
  node-local cap 与 Workflow remaining 之下，每次 Provider 请求前以幂等键原子申领、响应后按实际
  结算，不再预留整节点最坏额度。admission 把串行 cap 冻结到 NodeRun；恢复时由 frozen cap 减去该
  leaf 已 durable admitted 的 `purpose=agent` request rows 推导剩余额度，不增加只存在内存里的
  ledger 或第二个 request counter。
- 当前 automatic compaction Provider summary/retry 不经过上述 durable admission seam，因此 Stage 7
  不把该字段称为 total model/Provider requests。Compaction 继续受现有 AgentRun context/retry 上限，
  Workflow aggregate 明确显示 excluded/unavailable；本阶段不为此改造通用 AgentLoop observation。
- request/admission deadline 是 Node/agent-generation request 准入截止，不是 compaction/Tool 总 wall-
  clock deadline。过期后不会强制中断正在执行的 Tool 而制造 unknown side effect。Tool 安全 settle 后，现有 durable agent-request admission seam 在下一次 generation 请求前
  拒绝，并把 active Node 映射为 `failed(reason=budget_exhausted|deadline_exceeded)`、queued nodes 映射为
  cancelled、Workflow/root 映射为 failed；若 Tool outcome_unknown 则沿用 blocked。取消/恢复继续使用
  现有语义。
- tool-call/round 和 Provider token/cost 在 Stage 7 是可观察事实，不是硬总预算。若任一必要 usage
  缺失，总量标为 `unavailable`，不能以零或猜测的最坏值扣减并阻塞后续合法节点。
- 现有每 AgentRun 的 context/retry/tool-timeout Policy 继续生效，但不被误称为 total-token budget。
- Stage 7 没有递归和 Scheduler 自动 retry；usage/cost unavailable 不作为编译失败。

### 7.6 写入冲突

第一版默认规则：

- 每个 Scheduler 管理的 WorkflowRun 同一时刻最多一个运行中的 NodeRun；Writer 因此天然串行。
- Stage 7 不实现任何并发准入；只读并行 fan-out 连同其准入证明、预算申领和可见性屏障整体移至
  Stage 8，并有自己的进入条件。合法 `write` 节点稳定串行。
- 无依赖的多个 Writer 是合法静态图，Scheduler 仍稳定串行它们并发出可解释 warning；本阶段不实现
  并行 Writer、Git worktree 或分布式锁。其他 WorkflowRun、普通 Direct 或外部进程仍由现有文件
  revision/conflict 检查处理，本阶段不宣称 workspace-global lease。
- Reviewer 不直接修改 Coder 产物；需要修复时产生 `ReviewReport` 和 truthful
  `needs_revision` 结果，不自动回环。

## 八、Scheduler 与执行语义

### 8.0 StartWorkflow 准入与创建

`StartWorkflowCommand` 的最小实参固定为：workspace/Workflow Definition ID、command ID、exact
immutable Revision ID、active/healthy Session ID、该 Session 当前 exact `purpose=user` root TaskRun
ID 与 expected row version、bounded TaskContract，以及图使用 `invoking_session`（子计划 7 起）时独立的
client-message ID。Revision 必须属于 Definition，Workflow Head 必须 enabled，所有引用的
AgentDefinition Head 必须 enabled，且 Revision 与引用的 exact Version 都不存在 revocation 记录，且
`session.current_task_run_id == root_task_run_id`、root 为 OPEN。命令不隐式选择 current Task，不创建、
abandon 或 resume Task；调用方需要时先显式调用现有 TaskService。

Start 同一事务还要求 invoking Session/root durable idle：不存在 open Turn 或 nonterminal AgentRun，并
拒绝该 root 的第二个非终态 WorkflowRun。该检查与 ordinary Turn admission 的 active-Workflow 检查形成
双向事务互斥；Start 与 ordinary Turn 并发时只能有一个成功，不新增 process-local flag 或全局锁。
Direct leaf 的 Turn admission 在自己的事务中
再次校验 exact WorkflowRun/root ID、expected root version 和 client-message association；不能在 Start
之后 current Task 被替换时退回绑定新的 current Task。

Start 先按 request digest probe command receipt；新请求通过现有 ArtifactService 以 deterministic
`(command_id, workflow_input)` identity 发布 bounded TaskContract。发布前使用同一 value-sensitive input
projection：普通安全词保持原文，high-confidence credential 在 reserve/Run 创建前拒绝。matching AVAILABLE 复用；matching
STAGING 比较已知 deterministic bytes/hash/provenance：final bytes 匹配则 finalize，final bytes 缺失则经
现有 Artifact filesystem owner 安全重写已知 expected bytes 后 finalize，metadata/bytes 冲突则报告
corrupt/conflict 且不覆盖。same command/different content 冲突。然后单个 Operational Store transaction 再次检查
receipt digest 与全部可变 Start 事实：Session health/current root、root purpose/status/expected row
version、不存在 open Turn/nonterminal AgentRun/nonterminal WorkflowRun、Revision 属于 Definition，
Workflow Head 此刻仍 enabled、所有引用 Agent Head enabled，以及 exact Revision/Version 无 revocation。
通过后才记录 command receipt、冻结 Revision/budget、以 injected clock 得到的 `started_at` 与
`admission_deadline_at`、`WorkflowRun(status=running)`/input binding，以及冻结图每个节点唯一的 attempt-1 queued
NodeRun。Artifact 失败至多留下不可运行的 staging/unbound Artifact；事务失败不留下 partial runnable
Workflow。queued NodeRun 的 Session/leaf Task/AgentRun refs 可空，admission 只绑定并转换这条既有 row，
不能另建 attempt。

Direct 把同一 TaskContract text 只通过 supplied client-message ID 交给一次既有 TurnLifecycle；Workflow
command replay 与 Turn replay 使用不同 receipt 并分别验证。Multi-Agent root 可以没有新 root Turn，
其 TaskOutcome 以 bound TaskContract Artifact 作为 goal evidence。

### 8.1 Ready 判定

Node 在以下条件满足后进入 ready：

- 所有前置节点 completed；Stage 7 的每个 declared node 都是 execution-required。
- 输入 Artifact 已绑定并校验。
- 预算和能力可用；同一时刻只有一个运行中的 NodeRun（串行准入）。

`ready` 是查询/调度派生结果，不写入 NodeRun durable status。

### 8.2 失败策略

Stage 7 v1 只有固定语义：任何 declared Node 失败都使 root TaskRun 与 WorkflowRun 失败，后续节点不再启动；
所有未启动 queued NodeRun 被标记 cancelled，依赖失败节点的 reason 为 `upstream_failed`，其余为
`workflow_failed`。已经完成的 Artifact/副作用仍可检查。Reviewer 的阻塞 verdict 不是 Reviewer
执行失败，也不是系统失败：只有该 report 的 exact slot 位于当前 Revision 导出 required outputs 时，它才
驱动结果；图正常完成后，任一 result-driving report blocking 才使 WorkflowRun 为 completed 且
`result_status=needs_revision`，root TaskRun 进入现有 `READY_FOR_ACCEPTANCE`（与 succeeded 相同），结果
快照引用 ReviewReport 并带固定 `completion_basis` 事实 `workflow_result=needs_revision`。监控、SLO、
学习与 UI 都不得把它当作执行失败；不新增 TaskRun 状态或 TaskOutcome 字段。用户随后接受该结果，或
经现有 `READY_FOR_ACCEPTANCE -> OPEN` resume 后显式创建新的完整 WorkflowRun。其他
ReviewReport 只是 evidence。多种 failure policy、条件分支和自动 review loop 延期。
`output_contracts[].required_for_node_completion` 只决定成功时是否必须物化/bind 该 slot，不把 node 变成
optional；没有
skip/continue/fallback 语义。
blocking verdict 也不会提前跳过其他 execution-required node：Reviewer 自身 completed，其他 ready/queued
节点继续按正常依赖执行，只有每个 declared node completed 后才依据 exact result-driving reports 收口为
`completed/needs_revision`。

全 `isolated` Scheduler-owned Workflow `completed/succeeded` 或 `completed/needs_revision` 时，root
TaskRun 都进入现有 `ready_for_acceptance`（后者由快照中的 ReviewReport 引用与
`workflow_result=needs_revision` 事实区分）；failure、
cancel、abandon 分别委托现有 TaskService command；blocked 时 root TaskRun 保持 open。Direct 节点的
root Task 转移仍由现有 TurnLifecycle 完成（子计划 7 起），Scheduler 只观察，不重复写入。每个完成
Workflow 还写
一个 versioned root `TaskOutcome(trigger=SNAPSHOT)`：以 bound TaskContract Artifact 作为 goal evidence，
并引用导出 result Artifacts；typed `WORKFLOW_RUN` evidence ref + reserved
`workflow_result_snapshot` role 是不可通过普通 snapshot 伪造的标记，并配对现有 typed
`TASK_TRANSITION` evidence ref + reserved `workflow_ready_transition` role，指向本次同事务提交、使 root
进入 `READY_FOR_ACCEPTANCE` 的精确 transition。它不是用户 acceptance，也不触发 LearningReview。

root/Workflow 终态不能以释放 root 的半完成顺序对外可见。全 `isolated` Scheduler-owned Run 的 success/failure/cancel/
abandon，以及 Direct 在 Turn admission 之前的 preparation/admission failure，都在一个 Operational
Store application transaction 中提交 Node/Workflow terminal、root Task transition 与相应 Outcome：
success 使用上述 Workflow result snapshot，其他终态使用 existing terminal Outcome 与 partial evidence。
Direct Turn 一旦 admitted，既有 TurnLifecycle 仍是唯一 root transition owner：先同事务提交 output
binding/root terminal，WorkflowRun 保持 nonterminal 以继续占有 root。只有 STOP/success 的幂等
finalizer 才同事务写 marked Workflow result snapshot 并关闭 Workflow；ERROR/CANCEL/output-committer
failure 只根据 durable terminal 关闭 Node/Workflow，保留既有 terminal Outcome，不伪造缺 required refs
的 result snapshot。这个 exact Workflow-bound Direct Turn 产生的所有 TaskOutcome（STOP、ERROR、CANCEL
与 output-committer failure）均由 internal lifecycle 选择 Workflow text-safety profile；ordinary Direct
保持 legacy-strict。崩溃恢复不重跑 Node。Start validation 在创建 Run 前
失败则 root 保持 OPEN。

现有 acceptance TaskOutcome assembly 只增加一个窄的证据继承：先找到 root 最近一次进入
`READY_FOR_ACCEPTANCE` 的精确 transition，再从 prior SNAPSHOT 中只选择同时携带 typed
`WORKFLOW_RUN/workflow_result_snapshot` marker 与 matching
`TASK_TRANSITION/workflow_ready_transition` ref 的 snapshot，把其 Artifact refs 合并进 accepted Outcome。
中间插入 ordinary snapshot 会被忽略；Workflow success 后若 resume 再由 ordinary Direct 产生新的 READY
transition，旧 Workflow refs 不得泄漏；同一 root 连续运行两个 Workflow 时也只匹配当前 READY epoch。
若已有 Turn goal 则保留它，全 `isolated` root 没有 Turn 时才继承 matching snapshot 的 TaskContract Artifact
goal。只有继承了 matching typed Workflow marker 时，现有 acceptance assembler 才选择
`workflow_value_sensitive`；没有 matching marker 的 ordinary acceptance 保持 legacy-strict。TaskService
不查询 Workflow tables、不解析 summary 文本，也不新增第二套 Outcome model。

isolated leaves 的 Turn/ToolExecution 不属于 root Task，因此 Workflow lifecycle 必须按稳定 Node 顺序从
exact NodeRun -> leaf TaskRun/AgentRun/ToolExecution links、bound Artifacts 与 recovery facts 生成 bounded
deterministic evidence projection，再交给现有 TaskOutcome owner。这样 success/needs_revision/failure/
safe cancel/blocked-to-abandon 的 root Outcome 能保留 partial change、validation、side-effect 与 unresolved
evidence；TaskService 不扫描 Workflow tables，也不增加 LLM 或第二个 Outcome assembler。

固定映射如下；Stage 7 不提供可配置 failure-policy matrix：

| 触发 | 当前 NodeRun | 其他 queued NodeRun | WorkflowRun | root TaskRun |
|---|---|---|---|---|
| 当前节点 required outputs 有效 | completed | 继续/无 | 每个 declared node 都 completed 后才 completed/succeeded + Workflow result snapshot | 全 isolated：READY_FOR_ACCEPTANCE；invoking-session Direct（子计划 7 起）：既有 TurnLifecycle 结果 |
| result-driving required ReviewReport blocking | Reviewer completed | 按正常依赖继续 | 每个 declared node completed 后才 completed/needs_revision | READY_FOR_ACCEPTANCE + 引用 ReviewReport 并带 `workflow_result=needs_revision` 事实的结果快照 |
| model/Provider/preparation/output failure，且无 unknown side effect | failed | cancelled + 明确原因 | failed | FAILED + partial evidence |
| agent-generation capacity=0 或 deadline 在 Node admission 前过期 | 未启动节点 cancelled + budget/deadline reason | 全部 cancelled | failed | FAILED + partial evidence |
| Node 启动后、下一 generation request 前 capacity/deadline 耗尽 | active Node 在 Tool 安全 settle 后 failed + budget/deadline reason | 全部 cancelled | failed | FAILED + partial evidence |
| 冻结 Revision 或引用的 exact Version 在节点准入前被 revoke | 未启动节点 cancelled + policy_revoked | 全部 cancelled | cancelled/policy_revoked | CANCELLED + 撤销审计证据 |
| unresolved Tool outcome | blocked | 保持 queued | blocked | 保持 OPEN |
| cancel 后 active Tool 安全 settle | cancelled | 全部 cancelled | cancelled/user_cancelled | CANCELLED |
| cancel 后仍有 unknown Tool outcome | blocked | 保持 queued | blocked | 保持 OPEN |
| Recovery 解析带 `pending_terminal_intent=user_cancel` 的 blocked run | 按 durable leaf fact 闭合，否则 cancelled | 全部 cancelled | cancelled/user_cancelled | CANCELLED |
| abandon blocked run | blocked evidence 不改写 | 全部 cancelled | cancelled/abandoned | ABANDONED |

临时 Provider/MCP 在副作用前不可用属于普通 Node failure；只有既有 Recovery 判定的 unresolved/
unknown side effect 使用 blocked。

### 8.3 Retry

- Scheduler 默认不自动 retry；模型请求层继续使用现有 AgentLoop retry 所有权。
- Stage 7 不提供节点级 rerun。用户显式重跑创建新的 WorkflowRun；新 Run 的 NodeRun attempt 仍从
  1 开始，旧 Run/NodeRun 不可覆盖。若复用 failed root Task，用户必须先通过现有命令完成
  `FAILED -> OPEN`；`needs_revision` 的 root 处于 `READY_FOR_ACCEPTANCE`，用户接受或经现有
  `READY_FOR_ACCEPTANCE -> OPEN` resume 后再开新 Run。Workflow `resume` 只恢复同一个非终态 Run，不等于 rerun。attempt >1 留给
  后续真实需求；当前 Stage 8 方向也统一创建 child/new WorkflowRun，不向 terminal parent 追加 attempt。
- 已发生或可能发生写入/外部副作用时不透明重跑。
- Stage 7 一个 NodeRun 固定对应一个 AgentRun/Turn，不接 ordinary steering/follow-up queue；节点内部
  Agent 仍可在同一 Node Contract/ToolSet/预算内通过正常 model/tool rounds 调整下一步。外部控制只用
  cancel/recovery/abandon 或新 full Run；graph-level Replan 属于 Stage 8。ordinary Direct steering 不变。

### 8.4 Cancel

Stage 7 的 WorkflowRun 是前台执行：owning `workflow run` 进程/调用方持有 live cancellation handle，
Ctrl-C 或同进程 application cancellation 进入下列路径。Stage 7 不提供会跨进程声称成功的独立
`workflow cancel` 命令，也不新增 durable cancel watcher；远程/后台 cancel 随 Stage 9 background
execution 一并实现。

取消路径：

- 停止启动新节点。
- 取消当前 AgentRun/Tool。
- 保留已完成 Artifact 和副作用。
- active Tool 安全 settle 后，queued 节点才标记 cancelled，全 `isolated` root TaskRun 委托现有 cancel
  command；Direct 继续使用原 TurnLifecycle/TaskService 路径。
- 若 cancel 留下 outcome_unknown，则不伪装为 cancelled：Node/Workflow blocked、queued 保持、root
  OPEN，并在 WorkflowRun 持久化唯一的窄意图 `pending_terminal_intent=user_cancel`，等待现有 Recovery
  resolve 或显式 abandon；这不是通用 command queue 或 durable cancel watcher。

### 8.5 Resume

基于 Stage 4 恢复：

- completed 节点不重跑。
- queued 节点可重新派生 readiness 并调度。
- running 但崩溃的节点先执行恢复分类。
- outcome_unknown 的副作用只阻塞受影响 Workflow。`resume` 必须先由现有 RecoveryService 对账或
  解析 Tool outcome，不能自行把 blocked 清空。resume 还会复核冻结 Revision 与每个未准入节点的
  exact AgentDefinitionVersion 的 revocation 记录；被撤销版本以 `policy_revoked` 收口该 Run，不再
  准入新工作。普通 head disable 不影响已准入 Run 的恢复。
- 没有 pending terminal intent 的普通 crash-block 在 resolve 后才可继续 queued 节点；带
  `pending_terminal_intent=user_cancel` 的 run 在 resolve 后绝不 resume/准入 queued 节点，而是按 durable
  fact 闭合 active Node（否则 cancelled）、取消 queued，并以同一幂等事务把 Workflow/root 完成
  user-cancel。若仍 unknown，只能保持 blocked 或显式 abandon。
- recovery-only `abandon` 只接受 OCC-current durable blocked Run，不要求先 reconcile unknown outcome；
  它保留 blocked NodeRun、Artifact、审计、unknown evidence 和副作用，取消 queued 节点，以
  abandonment reason 将 WorkflowRun 关闭为 `cancelled(reason=abandoned)`，并委托现有 root TaskRun
  `ABANDONED` 命令，不把 unknown 改写成 safe。running/queued Run 与本进程仍持有 exact live handle 的
  情况拒绝并提示 owning foreground cancel；不能用缺 terminal row 或 PID absence 推断 liveness。

## 九、首批 AgentDefinition

内置定义应少而明确：

### 9.1 Direct Coder

- 完成普通 Code Agent 任务。
- 可读写项目、运行验证。
- 作为单 Agent 基线。

### 9.2 Explorer

- 只读。
- 定位代码、事实和风险。
- 输出 EvidenceBundle。

### 9.3 Planner

- 默认只读。
- 基于 Task + Evidence 生成 PlanArtifact。
- 不执行修改。

### 9.4 Coder

- 接收 Task/Plan/Evidence。
- 修改代码并输出 Patch/TestReport。

### 9.5 Reviewer

- 默认只读。
- 独立读取任务、Diff、测试和相关文件。
- 输出 ReviewReport。
- 不沿用 Coder ConversationLog。

### 9.6 Synthesizer

- 只读消费多个 Explorer Artifact。
- 输出一个有来源引用的 SynthesisReport。
- 不承担多 Writer/worktree 合并；Integrator 在出现真实隔离写用例前延期。

用户后续可以复制和编辑这些 Definition，但固定安全边界不随复制改变。

## 十、首批静态 Workflow Template

### 10.1 Direct

```text
Direct Coder
```

用于小任务，也是评估基线。

### 10.2 Explore–Implement–Verify

```text
Explorer → Coder → Reviewer
```

该模板把 Reviewer 的 exact ReviewReport slot 列入 Workflow 导出 required outputs；若它给出 blocking
verdict，本阶段按上面的 `completed + result_status=needs_revision` 结束图，root TaskRun 进入
`READY_FOR_ACCEPTANCE`，结果快照引用该报告并带 `workflow_result=needs_revision` 事实。用户需要时接受
或 resume 后显式创建新的完整 WorkflowRun；自动循环不在 Stage 7，completed-node partial rerun 也不进入
Stage 8 v1（Stage 8 只保留 failed retry 与 full rerun）。

### 10.3 Parallel Research

```text
Explorer A ─┐
Explorer B ─┼→ Synthesizer
Explorer C ─┘
```

全部只读，适合架构研究和方案比较。Stage 7 以串行方式执行该 fan-out/fan-in 图（Scheduler 按稳定
顺序每次准入一个节点）；各 Explorer 的 EvidenceBundle 声明为 `required_for_node_completion=true` 供
Synthesizer 绑定消费但不导出，只有聚合的 SynthesisReport 进入导出 required outputs，大 fan-out 不会
逼近 64-ref 编译上限。真正的并发 fan-out 执行是 Stage 8 条目。

### 10.4 Planned Refactor

```text
Explorer → Planner → Coder → Reviewer
```

第一版保持单 Writer，不立即并行多个 Coder。

## 十一、定义格式与管理入口

### 11.1 可读定义

AgentDefinitionSource 和 WorkflowDefinitionSource 使用 YAML 存放用户可编辑 desired state；完整
immutable AgentDefinitionVersion、DefinitionHead、WorkflowRevision、WorkflowHead 与 Run 进入
Operational Store。CLI/GUI Query 组合显示 desired/published 状态，但不会直接把 SQLite pointer 回写
YAML。workspace `agent-definitions.yaml`、`workflow-definitions.yaml` 与数据库记录都进入现有
current-format backup/verify/restore/doctor，包含未发布的 desired edit。

以下是单个 Definition body 的 v1 typed YAML 示例；文件级 revision/OCC wrapper 复用现有 typed
document adapter。`entry_nodes`、`terminal_nodes` 与 `declared_node_max_agent_generation_requests` 不由
用户填写，而由 Compiler 写入 immutable Revision：

```yaml
workflow_definition_id: user_explore_implement_verify
name: explore-implement-verify
description: Explore, implement, and review one bounded task.
origin: user
input_contract:
  kind: TaskContract
  version: 1
required_outputs:
  - node_id: reviewer
    slot: review_report
tags: [implementation]
default_budget:
  max_agent_generation_requests: 24
  default_node_max_agent_generation_requests: 8
  admission_timeout_seconds: 1800
  max_concurrency: 1
nodes:
  - node_id: explorer
    agent_definition_ref: builtin/explorer@1
    task_contract: Gather bounded evidence relevant to the task.
    input_bindings:
      - input_name: task
        accepts: {kind: TaskContract, version: 1}
        source_kind: workflow_input
        workflow_input: task
    output_contracts:
      - slot: evidence_bundle
        kind: EvidenceBundle
        version: 1
        required_for_node_completion: true
    access_mode: read
    conversation_scope: isolated
  - node_id: coder
    agent_definition_ref: user/coder@3
    task_contract: Implement the task using the bound evidence.
    input_bindings:
      - input_name: task
        accepts: {kind: TaskContract, version: 1}
        source_kind: workflow_input
        workflow_input: task
      - input_name: evidence
        accepts: {kind: EvidenceBundle, version: 1}
        source_kind: node_output
        node_id: explorer
        slot: evidence_bundle
    output_contracts:
      - slot: implementation_patch
        kind: ImplementationPatch
        version: 1
        required_for_node_completion: true
      - slot: test_report
        kind: TestReport
        version: 1
        required_for_node_completion: true
    access_mode: write
    conversation_scope: isolated
  - node_id: reviewer
    agent_definition_ref: builtin/reviewer@1
    task_contract: Review the implementation and validation evidence.
    input_bindings:
      - input_name: task
        accepts: {kind: TaskContract, version: 1}
        source_kind: workflow_input
        workflow_input: task
      - input_name: patch
        accepts: {kind: ImplementationPatch, version: 1}
        source_kind: node_output
        node_id: coder
        slot: implementation_patch
      - input_name: tests
        accepts: {kind: TestReport, version: 1}
        source_kind: node_output
        node_id: coder
        slot: test_report
    output_contracts:
      - slot: review_report
        kind: ReviewReport
        version: 1
        required_for_node_completion: true
    access_mode: read
    conversation_scope: isolated
edges:
  - from_node_id: explorer
    to_node_id: coder
  - from_node_id: coder
    to_node_id: reviewer
```

外部文件先解析为内部类型，不能直接执行任意表达式。

### 11.2 CLI

```text
morrow agent list
morrow agent show <definition-id> [--version <version-id>]
morrow agent create <definition-id>
morrow agent edit <definition-id>
morrow agent validate <definition-id>
morrow agent publish <definition-id> [--disabled]
morrow agent enable <definition-id> --expected-head-version <n>
morrow agent disable <definition-id> --expected-head-version <n>
morrow agent revoke <definition-id> --version <exact-version-id> --reason <text>
morrow workflow list
morrow workflow show <definition-id> [--revision <workflow-revision-id>]
morrow workflow create <definition-id>
morrow workflow edit <definition-id>
morrow workflow validate <definition-id>
morrow workflow publish <definition-id> [--disabled]
morrow workflow enable <definition-id> --expected-head-version <n>
morrow workflow disable <definition-id> --expected-head-version <n>
morrow workflow revoke <definition-id> --revision <workflow-revision-id> --reason <text>
morrow workflow run <definition-id> [--revision <workflow-revision-id> | --ensure-published] \
  --session <session-id> --root-task <task-run-id> --expected-task-version <n> \
  (--task <text> | --stdin) [--command-id <command-id>] \
  [--client-message-id <client-message-id>]
morrow workflow status <workflow-run-id>
morrow workflow node show <node-run-id>
morrow workflow resume <workflow-run-id>
morrow workflow abandon <workflow-run-id>
```

`validate` 只返回 pure Compiler diagnostics，且永远零写入：不创建 Version/Revision、不推进 Head、
不对内置定义 lazy publish，CI 与只读环境可安全反复调用。只有显式 `publish` 才发布 Version/Revision
并推进 Head。plain `run` 必须显式选择 exact 已发布 Revision；唯一例外 `--ensure-published` 与
`--revision` 互斥，会先发布当前 desired source、回显所选 Revision，并区分新建与 content-hash 复用；
缺省时对未发布定义直接失败并给出 publish
指引。`revoke` 只接受 exact immutable Version/Revision ID（拒绝 head selector），写入 additive、带
审计、单向的 revocation 记录；普通 disable 只门禁新准入，revoke 才是紧急制动。
`create/edit/publish` 的 desired-source 写入只面向 workspace `origin=user` definitions；对 packaged
`origin=builtin` 的 edit/publish 请求拒绝并提示使用新 ID 创建 user definition。builtin 仍可
list/show/validate/run，且其 Head 可 enable/disable；Stage 7 不提供 copy/fork 命令。
enable/disable 只 OCC 修改 Head gate，不生成新 Version/Revision。`run` 必须显式选择 Session/root
Task 和 exact Revision，并从 `--task`/`--stdin` 二选一生成 bounded TaskContract；CLI 不替用户创建、
abandon 或 resume Task。CLI 可生成并在 dispatch 前回显省略的 command ID，以及 Direct 所需的
client-message ID。`resume` 只继续同一个非终态 recovery run，不能当作失败 Run 的 rerun。再次
`run` 会创建新的 WorkflowRun；若显式复用 failed root Task，必须先用现有 root Task resume 使其回到
open。root 已有非终态 Workflow 时，ordinary Task/Turn mutation 不会暗中替换它；用户先用对应
前台 `workflow run` Ctrl-C/同进程 cancellation 或 recovery 收口。`abandon` 是 recovery-only 命令：
它只接受 OCC-current durable `blocked`/outcome-unknown；该状态按 Runtime invariant 只在当前 handler
handle 已释放/原进程已消失后出现，但不要求 unknown durable Tool fact 已 terminal/reconciled。running、
queued 或本进程仍持有 exact live handle 一律拒绝，并提示使用 owning foreground Ctrl-C/同进程
cancel；不通过 PID/缺 terminal row 猜 liveness。Stage 7 没有
standalone cross-process cancel command，也不允许用 abandon 冒充 remote cancel。

### 11.3 事件

Stage 7 必须先用 Workflow journal + Query projection 完整表达状态，CLI 可用轮询实现。现有
`ApplicationEvent` 是客户端 cursor contract，不是可以任意扩展的内部日志。下列只是候选最小事件
集合；只有在管理子计划中确认属于允许的 public lifecycle 变更并取得用户明确授权后才增加：

```text
workflow.compiled
workflow.started / completed / failed / cancelled
node.started / blocked / completed / failed / cancelled
artifact.bound
budget.updated
```

若未获授权，Stage 7 仍可通过 Query/CLI 完成；Stage 8 在构建 Event Stream 前把事件合同授权作为其
独立入口条件。`ready` 是派生投影，不需要为了它写 durable event。

## 十二、可观察性

每个 WorkflowRun 应回答：

- 使用哪个 Definition/Revision？
- 当前在哪个 Node？
- 哪些节点已完成、失败、阻塞或取消？
- 每个 Agent 使用哪个模型、Skill、工具和预算？
- 节点输入输出是什么 Artifact？
- 哪些文件由哪个 Node 修改？
- 哪个 Reviewer 发现了什么？
- 总成本与 Direct 基线相比如何？

Stage 7 先通过 CLI/只读投影实现，Stage 8 再可视化。

## 十三、评估策略

### 13.1 必须与 Direct 比较

对同一代表性任务做最小成对运行：

```text
Direct
vs
Explore–Implement–Verify
```

记录：

- 成功率和测试通过率。
- 用户返工。
- Reviewer 有效发现。
- Token/费用/时间。
- 重复探索和无效节点。

### 13.2 不以“节点都运行了”作为成功

Workflow 成功必须基于 TaskOutcome 和验证，而不是 DAG 全绿。

### 13.3 适合 Multi-Agent 的首批任务

- 大范围代码探索。
- 架构方案比较。
- 有明确独立 Review 价值的实现。
- 可并行的只读研究。

不适合：

- 单文件小改动。
- 简单解释。
- 一次命令即可确定的诊断。
- 无法定义节点产物的模糊任务。

## 十四、实施切片

生产计划按依赖拆为九个顺序子计划，分为四个阶段门禁：7A 合同（1–3）、7B 可靠串行执行（4–5）、
7C Multi-Agent 语义（6）、7D 产品化（7–8）；每次只激活一个：

### 子计划 1：Agent Definition Foundation

- 最小 AgentDefinitionSource（含 `tool_requirements` 声明模型）、immutable Version/Head store、
  additive revocation 记录、typed desired-state adapter 与 AgentFactory，Head operational enabled gate
  （只门禁新准入），并把 workspace source YAML 与数据库记录接入现有 backup/restore/doctor。
- 锁定 isolated standalone Session/TaskRun conversation scope factory contract，不增加日志 writer；
  `invoking_session` 留给子计划 7 的真实消费者；持久化用途字段留给子计划 2。
- 门禁：两个 Definition 产生准确且互不漂移的冻结 run evidence；`validate` 零写入；普通 Direct 不变。

### 子计划 2：Workflow Revision 与 Artifact Contract

- 最小 WorkflowDefinitionSource/Head、Workflow/Node/Run/attempt 领域、opaque Revision ID/hash、
  Head enabled gate、revocation 记录、root/internal leaf Task ownership、相对 admission duration →
  Run 绝对 deadline、稳定多输出 slot（`required_for_node_completion` 与导出清单分离）、
  TaskContract/TextResult、Artifact producer/binding 与 typed WorkflowRun TaskOutcome evidence marker。
- 加法持久化迁移以及现有 backup/doctor 引用覆盖，不接执行路径。
- 门禁：旧 Revision/Run 不漂移，持久化/迁移/恢复引用可验证。

### 子计划 3：确定性 WorkflowCompiler

- 只实现 DAG、必需合同、不可变引用、tool-requirement 合并优先级、capability intersection、
  access/capability 一致性、单弱连通分量（disconnected 即编译错误）和 Writer 串行 warning；pure
  Compiler 是唯一 normalize/validate/hash 路径，CompilationService 是唯一 Revision/Head publication
  路径；`validate` 纯只读，`publish` 显式写入。
- 门禁：每个 hard error 同时有最接近的合法接受案例；不做网络探活或通用 DSL。

### 子计划 4：isolated 单节点垂直闭环

- opt-in 单节点 isolated Revision → WorkflowRun → NodeRun → 现有 AgentLoop → Artifact/TaskOutcome，
  全部走在唯一统一的 WorkflowScheduler/transition/committer/finalizer 路径上；Scheduler-owned root
  原子终态与无 root Turn 的 evidence projection。
- 门禁：required output 在 root ready 前 durable publish/bind；取消/崩溃恢复不重跑 completed Node；
  revoked 版本按 `policy_revoked` 收口而普通 disable 不影响已准入 Run。

### 子计划 5：串行 DAG Scheduler

- 稳定依赖顺序、共享 agent-generation admission budget、固定 failure、cancel/resume/recovery/abandon，
  以及 isolated leaf → root Outcome evidence projection；与单节点切片同一套 Scheduler。
- 门禁：三节点图在节点/工具崩溃后可恢复且 root Outcome 不隐藏叶子副作用；不做自动 retry，无并发。

### 子计划 6：串行 Multi-Agent Artifact Pipeline

- 先独立验收 ChangeArtifactCapture（gate），再实现 `submit_node_result` 提交协议与
  Explorer → Coder → Reviewer，以及实际需要的 Evidence/Patch/Test/Review contracts；
  `needs_revision` 收口为 `READY_FOR_ACCEPTANCE` 而非 FAILED。
- 门禁：叶子 Session/上下文隔离、Reviewer 只读、Writer 实际 change evidence 在 durable tool close
  前捕获、结构化结果只来自 durable 校验提交、Review blocker 不自动回环。

### 子计划 7：Direct invoking-session Adapter

- 在已稳定的串行 Scheduler 上接入单节点 `invoking_session` 形状：Session 绑定策略、Direct Turn
  admission、TurnLifecycle root 收口委托与 ordinary-Direct parity 证据。
- 门禁：不产生第二套 runner/状态机；与普通 Direct 等价、无额外模型请求。

### 子计划 8：管理、模板与观察面

- Application Command/Query、CLI（validate/publish/revoke/run --ensure-published）与四个内置静态
  Workflow（Direct、Explore-Implement-Verify、串行 Parallel Research、Planned Refactor）；
  ApplicationEvent 只有获明确授权才增加。
- 门禁：接口只经 application service，旧 Revision 可观察，一个坏 Definition 不影响其他运行。

### 子计划 9：验收与收尾

- 完整离线工程验收（按 7A/7B/7C/7D 阶段门禁汇总）、最小 Direct 对照、文档与内置版本冻结；
  只读并行作为 Stage 8 进入项核验，不属于 Stage 7 交付。
- 门禁：Runtime 正确性与模板效果推广分开；无收益只禁止默认推广，不把 Runtime 判为不安全。

详细 ownership、任务、比例性决策和验证命令见 `.agent/subplans/1-*.md` 至 `9-*.md`。

## 十五、测试与故障注入

- 无效 DAG、循环、必需输出不可达；disconnected component 的编译拒绝案例（报错指名节点并给出
  control-edge/删除修法）；合法连通但未消费组件的 warning 正向案例，证明该 warning node 仍被
  调度且失败时按固定全图 failure 收口。
- 显式 input binding/Workflow 导出 output 引用 `required_for_node_completion=false` slot 的拒绝案例，
  unbound observation slot 的合法案例，以及绑定但不导出（fan-in）的正向案例。
- 跨节点 binding 缺少同向 edge 的拒绝案例，以及无 binding 的纯控制 edge 合法且确实排序的案例。
- 单节点 isolated TextResult 正向案例；invoking-session 单节点 Direct TextResult 与 result-driving
  ReviewReport 正向案例（子计划 7），以及含 invoking-session 的两节点图拒绝案例。
- `tool_requirements` 优先级矩阵：forbidden 覆盖 required 冲突、required 被 policy 拒绝、required
  在 catalog 缺失均为编译错误；optional 被移除并产生 diagnostic；节点叠加 Definition 集合外工具
  被拒绝。
- `validate` 零写入证明（含 CI 式反复调用后 Operational Store 无差异）；只有显式 `publish` 或
  `run --ensure-published` 产生 Revision/Head 写入。
- 结构化结果提交协议：schema 违例的 in-loop 修正、重复/冲突提交拒绝与重放 no-op、提交后崩溃由
  durable submission fact 完成 commit 而不重跑节点、必需结构化提交缺失时节点关闭为
  `output_contract_unsatisfied`；final message 从不被解析为结构化数据。
- result-driving ReviewReport blocking verdict：WorkflowRun `completed/needs_revision`，root
  `READY_FOR_ACCEPTANCE`，快照带 `workflow_result=needs_revision`，无任何消费者把它呈现为执行失败。
- Agent/Workflow Head disable 只阻止新准入；已准入 WorkflowRun 的未启动节点与恢复不受普通 disable
  影响。revoke exact Version/Revision 后：Start 拒绝、未启动节点准入拒绝、resume 以
  `policy_revoked` 收口，审计证据完整且撤销单向不可逆。
- Artifact schema 不兼容。
- exact `TaskContract@1` Workflow input 正向案例，以及其他 input kind/version 的拒绝案例。
- InputBinding accepted contract 不匹配、重复 input name、非法 Workflow input literal/source union 的
  拒绝案例，以及 exact match 正向案例。
- Agent/Skill/Provider 版本缺失。
- 同一 source 在 active model A 发布后切到 B，新 publish 产生冻结 B 的新 Revision、旧 Revision 仍
  冻结 A；随后相同 B candidate no-op，same-command replay 不受配置漂移影响。
- mutable AgentDefinition Head 移动不改变 source 中 exact Version ref；只有显式改 ref 才改变 candidate。
- Workflow name/description/tag 修改产生保存该 metadata 的新 Revision；重复相同 candidate 才 no-op。
- agent-generation-request/deadline 准入耗尽与 positive remainder 的 shrunken-cap 正向案例；Provider
  usage 缺失时保持 unavailable 且不误阻塞后续节点；覆盖 active Tool settle 后、下一 generation
  request 前到期，以及 Direct automatic compaction 不被误报为已计数。
- 无依赖 Writer 节点在同一 WorkflowRun 中稳定串行；外部写冲突仍走现有文件 revision/conflict。
- Reviewer/read 节点被错误授予 Host bash/write/opaque capability 时移除或失败，不能以 serial
  fallback 继续；冻结 native sandbox、无 promotion/外部 effect 的 read/test bash 有正向案例，合法
  Writer 仍可串行运行。
- Node 运行中取消和进程崩溃。
- cancel 后 Tool 安全 settle、outcome_unknown、以及带 user-cancel intent 的 blocked run resolve 后不再
  准入 queued 节点；对照普通 crash-block resolve 后可 resume。
- completed 节点恢复后被错误重跑。
- isolated Session 与 internal TaskRun 匹配、内部 Task 不可用户接受/触发 LearningReview。
- root Task 同时第二个 Workflow、运行中 ordinary Task replacement/Turn admission 被拒绝，以及
  Workflow Start 遇到既有 open Turn/nonterminal AgentRun 被拒绝、Start/ordinary Turn 并发只一方成功，
  Workflow terminal 后普通路径恢复。
- 下游读取上游完整 Session-owned ConversationLog 的隔离测试。
- MCP/Provider 在某节点临时不可用时只影响目标 Run，不污染编译器/应用启动。
- Definition 修改后旧 Run 快照保持不变。
- 两个 Definition source YAML 与 SQLite published state 的 backup/restore，包含
  desired-ahead-of-published，以及 malformed desired raw bytes 在有效 published head 旁可 backup/verify/
  restore、只由 validate/doctor 报告。
- final Assistant durable 后、Turn terminal 前 Artifact reserve/publish/bind（含 STAGING final bytes 缺失）
  的 fault injection，以及 mutation
  result 丢弃前实际 diff/结构清单 capture 的 replay。
- Definition、TaskContract 和输出中的 ordinary `password` 等叙述词通过；实际 secret 在输入侧局部
  拒绝、输出侧不落盘而降级为 redacted/incomplete；command-output ref 缺失时 TestReport 仍保留 exit/
  ValidationFact 并标记 incomplete；合法
  read/test-only sandbox bash 无 mutation 时不误报 capture missing，Workflow Coder Host bash 在 handler
  前被拒绝且不猜 whole-workspace diff。
- invoking-session Direct pre-Turn preparation failure 与 Direct/all-isolated Scheduler-owned
  root-Workflow terminal crash ordering 不释放半终态
  root，幂等 finalizer 可恢复且不重跑 Node。
- Direct success marker 在 intervening ordinary snapshot 后仍能把 Workflow refs 带入 acceptance，且
  ERROR/CANCEL 不生成虚假的 result snapshot。
- Multi Coder 已写后下游失败、completed node 后 safe cancel、blocked 后 abandon 时，root TaskOutcome
  从 exact leaf links 准确报告 change/test/side-effect/recovery/Artifact 证据。
- 每条新增拒绝规则都有一个最接近的合法正向案例。

日常门禁使用 focused deterministic tests；持久化、isolated 垂直闭环、串行恢复、Direct adapter 和最终
收尾再运行完整 offline suite。不使用 wall-clock sleep。Live Provider 与费用证据
需要单独授权，不是结构实现门禁。

## 十六、阶段交付物

- AgentDefinitionSource（含 `tool_requirements`）、immutable Version/Head、revocation 记录、Factory
  与 AgentRun 快照。
- 最小分层 Prompt/Context 组合与 Session-owned 叶子 conversation scope。
- WorkflowDefinitionSource/Head、Revision、Run、NodeRun。
- 类型化 Artifact Contract 与 `submit_node_result` 结构化提交协议。
- WorkflowCompiler（纯 validate + 显式 publish）与单一套串行 Scheduler。
- 首批内置 Agent 和 Workflow Templates（含串行 fan-in 的 Parallel Research）。
- CLI、Query 与运行观察；获单独授权时才包含 additive ApplicationEvent。
- 最小 Direct/Multi 成对离线评估；单独授权且凭据可用时补充真实 Provider 证据。

## 十七、完成标准

1. `AgentLoop` 保持单 Agent、领域无关的叶子执行器。
2. 用户能定义不同 Provider、Role Prompt、Skill、工具和预算的 AgentDefinition，工具必选性由声明
   模型显式表达。
3. 每个 AgentRun 冻结 Definition、Model、Skill、ToolSet、Preference 和 Policy 快照。
4. Workflow 使用不可变 Revision，Run 不受后续编辑漂移；普通 disable 不影响已准入 Run，紧急
   revoke 有独立审计路径。
5. Compiler 能在运行前阻止非法图（含 disconnected component）、合同不匹配和权限越界；多个 Writer
   由 Scheduler 稳定串行，而不是用过度保守的编译拒绝阻止合法图。
6. 节点通过类型化 Artifact 协作，不默认共享完整聊天历史；结构化结果只来自 durable 校验提交。
7. Direct 单节点 Workflow（子计划 7 adapter）保持现有单 Agent 能力与完成语义、不增加模型请求；
   编排开销如实测量，required output 在 root ready 前 durable，且不用未经定义的性能阈值阻塞合法运行。
8. Explore → Coder → Reviewer 能完成、取消、失败和恢复；blocking review 是
   `completed/needs_revision` 而非执行失败。
9. 串行节点受 agent-generation-request/request-admission-deadline 权威预算约束；静态图不重复
   计数 Node，token/cost 缺失如实显示。
10. 每个 Scheduler 管理的 WorkflowRun 同时只有一个运行节点；Reviewer 默认只读。
11. Workflow 可通过 CLI 完整观察和诊断。
12. 至少完成一种代表性任务的 Direct/Multi-Agent 成对评估并如实记录；只有观察到明确收益的
    模板才可成为对应任务类型的推荐或默认。无收益不阻止正确 Runtime 的工程验收。

## 十八、明确不包含

- 模型从零自由生成任意 DAG。
- GUI 拖拽编辑。
- 运行中任意修改已执行节点。
- 自动根据任务选择 Workflow。
- GraphPatch、Artifact 失效传播、pause/drain Replan、Replanner 和 nested dynamic graph。
- condition/loop DSL、approval/Converter node、通用 failure/retry policy。
- 后台/定时 Workflow Worker。
- 无限递归子 Agent 和自复制团队。
- 任何并发节点执行，包括只读并行 fan-out（Stage 8）、并行多个 Writer、Git worktree 编排和
  分布式锁。
- 通用 Policy/Schema/Plugin/Tracing 框架。
- A2A 或跨 Morrow 实例的 Agent 网络。

## 十九、进入 Stage 8 前必须确认

- 哪些任务特征和可选只读 Scout evidence 足以生成任务特化 Draft；模板只作为结构先验和
  Direct/失败回退，不作为最终图的唯一来源。
- 用户编辑 Workflow 的最小数据模型和 Revision 语义。
- Command/Query API 是否足以支持独立 GUI，以及 Stage 8 Event Stream 所需 ApplicationEvent 合同是否
  获得单独授权。
- Agent 模块编辑中哪些字段可实时修改，哪些需要新 Run。
- 受约束 GraphPlanner 只能引用哪些 Node/Agent/Artifact/Capability Catalog，以及编译失败的
  bounded repair 和 Direct fallback。
- 用户编辑与 Agent Replan 如何共用 future-only Patch、base Revision/CAS 和唯一协调者；Past/
  Active 节点继续不可篡改。
- 用户对 Preferences、Skills 和 Workflow 的统一可视化信息架构。
- Stage 8 编排能力的优先级顺序：child-run continuation（失败处续跑而不重执行已完成节点）优先，
  只读并行其次。只读并行的进入条件：串行 DAG 已经历真实 crash/restart 考验、ToolEffect 分类稳定、
  provider rate-limit ownership 已明确、按请求原子预算 claim 已实现、process/cwd/env 隔离经过
  压力测试、并行结果可见性屏障已验证。
