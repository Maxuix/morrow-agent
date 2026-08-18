# Stage 8：自适应编排与 GUI 控制面

> 状态：未开始
> 阶段结果：Morrow 能根据任务选择并生成可验证的 Workflow Draft，用户可通过 GUI 观察、编辑和控制 Agent、偏好、Skill 与运行状态
> 上级文档：[开发路线总览](../ROADMAP.md)
> 上一阶段：[Stage 7：Agent Definition 与静态 Workflow Runtime](stage-7-workflow-runtime.md)
> 下一阶段：[Stage 9：后台任务与可靠自动化](stage-9-background-automation.md)

## 一、阶段目标

Stage 8 把 Stage 5–7 的能力组合成用户可直接掌控的个人 Agent 工作台。

完整交互：

```text
用户提交任务
→ Task Classifier/Orchestration Policy 判断复杂度
→ 从已验证模板中选择 Direct 或 Multi-Agent
→ 填充 AgentDefinition、模型、Skill、权限和预算
→ 生成 Workflow Draft
→ WorkflowCompiler 预检
→ 按用户策略直接运行或等待编辑
→ GUI 实时显示 Task / Node / Agent / Tool / Artifact 状态
→ 用户可暂停并修改尚未执行节点
→ 形成新 Workflow Revision 和子 WorkflowRun 后继续
→ 记录用户编辑为 WorkflowFeedback
→ LearningReview 只提出 Orchestration Preference 候选
```

本阶段的重点不是“画一张漂亮流程图”，而是建立：

- 同一核心状态的可视化投影。
- 可解释的自动编排。
- 运行时可控编辑。
- 可配置 Agent 模块。
- Multi-Agent 相对单 Agent 的效果反馈闭环。

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

- Stage 7 已支持手写静态 Workflow、AgentDefinition、Artifact 和运行观察。
- Command/Query/Event/Approval 接口能够表达完整运行状态。
- Stage 5 的候选、Active Preference 和 Knowledge 可被查询和编辑。
- Stage 6 的 Skill 生命周期、来源、权限和版本可被查询。
- Direct 与 Multi-Agent 已有真实任务对照数据。
- 运行中暂停、取消和恢复语义已经稳定。

## 四、自适应编排策略

### 4.1 从模板选择开始

第一版不得让模型从空白自由生成任意 DAG。流程：

```text
任务特征提取
→ 候选模板排序
→ Direct 基线检查
→ 选择模板
→ 参数化填充
→ Compiler 校验
→ Draft
```

首批模板沿用 Stage 7：

- Direct。
- Explore–Implement–Verify。
- Parallel Research。
- Planned Refactor。

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
- 预算不足。

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
- budget_limits
- review_requirement
- parallelism_limit
- auto_run_mode
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

每次编辑后重新编译。

### 6.3 运行中编辑

固定语义：

- 已完成 Node 不可修改。
- 正在运行 Node 不可修改；必须先暂停/取消该 Node。
- Pending/Blocked Node 可在 Workflow 暂停后修改。
- 编辑产生新 `WorkflowRevision`。
- 新 Revision 保留与旧 Revision 的 parent/diff。
- 已完成 Artifact 可被新 Revision 引用，但不能伪造为新节点产物。
- 修改只影响尚未启动节点。

Stage 7 的“一个 WorkflowRun 永远引用一个冻结 Revision”继续成立：暂停后的原 Run 标记为
`superseded`/`continued`（最终状态名在子计划锁定），新建引用新 Revision 的子 WorkflowRun，并显式记录
`parent_run_id` 与复用的 Artifact。禁止在同一个 WorkflowRun 上原地替换 `workflow_revision`。

### 6.4 删除节点

删除节点前检查：

- 是否有下游依赖。
- 是否移除最终必需 Artifact。
- 是否破坏审批/Review 门禁。
- 是否造成无终止路径。

Compiler 返回可操作错误，GUI 不自行猜测重连。

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
- 时间、Token、调用和重试预算。
- 是否只读/Writer。

### 7.2 不可配置为绕过项

- 固定安全边界。
- Credential 原文。
- 路径越界能力。
- 隐藏 ToolResult 或审计。
- Provider reasoning 记录。
- 无上限预算。
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

桌面打包留到 Stage 10，避免当前被安装器和跨平台 sidecar 问题拖慢。

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
initial query snapshot
+ ordered event stream
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
选择：Explore–Implement–Verify
原因：涉及多个模块、需要代码修改、独立 Review 价值较高
未选择 Parallel Research：任务没有多个独立研究方向
预计节点：3
总预算：...
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
- Retry failed Node（同一 Revision 下创建新 attempt）。
- Rerun from Node（需要改图时创建新 Revision 和子 WorkflowRun；不改图时创建新 Run/attempt，保留旧记录）。
- Edit pending graph。
- Accept/Correct TaskOutcome。

Stage 8 是前台或 Core 进程存活期间的运行控制；真正独立后台守护在 Stage 9。

## 十四、Multi-Agent 成本与反馈

### 14.1 运行前

展示：

- 节点数量。
- 模型选择。
- 最大预算。
- 预计并行度。
- 哪些节点写入。

### 14.2 运行中

展示：

- 已使用/剩余预算。
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

### 8B：Context、Learning 与 Skill 管理

交付：

- Active Context Bar/Drawer。
- Preference/Knowledge/Candidate 管理。
- Skill Catalog、Draft、Diff、Eval 和启停。
- 统一 Command Service 调用。

门禁：GUI 修改和 CLI 修改产生完全一致的 revision、事件和运行时解析结果。

### 8C：Workflow Editor 与 Agent Module Inspector

交付：

- 节点图、边、Inspector。
- AgentDefinition 编辑。
- Provider/Model/Skill/Tool/Budget 配置。
- 编译错误可视化。
- Definition/Revision Diff。

门禁：用户可创建一个合法 Workflow，非法图无法运行。

### 8D：运行中暂停和 Pending 编辑

交付：

- Pause/Resume。
- Pending Node 修改。
- 新 Revision、子 WorkflowRun、parent_run_id 和 Diff。
- 已完成 Artifact 继承。
- retry/rerun 语义。

门禁：运行中编辑不会改写已完成节点或原 Run 的 Revision；UI 能展示父子 Run 与继承 Artifact。

### 8E：模板选择与自动 Workflow Draft

交付：

- TaskFeatures。
- Template selector。
- Agent/Model/Skill 参数化。
- Draft 解释、Compiler 和 Direct 回退。
- Auto-run 用户策略。

门禁：简单任务保持 Direct；复杂任务能生成可编辑且编译通过的 Draft。

### 8F：反馈学习与效果评估

交付：

- WorkflowFeedback。
- OrchestrationPolicy Candidate。
- Direct/Multi-Agent 对照 Dashboard。
- 无效节点、编辑频率和 Reviewer 价值指标。

门禁：系统不会因为一次用户编辑就自动永久改路由，且能证明至少一类自动 Workflow 有收益。

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
- Retry 产生新 attempt 而非覆盖旧记录。

### 16.3 安全

- 恶意网页访问 loopback API。
- 前端 XSS 渲染工具输出/Markdown。
- Credential 泄漏到 API payload。
- UI 尝试提升 Agent 权限。
- Workflow 文件中的注入式字段。
- MCP/Skill 内容在 GUI 中诱导审批。

### 16.4 自动编排

- 小任务误选多 Agent。
- 大任务漏选 Reviewer。
- 编译失败回退。
- 预算不足。
- 用户明确排除某角色。
- 工作空间 OrchestrationPolicy 覆盖全局默认。

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
- 运行中 Pause/Resume/Pending Revision 编辑。
- TaskFeatures、模板选择和 Workflow Draft 生成。
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
8. 自动编排先从验证过的模板生成 Draft，不执行任意未经编译图。
9. 简单任务默认 Direct，复杂任务的升级理由和成本可见。
10. 运行中只能在暂停后修改 Pending 节点，并生成新 Revision 与引用它的子 WorkflowRun；原 Run 的
    Revision 保持不变。
11. GUI 断线重连、Core 重启和事件缺口不会造成错误状态。
12. 用户的 Workflow 编辑只形成候选，不被一次行为永久自动学习。
13. 至少一种自动选择的 Workflow 在真实任务上优于 Direct 基线。
14. GUI 安全测试确认 loopback、XSS、Credential 和权限边界可靠。

## 十九、明确不包含

- 桌面安装器、自动更新和应用商店发布。
- 定时任务、后台 daemon 和进程退出后自动执行。
- 模型自由生成任意代码型节点或无限图。
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
