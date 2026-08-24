# Progress Tracker

## Current status

Subplan 63 (Dependency and Contract Spike) complete and merged to local `main`. No Stage 6
implementation code exists yet; Subplans 64+ are ready to activate in order.

## Last completed work

- Reconfirmed every Stage 6 seam with source refs (AgentRun assembly/replay, ToolExecutor/
  PermissionSnapshot/CapabilityPolicy, ProcessExecutionService/sandbox, AdapterRegistry/Provider,
  v13 migrations, backup v1/doctor) and recorded them in the ADR.
- Evaluated official MCP Python SDK `mcp 2.0.0` (MIT, Python >=3.10) in a temporary venv: stdio
  lifecycle, per-call read timeouts (`MCPError: Request 'tools/call' timed out`), typed results,
  no auto-retry, default-inert OpenTelemetry; wheel 342 KiB; jsonschema 4.26 already transitive.
- Chose `jsonschema` with a single controlled dialect (draft 2020-12; absent `$schema` treated as
  2020-12; everything else fail-closed) — the SDK's silent fallback to the latest draft was rejected.
- Ran the narrow Fake stdio connect/list/call/close prototype (`tests/spikes/`): pass, offline;
  timed-out call entered the handler exactly once; session usable afterwards.
- Measured and locked budgets: AgentRunSnapshot 3 192 B base, 4 822 B with 8 Skill refs + 1 MCP ref
  (64 KiB cap); Skill context per entry <= 2 KiB, per run <= 16 KiB; MCP tool snapshot per tool
  <= 4 KiB, per server <= 64 KiB; result chunk <= 128 KiB.
- Locked Skill package canonicalization (skv_ envelope, canonical tree digest, reject symlink/
  hardlink/device/socket/FIFO, Unicode/case collision rules, TOCTOU-safe reads) and confirmed
  v14/v15/v16 migration splits plus backup bundle v2 manifest versioning seams.
- Published `docs/research/stage6-mcp-dependency-spike.md` with exact dependency recommendation
  (`mcp >=2.0.0,<3` and `jsonschema >=4.20,<5`) behind the explicit user-approval gate.
- Gates: `ruff format --check .`, `ruff check .`, `compileall`, `git diff --check`, full offline
  suite `947 passed, 1 skipped, 2 deselected`; `pyproject.toml` and `uv.lock` unchanged.

## Active task

None in progress. Next: activate Subplan 64 (Per-AgentRun runtime preparation) from latest `main`.

## Next action

Create `codex/feat/stage6-<slice>` for Subplan 64 from the verified local `main` when the user asks
to continue. Subplan 72/73 remain conditional on the user approving the exact dependency change.

## Dependency gate

No dependency was added by Subplan 63. Before Subplan 72, ask the user to approve the exact change:
`mcp >= 2.0.0, < 3` and `jsonschema >= 4.20, < 5` (rationale and versions in the ADR). If denied,
complete Subplans 64–71 normally and mark 72–73 blocked.

## Blockers

None for Subplans 64–71. MCP implementation Subplans 72–73 are conditional on the recorded
dependency decision and explicit approval if new packages are recommended.

## Notes

- `main` is one local commit ahead of `origin/main` after the Subplan 63 merge; remote publication
  was not placed in scope, so no push was made.
- The spike test file skips cleanly in the default dev env (`importorskip("mcp")`); repo state is
  safe without the SDK.

## Preserved history

Detailed Stage 4/5 implementation and acceptance evidence remains in Git history and completed
subplans; Subplan 63 evidence is in the ADR and `tests/spikes/`. It is not duplicated in this
active tracker.