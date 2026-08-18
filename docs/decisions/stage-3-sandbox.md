# Stage 3 native sandbox decision

## Fixed reference

Subplan 33 uses Pi coding-agent `0.84.2` at commit
`209bc7b9a89b01c8fd05861cf5bbdda3e300037a` only as a behavioral reference for bounded
process execution and disposable command results. Pi's inherited Host authority is not a
sandbox contract and is not reused for automatic project execution.

## Borrowed behavior

- Keep one process-result contract and one replaceable adapter boundary for Host and native
  execution.
- Run a command in a task-private project copy, collect bounded output and changes, then let the
  caller decide whether a change should be kept.
- Preserve simple command ergonomics while keeping snapshot lifecycle and cleanup explicit.

## Deliberately strengthened for Morrow

- Auto Sandboxed uses a native default-deny backend only after a real capability probe; the
  current macOS backend is Seatbelt-based and Linux construction is present but unsupported
  until a real Linux runner passes.
- Snapshot creation prefers APFS `clonefile`, never uses writable hard links, excludes protected
  resources/VCS/cache paths, rejects special files and external symlinks, and enforces bounded
  preparation and Diff collection. The adapter reserves the temporary root before starting a
  worker, uses cooperative cancellation for prepare/collect, waits for a timed-out worker to
  settle, and then cleans the owned root so a late thread cannot leave a project copy behind.
- The sandbox has no network or loopback access, no user Home/credential/socket access, and no
  fallback to the non-isolated Host adapter. Host execution remains approval-required in Manual
  and Auto Safe.
- Snapshot changes are disposable by default. Only bounded text creates/modifications from the
  current run can enter an always-approval-required promotion tool, which delegates to the
  existing revision- and conflict-safe mutation service and records each applied result in the
  same current-run `ChangeSetService`; the model never supplies replacement content.

## Rejected behavior

- No Docker, helper installation, global permission persistence, network grant, Full Access, or
  automatic unsandboxed Host execution.
- No direct writable mount of the real workspace, full Home bind, inherited credential/socket
  environment, arbitrary snapshot-to-workspace copy, or promotion of delete/rename/chmod/link or
  binary changes in Stage 3.
- No second process/tool state machine or public event lifecycle; `AgentLoop`, ToolCycle,
  `ConversationLog` ownership and generic `ToolExecutor` remain authoritative.
