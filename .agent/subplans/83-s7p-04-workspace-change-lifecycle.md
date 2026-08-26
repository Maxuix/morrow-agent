# Subplan 83 — S7P-04 Workspace Change Lifecycle

> Status: complete; verified, review-repaired and ready for root integration
> Branch: `codex/feat/s7p-04-workspace-change-lifecycle`
> Base: `main@20e6ce3`

## Goal

Add conflict-safe regular-file delete, move and rename operations plus truthful sandbox promotion
and recovery evidence, without widening the workspace, silently overwriting user data or claiming
multi-operation atomic success.

## Deliverables

1. Strict structured delete/move/rename tools and domain results beside create/patch/replace.
2. Confined unlink and atomic no-replace move primitives with stable locks, SHA conflicts,
   no-symlink/no-directory behavior and mandatory approval.
3. Ordered delete/two-path durable evidence with expected-absence recovery classification.
4. Eligible sandbox delete and unambiguous move/rename promotion with partial-failure truth.
5. Production scripted acceptance, architecture updates and complete offline evidence.

## Owned areas

- Local tool domain/schema/fact models, factories and production registration.
- Workspace mutation and filesystem adapter publication primitives.
- Sandbox change modelling, collection, selection and promotion.
- Prepared intent file evidence and file recovery observation/classification.
- Focused lifecycle, persistence, recovery, Provider-contract and product acceptance tests.
- S7P-04 acceptance document, architecture/capability inventory and `.agent` execution state.

## Non-goals

- No copy, directory/recursive mutation, overwrite/force, chmod/link, cross-device fallback,
  run-level undo or Git write behavior.
- No public event lifecycle, runtime-policy default, Provider/model retry, completion checker,
  permission authority or dependency changes.
- No live tests, S7P-05 work or edits to the user-owned research documents.

## Acceptance gates

- create/patch/replace/delete/move/rename have success, conflict, boundary, symlink, denial/cancel
  and recovery coverage; destructive sources require exact SHA and destinations never overwrite.
- Sandbox preview/promotion parity covers delete and unambiguous move/rename; ambiguous identity is
  not guessed and source/destination drift fails closed.
- Multiple selected changes have deterministic preflight/order; a partial effect is visible as a
  failed partial result and ChangeSet fact, never atomic success.
- New operations use the ordinary ToolExecutor, CapabilityPolicy, approval and durable execution
  path; final scripted task proves delete/rename and verifier final-tree checks.
- Focused/full offline pytest, Ruff, compileall, both CLI help and diff checks pass after review
  repair.

## Review protocol

After first verified commits, the same Luna Max implementation task spawns one read-only Luna Max
subagent to review the entire activation-base...HEAD diff for overwrite races, path/symlink escape,
recovery truth, dirty-change loss, promotion ambiguity/partial failure and false-positive evidence.
Every confirmed finding is fixed and retested before the branch returns to the root task.
