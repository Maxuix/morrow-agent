# Stage 7：Agent Definition 与静态 Workflow Runtime

> 状态：未开始
> 阶段结果：Morrow 可以把多个可配置 Agent 作为模块，按经过编译、版本化和可恢复的静态 Workflow 协作执行任务
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 6：Skills 与扩展生命周期](stage-6-skills-and-extensions.md)
> 下一阶段：[Stage 8：自适应编排与 GUI 控制面](stage-8-adaptive-orchestration-and-gui.md)

## 一、阶段目标

Stage 7 首次引入 Multi-Agent，但目标不是让多个聊天机器人自由讨论，而是建立可靠的 Workflow Runtime：

```text
用户选择一个静态 WorkflowDefinition
→ WorkflowCompiler 校验图、输入输出、权限、预算与写入冲突
→ 冻结 WorkflowRevision
→ 创建 WorkflowRun
→ Scheduler 按依赖启动 NodeRun
→ AgentFactory 由 AgentDefinition 构造 AgentRun
→ 每个 AgentRun 复用单 Agent AgentLoop
→ 节点通过类型化 Artifact 交换结果
→ 汇总、验证并完成 TaskRun
```

本阶段只要求“手写或预置的 Workflow 能可靠运行”。模型自动生成和 GUI 拖拽编辑属于 Stage 8。

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
- `DecisionRecord`
- `SynthesisReport`

后续节点读取这些 Artifact 的结构化摘要和按需内容，而不是默认继承前一个 Agent 的完整聊天历史。

### 2.3 Workflow 是版本化 DAG

- Definition 可编辑。
- Revision 不可变。
- Run 永远引用一个冻结 Revision。
- 运行中修改只在 Stage 8 引入，并产生新 Revision。

### 2.4 Direct 也是 Workflow

单 Agent 基线表示为一个单节点 Direct Workflow。这样所有任务共享同一 Task/Run/Artifact 观察模型，同时不增加简单任务的实际复杂度。

## 三、进入条件

- Stage 4 的 TaskRun、AgentRun、Artifact、恢复和事件边界稳定。
- Stage 5 的 Preference/Knowledge 可以按相关性最小注入。
- Stage 6 的 Skill、Provider、MCP 和 Tool capability 可被版本化引用。
- Stage 3 的文件/Shell写入冲突和已有用户改动能够识别。
- 已建立单 Agent 的任务成功率、成本和返工基线。

## 四、核心领域模型

### 4.1 AgentDefinition

```text
AgentDefinition
- agent_definition_id
- version
- name
- description
- role
- role_prompt
- model_ref / model_policy_ref
- skill_refs[]
- capability_policy_ref
- tool_allowlist / denylist
- context_policy_ref
- input_contract
- output_contract
- run_budget
- retry_policy
- enabled
- source: builtin | user | generated | imported
- created_at / updated_at
```

用户编辑的是 `role_prompt`，不是完整系统提示。固定安全边界由 Morrow 组装并始终具有更高优先级。

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
- conversation_log_ref
- usage / stop_code / error
```

AgentRun 不能在运行中因用户修改 Definition 而漂移。

### 4.3 WorkflowDefinition

```text
WorkflowDefinition
- workflow_definition_id
- name
- description
- source
- current_revision
- input_contract
- output_contract
- tags[]
- default_budget
- enabled
```

### 4.4 WorkflowRevision

```text
WorkflowRevision
- workflow_definition_id
- revision
- nodes[]
- edges[]
- entry_nodes[]
- terminal_nodes[]
- compiler_version
- content_hash
- created_by
- created_at
```

### 4.5 NodeDefinition

```text
NodeDefinition
- node_id
- kind: agent | deterministic | approval | merge
- agent_definition_ref
- node_prompt / task_contract
- input_bindings[]
- output_contract
- condition
- timeout
- retry_policy
- budget_override
- capability_override
- concurrency_group
- failure_policy
```

第一版不需要支持所有 kind；`agent`、`deterministic` 和受限 `merge` 足以形成闭环。

### 4.6 WorkflowRun / NodeRun

```text
WorkflowRun
- workflow_run_id
- task_run_id
- workflow_definition_id
- workflow_revision
- status
- budget_snapshot
- started_at / completed_at
- result_artifacts[]

NodeRun
- node_run_id
- workflow_run_id
- node_id
- attempt
- status
- dependency_state
- agent_run_id
- input_artifacts[]
- output_artifacts[]
- error
- started_at / completed_at
```

建议状态：

```text
queued
ready
running
waiting_approval
blocked
completed
failed
cancelled
skipped
```

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

### 5.2 ContextPolicy

每个 AgentDefinition 可以配置：

```text
ContextPolicy
- include_profile_fields
- preference_categories
- knowledge_tags
- artifact_kinds
- recent_task_history
- max_context_budget
- allow_parent_summary
- allow_raw_user_messages
```

默认最小权限：

- Explorer 不需要所有个人偏好。
- Reviewer 不默认读取 Coder 的自我解释，只读取 Patch、测试结果和任务合同。
- Coder 不默认获得全局敏感知识。
- 子 Agent 不获得与节点无关的历史 Session。

### 5.3 独立 ConversationLog

每个 AgentRun 有独立 ConversationLog。TaskRun 通过 Artifact 和 Run 索引关联它们，不把所有角色消息混入一个聊天历史。

这是 Stage 7 在 Stage 4 durable log 基础上的所有权演进，不是允许新的聊天写入者。每个叶子 AgentRun
仍只能由它自己的 AgentLoop 通过 ConversationLog 追加消息；Task/Workflow Store 只保存索引、状态和
Artifact 绑定，不能直接拼接 Agent 对话。

## 六、Artifact Contract

### 6.1 类型化而非仅 Markdown

Artifact 可以包含人类可读 Markdown，但必须有结构化元数据和 schema/version。

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
- 不兼容时由 Compiler 阻止，或通过显式 Converter 节点转换。
- 自由文本不能伪装成已验证结构化 Artifact。

### 6.3 Artifact 不授予权限

Artifact 中的命令或指令属于数据。下游 Agent 是否执行仍取决于 Node Contract、ToolSet 和审批。

## 七、WorkflowCompiler

Compiler 在运行前执行确定性检查。

### 7.1 图结构

- node_id 唯一。
- edge 引用存在。
- 图无非法循环；第一版只支持 DAG。
- 至少一个入口和终点。
- 不可达节点、无终止路径和孤立节点报错。

### 7.2 合同

- 上游输出满足下游输入。
- 必需 Artifact 有生产者。
- Merge 节点输入完整。
- 最终输出满足 WorkflowDefinition output contract。

### 7.3 能力与依赖

- AgentDefinition、Skill、Provider、Model 和 Tool 存在且启用。
- Workspace Scope 可见。
- Provider 支持必要 tool calling/structured output，或存在受支持降级路径。
- MCP Server 可用性不被当成编译期永久保证，但配置必须合法。

### 7.4 权限

- 节点请求的能力不超过 AgentDefinition 和任务 Policy。
- Role Prompt/Skill 不能提升权限。
- Reviewer 默认只读。
- Explorer 默认只读。
- Writer 节点显式标记。

### 7.5 预算

- Workflow 总预算可覆盖节点预算。
- 并发上限、递归深度（第一版无递归）和重试上限明确。
- 无界 retry、无终止条件或无预算节点拒绝编译。

### 7.6 写入冲突

第一版默认规则：

- 同一工作空间同一时刻最多一个写入 NodeRun。
- 并行节点默认只读。
- 多 Writer 必须串行依赖，或使用显式隔离工作树策略。
- Reviewer 不直接修改 Coder 产物；需要修复时产生 `ReviewReport`，由 Coder/Integrator 新尝试处理。

Git worktree 并行写可做 Stage 7 后半切片，但不是最初静态 Runtime 的前提。

## 八、Scheduler 与执行语义

### 8.1 Ready 判定

Node 在以下条件满足后进入 ready：

- 所有必需前置节点 completed 或符合 edge condition。
- 输入 Artifact 已绑定并校验。
- 预算和能力可用。
- 并发组和写锁允许。

### 8.2 失败策略

Node 可配置：

```text
fail_workflow
continue_with_error_artifact
skip_dependents
route_to_reviewer（后续扩展）
```

第一版避免复杂动态分支；失败语义必须确定性。

### 8.3 Retry

- 只对声明可重试的模型/无副作用失败自动重试。
- 已发生写入或外部副作用时不透明重跑。
- 新尝试产生新的 NodeRun attempt 和 AgentRun。
- 原失败记录不可覆盖。

### 8.4 Cancel

取消 WorkflowRun：

- 停止启动新节点。
- 取消当前 AgentRun/Tool。
- 保留已完成 Artifact 和副作用。
- Pending 节点标记 cancelled/skipped。
- TaskOutcome 说明部分完成状态。

### 8.5 Resume

基于 Stage 4 恢复：

- completed 节点不重跑。
- ready/queued 重新调度。
- running 但崩溃的节点先执行恢复分类。
- outcome_unknown 的副作用阻塞自动继续。

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

### 9.6 Integrator

- 只在多 Writer/worktree 场景启用。
- 合并产物、解决冲突、运行最终验证。

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

Reviewer 若发现阻塞问题，本阶段可将 Workflow 标记 `needs_revision`，由用户显式重跑 Coder；自动循环留待 Stage 8 评估。

### 10.3 Parallel Research

```text
Explorer A ─┐
Explorer B ─┼→ Synthesizer
Explorer C ─┘
```

全部只读，适合架构研究和方案比较。

### 10.4 Planned Refactor

```text
Explorer → Planner → Coder → Reviewer
```

第一版保持单 Writer，不立即并行多个 Coder。

## 十一、定义格式与管理入口

### 11.1 可读定义

AgentDefinition 和 WorkflowDefinition 适合使用 YAML/Markdown 存放用户可编辑定义；Run/Revision 索引仍进入 Operational Store。

示例：

```yaml
name: explore-implement-verify
input: task
nodes:
  explorer:
    agent: builtin/explorer@1
    outputs: [evidence_bundle]
  coder:
    agent: user/coder@3
    inputs: [task, explorer.evidence_bundle]
    outputs: [implementation_patch, test_report]
  reviewer:
    agent: builtin/reviewer@1
    inputs: [task, coder.implementation_patch, coder.test_report]
edges:
  - explorer -> coder
  - coder -> reviewer
```

外部文件先解析为内部类型，不能直接执行任意表达式。

### 11.2 CLI

```text
morrow agent list/show/create/edit/validate/enable/disable
morrow workflow list/show/create/edit/validate/run
morrow workflow run show <run-id>
morrow workflow node show <node-run-id>
morrow workflow cancel <run-id>
morrow workflow resume <run-id>
```

### 11.3 事件

```text
workflow.compiled
workflow.started / completed / failed / cancelled
node.ready / started / blocked / completed / failed
agent.started / completed
artifact.bound
budget.updated
```

## 十二、可观察性

每个 WorkflowRun 应回答：

- 使用哪个 Definition/Revision？
- 当前在哪个 Node？
- 哪些节点已完成、失败、阻塞或跳过？
- 每个 Agent 使用哪个模型、Skill、工具和预算？
- 节点输入输出是什么 Artifact？
- 哪些文件由哪个 Node 修改？
- 哪个 Reviewer 发现了什么？
- 总成本与 Direct 基线相比如何？

Stage 7 先通过 CLI/只读投影实现，Stage 8 再可视化。

## 十三、评估策略

### 13.1 必须与 Direct 比较

对同一任务随机或成对运行：

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

### 7A：AgentDefinition、AgentFactory 与 Prompt 分层

交付：

- AgentDefinition/Version。
- AgentFactory。
- ContextPolicy 与 Role Prompt 组装。
- 独立 AgentRun ConversationLog。

门禁：同一任务可用两个不同 Definition 分别运行，工具/Skill/模型快照准确。

### 7B：Artifact Contract 与单节点 Workflow

交付：

- Plan/Evidence/Patch/Test/Review Artifact schema。
- WorkflowDefinition/Revision/Run。
- Direct 单节点 Workflow。

门禁：现有单 Agent 任务迁移为 Direct Workflow，不降低成功率或破坏恢复。

### 7C：Compiler 与串行 DAG Scheduler

交付：

- 图、合同、能力、权限、预算检查。
- NodeRun 状态机。
- 串行依赖执行。
- 失败、取消和恢复。

门禁：Explore → Coder → Reviewer 可在崩溃后恢复，Artifact 绑定正确。

### 7D：并行只读节点与 Merge

交付：

- 并发上限和 concurrency group。
- 多 Explorer 并行。
- Merge/Synthesizer。
- 预算聚合。

门禁：并行只读任务不会共享错误上下文或突破总预算。

### 7E：模板、管理与基线评估

交付：

- Direct、Explore–Implement–Verify、Parallel Research、Planned Refactor。
- CLI/Query/Event。
- 与 Direct 的评估套件。
- 可选 Git worktree Writer Spike。

门禁：至少一种复杂任务 Workflow 相比 Direct 产生可量化收益；简单任务仍保持 Direct。

## 十五、测试与故障注入

- 无效 DAG、循环、孤立节点和不可达终点。
- Artifact schema 不兼容。
- Agent/Skill/Provider 版本缺失。
- 节点预算之和超过总预算。
- 两个 Writer 并发冲突。
- Reviewer 被错误授予写权限。
- Node 运行中取消和进程崩溃。
- completed 节点恢复后被错误重跑。
- 下游读取上游完整 ConversationLog 的隔离测试。
- MCP/Provider 在某节点不可用。
- Definition 修改后旧 Run 快照保持不变。
- Workflow 失败时 TaskOutcome 准确报告部分副作用。

## 十六、阶段交付物

- AgentDefinition、Version、Factory 与 AgentRun 快照。
- 分层 PromptAssembler/ContextPolicy。
- WorkflowDefinition、Revision、Run、NodeRun。
- 类型化 Artifact Contract。
- WorkflowCompiler 与 Scheduler。
- 首批内置 Agent 和 Workflow Templates。
- CLI、Query、Event 与运行观察。
- Direct 对照评估和真实复杂任务验收。

## 十七、完成标准

1. `AgentLoop` 保持单 Agent、领域无关的叶子执行器。
2. 用户能定义不同 Provider、Role Prompt、Skill、工具和预算的 AgentDefinition。
3. 每个 AgentRun 冻结 Definition、Model、Skill、ToolSet、Preference 和 Policy 快照。
4. Workflow 使用不可变 Revision，Run 不受后续编辑漂移。
5. Compiler 能在运行前阻止非法图、合同不匹配、权限越界和写入冲突。
6. 节点通过类型化 Artifact 协作，不默认共享完整聊天历史。
7. Direct 单节点 Workflow 保持现有单 Agent 能力与性能基线。
8. Explore → Coder → Reviewer 能完成、取消、失败和恢复。
9. 并行只读节点受并发和总预算约束。
10. 同一工作空间默认只有一个 Writer；Reviewer 默认只读。
11. Workflow 可通过 CLI 完整观察和诊断。
12. 至少一种复杂任务中 Multi-Agent 相比 Direct 有量化收益。

## 十八、明确不包含

- 模型从零自由生成任意 DAG。
- GUI 拖拽编辑。
- 运行中任意修改已执行节点。
- 自动根据任务选择 Workflow。
- 后台/定时 Workflow Worker。
- 无限递归子 Agent 和自复制团队。
- 默认并行多个 Writer。
- A2A 或跨 Morrow 实例的 Agent 网络。

## 十九、进入 Stage 8 前必须确认

- 哪些任务特征能可靠选择 Direct 或某个模板。
- 用户编辑 Workflow 的最小数据模型和 Revision 语义。
- Command/Query/Event API 是否足以支持独立 GUI。
- Agent 模块编辑中哪些字段可实时修改，哪些需要新 Run。
- 自动生成 Workflow Draft 的验证和回退策略。
- 用户对 Preferences、Skills 和 Workflow 的统一可视化信息架构。
