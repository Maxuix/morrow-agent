# Stage 4 PermissionSnapshot, CapabilityGrant, and Full Access Manual decision

## Status

Accepted for Stage 4 implementation planning by S4.35.6. Full Access remains unimplemented and
unsupported until Subplan 44 passes.

## Authority boundary

Morrow has no local-user authentication subsystem. Therefore this decision does not use the phrase
“authenticated local user.” A grant may be created only by an explicit command received through a
trusted local interface adapter (CLI/REPL and a future local GUI) and executed by the grant
application service.

The following can never create, extend, copy, or elevate a grant:

- model output or ToolCall;
- a tool handler or tool result;
- project files, Profile, Preferences, imported state, Memory, or Skill;
- Provider responses or system-prompt content;
- recovery records, parent Session/Fork history, or a prior AgentRun snapshot.

There is no model-callable grant tool in Stage 4.

## CapabilityGrant

A grant is immutable authority metadata plus revocation state:

- opaque `grant_id`;
- exact `workspace_id`, `task_run_id`, and subject `agent_run_id`;
- explicitly enumerated capability subset;
- `granted_by = local_interface_command` and command receipt;
- bounded user reason/preview digest;
- policy/schema version;
- created/expiry/revoked timestamps and optimistic row version.

Default and only Stage 4 product lifetime is one AgentRun. A grant cannot be saved as a global or
workspace default and cannot be silently renewed. Expiry and revocation use the injected Clock and
are re-evaluated before every elevated side effect.

## PermissionSnapshot

Every AgentRun freezes an immutable PermissionSnapshot containing:

- base Stage 3 `AccessScope`, `ApprovalMode`, and `ProcessIsolation`;
- frozen workspace capability and read-only intersections;
- ToolSet/Schema and policy digests;
- any effective grant ID and exact granted subset;
- effective isolation label per elevated process capability;
- creation time and source revisions.

Subplan 37 records only the non-grant base permission context and digest. Subplan 44 introduces the
full snapshot table/link and migrates AgentRun without changing old evidence.

## Crash and resume

A resumed Turn creates a new AgentRun and a new PermissionSnapshot. Because the Stage 4 grant is
bound to the interrupted AgentRun, the new run receives no elevated capability. The user must grant
again through the local interface if recovery or continued work still requires Full Access.

Recovery cannot copy the old grant “for convenience.” UI and tests must state this explicitly.

## Full Access Manual capability set

Stage 4 activates only one elevated capability family:

```text
unconfined_host_process
```

It allows an explicitly approved opaque Host `run_command` intent to pass Stage 3 structural denials
for commands that name outside-workspace paths or network use, subject to existing argument, timeout,
output, redaction, destructive-command, credential-display, and privilege-escalation gates that the
Subplan 44 threat model keeps. The command starts from a validated workspace context; Stage 4 does
not add general outside-workspace file/search/mutation, browser, MCP, Git-write, or network-specific
tools merely to broaden the label “Full Access.”

Every such command:

- consumes a one-shot Approval bound to ToolExecution, PermissionSnapshot, intent, schema, and grant;
- stores `isolation = unconfined_host` as operational evidence, not only UI copy;
- shows a mandatory warning that the process is not OS-isolated and may reach user files, network,
  credentials, sockets, and Morrow state with the current user's authority;
- remains `outcome_unknown` after restart without a committed handler result;
- cannot run automatically in any Stage 4 permission preset.

Structured direct file/config/Git tools retain their current workspace and protected-resource
contracts. A grant does not turn credential files or Morrow state into direct tool resources.
However, Morrow must not claim those rules confine an approved unconfined shell.

## Approval and grant are separate

A valid grant makes an elevated intent eligible to request approval; it is never itself approval.
Approval remains per execution and one-shot. Revocation before approval consumption makes the
approval invalid. Revocation after handler start requests cancellation but retains all completed or
unknown facts and never claims rollback.

## Unsupported modes

The following return typed unsupported/denied results in Stage 4:

- `full_access + auto` and Controlled Full Access Auto;
- arbitrary Host commands without per-call approval;
- persistent/global Full Access defaults;
- inherited grants across AgentRuns, Sessions, Tasks, Forks, or workspaces;
- background or remote grants;
- organization roles/RBAC and remote approval.

Auto Safe and Auto Sandboxed remain workspace-scoped Stage 3 modes. Full Access does not silently
compose with Auto Sandboxed or promote sandbox output.

## Revocation

Revocation is an idempotent command with optimistic version and receipt. It:

1. prevents any new elevated approval or handler start;
2. invalidates an unconsumed pending approval;
3. requests cancellation of a currently executing relevant handler;
4. records whether the effect completed, was cancelled, or became unknown;
5. preserves the immutable grant, snapshot, approval, and execution history.

## Required tests

- grant create/query/freeze/expire/revoke and duplicate/conflicting commands;
- every non-interface elevation source above is rejected;
- cross-workspace/task/run/session/Fork reuse is rejected;
- crash-created AgentRun receives no grant and requires explicit regrant;
- unconfined warning and persisted isolation label appear for every elevated opaque Host command;
- expiry/revocation between preview, resolve, consume, and handler entry blocks execution;
- ordinary Manual/Auto Safe/Auto Sandboxed behavior is unchanged without a grant;
- Full Access Auto and raw auto have no registered or hidden path.

## Rejected alternatives

- claiming local-user authentication Morrow does not have;
- treating grant as approval or a remembered Preference;
- inheriting run-bound authority during crash recovery;
- claiming shell classification protects credentials or Morrow state;
- adding browser/network/MCP/outside-file tools only to make Full Access appear comprehensive.
