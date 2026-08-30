# Subplan 47 — Stage 4 Real-User-Test Remediation

> Status: completed
> Prerequisite: Subplan 46 completed; real-user report `docs/reviews/stage-4-real-user-test-report.md`
> Owns: RUT-001 through RUT-008 correctness, diagnostics, CLI discoverability, and regressions

## Objective

Close the integrated real-user findings without widening Stage 4 capability scope: prevent
cross-workspace Artifact loss, make Fork children usable, enforce Session lifecycle invariants,
restore meaningful optimistic timestamps, and make maintenance/query CLI behavior truthful.

## Locked invariants

- `ConversationLog` remains the only chat-history writer and ordinary chat remains on
  `AgentLoop.run_task()`.
- Cleanup must fail closed unless a target is globally unowned and still a safe private regular
  file at operation time. Apply may only atomically rename it into a retained private quarantine;
  it never unlinks or truncates the original bytes.
- Fork creation starts with no inherited current task, while the persisted child may create and own
  later tasks normally.
- Archived Sessions cannot retain or create an active current task; lifecycle changes remain
  receipt-backed and transactional.
- Ordinary Turn/Task work requires an active Session with health OK; recovery remains a narrow
  special path.
- Every observable Session mutation advances one injected-clock, outer-transaction-scoped,
  strictly monotonic `updated_at` stale token.
- No schema, dependency, policy-default, public-event schema/contract expansion, credential,
  network, or Stage 5 change; the pre-start error path is repaired to satisfy the existing
  started/completed contract.

## Tasks

- [x] S4.47.1 Reproduce and fix cross-workspace Artifact cleanup and managed-path diagnostics.
- [x] S4.47.2 Restore Fork child task/turn continuation through the production boundary.
- [x] S4.47.3 Enforce archived Session/task/health invariants and diagnose contradictory state.
- [x] S4.47.4 Make Session `updated_at` a reliable transaction-scoped mutation/stale token.
- [x] S4.47.5 Expose CLI pagination, truthful Doctor exit status, and stable domain errors.
- [x] S4.47.6 Run final focused real-user regressions and all Stage 4/offline quality gates.
- [x] S4.47.7 Close resolved-report repeat-resume side effects and rerun final gates.

## Validation

```bash
uv run pytest -q tests/test_operational_store.py tests/test_stage_boundary.py \
  tests/test_conversation_and_loop.py tests/test_stage4_application_api.py \
  tests/test_stage4_artifacts.py tests/test_stage4_cleanup.py \
  tests/test_stage4_cli_operational.py tests/test_stage4_context_fork.py \
  tests/test_stage4_doctor.py tests/test_stage4_journal.py \
  tests/test_stage4_permissions.py tests/test_stage4_recovery_crash.py \
  tests/test_stage4_session_conversation.py tests/test_stage4_task_outcome.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

## Completion gate

Each supplied RUT finding has either a regression-backed fix or an explicit documented disposition;
the three blocker/high lifecycle paths pass through production-facing boundaries; full offline and
quality gates pass; verified progress is committed before the subplan is closed.

## Completed gate state

- focused RUT/Stage 4 regressions (14 files): `199 passed in 5.83s`;
- full host-level non-live suite: `663 passed, 1 deselected in 12.26s`, `0 skipped`;
- Ruff format: `164 files already formatted`; Ruff check passed;
- compileall, main CLI help, cleanup CLI help, and `git diff --check`: exit 0;
- cleanup help truthfully says apply moves candidates into private quarantine and does not destroy
  original bytes;
- original independent reviewer re-review: no remaining P0/P1.
