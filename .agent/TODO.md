# TODO

## Current stage

Stage 7 preflight reliability repairs, S7P-02: Provider-visible tool schemas and recoverable
argument contracts.

## Active subplan

Subplan 81 — S7P-02 Tool Contract Audit and argument recovery.

## Tasks

- `[x]` Read S7P-02 and locate schema/runtime/handler mismatches in current code and tests.
- `[x]` Freeze the Provider-schema, audit, diagnostics, compatibility and validation decisions.
- `[>]` Add failing final-wire schema/runtime mismatch tests and a full static inventory audit.
- `[ ]` Add one normalized explicit Provider-schema seam and fail-closed drift checks.
- `[ ]` Repair run-command XOR, write-file branches/budgets and common path/query contracts.
- `[ ]` Add bounded actionable invalid-argument feedback and scripted next-call correction coverage.
- `[ ]` Prove error-code separation and dynamic MCP compatibility.
- `[ ]` Publish S7P-02 acceptance evidence and run focused/full offline quality gates.
- `[ ]` Complete same-session Luna Max subagent review and repair every confirmed finding.
- `[ ]` Commit the final verified implementation; leave merge/retirement to the root task.

## Boundaries

- Do not start S7P-03 prompts/project instructions or any later repair item.
- Do not change public event lifecycle, runtime-policy defaults, permission/sandbox authority or add
  dependencies.
- Do not run live Provider/model/Pi/MCP/network/credential tests.
- Do not persist or emit credentials, reasoning, complete arguments/results, SDK objects or
  tracebacks; durable validation diagnostics remain value-free.
- Preserve the three user-owned untracked research documents.
