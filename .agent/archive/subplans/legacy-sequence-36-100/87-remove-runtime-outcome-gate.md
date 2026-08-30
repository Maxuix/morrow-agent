# Subplan 87 — Remove Runtime Outcome Gate

> Status: completed and integrated locally at `63236ac`

## Goal

Let a valid model `stop` end the turn directly. Remove runtime-owned task-intent resolution and
pre-answer workspace/validation/verifier fact checks that can reject useful outputs.

## Required behavior

1. Production does not issue a separate OutcomeIntent model request.
2. AgentLoop does not prepare a workspace baseline for output validation.
3. A valid, non-empty tool-free model `stop` is committed and emitted without CompletionChecker.
4. Tool schema validation, permission checks, durable tool lifecycle, cancellation, provider errors,
   budget limits and unresolved tool-call repair remain unchanged.
5. `ValidationFact` remains execution telemetry only; it does not gate the final answer.
6. Old snapshots and operational rows containing completion fields remain readable. No destructive
   database downgrade or stored-state rewrite is introduced.

## Implementation boundary

- Remove active OutcomeContract/WorkspaceBaseline parameters from ordinary turn APIs and runtime
  composition where compatible.
- Retain the minimum legacy data models/columns needed to hydrate existing schema-v19 state, clearly
  marked as compatibility-only and never consulted for termination.
- Remove obsolete resolver/checker code and tests that assert runtime semantic judgment.
- Keep dynamic project-instruction pre-effect gates and scoped tool-failure tracking; these govern
  safe execution, not final-answer semantics.

## Validation

- Add production-composition tests proving change, explanation, failed validation and no-diff
  outputs all terminate according to the model's valid `stop`.
- Add recovery coverage proving old snapshots/rows remain readable but do not reactivate the gate.
- Run focused AgentLoop, preparation, observability and migration matrices, then the full offline,
  Ruff, compileall, CLI and diff gates.

## Boundaries

- No dependency, live Provider/network test, permission/effect expansion, public event change,
  runtime-policy default change or S7P-06 implementation.
- Preserve the untracked S7P-06 draft and three user-owned research documents.

## Validation evidence

- Focused AgentLoop, preparation, observability, migration and terminal matrix: `154 passed`.
- Full offline suite: `1254 passed, 2 skipped, 2 deselected in 63.16s`.
- The skips are host-only Seatbelt tests; the deselected cases are live tests.
- Ruff format/check, compileall, both CLI help entrypoints and `git diff --check` passed.
