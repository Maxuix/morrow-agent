# TODO

## Current stage

Stage 4 production implementation is active at CapabilityGrant and Full Access Manual. The
Command/Query/Event, CLI, doctor, and backup surface has landed; Stage 5 remains inactive.

## Active subplan

Subplan 44 — CapabilityGrant and Full Access Manual.

## Tasks

- [>] S4.44.1 Implement strict CapabilityGrant/PermissionSnapshot models and schemas with explicit
  capability subsets, subject IDs, source/user evidence, expiry, revocation, and policy version.
- [ ] S4.44.2 Implement user-interface-only grant create/query/revoke commands with command receipts,
  optimistic concurrency, and no Tool/model-callable elevation path.
- [ ] S4.44.3 Resolve and freeze the effective PermissionSnapshot at AgentRun start; link every
  elevated ToolExecution/Approval and fail closed on missing, stale, expired, revoked, mismatched,
  or unprovable evidence after restart.
- [ ] S4.44.4 Implement only `unconfined_host_process` for Full Access Manual, without adding
  outside-file/network/browser/MCP/Git-write tool families or weakening ordinary workspace modes.
- [ ] S4.44.5 Add the mandatory `unconfined_host` preview and explicit approval language for opaque
  Host commands, including reachability of user files, network, credentials, and Morrow state.
- [ ] S4.44.6 Implement revocation: block new effects, request cancellation of active relevant tools,
  preserve completed/unknown facts, and never pretend to roll back.
- [ ] S4.44.7 Add threat-model and boundary tests for every possible elevation source and every
  cross-scope reuse, including crash-created AgentRuns; extend doctor/application-event coverage for
  grants and keep Controlled Auto explicitly unsupported.
- [ ] S4.44.8 Run product/security/crash/package regressions and update public permission docs.

Only Subplan 44 may be executed. Stage 5 remains inactive.
