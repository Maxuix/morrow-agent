# Subplan 84 — S7P-05 Validation and Completion Truth

## Goal

Replace command-derived completion claims with scoped validation facts and an evidence-backed final
stop gate. A change task must show an attributable net diff, required checks must be recognized and
pass in scope, path violations and unresolved calls must be explicit, and an optional verifier must
remain the authority.

## Reproduced baseline

- `ToolRunContext.metrics()` returns `passed` for an `opaque` exit-zero command fact.
- A scripted `Please edit src/example.py` run returns ordinary `stop`, writes assistant history and
  records no change fact or diff.
- Process classification has no validator/scope metadata; AgentLoop has no Outcome Contract,
  workspace baseline, completion checker, correction feedback or verifier boundary.

## Deliverables

1. Strict `ValidationFact`/requirement/contract/check-result models with bounded safe fields.
2. Conservative pytest/Ruff/compile/static/build command classification and normalized scope.
3. Frozen pre-run Outcome Contract and Git-aware/no-follow fallback workspace baseline evidence.
4. Net-change, validation, path, unresolved-call, known-failure and verifier completion checking.
5. Buffered final candidates, one fact-only correction, precise stop codes and legal history.
6. Fresh/resumed durable contract evidence and bounded scheduler-facing terminal aggregates.
7. Terminal/architecture documentation and deterministic scripted Direct-agent acceptance evidence.
8. Same-task Luna Max read-only review, confirmed-finding fixes, focused/full offline validation and
   clean commits ready for root fast-forward integration.

## Ordered execution

1. Lock failing command-versus-validation and aggregation tests.
2. Implement conservative validator recognition and safe scoped facts.
3. Lock contract compiler, explicit contract and baseline attribution tests.
4. Implement contract/baseline preparation plus durable freeze/rehydration.
5. Lock completion/verifier/correction/stop-code/history tests.
6. Integrate the completion gate and terminal/observability projection.
7. Add scripted product acceptance, documentation and migration compatibility coverage.
8. Run focused gates, then full offline/static/CLI gates and commit coherent progress.
9. Spawn a read-only `gpt-5.6-luna` / `max` reviewer in this implementation task; fix every
   confirmed finding, rerun affected/full gates and commit final evidence.

## Required matrices

- Utility exit 0/1; recognized validator pass/fail/timeout/cancel; wrapper and ambiguous shell;
  scope match/mismatch; fail-then-pass and unrelated failure.
- Change/explanation/unspecified contracts; explicit target/allow/forbid; clean/dirty/untracked Git;
  pre-existing dirty content mutation; symlink, scan truncation and non-Git fallback.
- Empty/relevant/reverted diff; unexpected/forbidden paths; missing/failed validations; unresolved
  call; verifier pass/fail/inconclusive/absent; one correction and second rejection.
- Fresh and resumed durable runs, old snapshot/row compatibility, public lifecycle exactness,
  cancellation and no rejected-final chat append.

## Exclusions

- Business-semantic correctness inference, autonomous review/workflow scheduling, S7P-06+.
- Dependencies, live Provider/network tests, runtime-policy or permission/effect changes.
- New public event types/fields, a second history writer, or persisted raw commands/output/content.

## Acceptance

All S7P-05 checklist conditions and the matrices above pass through production composition. The
implementation task has a formal same-task Luna Max review with all confirmed findings fixed, full
offline/static gates recorded, a clean topic branch, and no changes to the three user research docs.
