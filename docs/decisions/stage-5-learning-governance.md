# Stage 5 可审查学习与长期记忆治理决策

> 状态：已接受（2026-08-20）
> 范围：Stage 5 Subplans 49–54

## 决策

Morrow 的长期学习必须从显式接受的 `TaskOutcome` 进入一个可审查的候选流程。模型可以
提出结构化 `CandidateDraft`，但不能直接创建 Review、写入 SQLite、修改 YAML、调用工具或
改变任何 Active 状态。只有用户明确的候选决策，以及唯一的 `LearningPromotionService`，
才能改变长期状态。

Stage 5 第一版只开放 `off` 和 `review_only` 两种 `LearningPolicy`。缺少工作区策略行时，
有效策略是不可变的 `review_only` 默认值；`explicit_auto` 只是保留的领域令牌，不能通过公开
命令选择、持久化或激活。

## 生命周期与事务

- 只有 `ready_for_acceptance → accepted` 且产生 `TaskOutcome` 的显式转移，才自动请求
  `LearningReview`。`cancelled`、`failed`、`abandoned` 不自动触发学习。
- 接受任务、Outcome、pending Review、应用事件和命令回执在同一个外层 SQLite 事务中提交。
  事务中不执行模型调用、终端确认、等待或 YAML 写入。
- Review 的 claim/lease、Reviewer 执行、Review finalize 是三个独立阶段。进程崩溃、超时、
  取消、无效模型输出或 Reviewer 失败都不能回滚或改变已接受的 Outcome。
- 当前没有后台 Worker。交互入口可以在提交后显式执行一次有界 Review；headless 入口只会
  真实地入队，后续必须由显式 learning 命令执行。
- 同一 Outcome 可以有新的 Review 版本；旧 Review 保留并通过 `supersedes` 关联，不能覆盖
  历史事实。

## 权威与作用域

YAML 继续是 global/workspace Preferences 与 workspace Profile 的运行时 Active 权威。SQLite
保存 LearningPolicy、Review/Evidence/Candidate/decision 历史、抑制记录、Promotion 恢复操作、
Project Knowledge、激活来源和 MemorySelection 审计；SQLite 不保存一份参与配置合并的副本。
Project Knowledge 只有 SQLite 一个权威，不复制到 YAML。

Reviewer 创建的候选默认是 workspace scope。Global Preference 只有在用户的 promotion 预览中
明确选择后才可使用；Profile 与 Project Knowledge 保持 workspace scope。Session-only 或一次性
请求只能形成 Evidence，不能直接形成长期候选。

## 模型与证据边界

LearningContext 是最小、显式、有限的输入，只含被验证的 Evidence、TaskOutcome 事实和相关
Active 记录。Reviewer 没有工具、Session、ContextBuilder、CredentialStore、完整
ConversationLog 或原始 Artifact 字节，也没有任何 journal/configuration writer。

Reviewer 只能返回最多三个严格校验的 `CandidateDraft`，并且每个 Evidence ID 必须来自本次
输入。模型自报的 confidence 不具有权威；`confidence_band`、敏感性、指纹、冲突、去重和
抑制结果全部由确定性策略计算。原始模型输出、Provider 私有 reasoning、凭据和 traceback
不进入事件、日志、YAML、候选或模型上下文。

Evidence 记录 actor、authority、explicitness、polarity、来源引用、最小化的有界脱敏摘录和
内容摘要。助手输出、仓库内容、网页/工具输出或 Skill 指令不能单独证明可激活的 Preference
或 Profile，也不能授予能力。秘密、敏感个人信息、能力授权、Prompt Injection 文本和隐藏
Unicode 控制符默认 fail closed；禁止内容最多保留摘要、来源和有界拒绝原因。

## 激活、撤销与记忆冻结

候选的 accept、edit-and-accept、reject、reject-with-suppression 都必须带 workspace、候选
版本和 command ID，并显示类型、作用域、证据、冲突和最终 diff。原始候选不可变；编辑后的
最终值另存为不可变 decision。拒绝和“永不建议”是不同决定。

Preference/Profile 经过 `ConfigPatchService` 的 revision/digest 校验与可恢复 Promotion Saga
后才能写 YAML；Project Knowledge 通过稳定 head 与不可变 revision 激活。任何其他模块不得
直接改变 Active 状态。

每个新的 foreground AgentRun 只创建一个有审计记录的 MemorySelection，并把其 ID、摘要和
workspace memory revision 冻结进 AgentRunSnapshot。一个 Run 内配置或知识变化不可见；恢复
Run 必须复用原 selection，新的 User Turn 才能创建新 selection。缺失或不一致的选择、知识
版本或来源必须 fail closed。

## 代码所有权

- `core/learning.py` 只包含与框架无关的有界领域模型、校验和稳定错误；不得导入 outer layer。
- `core/learning_ports.py` 只声明窄的 Learning/Reviewer/未来 Memory 端口。
- Learning application 负责策略、协调和查询；TaskService 保持不知道 Learning，AgentLoop
  只消费冻结上下文，ConversationLog 仍是唯一聊天历史写入者。
- SQLite Learning repositories 复用共享 Operational Store transaction backend，不复制父
  journal 的事务状态；终端/Typer 只解析、呈现、确认和调用服务。
- Learning UI 不得导入 SQLite/YAML adapter；Learning application 中只有 Promotion 模块可以
  依赖配置变更服务。

## 明确不做

本阶段不引入后台调度、自动重试循环、自然语言接受、向量/Embedding 服务、外部记忆服务、
自动 Skill 创建或激活、Workflow/编排策略激活、完整会话搜索、物理清除和新的第三方依赖。
