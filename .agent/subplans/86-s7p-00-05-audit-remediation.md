# Subplan 86 — S7P-00–05 Post-audit Reliability Remediation

> Status: verified; local fast-forward integration pending

## Goal

Close every confirmed post-audit defect without extending S7P-06 scope. Replace lexical Outcome
Contract guessing with a bounded structured semantic intent resolver; preserve immutable AgentRun
admission evidence; make project instructions effective before the first scoped write; and align
completion, validation, workspace, telemetry and cached-plan lifecycles around shared contracts.

## Confirmed defects

1. Keyword/regex intent inference produces both false change obligations and false no-change
   results. Quoted prose and commands can still become path allowlists.
2. Dynamic nested project instructions are discovered only after a tool cycle, so a first write can
   occur without its scoped rules. Refresh failures do not stop further effects, and more than eight
   same-cycle directories are silently lost.
3. The attempted dynamic refresh overwrites the supposedly immutable AgentRun snapshot, making
   earlier Provider requests appear to have seen evidence that was discovered later.
4. A successful unrelated tool cycle clears every prior failure. Completion can therefore accept a
   partially failed multi-target task.
5. Intent-side validation declarations and runtime argv recognition use divergent validator and
   option registries.
6. Raising the full-workspace manifest cap only moves the failure threshold and expands the global
   AgentRun snapshot budget from 64 KiB to 2 MiB, contrary to the durable execution ADR.
7. Completion correction telemetry estimates only content characters instead of the actual wire
   request.
8. Mutation previews are removed only after a successful apply; rejection, conflict, cancellation
   and other terminal paths retain cached plans.

## Design decisions

- Natural-language intent is resolved through a dedicated no-tool structured model request and a
  strict bounded schema. Production does not use lexical keyword/regex fallbacks. Explicit trusted
  Outcome Contracts remain authoritative; resolver failure is explicit and fail-closed.
- The intent request participates in normal AgentRun request observation. Its validated result is
  stored as append-only run evidence, not by replacing AgentRunSnapshot.
- Project-instruction scope expansion is an append-only, versioned run projection. A write to a new
  scope is deferred before effect, the projection is persisted, and the model must reconsider the
  write with the new rules. Read-only discovery may refresh before the next request. Refresh errors
  stop the run before more scoped effects.
- Validator kinds, wrappers, safe flags and scope parsing come from one shared registry consumed by
  both structured intent validation and process fact recognition.
- Known failures are tracked as scoped obligations. Only a compatible successful operation closes
  an obligation; unrelated success cannot erase it.
- Git workspaces baseline clean tracked state from Git and retain only bounded pre-existing
  dirty/untracked evidence. The non-Git manifest remains explicitly bounded. Restore the locked
  64 KiB AgentRun snapshot ceiling; larger bounded evidence lives behind dedicated durable rows.
- Request telemetry uses the exact value returned by ContextBuilder validation.
- Per-call cached resources have a generic terminal cleanup hook executed for success, rejection,
  failure, timeout and cancellation.

## Ordered execution

1. Update active planning state and lock failing semantic-intent, validator, obligation, prompt
   pre-effect, immutable-evidence, baseline, telemetry and cleanup tests.
2. Implement structured intent resolution and append-only durable intent/projection evidence.
3. Add the project-instruction pre-effect gate and recovery-safe projection rehydration.
4. Centralize validator specifications and implement scoped unresolved-failure obligations.
5. Replace Git full-tree baselines, restore the 64 KiB snapshot boundary and update stale ADR/
   architecture text.
6. Use exact request estimates and generic terminal resource cleanup.
7. Run focused matrices, migration/backup/recovery tests, full offline/static/CLI gates and commit
   coherent verified progress.

## Boundaries

- No third-party dependency, live Provider/network/Pi/MCP/credential test, S7P-06 behavior,
  runtime-policy default, permission/effect expansion or public AgentEvent type/field change.
- No raw user prompt, model response, command argv/output, project instruction body, secret,
  reasoning or traceback in durable evidence.
- Session-owned ConversationLog remains the only chat-history writer.
- Preserve the untracked S7P-06 draft and the three user-owned research documents.

## Acceptance

The audit reproductions and adversarial variants pass through production composition; the first
scoped write cannot precede applicable instructions; historical request-to-projection evidence is
unambiguous; large clean Git repositories do not require a full-tree snapshot; all terminal tool
paths release cached plans; focused and full offline gates pass; changes are committed on the
dedicated branch with execution state updated.

## Validation evidence

- Remediation and adjacent focused matrix: `79 passed`.
- Migration, backup and recovery matrix: `98 passed`.
- Final focused matrix after scoped-obligation hardening: `73 passed`.
- Full offline suite: `1282 passed, 2 skipped, 2 deselected in 61.15s`.
- Ruff format/check, compileall, `morrow --help`, `python -m morrow --help` and
  `git diff --check` passed.
- No live Provider/model/Pi/MCP/network/credential test ran.
