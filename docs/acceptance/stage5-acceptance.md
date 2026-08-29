# Stage 5 实现与离线验收证据

> 日期：2026-08-21
> 状态：Preference v2 实现、最终集成审查、模拟用户与真实 Provider 验收均完成
> 范围：Stage 5 Learning、通用 Preference v2、Promotion、MemorySelection、doctor/backup 和产品入口

本文只记录当前实现能够证明的行为。离线 Fake/脚本 Reviewer 证明确定性边界，不证明真实模型的
自然语言分类质量。修复前问题及修复后回放均记录在[模拟用户测试报告](stage5-simulated-user-evaluation.md)；
Subplan 55 已关闭 F1/F2/F3。Live Provider 评估仍需用户显式授权和兼容凭据；本次未尝试网络或真实模型调用。

## 验收矩阵

| 场景 | 直接证据 | 结果 |
|---|---|---|
| accepted Task → pending Review → bounded Candidate | `tests/test_stage5_learning_application.py`、`tests/test_stage5_learning_evaluation.py` | 通过；零候选是合法结果，单次 Review 最多 3 个候选 |
| no-tool production Reviewer | `tests/test_stage5_learning_reviewer.py`、`tests/test_stage5_learning_evaluation.py` | 通过；请求/响应有界，最多一次修复，禁止工具和原始 provider 内容落盘 |
| Preference/Profile 显式确认后 Promotion | `tests/test_stage5_configuration_promotion.py`、`tests/test_stage5_learning_cli.py`、Subplan 55 隔离回放 | 通过；新进程 accept/edit/reject 与 OCC 预览确认均可用 |
| Project Knowledge 与 MemorySelection | `tests/test_stage5_project_knowledge.py`、`tests/test_stage5_memory_agent_run.py`、`tests/test_stage5_memory_context.py`、Subplan 55 隔离回放 | 通过；非整秒首次 Promotion、重启读取和 Memory revision=1 均通过 |
| Skill/Workflow/Orchestration future candidate | `tests/test_stage5_project_knowledge.py::test_future_candidate_acceptance_remains_candidate_only` | 通过；只记录 Candidate，不创建文件、工具、权限、Workflow 或运行时规则 |
| REPL/headless review/retry 与 policy controls | `tests/test_stage5_learning_cli.py`、`tests/test_review_worker.py`、`tests/test_preference_review_jobs.py` | 通过；SQLite queue 是 durable authority，进程内 worker 在提交后 wake，显式 run-pending 可恢复；无 daemon，`explicit-auto` 被拒绝 |
| workspace/restart/crash/OCC/replay | `tests/test_stage5_learning_store.py`、`tests/test_stage5_memory_agent_run.py`、`tests/test_stage5_configuration_promotion.py`、`tests/test_stage4_recovery_crash.py`、Subplan 55 隔离回放 | 通过；新进程状态、Doctor 和 backup verify 均通过 |
| Doctor 只读完整性检查 | `tests/test_stage5_doctor_backup.py`、`tests/test_stage4_doctor.py`、`tests/test_stage5_memory_inspection.py` | 通过；Review/Evidence/Candidate/Decision/Promotion/Knowledge/Memory 引用和 lease 状态可诊断，数据库 mtime 不变 |
| 隔离 SQLite backup/restore verification | `tests/test_stage5_doctor_backup.py`、`tests/test_stage4_backup.py` | 通过；Learning/Memory 状态保留，篡改决策摘要会使 verify 失败，YAML/凭据不在 bundle |

## Subplan 55 模拟用户回放

回放在临时状态根使用 3 个 accepted Task 和 5 个候选，真实新进程完成 accept、edit、Project Knowledge
edit、reject、reject-and-suppress；随后重新读取 Learning/Memory、运行 `state doctor --json`，并创建及验证
SQLite backup。结果为：`fresh_process=passed`、`knowledge_revisions=1`、`memory_revision=1`、
`doctor=ok`、`backup=verified`。回放没有访问网络或凭据，详细历史和边界见
[`stage5-simulated-user-evaluation.md`](stage5-simulated-user-evaluation.md)。

## Operational boundary

- `state doctor` 是只读诊断；它不会修复 Review lease、Candidate、YAML 或 Knowledge。
- `state backup` 使用 SQLite online backup 和 Artifact manifest；bundle 不包含 `config.yaml`、
  `workspace-index.yaml`、CredentialStore 或 Keychain。
- Preference/Profile 的 YAML authority 仍在独立状态文件中；SQLite activation provenance 不能单独
  重建 YAML，跨存储恢复必须继续使用既有 YAML 状态文件备份。
- Legacy accepted-Task Learning Review 与 Preference Review 均由进程内 `ReviewWorker` 在提交后处理；
  SQLite job/lease/retry 是 durable authority，但本阶段没有 daemon、`explicit_auto` 或
  natural-language candidate acceptance。

## Live hold point

预声明目标仍为：durable-candidate proposal precision ≥ 0.85；injection/secret/Assistant-only
安全负例的错误 durable proposal = 0；每次 Review 中位候选数 ≤ 1、最大 ≤ 3；拒绝/编辑案例仍可审查。

后续获得显式授权和兼容 Keychain credential 后，Reviewer v4 的隔离 real-Provider corpus 已通过：
positive operations `12/12`、precision `14/14`、targets `8/8`、安全误写 `0`、下一 AgentRun
adherence `10/10`。它没有写入用户真实 Learning store/YAML/project，也不以离线 27/27 代替
真实模型证据。详细记录见 [`stage5-live-evaluation-hold.md`](stage5-live-evaluation-hold.md)。

## Preference v2 implementation evidence

- 普通终态 Turn 在同一 SQLite 事务持久化 v13 Review job 与唯一当前用户 Evidence；进程内
  `ReviewWorker` 使用 lease、最多三次尝试和有界退避，Provider 工作不占用 SQLite 事务。
- no-tool Reviewer 只接收完整当前用户消息、有界 recent dialogue 和冻结 Active Preference snapshot，
  输出 0–8 个 `add/replace/remove`；推断结果只进入独立 Inbox，不能自动写 YAML。
- 明确管理通过受审批的 `manage_preferences`，Inbox 接受与直接管理共享同 scope 原子 Writer；
  `update_configuration` 仅管理 Workspace Profile，旧 `/config edit` fixed-field 入口已退役。
- 每个新 AgentRun 在 SQLite 准入事务前重载 global/workspace YAML，冻结 64-entry/8-KiB 低权限
  Preference 投影；同一 Run 和 recovery 只重用冻结内容。Preference 注入与 Project Knowledge
  MemorySelection 分别可观察。
- `tests/test_preference_evaluation.py` 的 versioned v2 corpus 包含 22 个自然语言 contract cases，
  其中恰好 12 个 positive intents、8 个 target cases；scripted evaluator 不调用 Provider、不写 Active
  状态，也不声称语义准确率。
- Doctor 检查 v13 job/Evidence/proposal/write-batch、snapshot/lease lifecycle 与 YAML/AgentRun 投影；SQLite backup verification
  独立报告 `preference_references_ok`。YAML 与凭据按权威边界不进入 SQLite bundle。

Stage 5 的 implementation、最终集成 review、模拟用户和 Reviewer v4 real-Provider corpus 均已完成。
旧真实 Provider 证据（natural-language `0/3`、remove `0/2`、Mimo timeout、恢复 Session stale
projection）保留为 v2 重构的历史 baseline，不覆盖当前通过证据。

## Offline command record

最终命令记录由 `.agent/TODO.md`、`.agent/TRACKER.md` 和 `.agent/LOG.md` 同步；标准门禁为：

```text
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run pytest -m 'not live'
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run python -m compileall -q src tests
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run morrow --help
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run morrow learning --help
UV_CACHE_DIR=/tmp/morrow-stage5-uv-cache uv run morrow memory --help
git diff --check
```

命令输出只在实际执行后写入执行状态；Live hold 不计入离线门禁。
