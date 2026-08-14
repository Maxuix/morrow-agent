# Subplan 11 — Stage 1B Acceptance and Delivery

> Stage: 1B  
> Status: completed
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Verify the complete Stage 1 product and architecture contract, document safe operation and recovery, and produce an evidence-backed decision on readiness for Stage 2 implementation.

## Prerequisites

- Subplans 01 through 10 are complete.
- No earlier verification failure remains deferred.

## Tasks

1. Create a traceability table mapping every `S1A-*` and `S1B-*` requirement to automated tests or named manual/Live evidence.
2. Run formatting, linting, all non-Live tests, and unexpected-skip checks from a clean test environment.
3. Confirm the network guard proves default verification makes no external request.
4. Run the real-terminal checklist: continuous conversation, ordered streaming, long-response cancellation, post-cancel recovery, and EOF-driven handoff exit.
5. Run the explicit OpenCode Go Live checklist and inspect sanitized response shape, visible text, reasoning isolation, completion, and error mapping.
6. Test two isolated projects, separate worktrees, a moved project with relink, state corruption, revision conflict, and an offline-exit deterministic fallback.
7. Interleave global Preferences edits with Provider add/configure operations and prove that one aggregate revision advances without either domain losing fields.
8. Scan YAML, captured terminal output, events, handoffs, test artifacts, and any emitted logs with credential sentinels. If file logging is disabled, record that fact and verify no unexpected log file was created.
9. Write README/setup documentation covering installation, first Provider setup, `--dir`, commands, data ownership/location, offline versus network behavior, limitations, and safe recovery/troubleshooting.
10. Review the realized repository structure against `docs/ARCHITECTURE.md`; update architecture only if actual responsibilities changed.
11. Confirm that no prohibited Stage 2+ capability or unused future module has entered the implementation.
12. Record validation commands, Live/manual results, known limitations, and the Stage 2 readiness decision in `.agent/LOG.md`.
13. Update the roadmap, plan index, TODO, and tracker only after all gates pass.

## Required quality gates

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run pytest -m 'not live'` with zero failures and zero unexpected skips.
- All failed-write tests prove the old document remains valid or byte-preserved as specified.
- All output/data surfaces pass credential-sentinel scanning.
- Explicit Live and manual acceptance records are complete.

## Completion criteria

- All `S1A-01` through `S1A-08` and `S1B-01` through `S1B-06` gates pass.
- Documentation matches observed behavior and names limitations honestly.
- There are no unresolved P0/P1 defects in Stage 1 scope.
- Stage 1 is marked complete and Stage 2 implementation is explicitly unblocked.

If any criterion fails, Stage 1 remains active and the failure is routed back to the owning subplan. Passing a subset of tests, reaching a schedule target, or relying on a model self-report is not acceptance.

## Deliverables

- Complete offline, Live, terminal, recovery, and security validation evidence.
- User-facing setup and troubleshooting documentation.
- Updated execution state and an explicit Stage 2 implementation readiness decision.
