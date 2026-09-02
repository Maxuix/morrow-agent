# Progress Tracker

## Current status

Stage 7 is complete. Post-closeout real-user evaluation blockers are repaired: installed-ripgrep
regex argv, node-specific typed submission schemas and diagnostics, truthful Workflow CLI
serialization/exit codes, early durable Run ID output, and `workflow runs` discovery. The latest
real coding simulation exposed a Direct invoking-session integrity mismatch: the verifier required
its reused user root to have isolated-leaf purpose. Integrity now validates Direct against the exact
WorkflowRun root and same Session while preserving the `workflow_node` requirement for isolated
leaves. The original 67-request Provider history now passes Doctor, Backup and Verify. Stage 7
matrix: 235 passed. Full offline validation passed: 1538 passed, 2 skipped and 2 Live deselected;
Ruff format/check, compileall and diff checks passed.

## Active task

None. All nine Stage 7 subplans are completed and integrated.

## Next action

Await Stage 8 direction. Provider invalid-response and recoverable submission diagnostics remain
reliability/observability follow-ups.

## Blockers

No local implementation blocker. Additive `ApplicationEvent` types remain deferred because the
current request does not separately authorize a public event lifecycle change; Query/CLI polling is
the required complete path. Local `main` is ahead of `origin/main`; remote
publication remains blocked pending explicit authorization for the configured GitHub remote.
