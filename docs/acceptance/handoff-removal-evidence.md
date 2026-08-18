# Handoff Removal Acceptance Evidence

> Evidence date: 2026-08-17
> Status: final mandatory acceptance complete
> Historical authority: Git commit `cbc3d6d` 中当时的 `.agent/PLAN.md` 与 `.agent/TRACKER.md`；
> 当前同名文件是后续工作的 living documents

## Requirement matrix

| Requirement | Evidence | Observed result |
|---|---|---|
| No runtime/startup/context/config/state Handoff path | source scan; stage-boundary tests | Zero production-source matches; focused tests pass. |
| Removed commands are ordinary unknown commands | preferences/orchestration and product acceptance tests | Exact `/handoff` and `/continue` results pass with no action/value. |
| Dirty `/new` is explicit discard only | terminal tests | Confirm resets process-local Session; cancel/EOF preserve it. |
| Dirty `/exit` is explicit discard only | terminal tests | Confirm exits 0; cancel stays; confirmation EOF exits 2. |
| No model or state write during reset/exit | simplified `run_repl` signature and scripted tests | Terminal owns no Provider/store dependency; tests pass. |
| Legacy primary/backup ignored and preserved | legacy sentinel test | Corrupt/future bytes remain identical; Profile write succeeds. |
| Degraded-state isolation | orchestration/state tests | Profile corruption is workspace-read-only; Preferences corruption isolates one layer; legacy files do neither. |
| Domain/store/package excision | stage-boundary scan and wheel inventory | Types/methods/module absent; wheel has no Handoff entry. |
| Generic structured and state infrastructure survives | structured/state suites | Repair/deadline/projection and Profile/Preferences safety pass. |

## Command and exit matrix

| Input/state | Expected and observed |
|---|---|
| `/handoff`, `/continue` | `未知命令` with no action/value |
| clean `/new` | reset process-local Session |
| dirty `/new`, confirm/cancel | reset / remain |
| clean `/exit` or EOF | exit 0 |
| dirty `/exit`, confirm/cancel | exit 0 / remain |
| EOF during discard confirmation | exit 2; no reset/write |

## Current continuity boundary

Persisted: workspace identity, Profile, global/workspace Preferences, Provider configuration, and credential
references. Process-local only: Session-owned ConversationLog. Persistent Sessions, resume/list/archive/delete,
Fork, summaries/checkpoints, and memory are deferred to Stage 4. No legacy import or deletion is promised.

## Final observed gates

- Accepted source baseline: commit `831c4ea`; final result is the current reviewed worktree on that baseline.
- Final focused product/boundary/legacy suite: 18 passed.
- Final Agent-core/capability regression suite: 100 passed.
- Final offline suite: 287 passed, one explicit Live test deselected.
- Strict collection: 288 tests, including the opt-in Live test.
- Ruff format: 69 files formatted; Ruff lint, compileall, CLI help, Markdown link audit, and
  `git diff --check`: passed.
- Precise production-source scan: zero matches. Reviewed test allowlist contains only
  `test_preferences_and_orchestration.py`, `test_stage2_product_acceptance.py`,
  `test_stage_boundary.py`, `test_context_projections.py`, and `test_context_runtime.py`
  for legacy/negative assertions.
- Rebuilt `morrow_agent-0.1.0-py3-none-any.whl`: 44 entries, zero forbidden entries.
- Fresh CPython 3.12.13 environment: offline install of 33 packages, package import,
  bundled `agent-policy.toml` discovery/load, removed public-symbol check, and installed
  `morrow --help` all passed.
- Optional Live credential was absent. Live was not run and no Live pass is claimed.

Every mandatory removal, regression, package, documentation, and capability branch is green.
