# Stage 3 Local Code Agent and Safety Implementation Plan

> Status: closed on 2026-08-19 on the claimed macOS platform; implementation review and final Mimo acceptance remediated
> Active subplan: none; Stage 3 Subplans 29–34 accepted
> Next stage: Stage 4 planning opened; no Stage 4 implementation subplan is active
> Code baseline: `d9df0d24975217bda0ec375a5f637c39b7f85489`
> Scope: Stage 3B–3F local file, search, mutation, Host process, native sandbox, read-only Git, and end-to-end acceptance

## Objective

Turn the current function-calling Agent into a real local Code Agent that can safely complete:

```text
understand task
→ locate and read code
→ search symbols/text
→ apply conflict-safe changes
→ show actual Diff
→ run validation with bounded processes
→ optionally run project commands in a native sandbox
→ inspect Git status/diff
→ report only tool-backed changes and validation facts
```

The target is **Pi-grade coding-tool ergonomics plus Morrow-grade workspace safety,
visible changes, deterministic policy, and truthful acceptance evidence**. Stage 3 is complete
only when the entire loop works through the existing `AgentLoop` and standard ToolCycle without
adding a second task state machine or a tool-name branch to Runtime.

This document is the living high-level index. Executable detail is split into ordered Subplans
29–34. Subplan 34 is accepted; the final acceptance evidence is recorded in
`docs/acceptance/stage-3-local-code-agent-evidence.md`.

The 2026-08-18 external implementation review was also verified finding by finding. All nine
bugs, two suggestions, and one nit were actionable and are now covered by focused regressions,
the refreshed full offline suite, host-level macOS Seatbelt tests, and a rebuilt wheel smoke gate.

## Lifecycle transition

This file is retained as the closed Stage 3 implementation record. The final review on
2026-08-19 additionally verified the persistent OpenCode Go Mimo v2.5 environment, sanitized
Keychain failures, connection/first-token feedback, provider preset discovery, and the wrapper
command's persistent state path. Stage 4 is now open for planning against
`docs/roadmap/stage-4-task-session-and-persistence.md`; no persistence, Full Access, or other
Stage 4 production capability is activated by this closeout.

## Authority and precedence

1. The current user decisions on permission modes, native sandboxing, Full Access timing, and
   Pi as the primary mature reference.
2. Current code and commands just run.
3. `docs/roadmap/stage-3-local-tools-and-safety.md` and `docs/ARCHITECTURE.md`.
4. This master plan for locked cross-cutting contracts and subplan ordering.
5. The one active subplan for bounded implementation detail after separate authorization.

If implementation evidence conflicts with this plan, stop, reconcile the plan, and then
continue. Do not silently weaken a safety gate to make a test pass.

## Current baseline

At baseline `d9df0d2`:

- `AgentLoop.run_task()` owns one bounded model/tool loop and is the only chat-history writer.
- `ToolRegistry` freezes one `ToolSet`; `ToolExecutor` validates arguments, applies a static
  `never|required` approval flag, executes handlers serially, and bounds result envelopes.
- `ToolEffect` contains only `none`, `session_write`, and `persistent_write`.
- Production registers exactly `lookup_record`, `calculate`, and `update_configuration` for
  function-tool-capable Adapters.
- `TerminalApprovalPort` shows a sanitized preview and returns one local approval decision.
- `WorkspaceIdentity.path` is the authoritative local root selected by `WorkspaceService`.
- Project Profile/Preferences state already has revisioned, atomic YAML writes, but project
  source files have no capability service.
- There is no file, search, patch, Diff, Shell, process, Git, sandbox, artifact, or Full Access
  implementation.
- `tests/test_stage_boundary.py` intentionally rejects Stage 3 tool names and must be updated
  atomically as each approved capability becomes real; it must not simply be deleted.
- Public `tool.status` events have a fixed lifecycle/payload. This plan does not authorize a
  public event lifecycle change.
- The bundled `agent-policy.toml` has stable run/tool/result limits. This plan does not
  authorize changing its defaults.
- The unknown-model production baseline resolves to 160,000 request characters, 16,000
  characters per tool result, 56,000 characters per ToolCycle, and a 120-second outer tool
  timeout. `ToolExecutor` currently truncates an oversized success by embedding a raw JSON
  prefix, so Stage 3 services must finish semantic truncation before that fallback.
- `SYSTEM_BOUNDARY` currently says project files, Shell, and Git are unavailable even when a
  future ToolSet provides them. It must become capability-derived before read tools ship.
- Approval previews are currently capped at 8 lines × 200 characters. Mutation approval needs
  a larger, separately bounded Diff preview without expanding Host/configuration previews.

No production source or test file differed from `HEAD` when this plan baseline was recorded.
Existing roadmap commits and any unrelated future worktree changes remain user-owned.

## Gate P0 — pre-activation native-sandbox feasibility

After the user explicitly authorizes Stage 3 implementation, run this non-repository-mutating
feasibility gate before activating Subplan 29 or changing production code. It may create and
clean task-private temporary probe files, but it must not change repository or persistent Host
state:

1. Probe the current macOS host's available Seatbelt-class entry point without installing or
   changing Host state.
2. In task-private temporary paths, prove a basic standard-library Python command can run while
   outside/Home writes, external network, loopback, inherited credentials, and direct real-
   workspace mutation remain unavailable.
3. Probe an APFS `clonefile`/equivalent copy-on-write snapshot path and the minimum read-only
   system-interpreter/toolchain mounts needed by the acceptance fixture.
4. Record exact backend, OS, filesystem, timing, and unsupported/degraded reasons.

Plain hard links are forbidden for writable snapshots because writes could mutate the original.
No Docker, helper installation, Host fallback, or weakened network/filesystem contract is allowed.

The accepted product decision is exit **(b)** from the review: Auto Sandboxed remains a Stage 3
completion requirement. If Gate P0 cannot prove the contract, do not activate Subplan 29 and do
not relabel Manual + Auto Safe as completed Stage 3. Return to the user for an explicit scope or
platform decision. Linux rule/argv construction remains planned, but Linux runtime support is
claimed only after a real Linux runner passes; it is not a hidden dependency of the macOS gate.
Windows native sandboxing is explicitly outside the first Stage 3 platform claim.

### Gate P0 result — 2026-08-18

Gate P0 passed before any production or test implementation change. The probe ran on Darwin
25.6.0, macOS 26.6.1, arm64, using `/usr/bin/sandbox-exec` and the existing
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` standard-library runtime.
The writable data volume is `/dev/disk3s5`, APFS, mounted at `/System/Volumes/Data`; the
system volume is an APFS read-only snapshot. A task-private `clonefile` probe returned success,
produced a different inode on the same device, and preserved the source after mutating the
clone. The restricted probe allowed only task-private temporary writes and blocked workspace
and Home writes, `.ssh` directory reads, loopback bind, and external TCP connect; the launched
environment contained only minimal task variables. The first nested call from the Codex sandbox
was rejected with `sandbox_apply: Operation not permitted`; the host-level rerun passed, so this
is recorded as an execution-environment limitation rather than a host backend failure. No bwrap,
Docker, helper installation, repository write, or persistent Host change was used.

## Locked product decisions

### Permission dimensions and presets

The internal model has three independent dimensions:

```text
AccessScope: workspace | full_access
ApprovalMode: manual | auto_safe | auto
ProcessIsolation: host | native_sandbox
```

Stage 3 product presets are:

| Preset | Effective dimensions | Stage 3 behavior |
|---|---|---|
| Manual | workspace + manual + host | reads automatic; writes and Host commands prompt |
| Auto Safe | workspace + auto_safe + host | bounded structured writes automatic; Host project code still prompts |
| Auto Sandboxed | workspace + auto + native_sandbox | project commands automatic only in a temporary sandbox snapshot |

`full_access` is represented so later policy code does not need a redesign, but Stage 3 must
return `unsupported_capability`. Full Access activation, grant lifetime, persistence,
revocation, and AgentRun freezing belong to Stage 4.

Additional locked rules:

- `manual` is the default.
- The user selects a Stage 3 preset explicitly at workspace startup; the effective profile is
  frozen in the constructed `ToolExecutor` for the current process-local Session.
- `Session.read_only` intersects the selected profile and removes project mutation/process
  capability; it never upgrades permissions.
- The model, tool arguments, Profile, Preferences, project files, and Provider output cannot
  select or elevate a permission mode.
- `update_configuration` keeps its existing per-call approval behavior in every Stage 3 mode.
- An unsupported mode or missing sandbox backend fails closed before any handler runs.
- Destructive operations against the real workspace remain denied. A command inside Auto
  Sandboxed may freely damage only its disposable snapshot; none of those changes is promoted
  automatically.

### Auto Safe and process execution

Auto Safe can automatically perform only operations Morrow can enforce structurally:

- workspace-confined reads and searches;
- new files and exact bounded patches with conflict protection;
- read-only Git inspection.

“Bounded” is a policy contract, not an implementation placeholder. One automatic
`apply_patch` call must target one file and satisfy all of:

- at most 8 exact edits;
- at most 64 changed lines, counting inserted plus deleted lines;
- at most 4 KiB of changed UTF-8 bytes, counting replaced and replacement text;
- not replace all non-empty content and not change more than 25% of an existing file's lines.

One automatic `write_file(mode="create")` is limited to 64 lines and 4 KiB. A single
`run_task()` may automatically mutate at most 4 files, 16 edits, 128 changed lines, and 8 KiB
in total. Exceeding any per-call or cumulative threshold returns `require_approval`, not an
automatic allow. `write_file(mode="replace")` always requires approval. Exact boundary tests
cover values immediately below, at, and above every threshold. Auto Sandboxed uses the same
rules for structured writes to the real workspace; arbitrary snapshot-local command changes
remain disposable until separately promoted.

Tests, build tools, scripts, interpreters, package managers, and opaque executables are
arbitrary project code. On Host they require approval even if their `cwd` is inside the
workspace. A working-directory check is not an operating-system sandbox.

### Auto Sandboxed and workspace protection

Auto Sandboxed runs project commands in a temporary snapshot containing the current project
state, including current uncommitted source changes. The real workspace is not writable by
the sandbox command. Command-created changes are collected as Diff/ChangeSet facts, the
snapshot is discarded, and any desired promotion uses the same conflict-safe file mutation
path as ordinary edits.

Before discarding the snapshot, extract a bounded current-run `SandboxPromotionBundle` for
eligible text creates/modifications: opaque change-set id, relative paths, captured real-workspace
revisions, hashes, and exact patch/new-file payloads. It lives only in task-private memory/temp
until `run_task()` settles, is never persisted or exposed as raw Provider content, and is cleaned
on success/failure/cancellation. `promote_sandbox_changes` may reference only this bundle.

The first sandbox contract is:

- native OS sandbox only; no Docker;
- macOS Seatbelt-class backend and Linux bubblewrap-class backend behind one Port;
- current-host support is enabled only after Gate P0 and the definitive Subplan 33 escape
  suite pass; other platforms stay explicitly unsupported rather than passing by skip;
- project snapshot writable, required system/toolchain paths read-only, private temp writable;
- snapshot creation prefers APFS `clonefile` or a proven equivalent copy-on-write primitive;
  plain hard links are never used, and bounded streaming copy is only a small-fixture fallback;
- the mandatory sandbox acceptance fixture uses only the read-only system interpreter and
  standard library; an existing project `.venv` may be exposed only as an explicitly resolved
  read-only toolchain subtree with bytecode/cache writes redirected to private temp;
- no user Home, credentials, agent sockets, Docker socket, or complete Host environment;
- no external network or loopback;
- no automatic installation of sandbox binaries or helper packages;
- capability probe and rule compilation fail closed;
- no automatic fallback from Auto Sandboxed to Host.

The existing 120-second outer tool timeout is unchanged. A sandbox call reserves at most
15 seconds for snapshot preparation, 75 seconds for the requested command, 15 seconds for
Diff collection, and 10 seconds for cleanup, leaving 5 seconds of outer slack. Exceeding a
phase budget fails closed and still runs bounded cleanup. Oversized repositories that cannot
prepare within the snapshot count/byte/time admission limits return `sandbox_unavailable`;
they do not silently start a full-tree copy.

Loopback is reserved as a separate future capability. It is not silently implied by “run
tests”.

## Mature implementation references

### Primary: Pi

Use [`earendil-works/pi`](https://github.com/earendil-works/pi) at the fixed baseline
`@earendil-works/pi-coding-agent 0.84.2`, commit
[`209bc7b9a89b01c8fd05861cf5bbdda3e300037a`](https://github.com/earendil-works/pi/commit/209bc7b9a89b01c8fd05861cf5bbdda3e300037a).

Borrow behavior and structure from its coding-agent tools:

- small, stable read/write/edit/bash/grep/find/ls tool surface;
- replaceable operations adapters for deterministic tests;
- dual line/byte truncation and actionable continuation metadata;
- exact/unique multi-edit handling, display Diff, and unified patch;
- same-file mutation serialization;
- bounded streaming output, timeout, cancellation, and process-tree termination;
- faux-provider offline acceptance.

Use this concrete source map at the fixed commit rather than comparing against moving
`main`:

| Concern | Pi files to study | Morrow decision to record |
|---|---|---|
| tool inventory/factories | `packages/coding-agent/src/core/tools/index.ts` | small composable surface; no Pi-global registry import |
| bounded reads | `read.ts`, `truncate.ts` | line/byte limits and continuation; stricter UTF-8/path rules |
| list/find/search | `ls.ts`, `find.ts`, `grep.ts` | pluggable operations and truncation; no download/outside access |
| mutation | `edit.ts`, `write.ts`, `file-mutation-queue.ts` | exact edits and same-file serialization; add revision/atomic publication |
| process | `bash.ts`, `../bash-executor.ts`, `../exec.ts` | bounded output/cancel/tree cleanup; replace inherited Host authority |
| offline acceptance | `test/tools.test.ts`, `test/suite/README.md`, `packages/ai/src/providers/faux.ts` | deterministic scripted-provider coverage; retain Morrow ToolCycle rules |

At the start of each implementing subplan, record a short comparison note for the relevant
Pi files: behavior borrowed, behavior deliberately strengthened, and behavior rejected. That
note belongs in the eventual Stage 3 evidence matrix, not in source comments.

Do not copy Pi's security model: Pi inherits the launching user's filesystem, process,
network, and credential permissions. Morrow rejects absolute/out-of-scope paths, does not
auto-download tools, does not directly overwrite without a revision, does not auto-apply
fuzzy edits, and does not auto-run project code on Host.

Pi is MIT-licensed. Prefer independent Python implementation. Any substantial copied code
must retain the relevant copyright and license notice.

### Secondary: local Hermes checkout

Use the local Hermes implementation only as a regression catalogue, especially:

- `tools/file_operations.py`, `tools/file_state.py`, `tools/patch_parser.py`;
- `tools/terminal_tool.py`, `tools/process_registry.py`;
- `tools/approval.py`, `tools/write_approval.py`;
- tests for binary/special-file reads, stale writes, patch multi-match, output truncation,
  timeout, process cleanup, approval isolation, and command-policy bypasses.

Do not import its global architecture, approval configuration, plugin system, or background
process model into Morrow.

## Target dependency direction

```text
CLI preset selection
  → frozen PermissionProfile + WorkspaceCapability
  → AgentLoop (domain-agnostic ToolRunContext)
  → ToolExecutor
      → RegisteredTool intent resolver
      → CapabilityPolicy.evaluate(profile, OperationIntent)
      → ALLOW | REQUIRE_APPROVAL | DENY
      → ApprovalPort when required
      → thin tool handler
  → application tool factory
  → WorkspaceFile/Search/Patch/Process/Git service
  → Filesystem / rg / subprocess / native-sandbox / git adapters
```

Ownership rules:

- Core owns immutable local protocol values and result models with no CLI/SDK dependencies.
- Runtime owns generic policy evaluation, ToolExecutor sequencing, task-local facts, budgets,
  cancellation, and standard result envelopes.
- Application owns Provider-visible argument schemas, tool descriptions, factories, and
  mapping between tools and services.
- Services own path, file, patch, search, process, snapshot, and Git domain semantics.
- Adapters own concrete filesystem/process/platform calls.
- Bootstrap owns concrete composition and capability probing.
- Interface owns CLI mode selection, terminal preview, and human-readable errors.
- `AgentLoop`, `SessionOrchestrator`, and Provider adapters never branch on a concrete tool
  name or file/process/Git domain.

## Threat model and sensitive-data boundary

Treat Provider arguments, workspace contents, symlinks, Git configuration, project scripts,
and subprocess output as untrusted. User approval authorizes one described action; it does
not turn untrusted input into a broader capability or bypass output sanitization.

Add one local `SensitiveResourcePolicy`, reused by files, search, mutation, snapshot, process,
and Git services:

- deny content reads, searches, writes, patches, Diff, and snapshot inclusion for known
  credential-bearing paths such as real `.env` variants, `.netrc`, `.npmrc`, `.pypirc`,
  Git credential files, private-key names/extensions, and Morrow credential/state paths;
- allow clearly marked examples/templates such as `.env.example` through an explicit tested
  exception rather than a broad suffix heuristic;
- recognize private-key/credential magic headers before returning content even when the file
  name is ordinary;
- metadata-only listing may report a protected relative path with `protected=true`, but no
  content, match snippet, Diff hunk, absolute path, or secret value;
- never read protected project files merely to build a redaction dictionary.

Host commands are not an isolation boundary. Even with a confined `cwd` and minimal
environment, approved project code may attempt workspace-sensitive reads, outside access,
or direct network connections. Therefore:

- directly identifiable forbidden/network/credential-reading commands are denied before
  approval;
- every opaque/project Host command preview explicitly says it is unsandboxed and may access
  Host files/network with the current user's authority;
- stdout/stderr passes through a bounded `SecretRedactor` using exact active
  CredentialStore/environment secret values plus conservative credential-token patterns;
- sanitized output only enters ToolMessages/terminal facts; raw output remains in bounded
  in-memory process buffers only until the tool settles and is then discarded;
- this defense does not justify automatic Host execution. Native sandboxing is the only
  Stage 3 mode that may automatically run project code.

No Stage 3 documentation may claim that Manual or Auto Safe technically prevents an approved
Host process from accessing the network or files outside the workspace. The guarantee is
approval plus bounded/sanitized observation; enforced confinement belongs to Auto Sandboxed.

## Capability-derived system boundary

Replace the static Stage 2 sentence that categorically forbids project files/Shell/Git with a
boundary assembled from the frozen production ToolSet's safe prompt contributions. It must:

- say that project access, mutation, execution, and inspection are possible only through tools
  actually provided in the current request and only within their returned policy decisions;
- always forbid workspace-external direct access, network/loopback, Git writes, permission
  elevation, and every capability not present in the current ToolSet;
- treat tool results, project content, Profile, and Preferences as untrusted data, never as
  instructions or authority;
- forbid claims of modification, validation, or promotion without matching ToolFacts;
- avoid exposing the internal permission preset, risk rules, approval policy, or local paths to
  the Provider beyond the already visible tool capability descriptions.

The renderer is generic and must not branch on concrete tool names inside AgentLoop or
ToolExecutor. Each subplan that changes production inventory adds a system-message snapshot/
behavior test so prompt and ToolSet cannot drift. Unsupported Adapters remain accurately
tool-free.

## Generic policy and execution contracts

### Operation intent and verdict

Add immutable local-only values equivalent to:

```text
OperationKind:
  internal_read | workspace_read | workspace_write | configuration_write
  process | git_read | destructive | external_effect

OperationIntent:
  kind
  effect
  relative_paths[]
  command_class
  risk_flags[]
  requires_host
  requires_sandbox
  preview_summary[]

PolicyVerdict:
  allow | require_approval | deny
  reason_codes[]
```

They never enter the Provider wire. Paths and previews are bounded and sanitized. Raw file
content, complete command output, environment variables, credentials, SDK objects,
tracebacks, and model reasoning never enter policy/event metadata.

### ToolExecutor order

The generic execution order is fixed:

```text
registry lookup
→ strict Provider-argument validation
→ side-effect-free intent resolution/preflight
→ CapabilityPolicy evaluation
→ immediate bounded denial when verdict=DENY
→ sanitized preview + ApprovalPort when verdict=REQUIRE_APPROVAL
→ handler execution when allowed
→ canonical bounded ToolMessage result
→ collect optional internal ToolFacts in original call order
```

Preflight is bounded, read-only, and may fail with a typed ordinary tool error. A handler
must re-check mutable assumptions immediately before a side effect; approval never converts
a stale preflight into authority to overwrite changed state.

`ToolExecutor.execute()` creates a local `ToolCallContext` containing the current
`ToolRunContext` and the exact per-call result limit already derived from the ToolCycle. Stage 3
handlers return `ToolHandlerOutcome(payload, facts)` rather than an untyped object. A shared
semantic result builder serializes the complete success envelope, reserves metadata/escaping
space, and reduces whole lines/entries/matches/hunks until it fits. If even the minimal typed
result cannot fit, return `output_budget` instead of a JSON prefix.

For the current unknown-model baseline, `read_file` has a ceiling of 400 lines and 8 KiB of
text, but the actual window may be smaller when a multi-call cycle yields a tighter per-call
limit. List/find/search/Diff/process results obey the same dynamic envelope budget. The raw
Executor truncation path remains a legacy/emergency guard; tests assert it is never used for a
successful Stage 3 local-tool result and that continuation metadata remains valid in single-
and multi-call cycles.

Keep compatibility for current tools while migrating them to the generic intent path:

- `lookup_record` and `calculate`: internal read/compute, allow.
- `update_configuration`: configuration write, always require approval.

After migration is green, remove the obsolete registered `never|required` authorization branch;
all production tools use intent/verdict. Local tool factories may still declare display/audit
metadata, but no second approval engine remains.

`ToolEffect` remains an audit/display classification. It is not the authorization engine.

### Task-local facts without Stage 4 persistence

Introduce a generic process-local `ToolRunContext` and `ToolFact` path without changing the
public event lifecycle:

- one context is created at the start of each `AgentLoop.run_task()`;
- tool handlers may return a normal payload plus sanitized internal facts;
- `ToolExecutor` records facts in original execution order;
- mutation facts form the current run's `ChangeSet` and process facts form `CommandResult`;
- Provider-visible ToolMessages contain bounded facts needed for truthful final reasoning;
- a read-only `show_changes` tool can query the current run's accumulated ChangeSet;
- Session retains at most the latest process-local run facts for terminal inspection;
- Stage 4 later persists and associates them with AgentRun/Artifact records.

`ToolFact` is a strict tagged union with a bounded common header:

```text
kind, call_id, tool_name, ordinal, relative_paths[], approval_verdict
```

Change facts add operation, before/after revision, changed-line/byte counts, Diff truncation,
and optional current-run `change_set_id`. Command facts add command class, status,
exit-code/signal, duration milliseconds, output truncation, and redaction flags/counts. Git
facts add repository state and Diff truncation without object content. No fact contains full
arguments, full content/output, absolute paths, secrets, SDK objects, or tracebacks.

After `turn.completed`, the terminal queries the Session's latest facts and prints one bounded
summary line (for example changed-file count, validation exit state, truncation/redaction), not
a Diff. This does not add fields to public events. The same facts derive an optional, local-only
`RunMetricsSnapshot`: task outcome, tool success/failure, approval/rejection, timeout,
cancellation, changed-file count, and validation outcome. Metrics are disableable at composition,
JSON-serializable for local export, never uploaded, and not persisted before Stage 4.

This mechanism stays generic: AgentLoop does not inspect change/process fact types.

Stage 3 deliberately cannot satisfy the long-term “persist intent before side effect” invariant
because TaskRun/AgentRun storage does not exist yet. Atomic mutation, approval, and in-process
facts are the temporary boundary; the final evidence records this explicit Stage 4 handoff rather
than claiming crash-durable audit.

## Workspace filesystem contract

All Provider paths are non-empty workspace-relative POSIX-style strings. Reject absolute
paths, `~`, Windows drive/UNC paths, NULs, path traversal, empty segments with ambiguous
meaning, and normalized paths outside the frozen root.

For existing paths:

- resolve against the frozen workspace root;
- inspect every component and the final object;
- allow reads through symlinks only when the final real target remains inside the root;
- reject symlink components for mutation paths in the MVP;
- accept regular files/directories only as appropriate;
- reject devices, sockets, FIFO, and other special files.

Apply `SensitiveResourcePolicy` after safe path resolution but before content access. Search
and directory traversal never follow directory symlinks; a symlinked regular file is readable
only when its real target is inside the root and is not protected. Stable sorting uses
`(relative_path.casefold(), relative_path)` so case ties remain deterministic.

For new paths, verify the nearest existing parent is a real directory inside the root and
contains no symlink component. `write_file(mode="create")` may create at most four missing
intermediate directories in the same call; patch/replace never create parents. Preflight and
approval preview list those directories, every component is revalidated before creation, and
ChangeSet records them as auxiliary creates. On failure/cancellation, remove only still-empty
directories created by that call in reverse order; never remove a pre-existing or raced
non-empty directory. Race/swap tests must prove an outside sentinel is never read or modified.

Text reads use strict UTF-8, preserve/report BOM and newline metadata, and never silently
replace decoding errors. Binary detection rejects NUL-containing or undecodable files.

Initial service limits are constants covered by tests rather than changes to bundled
`agent-policy.toml`:

- read window ceiling: at most 400 lines and 8 KiB text per call, reduced further to fit the
  current `ToolCallContext` result budget;
- directory listing: at most 500 entries and depth 4;
- find: at most 1,000 results;
- search: at most 100 matches with bounded context;
- regular source file admission: at most 8 MiB in Stage 3;
- all services perform semantic truncation to the current RunPolicy-derived result limit before
  returning; Executor prefix truncation is not a continuation mechanism.

Continuation metadata must tell the model how to request the next read window.

## Provider-visible Stage 3 tool surface

Names and first-version contracts are locked for the implementation plan:

| Slice | Tool | Essential arguments | Result facts |
|---|---|---|---|
| 3B | `list_directory` | relative `path`, bounded `depth`, `max_entries` | sorted entries, type/size, truncation |
| 3B | `read_file` | relative `path`, 1-based `start_line`, `line_count` | text window, revision, continuation |
| 3B | `find_files` | relative `path`, glob/name `pattern`, `max_results` | stable relative paths, truncation |
| 3B | `search_text` | path, pattern, literal/regex, case, optional glob/context | path/line/snippet matches, engine, truncation |
| 3C | `apply_patch` | path, expected SHA-256, ordered exact edits | actual revision and unified Diff |
| 3C | `write_file` | path, content, `create|replace`, expected SHA-256 for replace | actual create/replace ChangeSet |
| 3C | `show_changes` | no external path authority | current run's bounded actual ChangeSet |
| 3D | `run_command` | exactly one of `argv[]` or shell string, relative cwd, bounded timeout | structured `CommandResult` |
| 3E | `promote_sandbox_changes` | current-run change-set id and eligible relative-path subset | approved real-workspace ChangeSet or conflict |
| 3F | `git_status` | none | parsed branch/head/worktree facts |
| 3F | `git_diff` | staged flag and optional relative paths | bounded unified Diff |

Tool schemas are strict Pydantic v2 models with `extra="forbid"`. Descriptions tell the
model when not to call a tool and never claim a capability that the current mode cannot
authorize.

Subplan 30 records the search choice in `docs/decisions/stage-3-search-adapter.md`. Search uses
an installed `rg` adapter when available and a deterministic Python fallback; Morrow never
downloads `rg`.

The rg adapter uses fixed argv with no shell: `--json`, `--hidden`, `--no-config`,
`--color=never`, explicit internal exclusion globs, literal `-F` or regex mode, explicit case
mode, validated optional glob, `--`, and one resolved relative search root. It never uses
`--follow` or `--no-ignore`, removes `RIPGREP_CONFIG_PATH`, and respects workspace `.gitignore`,
`.ignore`, and `.rgignore` only as subtractive visibility rules; ignore files never grant path
authority. Adapter execution has a 10-second search deadline plus semantic output limits.

The Python fallback never follows directory symlinks and admits at most 10,000 regular files,
32 MiB total scanned bytes, and 10 seconds, stopping with typed truncation metadata rather than
waiting for the outer 120-second timeout. A parity corpus covers literal, common regex, case,
glob, hidden/ignored/protected paths, and no-match behavior. Results expose the engine and
budget reason so supported-subset differences are observable.

## File mutation and ChangeSet contract

### Revisions and matching

`FileRevision` contains SHA-256, size, and `mtime_ns`. SHA-256 is the write authority:

- `apply_patch` requires the exact revision returned by `read_file`;
- `write_file(mode="replace")` requires the exact existing SHA-256;
- `write_file(mode="create")` requires the target to remain absent;
- stale/missing/mismatched baselines return `conflict` without writing;
- no last-write-wins and no fuzzy automatic matching.

`apply_patch` accepts multiple exact `{old_text,new_text}` edits. Every `old_text` must be
non-empty, unique in the original content, and non-overlapping with all other matches. All
matches are computed against the same original file, then applied in reverse offset order.
Preserve UTF-8 BOM, newline style, final newline, and file mode where supported.

### Publication

Use a simple mutation-service lock keyed by canonical target path and hold it until filesystem
work and cleanup fully settle, including cancellation. The current Executor remains serial;
Stage 3 does not design a general lease protocol or concurrent mutation runtime.

Publication uses a same-directory temporary regular file, bounded write, file `fsync`, atomic
replace, and parent-directory `fsync` where supported. Revalidate the target and parent
immediately before publication. On failure, preserve the original and remove only the exact
validated temporary file created by Morrow.

`ChangeSet` reports create/modify, before/after revision, relative path, bounded unified Diff,
truncation, and external-conflict status. Delete/rename/chmod/link operations are not exposed
in Stage 3.

Approval preview is metadata-driven rather than selected by tool-name branches. Existing Host
and configuration previews keep the 8 × 200-character ceiling. Mutation and sandbox-promotion
tools may request up to 40 sanitized Diff lines and 4 KiB total, showing the relative path,
operation, thresholds/risk, actual preflight unified Diff, and an explicit truncation marker.
Control/ANSI characters are escaped while meaningful Diff prefixes/indentation are preserved.
Manual users never approve a stats-only file mutation. `show_changes` returns actual current-run
ChangeSet Diff even before Git tools arrive; `git_diff` later covers the broader worktree.

Use Python stdlib `difflib`; adding a third-party Diff dependency requires separate approval.

## Host process and CommandResult contract

`run_command` supports either structured `argv[]` or an explicit shell string, never both.
It does not accept model-supplied arbitrary environment dictionaries, stdin, TTY, password
prompts, or sudo. A Host request timeout is capped at 90 seconds, reserving at least 10 seconds
for process-tree cleanup inside the unchanged 120-second outer deadline. Explicit shell strings
are always classified as high-risk Host execution and never gain automatic approval.

`CommandResult` includes sanitized stdout/stderr, original byte/line counts, truncation, and
redaction flags/counts. Redaction may remove content without changing the underlying process
exit status; the model is told that output was redacted but never receives the matched value.

`ProcessExecutionService` must:

- resolve `cwd` inside the workspace/snapshot;
- use a minimal allowlisted environment and remove credential/agent/proxy variables;
- launch a new process group/session where supported;
- read stdout and stderr concurrently with incremental UTF-8 decoders;
- retain bounded tails plus original byte/line counts and truncation flags;
- enforce a command timeout below the outer tool timeout so cleanup has a bounded grace;
- on timeout/cancel, terminate the complete process tree, wait, then escalate once if needed;
- reap children and never use wall-clock sleeps as test assertions;
- sanitize retained output before it can enter Provider messages, events, terminal rendering,
  logs, or task-local facts; raw output is never persisted.

Normal non-zero exit, signal exit, and service-enforced timeout are completed
`CommandResult` states, not infrastructure exceptions. Spawn failure, policy denial,
sandbox-unavailable, output-decoding failure, and cleanup failure are typed tool errors.

Command preflight is structural, not a security proof. It flags shell syntax, redirection,
pipes, command substitution, network tools, package installation, Git writes, deletion,
permission/process control, system paths, and opaque programs. String deny-lists are defense
in depth only; they do not justify automatic Host execution.

Direct Git command classification also rejects `--git-dir`, `--work-tree`, absolute `-C`, and
equivalent environment/config redirection. This does not make approved Host project code safe;
it only prevents obvious command-line bypasses.

An approved opaque Host command can still perform effects hidden inside project code. The
approval preview and final evidence must state this limitation; tests may prove classifier
coverage for known commands, but must not label that as operating-system isolation.

The public event lifecycle remains unchanged. Stage 3 returns bounded final process results;
live stdout/stderr event streaming requires separate approval because it changes public event
semantics.

## Git inspection contract

Git is read-only and separate from arbitrary Shell classification:

- resolve `--show-toplevel`, `--git-dir`, and `--git-common-dir` with fixed non-mutating probes;
  the worktree root must equal the frozen workspace root and Git metadata/common directories
  must remain inside it. Linked worktrees or external object directories return typed
  `external_git_metadata` in Stage 3 rather than gaining a hidden outside-read exception;
- `git_status`: `git status --porcelain=v2 -z --branch` parsed into typed facts;
- `git_diff`: bounded unified Diff with `--no-ext-diff`, `--no-textconv`, no pager/color, and
  optional validated relative paths;
- use fixed argv, `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`, disabled pager, and
  global/system config isolation; override local `core.fsmonitor` and other executable
  extension points so status/diff cannot launch user programs or refresh the index;
- suppress protected-file Diff content through `SensitiveResourcePolicy` while retaining a
  bounded metadata marker;
- no add/commit/checkout/reset/clean/pull/push/merge/rebase/config/credential operations;
- non-repository is an ordinary typed result;
- user pre-existing changes are recorded separately from Morrow's task-local ChangeSet.

## Ordered subplans

| Order | Roadmap slice | File | Status | Depends on |
|---|---|---|---|---|
| 29 | 3B.1 | `subplans/29-stage3-policy-workspace-foundation.md` | completed | completed Subplan 28 + Gate P0 passed |
| 30 | 3B.2 | `subplans/30-stage3-read-search-tools.md` | completed | 29 |
| 31 | 3C | `subplans/31-stage3-file-mutation-diff.md` | completed | 30 |
| 32 | 3D | `subplans/32-stage3-host-process-execution.md` | completed | 31 |
| 33 | 3E | `subplans/33-stage3-native-sandbox.md` | completed | 32 |
| 34 | 3F | `subplans/34-stage3-git-and-acceptance.md` | completed | 33 |

Execution is strictly serial. A later subplan may be refined after evidence from the current
one, but it may not begin early or change files owned by the active subplan in parallel.

## Temporary compatibility and activation strategy

- Subplan 29 changes only generic policy/context/composition contracts; production project
  tools remain absent until its tests are green. It replaces the static Stage 2 system boundary,
  but `auto-sandboxed` process authorization stays unsupported rather than being predesigned.
- Subplan 30 atomically registers read-only tools and updates the former Stage 2 capability
  guard to use exact allowed inventory plus continuing forbidden capability families. At this
  cutover, `lookup_record` and `calculate` leave production registration but remain explicit
  test fixtures; `update_configuration` stays.
- Subplan 31 atomically registers mutation tools after conflict/atomic-write tests are green;
  it is accepted with actual Diff/ChangeSet and threshold evidence.
- Subplan 32 adds Host process execution. Manual/Auto Safe never auto-run project code.
- `auto-sandboxed` may exist as a parsed enum earlier but remains unavailable until Subplan 33
  proves a real backend. Selecting it before then returns a controlled unsupported result.
- Subplan 33 enables Auto Sandboxed only after platform probe, snapshot, isolation, escape,
  and no-fallback tests pass; it adds only the current-run, always-approval-required
  `promote_sandbox_changes` subset tool.
- Subplan 34 adds only structured read-only Git tools and then runs final product/package
  acceptance, local metrics/fact-summary acceptance, and prompt/inventory reconciliation.
- README/ARCHITECTURE capability claims are updated only after the corresponding production
  path and tests are green.

## Error taxonomy

Extend ordinary bounded tool errors with stable categories as needed:

```text
permission_denied
unsupported_capability
protected_resource
output_budget
path_outside_workspace
invalid_path
not_regular_file
binary_file
encoding_error
file_too_large
conflict
command_rejected
sandbox_unavailable
sandbox_violation
process_spawn_failed
process_cleanup_failed
git_unavailable
not_a_repository
external_git_metadata
```

Do not include raw OSError text, absolute sensitive paths, subprocess environment, stderr
secrets, or tracebacks in Provider results/events. Human-facing terminal messages remain
specific enough to recover without exposing hidden data.

## Test strategy

### Deterministic default

- Default tests remain offline under the existing socket guard.
- Use injected operations/adapters, scripted Providers, fake clocks, events, pipes, and small
  temporary repositories.
- Do not assert timing with sleeps. Coordinate real process tests with files/pipes/events and
  poll bounded state only when an OS process boundary makes injection impossible.
- Never use a real credential or paid Provider for mandatory evidence.
- Scripted Provider tests prove plumbing and product behavior, not model intent quality.

### Security matrix

Cover every profile/verdict combination and at least:

- capability-derived system prompt snapshots at every inventory cutover, including tool-free
  Adapters and proof that Profile/Preferences cannot select permission mode;
- semantically complete result envelopes under 16,000-character and multi-call 56,000-character
  baselines; Executor prefix truncation must stay unused for successful local tools;
- absolute, `..`, UNC/drive, NUL, alias, case, symlink, missing-parent, and swap races;
- bounded parent creation, partial failure/cancellation cleanup, and four-level depth edges;
- regular/binary/invalid UTF-8/large/sparse/device/FIFO/socket files;
- exact patch, multi-match, overlap, stale hash, external mutation, cancellation, and failed
  atomic publication;
- every per-call and cumulative Auto Safe mutation threshold immediately below/at/above the
  boundary, plus bounded Diff approval preview/truncation;
- approval accept/reject/unavailable/timeout/cancel paths;
- protected workspace credential files, misleading filenames/private-key headers, Diff/search
  redaction, exact credential-value output redaction, and false-positive-safe templates;
- Host process non-zero, signal, timeout, cancellation, descendant cleanup, output overflow,
  invalid UTF-8, environment secret sentinel, and shell bypass attempts;
- sandbox Home/outside sentinel read/write, network/loopback, symlink, subprocess, environment,
  snapshot mutation, CoW/copy admission, phase timeouts, toolchain mounts, promotion conflicts,
  cleanup, backend absence, and no-fallback behavior;
- Git repository/non-repository, dirty/staged/untracked/detached states, path filtering,
  external metadata/linked worktree rejection, external diff/textconv suppression, output
  truncation, and write-command absence;
- rg fixed argv/config isolation and ignore semantics; fallback file/byte/time budgets;
- bounded terminal fact summary plus enabled/disabled local metrics without public-event drift.

### Product scenarios

Final acceptance includes at least two different Fixture projects:

1. A Python project with one deterministic failing test: search, read, patch, Diff, run test,
   correct a first failed attempt, and report the final evidence.
2. A non-Python/text project with nested files and pre-existing user changes: locate, edit,
   preserve unrelated changes, inspect Git, and produce an honest unverified/verified result.

Run each applicable story through Fake Provider and the real terminal composition. Exercise
Manual approval and Auto Sandboxed snapshot execution separately.

Real model evaluation or external-network tests are optional and require an explicit user
request plus compatible credentials. Absence is recorded as `not run`, never as pass.

## Validation gates

Each subplan runs its focused tests plus:

```bash
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff check .
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run python -m compileall -q src tests
git diff --check
```

Use the temporary cache override only because this checkout has previously encountered an
unwritable default `uv` cache; current command output remains authoritative.

Before completing Stage 3, run:

```bash
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest -m 'not live'
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest --collect-only -q
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff format --check .
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff check .
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run python -m compileall -q src tests
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run morrow --help
git diff --check
```

Also build a fresh wheel, inspect its inventory, install it into a fresh environment using
available offline dependencies, import Morrow, load the bundled policy, and run installed
`morrow --help`. Report exact observed counts/hashes rather than reusing historical results.

Native sandbox evidence is platform-specific. The current macOS host must pass Gate P0 and the
definitive Subplan 33 escape/isolation run before Stage 3 starts/completes under this plan. Linux
rule construction is tested everywhere, but Linux support is claimed only after a real Linux
runner passes; missing Linux execution is `unsupported`, not pass and not a macOS release blocker.
Windows Auto Sandboxed remains an explicit non-target. Any missing claimed-platform backend is a
failure, never a passing skip.

## Documentation and acceptance artifacts

During implementation:

- keep `.agent/PLAN.md`, active subplan, `TODO.md`, `TRACKER.md`, and `LOG.md` synchronized;
- create `docs/acceptance/stage-3-local-code-agent-evidence.md` in Subplan 34;
- create `docs/decisions/stage-3-search-adapter.md` in Subplan 30 before search implementation;
- create `docs/decisions/stage-3-mutation.md` in Subplan 31 before mutation acceptance;
- create `docs/decisions/stage-3-process.md` in Subplan 32 before process acceptance;
- update `docs/ARCHITECTURE.md` only when the actual dependency graph changes;
- update README only for capabilities proven in the production composition;
- keep `docs/ROADMAP.md` and the Stage 3 roadmap honest about incomplete later slices;
- record the Pi fixed baseline and any intentionally different Morrow behavior in the final
  evidence matrix.

## Hold points requiring new user authority

Stop and ask before:

- adding any third-party Python dependency;
- changing bundled `agent-policy.toml` defaults;
- changing the public event lifecycle or exposing command output in public events;
- enabling network/loopback, browser, MCP, remote execution, package installation, Git
  writes, delete/rename/chmod/link, TTY, sudo, or credentials in child processes;
- enabling Full Access or persisting permission grants;
- automatically installing `rg`, bubblewrap, or any OS helper;
- replacing the native sandbox requirement with Docker or an unsandboxed fallback.

If Gate P0 cannot prove the current-host filesystem/network/toolchain contract, no Stage 3
subplan is activated. If a later definitive probe regresses, stop before enabling the preset.
Do not silently redefine Stage 3 as Manual + Auto Safe or weaken the contract without an
explicit user decision.

## Definition of done

Stage 3 is complete only when all of the following are directly evidenced:

1. The exact final common production inventory is `update_configuration`, `list_directory`,
   `read_file`, `find_files`, `search_text`, `apply_patch`, `write_file`, `show_changes`,
   `run_command`, `git_status`, and `git_diff`; supported native-sandbox composition adds only
   `promote_sandbox_changes`. All require compatible Adapters/capabilities. Demo
   `lookup_record`/`calculate` remain test fixtures, not production.
2. The capability-derived system boundary matches the frozen ToolSet and never contradicts an
   available tool; unsupported Adapters remain accurately tool-free.
3. Permission dimensions are frozen locally and never enter Provider arguments/protocol;
   configuration/Profile/Preferences cannot change them.
4. Policy returns allow/approval/deny dynamically from validated operation intent, with no
   retained static production approval branch.
5. Manual and Auto Safe never auto-run Host project code or opaque commands.
6. File/list/find/search cannot disclose workspace-external, special-file, or protected content.
7. Reads are strict UTF-8, revisioned, capped at 400 lines/8 KiB before dynamic tightening, and
   preserve actionable continuation inside every complete RunPolicy-bounded envelope.
8. Search fixed argv/ignore semantics and Python fallback file/byte/time budgets match the ADR.
9. Patches/writes enforce exact per-call/cumulative Auto Safe thresholds, detect stale baselines,
   create at most four safe parent levels, publish atomically, preserve unrelated work, and
   return actual Diff/ChangeSet facts.
10. Manual mutation and promotion approval displays bounded actual Diff, never stats only.
11. Delete/rename/chmod/link and Git writes are absent from the Provider tool surface.
12. Host commands have structured results, bounded/redacted stdout/stderr, a 90-second maximum,
    timeout/cancel semantics, and no leaked descendants or environment secrets.
13. Auto Sandboxed passes real current-host evidence and runs only in a CoW/admitted temporary
    snapshot with phase budgets, no Host fallback/network/Home/credential/socket access, and no
    direct real-workspace mutation.
14. Sandbox-generated text creates/modifications can be promoted only by the always-approval-required,
    current-run subset tool through ordinary conflict-safe mutation; other changes stay discarded.
15. Git inspection is read-only, bounded, hook/textconv/fsmonitor-safe, rejects external Git
    metadata/linked-worktree escape, and separates pre-existing user changes from Morrow facts.
16. Protected credential content and secret-bearing process output do not enter Provider
    messages, events, terminal output, logs, state, Diff, or sandbox snapshots.
17. Host execution documentation and previews state its real non-isolation boundary; no
    classifier test is presented as filesystem/network containment.
18. ToolFacts use the locked bounded schema; terminal summary and disableable local metrics are
    derived without changing public events or claiming Stage 4 persistence.
19. AgentLoop, ToolExecutor, SessionOrchestrator, Provider adapters, public event lifecycle,
    and ConversationLog ownership remain domain-agnostic and valid under success, denial,
    timeout, failure, and cancellation.
20. Two Fixture product stories and real terminal composition produce honest tool-backed final
    claims with no mandatory evidence gap.
21. Full offline, quality, package, documentation, security, and claimed-platform sandbox gates
    pass with exact recorded evidence; unsupported platforms are never labeled pass.
22. Full Access and crash-durable intent/fact persistence remain unsupported until Stage 4
    `CapabilityGrant`/AgentRun work.

Every clause is now green for the claimed macOS platform. Stage 3 is closed; Stage 4 requires a
separate scoped implementation subplan and its own persistence/recovery gates before production
code is changed.
