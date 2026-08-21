# Stage 5 实现与离线验收证据

> 日期：2026-08-21
> 状态：离线实现验收完成；真实 Provider 质量评估 pending
> 范围：Subplans 49–54 的可审查 Learning、Promotion、MemorySelection、doctor/backup 和产品入口

本文只记录当前实现能够证明的行为。离线 Fake/脚本 Reviewer 证明确定性边界，不证明真实模型的
自然语言分类质量。Live Provider 评估需要用户显式授权和兼容凭据；本次未尝试网络或真实模型调用。

## 验收矩阵

| 场景 | 直接证据 | 结果 |
|---|---|---|
| accepted Task → pending Review → bounded Candidate | `tests/test_stage5_learning_application.py`、`tests/test_stage5_learning_evaluation.py` | 通过；零候选是合法结果，单次 Review 最多 3 个候选 |
| no-tool production Reviewer | `tests/test_stage5_learning_reviewer.py`、`tests/test_stage5_learning_evaluation.py` | 通过；请求/响应有界，最多一次修复，禁止工具和原始 provider 内容落盘 |
| durable/temporary/negative/quoted/hypothetical/Assistant-only 分类 | `tests/test_stage5_learning_evaluation.py`、`src/morrow/resources/stage5-learning-evaluation.json` | 26/26 离线案例通过；安全负例 5 个，Active 写入 0 |
| Preference/Profile 显式确认后 Promotion | `tests/test_stage5_configuration_promotion.py`、`tests/test_stage5_learning_cli.py` | 通过；YAML 仍是权威，PromotionOperation 可恢复，`/accept` 不接受 Candidate |
| Project Knowledge 与 MemorySelection | `tests/test_stage5_project_knowledge.py`、`tests/test_stage5_memory_agent_run.py`、`tests/test_stage5_memory_context.py` | 通过；版本化 SQLite Knowledge、预算、解释、同一 Run 冻结与恢复复用 |
| Skill/Workflow/Orchestration future candidate | `tests/test_stage5_project_knowledge.py::test_future_candidate_acceptance_remains_candidate_only` | 通过；只记录 Candidate，不创建文件、工具、权限、Workflow 或运行时规则 |
| REPL/headless review/retry 与 policy controls | `tests/test_stage5_learning_cli.py`、`tests/test_stage5_learning_cli.py::test_repl_learning_mode_is_explicit_and_rejects_explicit_auto` | 通过；前台执行，无 worker/scheduler，`explicit-auto` 被拒绝 |
| workspace/restart/crash/OCC/replay | `tests/test_stage5_learning_store.py`、`tests/test_stage5_memory_agent_run.py`、`tests/test_stage5_configuration_promotion.py`、`tests/test_stage4_recovery_crash.py` | 通过；工作空间隔离、重启恢复、YAML/SQLite 边界和命令 replay 有证据 |
| Doctor 只读完整性检查 | `tests/test_stage5_doctor_backup.py`、`tests/test_stage4_doctor.py`、`tests/test_stage5_memory_inspection.py` | 通过；Review/Evidence/Candidate/Decision/Promotion/Knowledge/Memory 引用和 lease 状态可诊断，数据库 mtime 不变 |
| 隔离 SQLite backup/restore verification | `tests/test_stage5_doctor_backup.py`、`tests/test_stage4_backup.py` | 通过；Learning/Memory 状态保留，篡改决策摘要会使 verify 失败，YAML/凭据不在 bundle |

## Offline quality result

版本化数据集和确定性 evaluator 见
[`stage5-offline-evaluation.md`](stage5-offline-evaluation.md)，当前结果为 26/26、5 个安全负例、
0 个安全负例 Active 写入。报告只保存 case ID、状态、reason/safety code 和计数，不保存源文本、
raw Reviewer 输出或合成凭据值。

## Operational boundary

- `state doctor` 是只读诊断；它不会修复 Review lease、Candidate、YAML 或 Knowledge。
- `state backup` 使用 SQLite online backup 和 Artifact manifest；bundle 不包含 `config.yaml`、
  `workspace-index.yaml`、CredentialStore 或 Keychain。
- Preference/Profile 的 YAML authority 仍在独立状态文件中；SQLite activation provenance 不能单独
  重建 YAML，跨存储恢复必须继续使用既有 YAML 状态文件备份。
- Learning Review 是前台、显式、一次性的；没有后台 worker、自动 retry、`explicit_auto` 或
  natural-language candidate acceptance。

## Live hold point

预声明目标仍为：durable-candidate proposal precision ≥ 0.85；injection/secret/Assistant-only
安全负例的错误 durable proposal = 0；每次 Review 中位候选数 ≤ 1、最大 ≤ 3；拒绝/编辑案例仍可审查。

本次没有显式 Live 执行授权，也没有在当前请求中选择兼容 Provider credential，因此未运行
`pytest -m live`、未联网、未写入用户真实 Learning store/YAML/project。真实模型质量评估保持 pending，
不将离线 26/26 结果描述为真实模型质量通过。

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
