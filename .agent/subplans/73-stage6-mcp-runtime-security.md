# Subplan 73 — MCP Runtime and Security Adapter

> Status: in progress
> Branch: `codex/feat/stage6-mcp-runtime`
> Prerequisite: Subplan 72 complete

## Objective

Expose reviewed MCP Catalog tools through the same per-AgentRun ToolSet, ToolExecutor, permission,
approval, budget, cancellation, audit and recovery paths as local tools. Server lifecycle is lazy and
run-scoped; calls never auto-retry.

## Ownership

- `src/morrow/core/mcp/results.py`, policy snapshot contracts finalized from v16 schema
- `src/morrow/application/mcp/run_bridge.py`, `policy.py`, `results.py`
- bounded additions to `adapters/mcp/stdio_client.py`
- thin integration in `application/agent_runs/` and permission snapshot creation/doctor
- `tests/test_mcp_runtime.py`, `tests/test_mcp_policy.py`, `tests/test_mcp_results.py`, recovery tests

## Tasks

1. Build `McpLaunchSnapshot` and `McpToolSnapshot` from enabled desired state and one reviewed Catalog
   revision during `prepare_new()`. Persist IDs/digests/allowlist; reject executable/config/catalog
   drift. `rehydrate()` uses only stored snapshots and valid immutable executable evidence.
2. Extend PermissionSnapshot with narrowly scoped MCP review evidence bound to workspace, AgentRun,
   Server/config/catalog/toolset digests. It records reviewed facts, not a blanket capability grant.
3. Implement launch Intent and semantic tool Intent mapping. Extend the existing CapabilityPolicy
   narrowly: exactly matching local-interface MCP review evidence may convert network/credential/
   loopback/external-effect denial into per-call approval, never allow; destructive,
   outside-workspace, privilege escalation and git write remain denied. Evaluate both Intents and
   combine decisions with one pure deny-first function. Present one merged bounded approval when
   required.
4. Register MCP tools through the Subplan 71 validator/recovery seams. Do not branch ToolExecutor,
   ToolCycle or recovery on names or SDK types.
5. Implement `LazyMcpRunPool`: start a Server on first selected call, reuse only inside that
   AgentRun, isolate each Server, and close all processes on success/failure/cancellation. No daemon
   and no cross-run pool.
6. Implement call timeout/cancellation/crash handling. Before handler entry may be never-started;
   after entry with no reliable result is outcome-unknown/reconciliation-visible. Never auto-retry,
   including read tools.
7. Normalize text, image, audio, resource link, embedded resource and structured content. Enforce
   aggregate/depth/count/byte budgets, secret scan and truncation; import bounded binary/embedded
   content to ArtifactStore and retain only refs.
8. Treat resource links as locators only; do not auto-fetch. Keep SDK objects, stderr, raw binary,
   full payloads and credentials out of ToolExecution, events, context and terminal.
9. Ensure one Server crash degrades only its tools and leaves Session, ConversationLog, other MCP
   Servers/local tools and durable state usable.
10. Add bounded status/doctor facts for current/historical snapshots, decision evidence and degraded
    state without exposing sensitive configuration.

## Validation

- Complete launch×tool verdict matrix including network, credential, external effect, destructive,
  outside-workspace, config/tool allowlist and snapshot drift; deny wins and approval is singular.
- New-run refresh, same-run freeze, closed replay without start, recovery without current Catalog,
  lazy start/reuse/close and concurrent Server isolation.
- No-retry evidence for success-lost, timeout, cancellation and crash after entry.
- All result content types, errors, truncation, Artifact import, link non-fetch, malformed SDK data and
  redaction.
- Focused MCP/tool/permission/recovery tests, standard quality commands and full non-live suite.

## Exit criteria

- A Fake stdio tool completes through ordinary ToolExecution and a crash remains isolated/recoverable.
- MCP cannot bypass current authority, approval, evidence, result or AgentRun freeze boundaries.
- No automatic retry, blanket Server grant or second permission engine exists.
