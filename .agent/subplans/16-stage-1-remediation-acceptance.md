# Subplan 16 — Stage 1 Remediation Acceptance and Truth Reconciliation

> Stage: 1B remediation
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)
> Depends on: Subplans 12–15

## Objective

Rebuild Stage 1 acceptance evidence from executable behavior, correct documentation drift, and decide Stage 2 readiness without relying on prior self-reports or overbroad test claims.

## Executable tasks

1. Build a finding-to-regression matrix covering every independently confirmed P1 and P2 item, its owning subplan, exact test, observed result, and documentation impact. No compound finding may be marked covered by testing only one branch.
2. Re-run and explicitly verify all `S1A-01` through `S1A-08` and `S1B-01` through `S1B-06` criteria against the implemented code.
3. Add/execute the previously missing ten-turn integration test, asserting ordered full history and stream deltas across all turns.
4. Execute genuine multiprocess tests for workspace claims, writer locking, revision conflicts, and failed/competing relinks. Any failure reopens Subplan 12 and cannot be treated as an evidence-only defect.
5. Exercise the complete Stage 1 CLI surface under NetworkGuard, including success/failure exit codes, corrupt/future state, missing credentials, preview decline, and read-only mode.
6. Run deterministic terminal coverage for typed `/exit`, closed stdin, Ctrl+D, Ctrl+C during chat, Ctrl+C during Handoff generation, dirty independent/continuation transitions, post-cancel conversation, and save failure preservation.
7. On the final code state, run and record the complete manual/Live checklist rather than inferring it from automation:
   - configure from empty state with a real OpenCode Go credential and stream visible text from the declared active model;
   - inspect the real stream boundary for visible text, reasoning isolation, one normal completion, and sanitized failure classification where safely testable;
   - complete ten real-terminal turns, long-response Ctrl+C, post-cancel chat, `/handoff update`, and Ctrl+D exit;
   - verify isolation in two projects and distinct Git worktrees, then move/relink a repository and retain its one ID/state;
   - force an offline continuation exit fallback, then start again and verify that the deterministic fallback/recovery Handoff is displayed and can be explicitly continued; do not confuse this valid fallback artifact with the corrupt/unsupported workspace degraded mode, where `/continue` is unavailable.
   Keep Live tests opt-in, never expose the credential, and record any unexecuted item as pending rather than passed.
8. Scan YAML, backups, captured terminal output, public events, Handoffs, and logs with credential sentinels; verify no project files or subprocesses are touched.
9. Run all quality gates from a clean environment: Ruff format/check, non-Live tests with strict markers and zero unexpected skips, compile checks, CLI help, package/import smoke, and Stage 2 boundary checks.
10. Update README recovery/troubleshooting and actual command semantics. Reconcile `docs/ROADMAP.md`, the Stage 1 roadmap, `docs/ARCHITECTURE.md`, acceptance evidence, PLAN, TODO, TRACKER, and LOG with observed results.
11. Keep Stage 2 blocked if any confirmed P1 remains, any Stage gate lacks executable/manual evidence, or documentation still overstates coverage. Only then mark Subplans 12–16 and Stage 1 complete.

## Required quality gates

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run pytest -m 'not live'` with zero failures and zero unexpected skips.
- Strict marker collection includes the explicit Live test without running it by default.
- Multiprocess, terminal/EOF, ten-turn, configuration preview, environment credential, read-only, lifecycle, and persistence regressions pass.
- The complete empty-state Provider, stream-shape, ten-turn terminal, two-project/worktree/relink, and offline-fallback-next-start evidence is recorded only if actually executed in the final code state.
- Credential-sentinel and Stage 2 boundary scans pass.

## Completion criteria

- Every confirmed P1/P2 finding is fixed or explicitly reclassified with evidence and user approval; no P1 remains open.
- Acceptance documents cite exact tests/manual runs that exercise the claimed behavior.
- Documentation status is internally consistent and matches the final observed code.
- Stage 2 is unblocked only by an explicit final readiness decision after all gates pass.

## Deliverables

- Complete remediation traceability and validation evidence.
- Accurate user/recovery documentation and architecture/status files.
- Evidence-backed Stage 1 completion or an explicit remaining-blocker report.
