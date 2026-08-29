# Subplan 93 — Remove v1 Bounded Runtime Compatibility

Status: completed, verified and fast-forward integrated into local `main` at `058b508`.

## Objective

Keep one AgentRun execution contract: v2 long-horizon. Remove the v1 bounded resolver, cumulative
task limits, strict repeated-cycle detector, legacy runtime-policy override selector and implicit
fallback for injected Providers. Reject v1 snapshots and retired override fields rather than
silently changing execution behavior.

## Ownership

- `src/morrow/core/models.py`, `src/morrow/core/runtime_policy.py`
- `src/morrow/runtime/policy.py`, `src/morrow/runtime/agent.py`, `src/morrow/runtime/tools.py`
- `src/morrow/application/context.py`, `src/morrow/application/agent_runs/preparation.py`
- `src/morrow/bootstrap.py`, `src/morrow/resources/runtime-policy.toml`
- focused tests and current architecture/runtime-policy documentation

Historical evaluation bundles and Live Provider execution are out of scope.

## Acceptance

- Every newly composed or injected Provider runtime carries policy schema v2.
- v1 RunPolicy snapshots and retired override fields fail strict validation.
- No v1 cumulative budget or repeated-cycle branch remains in the runtime.
- Per-operation timeout, cancellation, steering, compaction, retry and output bounds remain covered.
- Focused tests and the complete offline/static/CLI/diff gates pass.
