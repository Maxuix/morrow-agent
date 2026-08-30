# Subplan 55 — Stage 5 模拟用户测试修复

> 状态：S55.1–S55.5 已完成；Live model-quality hold pending
> 输入：`docs/acceptance/stage5-simulated-user-evaluation.md`
> 基线：本地 `main`，`5cfb99f`
> 实施分支：`fix/stage5-simulated-user-remediation`
> 范围：Candidate 决策 CLI、拒绝预览、Project Knowledge 首次晋升时间精度、回归与复测

## 1. 目标

恢复 Stage 5 的真实用户闭环：用户能够通过 headless CLI 预览并确认 accept、edit、reject、
reject-and-suppress；全新 Project Knowledge 能在真实非整秒时钟下完成首次晋升、进入 Memory，且
重启、doctor 与 backup 后仍保持一致。

本子计划只修复模拟用户测试已经证明的正确性和 UX 缺陷。它不增加 schema、依赖、后台任务、
自动激活、自然语言接受、Skill/Workflow 激活或 Live Provider 行为。

## 2. 问题裁决

| 编号 | 裁决 | 代码证据 | 修复范围 |
|---|---|---|---|
| F1 / P1 | 成立，并同时影响 `accept`、`edit`、`reject` | `_candidate_or_error()` 返回 `LearningCandidateView`，三个命令却读取顶层 `candidate_id` / `row_version` | 修正 Typer adapter 的 typed view 使用；命令 OCC token 取自用户实际看到的 preview |
| F2 / P1 | 成立 | 新 head 以微秒时间创建，SQLite 按秒落盘；同事务 save 用原内存对象与已规范化对象比较 | 明确 Project Knowledge 时间的持久化精度，并让 create→save 使用持久化返回对象 |
| F3 / P2 | 成立 | reject 调用默认 accept preview；`decision_kind` 固定为 `accept` / `edit_and_accept` | preview 接受显式决策意图，reject 与 never-suggest 显示真实 kind |
| F1 邻接缺陷 / P2 | 成立，报告未单列 | Project Knowledge `--statement/--semantic-key/--category` 的 guard 在参数存在时反而报错 | 修正专用 edit 参数守卫并纳入同一 CLI 回归 |
| F4 环境限制 | 不属于产品 bug | 默认状态根被当前嵌套沙箱阻止；显式隔离 `--state-root` 后功能可运行 | 不修改产品默认路径或暴露新的公开配置面 |

## 3. 锁定合同

### 3.1 Candidate view 与 OCC

- `_candidate_or_error()` 的返回类型明确为 `LearningCandidateView`；变量命名使用 `view`，领域对象只从
  `view.candidate` 取得。
- accept、edit、reject 的最终命令使用 preview 中的 `candidate_id` 和
  `expected_row_version`。确认前显示的 preview 与确认后提交的 OCC token 必须是同一份事实。
- preview 之后发生并发更新时仍由既有 stale 检查拒绝；CLI 不刷新后静默覆盖。
- 取消确认不得写 Candidate decision、suppression、YAML、Knowledge 或 Memory。

### 3.2 决策预览

- `preview_learning_candidate_decision()` 增加有界的显式决策意图；默认仍为 accept，保持既有调用兼容。
- `edit` 必须生成 `edit_and_accept`；普通 reject 生成 `reject`；`--never-suggest` 生成
  `reject_and_suppress`。
- reject preview 只判断 Candidate 是否仍为 proposed、未过期和可由当前 workspace 操作；它不得复用
  accept 专属的 target conflict、duplicate 或 suppression 可激活判定。
- 为避免新增一套平行 preview DTO，继续使用现有 typed preview；reject 的 `after` 仅承载“被拒绝的
  Candidate 提议快照”，renderer 必须按 decision kind 标注，不能把它描述为 Active 状态变更。
- REPL 文本预览明确显示 decision kind。reject 预览使用“将拒绝的提议值”语义，不声称会产生 Active
  after value；never-suggest 还要明确说明会创建 suppression。
- 领域决策、事件与 receipt 的 kind 继续由既有 `LearningDecisionService` 写入，不在 UI 层复制第二套
  authority。

### 3.3 Project Knowledge 时间精度

- SQLite 的当前持久化精度仍为整秒；不做 schema migration。
- `put_project_knowledge_head()` 返回的规范化对象是后续同事务更新的输入；Promotion 必须接住该返回值。
- `save_project_knowledge_head()` 对 immutable `created_at` 按实际持久化精度比较。跨秒修改仍必须以
  `knowledge head identity is immutable` 拒绝。
- `updated_at`、row version、current revision、Evidence link、Memory revision 和事件仍在同一事务内完成；
  修复不得把 model call、YAML 或额外 I/O 放进事务。

## 4. 实施任务

### S55.1 先建立失败回归

在改生产代码前增加能够在 `5cfb99f` 上失败的测试：

1. 使用真实 `LearningCandidateView` 和 Typer `CliRunner`，确认后执行 Preference accept。
2. 覆盖 Preference/Profile edit、Project Knowledge 专用字段 edit、普通 reject、
   `reject --never-suggest`；禁止用伪造顶层 `candidate_id` 的 `SimpleNamespace` 掩盖 DTO 形状。
3. 断言 preview 的 `decision_kind` 与最终 decision 一致，取消确认保持零写入。
4. 在 preview 后制造 row-version 变化，断言 CLI 返回受控 stale/exit 2、无 traceback 且不覆盖新状态。
5. 用非零微秒时钟执行全新 Project Knowledge 首次 Promotion，复现 identity immutable 回滚。
6. 保留一个跨秒篡改 `created_at` 必须失败的 journal 负例，防止修复放松真实 identity 守卫。

### S55.2 修复 Candidate CLI 与拒绝预览

1. 在 `src/morrow/interfaces/learning_cli.py` 中统一 `view` / `subject` 使用，candidate ID、row version
   改从 preview 取得。
2. 修正 Project Knowledge 专用 edit 参数的反向 guard；其他 Candidate 类型继续拒绝不属于自己的字段。
3. 在 `LearningApplicationService` / API facade 中加入显式 decision intent，不增加第二个决策服务。
4. Typer 与 REPL reject 都传递 `reject` 或 `reject_and_suppress`；文本预览显示实际动作和 suppression
   后果。
5. 聚焦测试同时断言 exit code、用户输出、Candidate 状态、decision kind、suppression 和 Active 状态。

### S55.3 修复 Knowledge 首次晋升时间精度

1. Promotion 创建 head 后立即使用 `put_project_knowledge_head()` 返回的规范化对象。
2. Journal 的 immutable 时间比较使用同一整秒 codec；保持 workspace、semantic key、category 与跨秒
   `created_at` 的严格不可变性。
3. 增加非零微秒时钟的首次激活集成测试，断言 Candidate accepted、head active、revision 1、Evidence
   link、Memory revision 1、lexical terms 和 `memory.record_activated` 事件全部存在。
4. 重开 SQLite 后复查 head/revision/Memory，证明修复不是只在进程内成立。

### S55.4 复跑模拟用户流程并更新证据

使用新的临时 workspace 和独立 `--state-root`，不读取真实凭据或用户状态：

1. 创建并接受 2–3 个简单 Task，运行 scripted Reviewer，检查 Review/Inbox 持久化。
2. 通过真实 headless CLI 分别完成 accept、edit、reject、never-suggest；至少一个 Preference 写入 YAML，
   一个 Project Knowledge 进入 `memory list`。
3. 新进程复查 Learning policy、Candidate decisions、suppression、Preferences、Knowledge 和 Memory。
4. 运行 doctor、backup、verify-backup；要求 Learning/Memory references 通过且 credentials excluded。
5. 更新原模拟用户报告、Stage 5 acceptance、roadmap、PLAN/TODO/TRACKER/LOG。只有复测通过后才能恢复
   “Stage 5 用户可用”的表述；Live 模型质量仍保持独立 hold。

### S55.5 独立 review 与收尾

实现与聚焦门禁通过后，对整个 Subplan 55 执行一次 Grok review，等待完整结果，独立核实并修复
存在且有采用价值的问题一次；review-fix 后不再发起第二次 Grok review。Grok 确认主修复正确，指出
REPL `/learn edit` 首屏仍显示 `accept`；修复后补充字段守卫负例和 preview 回归。最终离线门禁已通过，
接下来提交已验证状态，fast-forward 合并到本地 `main` 并删除 clean topic branch。未经明确授权不 push。

## 5. 主要文件边界

| 领域 | 预计文件 |
|---|---|
| Headless Candidate CLI | `src/morrow/interfaces/learning_cli.py` |
| REPL preview | `src/morrow/application/learning/interaction.py` |
| Typed preview/API | `src/morrow/application/learning/inbox.py`、`src/morrow/application/api.py`、`src/morrow/core/learning_views.py` |
| Knowledge Promotion | `src/morrow/application/learning/promotion.py` |
| SQLite time invariant | `src/morrow/adapters/state/learning_memory_journal.py` |
| 回归 | `tests/test_stage5_learning_cli.py`、`tests/test_stage5_project_knowledge.py`、`tests/test_stage5_learning_store.py` |
| 验收证据 | `docs/acceptance/stage5-simulated-user-evaluation.md` 及 Stage 5 状态文档 |

不得把修复集中到新的通用 helper/god file；每个合同由其现有 owner 负责。除非测试证明必要，不修改
`LearningDecisionService`、migration、ContextBuilder、Provider、Tool、Capability 或 public event lifecycle。

## 6. 验收矩阵

| 门禁 | 通过条件 |
|---|---|
| Typer accept/edit/reject | 确认后 exit 0；真实 typed view 不触发 AttributeError |
| Preview 后并发变化 | 受控 stale/exit 2；无 traceback、无覆盖写 |
| Reject preview | kind 分别为 `reject` / `reject_and_suppress`，最终 decision 完全一致 |
| Project Knowledge edit | 专用字段可编辑；跨类型字段稳定拒绝 |
| 微秒首次 Promotion | 新 head、revision、Evidence、Memory、terms、event 同事务完成 |
| Identity guard | 同秒序列化往返可保存；跨秒 created_at 修改仍拒绝 |
| Restart | 新进程读取到一致 Candidate/YAML/Knowledge/Memory 状态 |
| Doctor/backup | health OK；Learning/Memory references 与 manifest 全部通过 |
| 模拟用户复测 | 原 F1/F2/F3 不再复现，无新增 P0/P1 |

## 7. 验证命令

```bash
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run pytest -q \
  tests/test_stage5_learning_cli.py \
  tests/test_stage5_project_knowledge.py \
  tests/test_stage5_learning_store.py \
  tests/test_stage5_doctor_backup.py
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run pytest -m 'not live'
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run python -m compileall -q src tests
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run morrow --help
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run morrow learning --help
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run morrow memory --help
git diff --check
```

不运行 `pytest -m live`，除非用户另行显式授权并提供兼容凭据。

## 8. 完成条件

- F1、F2、F3 及同源 Project Knowledge edit guard 缺陷全部有先失败后通过的回归证据。
- 原模拟用户流程在新的隔离环境中完整通过，持久化、doctor 与 backup 结果可复查。
- Stage 5 状态文档与实际用户可用性一致，不用旧自动化结果覆盖新的模拟用户证据。
- 一次独立 Grok review/fix 和完整非 Live 门禁完成；所有验证代码进入本地 `main`，topic branch 退休。
- Live model quality 仍明确标为 pending，不被本次 scripted/offline 复测替代。
