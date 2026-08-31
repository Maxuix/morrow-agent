# Stage 7 Subplan 1 review follow-up

Date: 2026-09-01
Reviewed range: `origin/main..06e0c45` (including prerequisite `cb8fcc8`).
Repair base: verified local `main@25cad16`.

## Findings and disposition

| Finding | Assessment | Resolution |
| --- | --- | --- |
| Definition preparation errors reported as Provider/credential failures | Confirmed, including exact Skill selection failing during submission | Added bounded typed definition diagnostics for preparation and atomic admission. The orchestrator preserves application errors through the existing known-failure path. Exact Skills preflight through the current selection owner; submission still rechecks bindings and rolls back on change. Recovery continues from frozen Skill evidence. |
| A generation cap of one cannot complete a tool round and subsequent answer | Expected budget semantics, but documentation and end-to-end coverage were incomplete; adapter exception inconsistency confirmed | Every admitted `purpose=agent` request counts, including tool follow-ups and retries. Kept this count, clarified the field/docs, and moved the storage-to-application error translation into SessionPersistence. No retries or follow-ups bypass the limit. |
| Revocation parsing leaks Pydantic errors | Confirmed | Invalid JSON, invalid schema and workspace/version identity mismatches consistently produce sanitized `StorageError(NEEDS_REPAIR)`. |
| Duplicate tool message claims forbidden wins | Confirmed wording defect | Message now states that conflicting or duplicate declarations are rejected; validation behavior is unchanged. |
| Comments narrate plan/architecture | Partly confirmed | Removed redundant validation/policy comments. Kept the short constraint that ordinary disable must not block recovery, and the test-only policy-injection explanation. |

## Regression evidence

- Six preparation failures produce actionable known failures, make no Provider request and create
  no durable Turn or AgentRun.
- Exact Skill admission succeeds when enabled; a disable between preparation and submission
  rolls back and produces a known failure. An admitted Skill leaf can rehydrate after ordinary
  binding/head disable.
- With a real read-tool path and a scripted Provider, cap 1 completes the tool but refuses a second
  model call with `budget_exhausted`; cap 2 completes the final answer. Failed admitted requests
  remain charged, while ordinal replay is free.
- Four revocation corruption cases retain the storage repair contract and doctor repair status.
- Focused suite: 134 passed across agent definitions, preparation, observability, Skill selection
  and configuration tools.
- Full offline suite: 1343 passed, 2 nested-Seatbelt skips, 2 Live tests deselected (130.83s).
- Static checks passed: Ruff format (519 files), Ruff lint, compileall, CLI help and diff check.

No dependency, bundled policy default, new public event, Live test or Stage 7 Subplan 2 work was
introduced. Remote publication remains subject to the existing explicit-authorization blocker.
