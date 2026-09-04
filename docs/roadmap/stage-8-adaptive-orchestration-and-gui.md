# Stage 8：自适应编排与 GUI 控制面

> 状态：进行中（Subplans 1–5 已完成；Subplan 6 尚未激活，2026-09-04）
> 阶段结果：Morrow 能根据任务选择并生成可验证的 Workflow Draft，用户可通过 GUI 观察、编辑和控制 Agent、偏好、Skill 与运行状态
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 7：Agent Definition 与静态 Workflow Runtime](stage-7-workflow-runtime.md)
> 下一阶段：[Stage 9：后台任务与可靠自动化](stage-9-background-automation.md)

> 2026-09-04 现行纠偏：模板只是可复制、可任意改节点/边/角色的建议，默认节点通信统一为
> `TextResult@1/result`；GraphPlanner 也必须生成普通可编辑 source，而非选择角色专用执行链。
> request cap 和 admission timeout 是可空的用户 guardrail，缺省不终止任务。所有 request/usage
> 仍持久化计量，用户 stop/Pause 是运行控制，不能用猜测预算替代 Harness 设计。

## 一、阶段目标

Stage 8 把 Stage 5–7 的能力组合成用户可直接掌控的个人 Agent 工作台。

完整交互：

```text
用户提交任务
→ Task Classifier/Orchestration Policy 判断复杂度
→ 受约束 GraphPlanner 基于任务和 Catalog 生成初版 Workflow Draft
→ WorkflowCompiler 预检
→ 按用户策略直接运行或等待编辑
→ GUI 实时显示 Task / Node / Agent / Tool / Artifact 状态
→ 用户可在运行前拖拽/新增/删除节点
→ 执行中由用户或 ReplanSignal 请求 future-only 修改
→ Pause/Drain 后只修改尚未开始节点
→ 形成新 Workflow Revision 和子 WorkflowRun 后继续
→ 记录用户编辑为 WorkflowFeedback
→ LearningReview 只提出 Orchestration Preference 候选
```

本阶段的重点不是“画一张漂亮流程图”，而是建立：

- 同一核心状态的可视化投影。
- 可解释、受 Catalog/GraphGrammar 约束的任务特化编排。
- 运行时可控编辑。
- 可配置 Agent 模块。
- Multi-Agent 相对单 Agent 的效果反馈闭环。
- 风险分级的运行自治：叶子内自我纠错始终自由；图级低风险 Patch 可按用户策略自动接受；
  越权或放宽用户显式 guardrail 的变更始终需用户批准（对齐 Codex approval policy、Claude Code permission mode、
  OpenCode per-tool allow/ask/deny 的包络内自主、越界升级原则）。

## 二、GUI 产品定位

GUI 是 Morrow Core 的客户端，不是另一个 Agent 实现。

```text
Morrow Core Process
├── Command API
├── Query API
├── Event Stream
├── Approval API
└── Artifact API
        ↑
        ├── CLI
        ├── Local Web GUI
        └── Future Desktop Shell
```

固定规则：

- GUI 不直接读写 SQLite、YAML、CredentialStore 或 Skill 文件。
- GUI 不 Import Runtime 内部对象作为业务接口。
- GUI 与 CLI 调用相同 Application Service。
- GUI 关闭不应终止 Core；是否保持前台任务由运行模式决定。
- UI 缓存是投影，不是权威状态。

## 三、进入条件

- **最高优先级运行时跟进**：先实现 child-run continuation，使晚失败后的显式重试可以复用已完成
  节点的不可变 Artifact，而不是像 Stage 7 full rerun 一样从 attempt 1 重跑整图；Stage 7 的两节点
  确定性证据显示失败 Run 4 次加新 Run 2 次、合计 6 次 primary admissions。
- Stage 7 已支持手写静态 Workflow、AgentDefinition、Artifact 和运行观察。
- Stage 7 的串行 DAG 已经历真实 crash/restart 考验，取消、恢复、blocked/unknown outcome 语义稳定。
  Pause/Drain 与运行中 future-only
  编辑是 Stage 8 自己的首要运行控制前置，不假定 Stage 7 已实现。
- Stage 7 只交付串行执行；只读并行是本阶段的独立运行时切片（8H），其开工另有前置：ToolEffect
  分类稳定、provider rate-limit ownership 已明确、按请求原子计量与可选 cap claim 已实现、process/cwd/env 隔离
  经过压力测试、并行结果可见性屏障已验证。
- Command/Query/Approval 接口能够表达完整运行状态；若 Stage 7 未获授权扩展
  ApplicationEvent，则 Stage 8A 在建立 Event Stream 前先取得该 public contract 授权。
- Stage 5 的候选、Active Preference 和 Knowledge 可被查询和编辑。
- Stage 6 的 Skill 生命周期、来源、权限和版本可被查询。
- Stage 7 已有最小离线 Direct/Multi 成对证据。缺少真实 Provider 对照不阻止 GUI、手工 Draft
  编辑或 suggestion-only GraphPlanner；它只阻止自动执行/自动 Replan 的产品推广。

## 四、自适应编排策略

### 4.1 受约束的任务特化 Draft

第一版不允许模型凭空输出任意 DAG，也不把模板当作唯一完整图。模板是结构先验、可编辑脚手架和
失败回退；GraphPlanner 必须针对当前任务选择实际节点、依赖、合同与 Artifact 绑定：

```text
任务特征提取
→ 可选一次只读 Scout 产出 TaskBrief
→ 读取冻结 Node/Agent/Artifact/Capability Catalog
→ 选择模板先验或最小 GraphGrammar 组合
→ 受约束 GraphPlanner 生成 TaskGraphDraft
→ Compiler 校验
→ 用户编辑/批准，或按已获推广的策略运行
```

首批起点建议沿用 Stage 7 纠偏后的最小集合：Direct 与 Explore–Implement–Verify。后者只示范
三节点通用 result 链，用户和 GraphPlanner 都可以从空图开始，或克隆后任意新增、替换、删除角色、
节点与边；Parallel Research、Planned Refactor 等形状应当由同一个普通图模型组合出来，而不是成为
拥有专用转接协议的新模板。

`NodeCatalog + ArtifactCatalog + CapabilityCatalog` 是现有 AgentDefinition、Artifact contract 和
Capability authority 的有界只读投影，不是第二套 Registry。`GraphGrammar` 只描述当前 Runtime 真正
支持的 Agent 节点、依赖、绑定和并行规则，初版直接复用 Compiler schema，不单独持久化或发展成
通用 DSL。若没有足够信息生成有针对性的合法 Draft，则回退 Direct 或要求用户补充，而不是硬套
一个模板。

### 4.2 Task Feature

使用结构化特征，而不是只让模型返回模板名字：

```text
TaskFeatures
- task_type
- expected_scope
- number_of_areas
- requires_code_write
- requires_research
- review_value
- parallelizable_read_work
- ambiguity
- risk_level
- expected_duration_class
- user_requested_roles[]
- workspace_constraints[]
```

这些特征可以由本地规则、项目元数据和一次受限模型分类共同生成。

### 4.3 Direct 优先规则

满足以下情况默认 Direct：

- 单文件或范围明确的小修改。
- 简单解释、命令或诊断。
- 不需要独立证据收集。
- Reviewer 价值低于额外成本。
- 用户偏好不启用多 Agent。
- 用户显式设置的费用或请求 guardrail 不适合当前候选图。

只有复杂度、风险或并行收益达到阈值时，才选择 Multi-Agent。

### 4.4 受控参数化

自动填充：

- AgentDefinition 引用。
- Provider/Model。
- Skill。
- Tool/Capability Policy。
- Node Budget。
- 输入输出绑定。
- 并发设置。

填充不能：

- 创建未注册的权限。
- 引用未启用 Skill。
- 绕过 Compiler。
- 自动选择未授权 Provider 或产生不可预期费用。
- 修改固定 System Boundary。

### 4.5 编译失败回退

```text
Draft compile failed
→ 尝试一次确定性修复或受限重新生成
→ 仍失败则回退 Direct 或等待用户
→ 显示具体错误
```

不允许静默执行未经编译的图。

### 4.6 推广与可运行性分离

- 手工编辑、Compiler、future-only Patch 和 suggestion-only Draft 属于工程能力，可用确定性离线
  证据验收。
- 自动选择后直接运行、以及 task class 级默认自动化（免批准的自动 Draft 运行或自动接受
  Replan）属于产品推广，必须有对应任务类型的 Direct/Multi 对照收益和用户策略授权。
- Replan 的自动接受按 Patch 风险分级，与 task class 推广是两个独立门槛：不扩大权限、不放宽
  用户显式 cap/deadline、不引入新角色、仅修改 Future 节点的低风险结构修复 Patch，可在用户策略
  （`auto_replan_mode`）允许时自动应用并事后可审计；任何越权或扩容 Patch 无论证据如何都
  必须用户明确批准。
- 证据不足时系统仍正常运行，只把 Draft/Patch 交给用户批准；不得以“为了安全”禁用 Direct、手工
  Workflow 或 GUI。

## 五、Orchestration Policy

### 5.1 定义

```text
OrchestrationPolicy
- policy_id
- scope
- task_matcher
- preferred_template
- excluded_templates[]
- required_roles[]
- model_preferences_by_role
- budget_limits（可空；只来自用户显式配置）
- review_requirement
- parallelism_limit
- auto_run_mode
- auto_replan_mode（approval_only | allow_low_risk，默认 approval_only）
- source / evidence / status / revision
```

### 5.2 来源

- 用户明确设置。
- 系统内置安全默认。
- Stage 5/8 产生的 proposed 候选。
- 工作空间模板覆盖。

### 5.3 学习边界

GUI 中反复删除 Planner、替换模型或增加 Reviewer，可以形成 `WorkflowFeedback`。系统只提出候选：

> “在该工作空间的小型实现任务中默认跳过 Planner？”

用户接受后才写入 OrchestrationPolicy。

## 六、Workflow Draft 与运行 Revision

### 6.1 Draft

自动生成或用户编辑的图先处于 Draft：

```text
draft
→ validating
→ valid
→ rejected / invalid
→ frozen revision
→ run
```

### 6.2 运行前编辑

用户可以：

- 增加、删除或替换 Pending Node。
- 修改 AgentDefinition 引用。
- 为节点选择模型和 Skill。
- 调整预算、超时和并发。
- 修改 Node Task Contract。
- 修改输入输出绑定。

每次编辑后用 pure Compiler validate 当前 Draft；只有用户 freeze/run 时才通过唯一 publication
service 生成 immutable Revision，避免拖拽过程制造大量无意义 Revision。

### 6.3 运行中编辑

固定语义：

- 已完成 Node 不可修改。
- 正在运行 Node 不可修改。Pause 请求进入 drain：不再启动新节点，当前节点完成、失败或取消后
  成为不可变历史；不能通过“先取消”把它伪装成可编辑 Pending。
- Past 的判定是“曾 admission/start，或已有 AgentRun/effect/evidence”，不是“数据库已有预创建
  attempt-1 row”。因此 Active、completed/failed/blocked 以及 admission 后 cancelled 的节点不可修改；
  queued 或明确 cancelled-before-admission/supersession 的旧 row 本身仍不可改写，但对应 semantic node
  仍是 Future，可在 child 中以新 attempt-1 row 编辑/执行。
- 只有 Future Node 可修改。它的目标边/输入 binding 也可修改：允许 exact immutable Past
  `node_id.slot`/Artifact → Future 的只读依赖，或 Future → Future；Past node/output、Past 内部边、任何
  指向 Past 的新边、Future → Past 与既有 Artifact provenance 都不可修改。
- 编辑产生新 `WorkflowRevision`。
- 新 Revision 保留旧 Revision parent 和 FutureGraphPatch diff。
- 已完成 Artifact 可被新 Revision 引用，但不能伪造为新节点产物。
- Patch 可显式替换 Workflow `required_outputs`，但每个 ref 必须指向 exact immutable Past slot 或新
  Revision 的 Future slot并通过 Compiler；这不修改 Past Artifact/provenance。
- 修改只影响尚未启动节点。

Pause 不是 process-local flag。Stage 8 增加 durable nonterminal `draining`/`paused` Workflow 状态，以及
唯一正交布尔事实 `pause_requested`；它只保存 drain 意图，不扩展成通用 command queue。Pause 以 OCC
把 `running,pause_requested=false -> draining,true`。每次 Node admission 在同一 authoritative store
transaction 同时重检 parent `status=running` 且 `pause_requested=false`，因此 Pause 与 queued→running
竞态只能有一个赢家。draining 不再准入新节点；active 安全完成且仍无终态映射时转为 paused。等待未
consume 的 Approval 时 Node 保持 running + approval-pending 投影、Workflow 保持 draining；它不是
blocked。若 in-flight Tool 的结果变成 unknown，Workflow 可按既有恢复语义进入 blocked，但必须保留
`pause_requested=true`。Recovery resolve 为安全成功且 Workflow/root 仍 nonterminal 时，根据剩余 Active
node 回到 draining 或 paused，绝不准入 queued node；resolve 为 failed/cancelled 则按既有 terminal
mapping 收口。Resume 只对无 child/patch handoff 的 paused Run 原子清除 `pause_requested` 并改回
running。若 ordinary crash/unknown Run 已先进入 exact blocked 且
`pending_terminal_intent=null`，Pause 也可用 OCC 只把 `pause_requested=false -> true`，status 与 unknown
evidence 不变；其 resolve-success 直接走上述 draining/paused 分支，不经过可准入 queued node 的 running
窗口。带 `pending_terminal_intent=user_cancel` 的 blocked Run 拒绝 Pause，cancel intent 优先，resolve 仍
必须按 Stage 7 terminal cancel 收口。Core restart 继续遵守该事实，
paused/draining/blocked-with-pause 期间 root ownership 不释放。

Stage 7 的“一个 WorkflowRun 永远引用一个冻结 Revision”继续成立。Stage 8 固定新增 terminal
`WorkflowRun.status=superseded(reason=continued_by_patch)`；不是待定的 `continued` 非终态。handoff 对
exact paused parent row version、无 Active node 和无 unknown outcome 做 CAS，且确认原 Workflow/root 仍
nonterminal 后，才可在同一个
Operational Store transaction 中把原 Run 及其尚未启动 NodeRuns 关闭为
superseded/cancelled-by-supersession、创建引用新 Revision 的 child WorkflowRun，并把同一 root 的唯一
nonterminal ownership 原子交给 child。不存在 ordinary Turn/Task 可插入的“旧 Run 已释放、child 尚未拥有”
窗口。非空 execution set 的 handoff child 在该事务中明确创建为
`status=running,pause_requested=false`，随后才可准入其 queued rows；pause intent 是 run-local，不从
paused parent 自动继承。禁止在同一个 WorkflowRun 上原地替换 `workflow_revision_id`。

若 drain 中 Active node failed 或被 cancelled，Stage 7 已终态化 Workflow/root，不能再改写成
superseded；Patch 仍可保存，但继续执行必须走相应显式 root resume（仅现有合法 transition）与
child/new Run，或新建 Task。Reviewer blocking verdict 只完成 Reviewer node：若它是 current Revision
required outputs 指定的 result-driving report，且同时使每个 declared node completed，Stage 7 才终态化
为 `completed/needs_revision`；非 result-driving block 只保留 evidence，整图仍可 succeeded。若仍有
queued execution-required Future，drain 到 paused，保留该不可变 ReviewReport，并允许用户对 Future
patch 或 resume，不能提前跳过它们。
只有 child 当前 Revision `required_outputs` 中 exact ReviewReport slots 驱动最终结果：修复后 Replan 可
显式改指 Future 新 Reviewer；旧 blocking report 仍是 inherited evidence，但不永久污染新 child。多个
result-driving reports 仍按 Stage 7 any-blocking 规则。
unknown outcome 继续 blocked。

child 显式记录 `parent_run_id`、`execution_node_ids` 与 inherited Artifact bindings。对
`run_relation=continuation`，该集合是新 Revision 中所有未映射为 immutable inherited Past 的 retained
execution-required nodes；显式从新 Revision 删除才可不执行。它自然包含 unchanged pending
ancestors/siblings/disconnected or independent branches，并经 Compiler 补齐合同/依赖闭包；不是当前 ready
frontier，也不是简单 descendants 或“只够 required outputs”的子集。Compiler 证明每个 child input 要么绑定
exact inherited Past Artifact/root input，要么来自集合内 producer，并在创建事务中为整个集合全量预创建
attempt-1 NodeRun。Past/Active/blocked attempt 不在 continuation child 中重新物化或调度，只通过不可变
parent NodeRun/Artifact provenance 出现。若原 Run仍有 blocked/outcome-unknown Tool，Patch
可以保存和编译，但 handoff/child Start 必须拒绝；Recovery resolve/reconcile 后只有 durable result 使
old Workflow/root 仍 nonterminal 时才能重新 drain 并原子 handoff，若解析为 failed/cancelled 则走
terminal-parent child/new-Run 路径。选择 abandon 则按 Stage 7 关闭旧 root/lineage，不能在同一 root 上偷偷继续；用户若仍要运行
该 Patch，需显式创建新的 user Task/Workflow lineage。

`execution_node_ids` 可以为空，例如用户显式删除全部 Future，或全部 retained nodes 都已成为 inherited
Past。Compiler 只有在 exact inherited Artifacts 已满足新 Revision 的全部 required contracts 时才接受；
handoff transaction 此时不创建 queued NodeRun，而是创建并立即终态化 continuation child，通过 Stage 7
同一个 fixed result owner、只按新 Revision required outputs 中 exact inherited result-driving
ReviewReport（若用户显式声明）收口：`succeeded` 写 READY transition + marked Workflow result snapshot；`needs_revision`
写 FAILED transition + 引用 required blocking reports 的 terminal TaskOutcome，不伪造 success snapshot。
两条路径都在同一 transaction terminalize old parent/child/root。若合同未满足则 Patch compile 失败。绝不留下
`running` 的空 child，也不增加单独 finalize command。

WorkflowRun 记录 `run_relation: initial | continuation | rerun` 与
`lineage_budget_root_run_id`；该 root 始终是 request accounting 边界，不代表必有有限预算。初始/new
Run（`initial`）与显式 `rerun` 都把自己设为新的 accounting root；`continuation` 继承 parent root。
累计 `agent_generation_request_count` 只从该 root 所属 continuation chain 的 durable `purpose=agent`
rows 推导，遍历不得越过最近的 rerun/new-root 边界。仅当 Revision 有显式 aggregate cap 时，child 才
获得 cap 减去该 lineage 已消费量后的剩余额度；仅当 parent 有 deadline 时才继承原
`admission_deadline_at`。FutureGraphPatch 若提高/移除显式 cap 或延长/移除显式期限，必须列为用户
exact edit 或经用户明确批准的 proposal，并与 parent 事实一起做 OCC；Agent signal/模板不能静默
放宽。显式剩余额度非正或 inherited deadline 已过期时 Patch 仍可保存，但 continuation
child 的非空 execution set 不启动；empty child 不做 Node/model admission，仍可按上段用 inherited
contracts 原子终态闭合。只有 terminal parent 后用户显式触发的 `rerun`/new WorkflowRun（并先完成现有合法 root
transition）才把自己设为新 `lineage_budget_root_run_id` 并采用 Revision 的可选 guardrail，且 UI/CLI
明示这是新 accounting root；它后续的 continuation 只累计这个 root 之内的消费。

### 6.4 删除节点

删除节点前检查：

- 是否有下游依赖。
- 是否移除最终必需 Artifact。
- 是否破坏审批/Review 门禁。
- 是否造成无终止路径。

Compiler 返回可操作错误，GUI 不自行猜测重连。

### 6.5 Global future-only Replan

用户精确编辑不经过模型重新解释；Agent/Orchestrator 只能提出建议。两者最终共用同一个确定性
Patch application/publication 路径：

```text
User exact edit
→ explicit FutureGraphPatch ───────────────────────────┐
                                                       ├→ PatchApplicationService
Node Agent / Orchestrator                              │  → pure WorkflowCompiler + OCC/CAS publish
→ ReplanSignal / ReplanProposal                        │  → new WorkflowRevision
→ ReplanCoordinator proposes FutureGraphPatch          │  → continuation child WorkflowRun
→ user approves ───────────────────────────────────────┘
```

固定规则：

- Past（曾 admission/start 或已有 AgentRun/effect/evidence）和 Active 节点不可篡改；预创建但从未
  admission 的 queued/cancelled rows 不会把 semantic node 变成 Past。Patch 只能修改 Future node，以及
  target 为 Future 的 binding/edge；其 source 可是 exact immutable Past output 或 Future producer，不能
  修改 Past producer/provenance、给 Past 增加 incoming work 或建立 Future → Past。
- ReplanCoordinator 是唯一自动/Agent Patch proposer，不是 Revision writer，也不要求实现成另一个
  LLM Agent。Node Agent 只能发出 ReplanSignal，不能直接改 Revision。运行中的叶子 Agent 不为
  全局 Replan 挂起自己，也不能通过取消伪装回 Pending：ReplanSignal 是随节点自身收口（typed
  submission / 终态边界）落盘的 durable evidence，Scheduler/Orchestrator 在该节点 settle 后才
  消费它并请求 Pause/Drain；handoff（无论用户批准还是低风险自动应用）只在完全 paused 且无
  Active 节点的窗口执行。
- `PatchApplicationService` 是手工和建议 Patch 的唯一 deterministic apply 入口，并复用 Stage 7 的
  pure Compiler + publication service。Patch 必须引用 base Revision 并经过 OCC/CAS、权限/合同/预算
  校验；stale patch 返回冲突供重新生成或用户处理，不静默重放。运行中 Patch（含自动 Replan）产生
  的是 **run-local detached Revision**：不可变、记录 parent 谱系、仅供该 continuation child 引用，
  不移动 WorkflowDefinition head、不进入模板列表；只有显式的“保存/发布为定义”用户命令才更新
  desired source 和 head。
- 新 child Run 显式引用 parent run、复用的历史 Artifact 和新 Revision；旧 Run/Revision/NodeRun
  永不原地修改。
- handoff 是 old Run terminal + future NodeRun supersession + child/root ownership creation 的单事务；
  continuation child 为 Compiler 闭包后的完整 `execution_node_ids` 全量预创建 attempt-1 rows，不按
  Stage 7 full-Start 重建 Past 节点，也不把集合简化为 immediate frontier/descendants。
- blocked/outcome-unknown parent 只能产出 Patch proposal，不能启动 child；先 Recovery resolve/reconcile，
  或 abandon 并结束该 root lineage。
- 图级 Replan 的默认自治级别对齐主流 harness 的风险分级（Codex approval policy、Claude Code
  permission mode、OpenCode per-tool allow/ask/deny 的“包络内自主、越界升级”原则）。低风险
  Patch——不创建未注册权限、不提高 cap/deadline、不新增/替换角色、不更换为未授权
  Provider/Model/Skill、仅修改 Future node 及其 target binding/edge——在
  `auto_replan_mode=allow_low_risk` 时可由系统直接应用，并在 UI/CLI 事后可见可审计；默认
  `approval_only` 下等待用户批准。任何越权或扩容 Patch 无论策略与证据如何都必须用户明确批准。
  task class 级的默认自动化推广仍按 §4.6 需要对照收益证据；证据不足时回落建议/批准模式，且不
  阻塞手工能力与工程验收。
- Node 内部 Agent 仍可在固定 Node Contract、ToolSet 和用户 guardrail 内调整下一次模型/工具动作，这属于
  leaf-local replanning；第一版不让叶子创建嵌套 DAG，也不绕过 ReplanCoordinator 修改全局图。

## 七、Agent 模块编辑

### 7.1 可配置字段

用户可通过 GUI 配置：

- 名称与描述。
- Role Prompt。
- Provider/Model。
- Skills。
- Tool/Capability Policy。
- ContextPolicy。
- 输入输出合同。
- 可选的时间、Token、调用和重试 guardrail。
- 是否只读/Writer。

### 7.2 不可配置为绕过项

- 固定安全边界。
- Credential 原文。
- 路径越界能力。
- 隐藏 ToolResult 或审计。
- Provider reasoning 记录。
- 隐藏 request/usage 计量。
- 绕过审批的高风险工具。

### 7.3 Definition 与 Node Override

- AgentDefinition 保存可复用默认。
- Node 可以做受限 Override。
- Override 不能超过 Definition/Task Policy 权限。
- Run 显示最终解析结果及来源。

### 7.4 复制与模板

用户可以复制内置 Agent 形成自定义版本，但：

- 新 ID/Version。
- 记录 parent/source。
- 内置更新不静默覆盖用户副本。
- 可以查看与 parent 的 Diff。

## 八、GUI 信息架构

### 8.1 主界面

建议布局：

```text
┌────────────────────────────────────────────────────────────┐
│ Active Context Bar：语言 · 详细度 · Workspace · 待确认学习 │
├───────────────┬──────────────────────────┬─────────────────┤
│ Session/Task  │ Chat / Task / Artifacts  │ Workflow Panel  │
│ Navigation    │ Main Workspace           │ Node Inspector  │
├───────────────┴──────────────────────────┴─────────────────┤
│ Tool / Approval / Run Status / Budget                      │
└────────────────────────────────────────────────────────────┘
```

### 8.2 Active Context Bar

顶部不永久展开全部偏好，只展示紧凑摘要：

```text
中文 · 详细回答 · Workspace: morrow-agent · 5 条项目约定 · 2 条待确认学习
```

点击后打开 Context Drawer：

- Resolved Preferences。
- Profile。
- 本次选中的 Knowledge。
- 来源和 Scope。
- 添加、编辑、停用、删除。
- 待确认 Learning Candidates。

### 8.3 Workflow Panel

右侧显示：

- 当前 Workflow 名称/Revision。
- 节点图和运行状态。
- Ready/Running/Blocked/Completed/Failed。
- 当前 Agent、Provider/Model、Skills、工具和预算。
- 输入输出 Artifact。
- 节点日志和错误。
- Pause/Cancel/Rerun/Edit Pending。

简单 Direct 任务可显示线性单节点卡片，不强制占用大型画布。

### 8.4 Main Workspace

中心支持：

- Chat/Task 交互。
- Plan、Evidence、Patch、Diff、Test、Review Artifact 查看。
- 文件变更列表。
- Reviewer findings。
- 最终 TaskOutcome。

### 8.5 Approval Surface

审批必须显示：

- 哪个 Task/Workflow/Node/Agent 请求。
- 操作类型与受影响对象。
- 风险等级。
- 脱敏预览。
- 单次允许、拒绝或可选的受限会话策略。

不能只显示“Agent 想运行一个工具”。

## 九、Preference 与 Learning 管理界面

### 9.1 三种视图

- Active：当前生效。
- Proposed：等待确认。
- History：被拒绝、替代、停用和删除记录。

### 9.2 编辑体验

用户修改 Preference 时展示：

- Scope。
- 当前值。
- 来源和 Evidence。
- 受影响的 Resolved Context。
- 是否 supersede 旧记录。

### 9.3 实时显示的含义

“实时显示当前用户偏好”应展示当前 Task/Agent 实际解析后的 `ResolvedPreferences`，而不是数据库全部记录。这样用户能理解为什么某条偏好在本任务生效或被更高 Scope 覆盖。

## 十、Skill 管理界面

显示：

- 来源、版本、Scope、状态。
- SKILL.md 摘要。
- requested tools/capabilities。
- scripts 和信任等级。
- 测试/eval 结果。
- Draft Diff。
- Enable/Disable/Pin/Update/Rollback。

自动生成 Skill 必须以 Draft 卡片出现，不能混入 Active Skill 列表造成已启用错觉。

## 十一、前后端通信

### 11.1 初期形态

建议先实现本地 Web GUI：

```text
Python Morrow Core
↕ local authenticated RPC / HTTP / WebSocket（激活时选型）
React + TypeScript Client
```

Core Server 是前台长运行进程，不是 daemon（后台守护在 Stage 9）：`morrow serve` 启动
headless API 并打印 loopback 地址与一次性会话 token，`morrow gui` 在此基础上自动打开浏览器；
两者都在 SIGINT 时优雅退出。GUI 静态资源以预构建产物嵌入 Python 包分发，终端用户无需安装
Node.js；桌面打包留到 Stage 10，避免当前被安装器和跨平台 sidecar 问题拖慢。

### 11.2 API 要求

- 版本化。
- 本地认证/随机会话 token。
- 默认只监听 loopback。
- CSRF/WebSocket origin 防护。
- 断线重连和事件 sequence 补拉。
- 幂等 Command ID。
- 不把 Credential/完整敏感 Tool 参数传给 UI。
- Query 与 Command 分离。

### 11.3 事件投影

GUI 使用：

```text
initial query snapshot（同事务捕获 max cursor）
+ ordered event stream（durable /events?after= 拉取）
+ WebSocket latest_cursor 通知（仅提示，不承载事实）
+ gap detection
+ resync query
```

不能假设前端永不掉线，也不能把 WebSocket 事件本身当永久权威。

### 11.4 GUI 技术方向

推荐：

- React + TypeScript。
- 节点图使用成熟的 node-based UI library，例如 React Flow 类方案。
- 状态通过 API 类型生成或共享 schema。
- 不在前端复制 WorkflowCompiler。

具体依赖在 Stage 激活时评审，新增依赖需遵循项目规则。

## 十二、自动编排解释

生成 Draft 时，系统必须提供简短解释：

```text
起点建议：Explore–Implement–Verify（已克隆为普通可编辑图）
任务特化：Explorer 聚焦 API/持久化；Coder 修改两个目标模块；Reviewer 校验恢复语义
自定义：按任务需要可插入 Web Developer；本次没有独立研究分支
预计节点：3
显式 guardrail：未设置（仍显示实时 request/usage）
写入节点：Coder（唯一）
```

解释来自结构化特征和策略，不展示模型隐藏推理。

## 十三、运行控制

GUI/CLI 同等支持：

- Start。
- Pause。
- Resume。
- Cancel。
- Resolve Approval。
- Retry failed Node：parent WorkflowRun/NodeRun 保持 terminal immutable；用户先显式把 FAILED root
  `resume` 到 OPEN，再创建 `run_relation=rerun`、引用同一 Revision 的 child/new WorkflowRun。
  `execution_node_ids` 包含所选失败节点，以及目标 Revision 中所有未由 immutable Past 满足的 retained
  execution-required nodes（包括 cancelled-before-admission、disconnected/independent branches），再由
  Compiler 验证合同/依赖闭包；继承仍有效的上游 Artifact。若用户确实不想执行某个 Future node，必须
  显式 Patch 删除它并生成新 Revision，不能用 descendants/required-output 子集静默省略。
- Full rerun：创建 `run_relation=rerun` 的 new WorkflowRun，可引用原 Revision 或用户已发布的新
  Revision；它把所选 Revision 的全部 declared nodes 作为新 execution set，不继承 prior node-output
  Artifacts，并建立新 budget root。Stage 8 v1 不提供 completed-node partial rerun，因为那需要传递
  consumer Artifact invalidation/provenance replacement；要重做 completed node 必须 full rerun，不能混用
  新 producer 与旧 downstream evidence。
- Edit pending graph。
- Accept/Correct TaskOutcome。

Stage 8 是前台或 Core 进程存活期间的运行控制；真正独立后台守护在 Stage 9。

## 十四、Multi-Agent 成本与反馈

### 14.1 运行前

展示：

- 节点数量。
- 模型选择。
- 用户显式设置的最大费用/请求 guardrail（未设置时明确显示“无上限”）。
- 预计并行度。
- 哪些节点写入。

### 14.2 运行中

展示：

- 已使用 request/usage，以及存在显式 guardrail 时的剩余额度。
- 节点耗用。
- 重试和失败。
- Tool 调用和 Approval。

### 14.3 运行后

比较：

- TaskOutcome。
- 验证结果。
- Reviewer findings。
- 用户是否修改 Workflow。
- Direct 基线估计或成对评估结果。

用户可以反馈：

```text
太复杂
缺少探索
Reviewer 有价值/无价值
模型太贵
以后此类任务使用/不要使用该模板
```

反馈进入 Candidate，不直接改路由。

## 十五、实施切片

### 8A：Core API 与只读运行观察器

交付：

- 稳定 Command/Query/Event/Approval 协议。
- Local Core server。
- Session/Task/Workflow/Node/Artifact 只读 GUI。
- 断线重连和 event gap 恢复。

门禁：GUI 与 CLI 展示同一个 WorkflowRun 状态，重连后无丢失或重复状态。

实施顺序（2026-09 计划决策）：先交付 8C 的运行时内核——Pause/Drain、durable
`pause_requested`、PatchApplicationService、superseded handoff、continuation child 与 lineage
budget，全部不经 GUI、用确定性离线证据验收；这是第三节“最高优先级运行时跟进”的落地方式。随后
再按 `8A → 8B → 8C(GUI) → 8D → 8E` 走核心链路，其中 8C(GUI) 只剩运行控制与父子 Run 展示等
界面工作。完整 Context/Learning/Skill 管理不是任务特化 DAG、编辑或 Replan 的技术前置，放到
8F；8B/8D 只实现 Editor/Planner 当下需要的只读 Catalog 与选择器。

### 8B：Workflow Editor 与 Agent Module Inspector

交付：

- 节点图、边、Inspector。
- AgentDefinition 编辑。
- Editor 所需的 Provider/Model/Skill/Tool/Artifact 只读选择器与 Budget 配置（Catalog Query API
  由 8A 的 Local Core server 一并提供，编辑器不承担后端扩展）。
- 编译错误可视化（含结构化 node/edge 定位）。
- Definition/Revision Diff。

门禁：用户可创建一个合法 Workflow，非法图无法运行。

### 8C：Pause/Drain 与手工 FutureGraphPatch

交付：

- Pause/Drain/Resume。
- durable `pause_requested` admission guard，以及 pause → unknown/blocked → resolve 后仍不准入 queued
  节点的恢复语义。
- 最小 Operational Store migration：Stage 7 存量 Run 回填 `pause_requested=false`、
  `run_relation=initial`、`lineage_budget_root_run_id=self`；验证升级中的 running/blocked Run 仍可按
  原恢复路径继续，不以 NULL 误阻塞 admission 或重置预算。
- 仅 Future Node 的手工修改。
- explicit FutureGraphPatch 与唯一 PatchApplicationService。
- old Run terminal supersession + child/root ownership 原子 handoff。
- 新 Revision、全量预创建所有未由 immutable Past 满足的 retained execution-required
  `execution_node_ids` 的子 WorkflowRun、parent_run_id 和 Diff；handoff child 明确从
  `running,pause_requested=false` 开始。
- empty execution set 在 inherited contracts 已满足时同事务创建并立即终态化 child/root；不满足则
  Compiler 拒绝，不留下空 running Run。
- 已完成 Artifact 继承，以及 blocked/unknown parent 不得启动 child。
- `lineage_budget_root_run_id` 边界内的 continuation 累计 request budget、继承 deadline 与显式扩容
  审批；rerun 建立新 budget root。
- 统一 failed-node retry child 与 full-rerun new-Run 语义，不向 terminal parent 增加 attempt；completed-node
  partial rerun/Artifact invalidation 延后。

门禁：运行中编辑不会改写 Past/Active 节点或原 Run 的 Revision；UI 能展示父子 Run 与继承
Artifact。Pause 遇到 in-flight Approval 时 Node 保持 `running` + approval-pending 投影、Workflow 保持
draining；等待审批不是 `blocked`。只有取消/崩溃留下 unknown outcome 才使用 recovery-only blocked。

### 8D：任务特化 GraphPlanner Draft

交付：

- TaskFeatures。
- 可选只读 Scout/TaskBrief。
- Node/Agent/Artifact/Capability Catalog 与最小 GraphGrammar。
- 模板先验、Agent/Model/Skill 参数化和受约束 TaskGraphDraft。
- Draft 解释、Compiler 和 Direct 回退。
- Auto-run 用户策略。

门禁：简单任务保持 Direct；不同复杂任务能生成具有任务差异、可编辑且编译通过的 Draft；模板不
是唯一完整图来源。无推广证据时只建议/待批准，不阻塞手工运行。

### 8E：Global future-only Replan

交付：

- ReplanSignal/ReplanProposal 与 sole automatic Patch proposer ReplanCoordinator。
- 对 8C PatchApplicationService 的复用；不增加第二个 Revision writer。
- Past/Active immutable enforcement、Artifact 复用/来源投影。
- 风险分级的 Replan UI 与 CLI：建议 Patch 展示 diff、风险类别与升级理由；`auto_replan_mode`
  默认 `approval_only`，`allow_low_risk` 下仅低风险类别自动应用且事后可审计；task class 级
  默认自动化在对照证据齐备前保持关闭。

门禁：用户编辑与 Agent 建议走同一写路径；并发 stale Patch 不覆盖新 Revision；恢复和 Replan 都不
重跑/改写已完成节点，也不把 unknown side effect 伪装为安全；低风险自动接受不绕过 Compiler/OCC，
任何越权或扩容 Patch 都被拒绝自动应用并转为待批准建议。

### 8F：Context、Learning 与 Skill 管理

交付：

- Active Context Bar/Drawer。
- Preference/Knowledge/Candidate 管理。
- Skill Catalog、Draft、Diff、Eval 和启停。
- 统一 Command Service 调用。

门禁：GUI 修改和 CLI 修改产生完全一致的 revision、事件和运行时解析结果；该产品面延期不能阻塞
8B–8E 的 Workflow 核心链路。

### 8G：反馈学习与效果评估

交付：

- WorkflowFeedback。
- OrchestrationPolicy Candidate。
- Direct/Multi-Agent 对照 Dashboard。
- 无效节点、编辑频率和 Reviewer 价值指标。

门禁：系统不会因为一次用户编辑就自动永久改路由；有收益的 task class 才能升级自动运行/自动
Replan，无收益不阻止 suggestion-only 和手工能力的工程验收。

### 8H：有界只读并行

Stage 7 的全部 Workflow 都是串行的；本切片把并发准入限制在编译期可证明只读的普通 fan-out
frontier，不依赖 Parallel Research 等专用模板，也不引入新的角色执行器平台。

交付：

- 固定 ready frontier 的只读证明：以冻结 effective ToolSet、ToolEffect 与 PermissionSnapshot 为准，
  角色名不是证据；unknown/opaque effect 不得进入并行准入，read-contract drift 直接失败目标节点。
- 按请求原子 accounting/可选 cap claim：每次 Provider 请求前以幂等键
  （run/node/request 序号）记录请求；显式 Workflow/Node cap 存在时在剩余额度内原子申领，未配置时
  只计量。响应后按实际结算，不预留整节点最坏额度，不新增内存态账本或第二 request counter。
- 并发 slot 上限、确定性 gather（持久化与下游可见顺序按稳定 node 序，与完成顺序无关）、单 Writer
  串行不变量。
- frontier 的确定性取消、每个已准入 NodeRun 恰好结算一次、部分完成恢复不重跑已完成节点。
- 并发证明/容量不可用时的稳定串行 fallback；drift 永不 fallback。

门禁：用 barrier/event 而非 sleep 证明并发；权威预算/concurrency 不被超准入；取消/恢复语义与串行
路径一致。本切片不早于 8C（continuation 优先于并行），也不阻塞 8A/8B 的 GUI 核心链路。

## 十六、测试与验收

### 16.1 API/UI 一致性

- CLI 与 GUI 同时操作的 revision 冲突。
- Event 丢包、乱序、重复和重连。
- 前端陈旧 Snapshot。
- Command 重试和幂等。
- Core 重启后 GUI 恢复。

### 16.2 Workflow 编辑

- 删除被依赖节点。
- 修改 Running/Completed 节点。
- Provider/Skill 在编辑期间被停用。
- 新 Revision 编译失败。
- Pause 时有 in-flight Tool/Approval。
- Failed-node Retry 创建 child Run、Full rerun 创建 new Run，均不向 terminal parent 增加 attempt；同
  Revision 与新 Revision 路径都为各自完整 `execution_node_ids` 全量预创建 attempt-1 rows。full rerun
  不继承 prior node-output，completed-node partial rerun 请求被拒绝并提示 full rerun。
- User/Agent Replan 同时提交导致 stale base Revision。
- Patch 尝试修改 Past/Active Node 或伪造历史 Artifact producer。
- continuation child Run 恢复后错误重跑已完成节点。
- old Run terminal supersession 与 child/root ownership handoff 的事务故障注入，不出现 ownership 空窗。
- 删除全部 Future/全部工作已由 inherited Past 满足时，empty execution set 同事务完成 child 与 root
  result closure，包括 lineage budget=0/deadline 已过期的相邻案例；缺 required inherited
  contract 的 Patch 被拒绝。分别证明 succeeded 的 READY + marked snapshot 与 needs_revision 的 FAILED +
  terminal Outcome，后者不产生 success snapshot。
- Past blocking ReviewReport + Future fix/new Reviewer pass 的 Patch 必须显式把新 Revision required output
  指向新 report；旧 block 保留 evidence 但不驱动 child。对照 empty child 仍引用 inherited blocking
  required report 时确定得到 needs_revision。
- drain 中 active failure/cancel 保留 Stage 7 terminal parent，并要求显式合法 root transition 后创建
  child/new Run；result-driving blocking ReviewReport 在仍有 queued declared node 时只让 Reviewer
  completed 并 drain 到 paused，只有整图结束时才形成 terminal `needs_revision` parent；非
  result-driving block 只作 evidence。
- blocked/outcome-unknown parent 可保存 Patch 但不能启动 child；resolve 后仅当 old Workflow/root 仍
  nonterminal 才可重新 drain/handoff。若 durable result 为 failed/cancelled，必须先完成相应 root
  transition，再创建 `run_relation=rerun` child/new Run；abandon 后同 root continuation 被拒绝。
- continuation child 不能重置其 `lineage_budget_root_run_id` 内已耗 request count 或 absolute deadline；
  显式获批提高 cap/延长期限与普通 inherited-budget 路径分别验证。覆盖 failed Run → explicit rerun
  （新 budget root）→ replan continuation，证明后者只累计 rerun root 之后的消费。
- approval-pending drain 保持 running/draining 而不是误用 blocked；unknown effect 才进入 blocked。
- Pause 后 in-flight Tool outcome unknown 时保留 `pause_requested`；resolve-success 回到 draining/paused
  且不准入 queued，resolve-failure 走 terminal mapping，只有显式 Resume 才清除 pause intent。
- `pending_terminal_intent=null` 的 blocked/outcome-unknown Run 也可 OCC 设置
  `pause_requested=true` 而不改 unknown evidence；resolve-success 不经过 running admission 窗口。
  user-cancel-intent blocked Run 的 Pause 相邻拒绝案例证明 cancel 优先并仍 terminalize；handoff child 不
  继承 parent pause intent。
- Stage 7 存量 running/blocked WorkflowRun migration 后具有 false pause intent、initial relation 和
  self budget root；重启/resolve 不因新增 NULL 字段卡死或重置消费。

### 16.3 安全

- 恶意网页访问 loopback API。
- 前端 XSS 渲染工具输出/Markdown。
- Credential 泄漏到 API payload。
- UI 尝试提升 Agent 权限。
- Workflow 文件中的注入式字段。
- MCP/Skill 内容在 GUI 中诱导审批。

### 16.4 自动编排与 Replan

- 小任务误选多 Agent。
- 大任务漏选 Reviewer。
- 编译失败回退。
- 预算不足。
- 用户明确排除某角色。
- 工作空间 OrchestrationPolicy 覆盖全局默认。
- 相似但范围不同的任务生成有差异的 TaskGraphDraft，而不是机械复制同一模板。
- Node Agent 只能发 ReplanSignal，不能直接修改 Revision。
- 低风险 Patch 在 `auto_replan_mode=allow_low_risk` 下自动应用且事后可审计；同一 Patch 在
  `approval_only` 下等待批准。
- 扩大权限、提高 cap/deadline、引入新角色或引用未授权 Provider/Model/Skill 的 Patch 在任何
  策略下都不得自动应用，必须转为待批准建议。
- 低风险自动接受不绕过 Compiler/OCC：编译失败仍回退，stale base 仍冲突。
- 无收益/无授权时保持建议式，不误阻塞 Direct 或手工 Workflow。

### 16.5 可用性

- 只用键盘完成任务、审批和节点选择。
- 大 Workflow 可缩放、搜索和折叠。
- 色彩不是唯一状态信号。
- 错误提供可执行修复。
- Direct 任务不被复杂 UI 淹没。

## 十七、阶段交付物

- Local Core API 与安全通信。
- Web GUI 运行观察器。
- Active Context、Learning 与 Skill 管理界面。
- Agent Definition 编辑器。
- Workflow 节点编辑器与 Compiler 错误展示。
- 运行中 Pause/Drain、future-only Revision 编辑与 continuation child Run。
- TaskFeatures、可选 Scout、受约束任务特化 Workflow Draft 生成。
- Global ReplanSignal、ReplanCoordinator 与 FutureGraphPatch。
- WorkflowFeedback、OrchestrationPolicy Candidate 和评估 Dashboard。
- GUI 安全、可访问性和端到端测试。

## 十八、完成标准

1. GUI 与 CLI 使用同一 Command/Query/Event/Approval 服务。
2. GUI 不直接读写任何权威存储或 Runtime 内部对象。
3. 用户可以实时看到 Task、Workflow、Node、Agent、Tool、Artifact 和预算状态。
4. Active Context Bar 展示本次实际解析的偏好/知识，并支持查看来源和编辑。
5. 用户可以通过 GUI 管理 Learning Candidate 和 Skill 生命周期。
6. 用户可以配置 Agent 的 Provider、Role Prompt、Skill、能力、Context 和预算。
7. 用户可以创建、验证、运行和保存 Workflow。
8. 自动编排从受支持 Catalog/GraphGrammar 生成任务特化 Draft；模板只是先验/回退，不执行任意未经
   编译的图。
9. 简单任务默认 Direct，复杂任务的升级理由和成本可见。
10. 运行中只能在 Pause/Drain 后修改从未启动的 Future 节点，并生成新 Revision 与引用它的子
    WorkflowRun；原 Run 的 Revision 保持不变且以 terminal `superseded` 与 child/root ownership 在同一
    事务交接。child 为完整 Compiler-closed `execution_node_ids` 全量预创建 rows，按
    `lineage_budget_root_run_id` 继承 continuation 已耗 budget/deadline；empty execution set 合法时同事务
    终态闭合而不遗留 running child；blocked/outcome-unknown parent 未 resolve 前不能启动，pause intent
    也不能因 blocked/restart 丢失。
11. GUI 断线重连、Core 重启和事件缺口不会造成错误状态。
12. 用户的 Workflow 编辑只形成候选，不被一次行为永久自动学习。
13. User exact edit 直接形成 FutureGraphPatch；Agent Replan 经 Coordinator 形成建议 Patch。两者只在
    同一个 PatchApplication/Compiler publication 路径汇合，Past/Active Node、旧 Revision 和历史
    Artifact provenance 不被改写。
14. 图级 Replan 按风险分级自治：不扩大权限/预算、仅修改 Future 节点的低风险 Patch 可经
    `auto_replan_mode=allow_low_risk` 自动接受并事后可审计；任何越权或扩容 Patch 始终需用户
    明确批准。task class 级默认自动化只对已有明确对照收益且用户允许的类别推广；证据不足时
    保持建议/批准模式，不阻止 Stage 8 工程完成。
15. GUI 安全测试确认 loopback、XSS、Credential 和权限边界可靠。

## 十九、明确不包含

- 桌面安装器、自动更新和应用商店发布。
- 定时任务、后台 daemon 和进程退出后自动执行。
- 模型自由生成任意代码型节点或无限图。
- 叶子 Agent 自行修改全局 DAG、嵌套动态子图和多个 Replan writer。
- completed-node partial rerun 及其传递 consumer Artifact invalidation；v1 使用 full rerun。
- 多用户协同编辑和团队权限。
- 分布式 Agent、远程 Worker 和跨设备同步。
- 用 UI 隐藏工具副作用、来源或审批。
- 同时建设多个前端框架或消息渠道。

## 二十、进入 Stage 9 前必须确认

- 哪些 Workflow 状态可以安全地在无人值守下恢复。
- 哪些工具具有幂等/对账合同，哪些必须人工介入。
- Approval 在后台任务中的有效期和通知方式。
- Local Core 的进程模型是否适合演进为 daemon/worker。
- GUI/CLI 如何显示离线期间的任务事件。
- ScheduleDefinition、Worker 和 WorkflowRun 的职责边界。
