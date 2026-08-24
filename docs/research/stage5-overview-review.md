## 总体判断

Stage 5 路线是对的，而且方向比许多 Agent 项目的“自动写一份 MEMORY.md”成熟得多。

Stage 5 真正要实现的不是一个 Memory Tool，而是一个**受治理的状态演化系统**：

> 任务事实 → 可追溯证据 → 学习候选 → 安全与冲突检查 → 用户或策略决策 → 长期状态晋升 → 可解释检索

路线图已经明确区分 `TaskOutcome` 和 `LearningReview`，规定 Review 失败不能影响任务完成、Review 不能直接写 Active 状态、长期状态必须有来源、作用域、版本和撤销能力；这应该继续作为架构最高约束。

我建议把 Stage 5 的核心原则浓缩成三条：

1. **模型只有提案权，没有长期状态写入权。**
2. **候选、证据和决策记录是审计事实；Active 状态仍由原有 Application Service 管理。**
3. **没有被选择进 `MemorySelection` 的记录，不进入 AgentRun 上下文。**

---

# 一、Hermes 值得借鉴什么

Hermes 的自学习闭环有几个非常值得吸收的设计：

- 它把短记忆限制在固定容量中，并在会话开始时冻结成 Prompt 快照。
- 它区分短事实记忆和长程序性 Skill。
- 它提供 Memory/Skill 的 staged approval、pending、diff、approve、reject。
- 它把历史会话搜索与常驻记忆分开，历史查询使用按需 FTS5 检索。
- 它提供 `/journey` 时间线来查看、编辑和删除学到的内容。
- 它对进入系统提示词的 Memory 和项目 Skill 做注入、凭据外传和隐藏 Unicode 扫描。
- Skill 使用 Level 0 索引、Level 1 完整内容、Level 2 引用文件的渐进披露。

这些原则很适合 Morrow。

但是 Hermes 的具体实现不应该直接复制。Hermes 的内置 Memory 是两个受字符上限约束的文件，由 Agent 直接执行 add/replace/remove；其 Memory 和 Skill 的写审批默认都是关闭的，后台 Review 可以直接写入。Memory 的替换和删除还使用 substring matching。

对 Morrow 来说，以下几项不适合照搬：

| Hermes 方案 | Morrow 更合适的方案 |
|---|---|
| Agent 直接写 Active Memory | Agent 只输出结构化候选 |
| `write_approval=false` 默认 | `review_only` 默认 |
| 两个 Markdown 文件作为记忆主体 | SQLite 保存证据、候选、知识版本；YAML 保留现有配置权威 |
| 全部小记忆进入 Prompt | 按任务生成 `MemorySelection` |
| substring 修改 | 稳定 ID、revision、expected revision |
| 每轮后台学习 | 默认在 accepted TaskOutcome 后学习 |
| Stage 中直接创建/修改 Skill | Stage 5 只产生 `SkillCandidate` |
| Memory 内容本身是主要状态 | 来源、审查、激活和检索过程都是一等记录 |

可以概括为：

> **借鉴 Hermes 的容量管理、冻结快照、审批体验、安全扫描和渐进披露；不要复制它的直接自写权和文件型权威模型。**

---

# 二、推荐的整体架构

```text
Task accepted / 显式 /learn review
        │
        ▼
┌─────────────────────────────────────┐
│ SQLite 外层事务                     │
│                                     │
│ TaskOutcome                         │
│ LearningReview(status=pending)      │
│ task.accepted event                 │
│ learning.review_requested event     │
│ command receipt                     │
└─────────────────────────────────────┘
        │ COMMIT
        ▼
┌─────────────────────────────────────┐
│ LearningReviewCoordinator           │
│                                     │
│ 1. claim pending review             │
│ 2. 构造最小 LearningContext         │
│ 3. 确定性 Evidence 提取             │
│ 4. ReviewerPort 产生结构化草案      │
│ 5. Schema / 来源 / 安全检查         │
│ 6. scope / dedup / conflict         │
│ 7. 保存 Evidence 和 Candidate       │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Learning Inbox                      │
│ accept / edit / reject / suppress   │
└─────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│ LearningPromotionService                 │
│                                          │
│ Preference/Profile ─→ ConfigPatchService │
│ ProjectKnowledge ─→ SQLite version       │
│ SkillCandidate ─────→ 保持 Candidate     │
│                                          │
│ PromotionOperation + crash recovery      │
└──────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ MemorySelector                      │
│                                     │
│ Active + scope + lexical + budget   │
│ → MemorySelection                   │
│ → AgentRunSnapshot                  │
│ → ContextBuilder                    │
└─────────────────────────────────────┘
```

这里的 `LearningReview(status=pending)` 就可以承担前面所说的 `LearningReviewRequest`。第一版不必再拆一张 Request 表；需要记录多次执行尝试时，再增加 `learning_review_attempts`。

---

# 三、最合适的触发点：`task_accept`

当前代码已经给出了非常自然的挂载位置：

- `TaskCommandResult` 本身可以携带 `outcome`。
- `TaskService.accept()` 会在任务转为 accepted 时创建 `TaskOutcome`。
- `ApplicationApi._task_command()` 当前拿到了整个 result，但只把 Task 状态和 row version 写入事件，随后只返回 task。
- SQLite journal 的嵌套 `transact()` 会加入已有外层事务，而不是再开启独立事务。

所以最小而正确的改法不是让 `TaskService` 依赖 Learning，而是在应用层增加一个 acceptance coordinator 或 result hook：

```python
result = task_service.accept(...)

if result.outcome is not None:
    learning_reviews.ensure_pending(
        task=result.task,
        outcome=result.outcome,
        trigger="task_accepted",
        policy_snapshot=policy_snapshot,
    )

emit_task_event(...)
store_command_receipt(...)
```

以上全部仍在现有外层 SQLite 事务中。

## 模型调用必须在提交后

绝对不要这样做：

```text
BEGIN IMMEDIATE
→ TaskOutcome
→ await model.review(...)
→ candidates
→ COMMIT
```

这会把网络延迟、模型超时和 Provider 错误放进数据库写事务。

正确方式是：

```text
短事务 1：创建 pending Review
COMMIT

短事务 2：claim Review，写 running/lease
COMMIT

事务外：调用 Reviewer

短事务 3：写 Evidence/Candidate，完成 Review
COMMIT
```

第一版即使只运行一个本地进程，也建议保留：

```text
lease_token
lease_expires_at
attempt_count
row_version
```

进程在模型调用期间崩溃后，可以把超时的 running Review 重新置为 pending。

## 不建议给 TaskRun 新增 `corrected` 状态

路线图提到 accepted、corrected 等结果，但公开代码里的实际 TaskRun 生命周期是：

```text
open
ready_for_acceptance
accepted
cancelled
failed
abandoned
```

我不建议仅仅为了 Stage 5 增加 `corrected` TaskRun 状态。更清晰的做法是：

```text
TaskFeedback
- kind: correction | acceptance_note | rejection_note
- source_turn_id
- task_run_id
- content/reference
```

用户要求修改时，Task 可以从 `ready_for_acceptance → open`，同时记录 `TaskFeedback(kind=correction)`。最终 accepted 后，Review 能同时看到 correction evidence 和最终 outcome。

---

# 四、Evidence 应该是系统的核心，而不是模型解释

路线图中的 Evidence 已经有正确方向，但我建议再增加两个关键维度：

```text
LearningEvidence
- evidence_id
- workspace_id
- task_run_id
- source_kind
- source_id
- source_pointer
- actor
- authority
- explicitness
- polarity
- scope_hint
- excerpt_redacted
- content_digest
- observed_at
```

其中：

```text
actor:
  user | assistant | tool | system

authority:
  user_explicit_persistent
  user_correction
  user_acceptance
  configuration_change
  deterministic_task_fact
  deterministic_artifact_fact
  behavioral_signal
  untrusted_external_content
```

路线图明确规定 Assistant 回答不能单独构成用户明确陈述，工具输出和仓库内容属于不可信输入，用户纠正与拒绝属于高权重负证据。

我建议把候选资格直接写成确定性矩阵：

| 候选类型 | 至少需要的证据 |
|---|---|
| Preference | 明确用户长期意图，或多个独立 Task 中重复的用户行为 |
| Profile | 明确用户陈述；不允许从写作风格、模型猜测中推断 |
| Project Knowledge | 用户确认，或确定性事实 + accepted TaskOutcome |
| SkillCandidate | 多步骤流程 + 成功验证事实，最好跨多个 Task |
| WorkflowFeedback | 实际 Workflow 编辑或运行结果 |
| OrchestrationPolicyCandidate | 只存信号，Stage 8 前不激活 |

尤其要注意：

> **Evidence 可以存在，而 Candidate 不一定存在。**

例如用户第一次从某个小任务中删除 Planner：

```text
保存 behavioral evidence
不创建候选
```

用户在三个独立任务中都这样做：

```text
聚合三个 evidence
创建 WorkflowFeedback Candidate
```

这样不会在第一次偶然行为后就污染 Inbox。

---

# 五、Reviewer 的职责必须很窄

Reviewer 不应该有任何工具，也不应该访问整个 ConversationLog。

输入建议是：

```text
LearningContext
- TaskOutcome
- bounded Evidence[]
- relevant active record summaries[]
- applicable suppression rules[]
- LearningPolicy snapshot
- candidate budget
```

输出只能是严格 Schema：

```text
CandidateDraft
- candidate_type
- operation
- semantic_key
- proposed_value
- proposed_scope
- evidence_ids[]
- classification
- temporary_or_durable
```

固定规则：

1. `evidence_ids` 必须来自输入集合，模型不能创造 ID。
2. 模型自报的 confidence 不进入最终记录。
3. `confidence_band` 由系统根据证据组合计算。
4. 一次 Review 最多生成 2–3 个 Candidate。
5. 零 Candidate 是正常成功结果。
6. 不保存 Provider 私有 reasoning。
7. 原始模型输出只在内存中存在；持久化的是通过 Schema 和安全检查的结果。
8. Reviewer Prompt、Provider、Model、Schema 和 Policy 版本全部记录。

推荐 Pipeline：

```text
确定性信号提取
→ 模型生成 CandidateDraft
→ Schema 校验
→ Evidence 资格校验
→ Secret / PII / capability authorization 检查
→ Prompt Injection 检查
→ scope 解析
→ 规范化与 fingerprint
→ duplicate / conflict
→ suppression
→ policy decision
→ persist
```

路线图同样要求先处理确定性信号，再让模型辅助分类，并限制 LearningContext 为最小证据、TaskOutcome 和相关 Active 记录。

---

# 六、Active 状态的权威划分

我建议明确锁定：

```text
YAML 是：
- global Preferences Active authority
- workspace Preferences Active authority
- workspace Profile Active authority

SQLite 是：
- LearningReview authority
- LearningEvidence authority
- LearningCandidate authority
- rejection / suppression authority
- PromotionOperation authority
- ProjectKnowledge authority
- MemorySelection audit authority

FTS / 搜索索引是：
- 可重建投影，不是权威
```

路线图也倾向于继续让现有版本化 YAML 负责 Active Preference/Profile，把证据保存在 SQLite，并要求避免富记录和旧 YAML 标量形成双重权威。

因此 SQLite 中不要再创建一个参与运行时解析的“Active Preference value”。

可以创建：

```text
PreferenceActivation
- activation_id
- candidate_id
- scope
- target
- key_path
- config_revision
- value_digest
- activated_at
- supersedes_activation_id
```

它只是：

- 来源记录；
- 晋升结果证明；
- 历史和撤销依据；

运行时仍然只从 YAML 读取当前值。

## Project Knowledge 建议以 SQLite 为唯一权威

Project Knowledge 天然需要：

- 稳定 ID；
- workspace 隔离；
- immutable revisions；
- disputed、superseded、disabled；
- 多个 Evidence；
- 检索和排序；
- 有效期和重新确认。

适合使用：

```text
project_knowledge_heads
project_knowledge_revisions
```

而不是同时维护 YAML 和 SQLite 两份。

---

# 七、Promotion Service 的关键难点：SQLite 与 YAML 一致性

路线图已经正确指出跨 SQLite/YAML 需要可恢复 operation record，而不能让 Review handler 直接写 Active 状态。

这里我建议先小幅升级 `ConfigPatchService` 的合同。

当前实现中，`preflight()` 会构造内部 `_OperationPlan`，但返回值没有携带完整 plan；`apply_command()` 随后又重新读取状态并重新 `_prepare()`。它已经使用 revision 做写冲突保护，但还不适合直接支撑一个可持久化、可恢复的跨存储 Promotion Saga。

建议增加公开类型：

```text
PreparedConfigurationChange
- command
- expected_revision
- before_digest
- after_digest
- changed
- preview
```

以及：

```python
prepare(command) -> PreparedConfigurationChange

apply_prepared(
    prepared,
    *,
    operation_id: str,
) -> ConfigurationChangeResult
```

Promotion 流程：

```text
1. 校验 Candidate、状态、作用域和 current target revision

2. ConfigPatchService.prepare()
   获得 expected_revision / before_digest / after_digest

3. SQLite:
   写 PromotionOperation(state=prepared)
   COMMIT

4. 通过 ConfigPatchService.apply_prepared() 写 YAML

5. SQLite:
   Candidate → accepted
   写 PreferenceActivation
   PromotionOperation → finalized
   发 learning.candidate_accepted / memory.record_activated
   COMMIT
```

恢复逻辑：

| 实际状态 | 恢复动作 |
|---|---|
| YAML 仍是 before digest/revision | 安全重试 |
| YAML 已经是 after digest | 说明写入成功，补做 SQLite finalize |
| YAML 是其他值或 revision 异常 | 标记 `needs_resolution` |
| Candidate 已 finalized | 幂等返回已有结果 |

任何情况下都不要因为“模型候选置信度更高”去覆盖未知的新 YAML revision。

Project Knowledge 全部位于 SQLite，因此它的晋升可以在一个外层事务内完成，不需要 Saga。

---

# 八、Candidate 模型建议

不要只用一个大而松散的 `payload_json` 领域模型。应用层应使用判别联合：

```text
ProfileCandidate
PreferenceCandidate
ProjectKnowledgeCandidate
SkillCandidate
WorkflowFeedbackCandidate
```

通用字段：

```text
LearningCandidate
- candidate_id
- review_id
- type
- operation: create | update | disable
- semantic_key
- scope
- status
- fingerprint
- confidence_band
- confidence_basis[]
- sensitivity
- expected_target_revision
- duplicate_of_id
- conflict_refs[]
- expires_at
- row_version
- supersedes_id
```

`fingerprint` 建议由以下内容确定：

```text
candidate type
+ workspace/global scope
+ semantic key
+ normalized proposed operation/value
```

Preference/Profile 的去重比较直接，因为 key 是有限集合。

Project Knowledge 则应要求 Reviewer 提供稳定语义键，例如：

```text
testing.command
architecture.provider_registry
environment.python_version
convention.commit_style
decision.memory_authority
```

再配合词法匹配做相似检查，而不是第一版就上 Embedding。

`never-suggest` 不要表达成另一条 Preference，而应该是一等对象：

```text
LearningSuppression
- suppression_id
- workspace_id
- scope
- semantic_key / fingerprint pattern
- reason
- source_candidate_id
- expires_at
- status
```

---

# 九、分类上我建议调整一个路线图示例

路线图把：

> “这个项目以后测试都用 `uv run pytest`”

作为 workspace Preference 示例。

我更倾向于把它分类为：

```text
ProjectKnowledge
category = convention
semantic_key = testing.command
statement = "本项目默认测试命令是 uv run pytest"
```

原因是它回答的是“这个项目如何工作”，而不是“用户希望 Agent 如何交流”。

建议固定：

| 类型 | 边界 |
|---|---|
| Profile | 用户或项目的粗粒度、稳定身份信息 |
| Preference | Agent 与用户怎样协作，例如语言、详细度、是否先写计划 |
| Project Knowledge | 项目事实、决策、命令和工程约定 |
| Episodic | 某次任务发生了什么 |
| Skill | 怎样完成一种可复用流程 |

否则 `Preferences.instructions` 很容易变成所有项目事实的兜底列表，后续既难检索，也会和 Project Knowledge 重复。

---

# 十、Memory Selection 与 ContextBuilder

路线图要求只检索 Active 且相关的记录，并建议第一版使用作用域、类别、key、关键词、近期确认和 Token 预算，不需要 Embedding。

建议检索流程：

```text
1. workspace/global hard scope filter
2. active status
3. requested category / explicit semantic key
4. task goal lexical overlap
5. identifier/path/command token overlap
6. last_confirmed / stability
7. category diversity
8. character/token budget
```

对于中文和代码混合检索，建议索引时同时生成：

- snake_case / camelCase / dotted key tokens；
- 命令和路径 token；
- 英文词；
- 中文二元或三元 n-gram。

这样不必等待向量检索，也能处理“测试命令”“Provider Registry”“配置修订”等混合查询。

## 冻结粒度建议是 AgentRun，而不是 Session

Hermes 在 session 开始时冻结全部 Memory，以保持系统提示词和 prefix cache 稳定。

Morrow 更自然的边界是：

```text
AgentRun 创建前：
  构造 MemoryQuery
  生成并保存 MemorySelection
  把 selection id + digest 放入 AgentRunSnapshot
```

原因是当前路线图已经把 AgentRun 定义为冻结模型、工具、权限、上下文策略和配置版本的一次执行。

建议：

```text
AgentRunSnapshot
- ...
- memory_selection_id
- memory_selection_digest
- memory_snapshot_revision
```

`MemorySelectionItem` 保存：

```text
record_kind
record_id
record_revision
rank
reason_codes[]
estimated_chars
```

不必把完整 Knowledge 内容复制进 Snapshot，因为 Project Knowledge revision 应该是不可变的。

## 上下文层级

当前 `ContextBuilder` 已经明确把工具输出、项目内容、Profile 和 Preferences 标为不可信用户状态数据，并以单独 SystemMessage 注入。这个边界非常适合扩展到 Project Knowledge。

推荐结构：

```text
固定安全边界
Task Contract

Relevant User State:
{
  "baseline_preferences": {...},
  "profile": {...},
  "project_knowledge": [
    {
      "id": "...",
      "revision": 3,
      "category": "convention",
      "statement": "...",
      "scope": "workspace"
    }
  ]
}

Skills
Artifacts
Conversation
```

其中：

- `language` 和 `response_detail` 很小，可以始终作为 baseline。
- `instructions` 如果持续增长，应按 semantic key 选择。
- Project Knowledge 必须按任务选择。
- 任何 Memory 都不能授予权限、改变审批、扩大工具范围或修改系统身份。

同时，把“长期记忆”和“历史任务搜索”分开。Hermes 也把常驻 Memory 与按需 FTS5 Session Search 视作两种不同能力。

在 Morrow 中：

```text
MemorySelector
  回答：当前任务需要哪些长期状态？

Task/Conversation Search
  回答：过去某次任务发生了什么？
```

不要把所有 TaskOutcome 都晋升成长期知识。

---

# 十一、第一版策略建议

路线图提供：

```text
off
review_only
explicit_auto
```

并明确建议 `review_only` 为默认，禁止“所有高置信推断自动写入”；`explicit_auto` 还必须显式开启，并保持现有配置审批的等价语义。

我的建议更保守一些：

## Stage 5.1

真正启用：

```text
off
review_only
```

保留但不开放：

```text
explicit_auto
```

理由是用户明确说“以后默认用中文”时，现有 `update_configuration` 已经有明确写配置路径、预览和必需审批。当前工具描述也明确禁止因一次性风格、引用、假设和否定句而写配置。

因此 Stage 5 最有价值的部分恰恰是**推断型学习**，而推断型学习在第一版应该全部进入 Inbox。

等这些条件完成后再开放 `explicit_auto`：

- Promotion Saga 有完整 crash injection 测试；
- 只允许有限的 Preference/Profile 字段；
- scope 无歧义；
- 无冲突；
- 用户完成显式预授权；
- 每次激活都有通知、历史和撤销；
- acceptance 数据证明误激活足够低。

---

# 十二、建议的代码模块

```text
src/morrow/core/learning.py
  LearningReview
  LearningEvidence
  LearningCandidate unions
  LearningPolicy
  LearningSuppression

src/morrow/core/memory.py
  ProjectKnowledgeHead
  ProjectKnowledgeRevision
  MemoryQuery
  MemorySelection

src/morrow/core/learning_ports.py
  LearningJournalPort
  MemoryJournalPort
  LearningReviewerPort

src/morrow/application/learning/
  coordinator.py
  context.py
  extraction.py
  validation.py
  candidates.py
  promotion.py
  recovery.py
  queries.py

src/morrow/application/memory/
  selection.py
  projection.py

src/morrow/adapters/state/
  learning_journal.py
  knowledge_journal.py
  memory_selection_journal.py
```

关键边界是：

```text
AgentLoop
  只消费 frozen MemorySelection
  不创建 Candidate
  不修改 Active Memory

LearningReviewCoordinator
  可以调用模型
  不能修改 Active 状态

LearningPromotionService
  唯一有资格把 Candidate 变成 Active 状态
```

---

# 十三、数据库切片建议

第一批表：

```text
learning_reviews
learning_review_attempts          # 可延后
learning_evidence
learning_candidates
learning_candidate_evidence
learning_suppressions
promotion_operations
preference_activations
project_knowledge_heads
project_knowledge_revisions
memory_selections
memory_selection_items
```

迁移可拆成：

```text
V10
- reviews
- evidence
- candidates
- suppressions

V11
- promotion operations
- preference activations
- project knowledge versions

V12
- memory selections
- lexical/FTS derived index
```

所有 mutable 行继续使用 `row_version`；所有语义历史使用 immutable revision 或 `supersedes_id`。

---

# 十四、推荐实施顺序

## Subplan 49：先做领域和持久化，不接模型

- 锁定分类和权威 ADR。
- 建立 Review、Evidence、Candidate、Suppression。
- 完成 V10 migration 和 journal adapter。
- 实现 FakeReviewer。
- 建立 fingerprint、scope 和 evidence eligibility。
- `review_only` 为唯一可用学习模式。

## Subplan 50：TaskOutcome 到 Candidate

- 在 `task_accept` 外层事务中创建 pending Review。
- 增加 claim/lease/retry。
- 确定性 EvidenceExtractor。
- 最小 LearningContext。
- FakeReviewer 生成严格 CandidateDraft。
- 同一 Outcome 重审去重并建立 supersedes。

## Subplan 51：Inbox 与 Project Knowledge

- inbox/show/accept/edit/reject/never-suggest。
- duplicate、conflict、expiry。
- ProjectKnowledge SQLite 晋升。
- Active Knowledge 查询和历史。

## Subplan 52：Preferences/Profile Promotion Saga

- `PreparedConfigurationChange`。
- PromotionOperation。
- YAML 写前、写后和 finalize 间的 crash injection。
- revision mismatch recovery。
- PreferenceActivation provenance。

## Subplan 53：Memory Query 与 Context

- `MemoryQuery`、`MemorySelection`。
- 词法检索和预算。
- selection reason codes。
- AgentRunSnapshot 集成。
- ContextBuilder 注入。

## Subplan 54：真实 Reviewer 和评估

- Live Reviewer opt-in。
- 敏感信息与 Prompt Injection 对抗集。
- CLI/REPL。
- SkillCandidate 与 WorkflowFeedback 只候选。
- 多任务长期试跑。

路线图本身也建议按 Schema/Policy、Candidate Pipeline、Promotion、Memory Query、Inbox、评估切片，并规定 Stage 5 不能创建 `SKILL.md` 或自动启用 Skill。

---

# 十五、必须守住的验收不变量

这些比“候选生成准确率达到多少”更重要：

```text
1. 不存在没有显式配置事件或 Accepted Candidate 的 Active 状态。

2. Assistant 输出、网页、工具输出或仓库内容，
   不能单独激活 Preference/Profile。

3. Candidate 的所有 Evidence 都真实存在且属于同一 Workspace。

4. Review 失败、Provider 超时或进程崩溃，
   不改变 TaskOutcome 和 accepted 状态。

5. 同一 command_id、outcome 和 review version 重放时不产生重复 Candidate。

6. YAML 写成功但 SQLite finalize 前崩溃，可以安全恢复。

7. YAML revision 已被用户修改时，Promotion 不覆盖用户状态。

8. prohibited/sensitive 内容不进入事件载荷；
   被拦截的 secret 最多持久化 digest 和拒绝原因。

9. Workspace A 的 Candidate、Knowledge、Selection
   永远不会出现在 Workspace B。

10. “这次”“临时”“举例”“他说……”“不要记住”
    等一次性、引用和否定场景不产生长期候选。

11. 删除和“不要再建议”是两种不同操作。

12. 每个 MemorySelection 都能解释：
    选中了什么、为什么、什么因预算被省略。
```

评估上应优先优化 **precision 而不是 recall**。少学一些可以接受，错误地把一次性要求、模型回答或注入内容变成永久状态则不可接受。

---

## 最终推荐

Stage 5 第一版不要从 Reviewer Prompt 开始，而应从以下三个设计决策开始：

1. **accepted TaskOutcome 同事务创建 pending Review。**
2. **YAML 继续作为 Profile/Preferences Active 权威，Project Knowledge 以 SQLite 为权威。**
3. **只有 Promotion Service 能改变 Active 状态，并使用可恢复 operation record。**

这三个边界稳定以后，Reviewer 换模型、增加分类、改 Prompt 或未来引入 Embedding，都只是可替换实现；反过来，若先做“模型自动总结并保存”，之后再补来源、冲突、事务和撤销，重构成本会非常高。

