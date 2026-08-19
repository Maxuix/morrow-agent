# Subplan 44 — CapabilityGrant and Full Access Manual

> Status: pending
> Prerequisite: Subplan 43 accepted
> Owns: explicit elevated authority, immutable permission evidence, and honest Host risk UX
> Schema: v9 CapabilityGrant and complete PermissionSnapshot

## Objective

Allow a user to grant one foreground AgentRun explicitly bounded elevated capabilities with durable,
revocable evidence, while keeping every elevated effect manually approved and making the lack of
Host confinement unmistakable.

## In scope

- CapabilityGrant and immutable PermissionSnapshot models, stores, commands, queries, and sanitized
  application events.
- Trusted-local-interface-command-only creation, explicit capability/operation scope,
  workspace/task/run binding, reason, policy version, creation/expiry/revocation, and optimistic
  version; Morrow does not claim local-user authentication.
- AgentRun-start resolution and freeze; fail-closed restart and revocation behavior.
- Full Access Manual support only for the ADR's sole elevated capability family,
  `unconfined_host_process`; ordinary structured tools retain their Stage 3 bounds.
- Per-operation approval linked to grant, permission snapshot, intent, and ToolExecution.
- `unconfined_host` warning/confirmation and audit projection for opaque Host commands.
- Cross-workspace/task/run isolation and model/config/history escalation tests.

## Out of scope

- Controlled Full Access Auto, raw auto, persistent/global default elevation, and grant renewal by
  the model.
- Claiming protected-resource confinement for an approved opaque Host command.
- Background/remote grants, organization policy, role-based access control, or team administration.
- New arbitrary network/browser/MCP tools solely to make Full Access appear broader.

## Tasks

- [ ] S4.44.1 Implement strict CapabilityGrant/PermissionSnapshot models and schemas with explicit
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

## Locked product contracts

- Only an explicit trusted local CLI/REPL/future-GUI application command creates or elevates a
  grant. This is an authority boundary, not an authentication claim. Model, Tool, project content,
  Profile, Preferences, Memory, Skill, imported state, and recovery records cannot do it.
- A grant is not itself an approval. Every elevated side effect in Full Access Manual still consumes
  an intent-bound Approval.
- Lifetime is exactly one AgentRun; it is never restored as a global/workspace default. A crash-
  resumed AgentRun receives no prior grant and requires an explicit new grant.
- Direct structured tools enforce their declared protected resources. An approved opaque Host
  command is unconfined; Morrow presents this fact rather than claiming classifier-based isolation.
- `full_access + auto` returns unsupported in Stage 4.

## Tests and faults

- user grant create/query/freeze/expire/revoke and duplicate/conflicting receipts;
- crash before/after grant commit, run snapshot, approval consume, handler start, and revocation;
- crash recovery creates an ungranted AgentRun and cannot copy an old grant/snapshot;
- model/tool/config/Profile/project/history/import attempts to create or extend a grant;
- workspace/task/run mismatch, expired/revoked/missing policy version, and clock-boundary tests;
- ordinary Manual/Auto Safe/Auto Sandboxed behavior remains unchanged without a grant;
- unconfined Host preview is shown and acknowledged before every elevated opaque execution;
- revocation during pending approval/running process/completed unknown effect;
- Controlled Auto and raw auto remain unregistered/unsupported.

## Completion gate

A local-interface user can explicitly grant and revoke `unconfined_host_process` for one foreground
AgentRun, and every resulting effect is traceable to a frozen snapshot plus one-shot approval.
Restart, recovery-created runs, and all non-interface inputs fail closed. Product text never
confuses approved Host execution with OS isolation, and no Full Access Auto path exists.

## Deliverables

- CapabilityGrant/PermissionSnapshot domain, persistence, and application APIs.
- v9 migration, grant-aware doctor/events, and Full Access Manual unconfined Host UX.
- Complete escalation, revocation, crash, and isolation evidence.
