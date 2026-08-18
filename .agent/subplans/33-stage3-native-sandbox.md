# Subplan 33 — Stage 3E Native Sandbox and Auto Sandboxed

> Status: completed; accepted 2026-08-18
> Depends on: Gate P0 passed and Subplan 32 completed and accepted

## Goal

Add native OS sandbox backends and a temporary project snapshot so Auto Sandboxed can run
project commands automatically without network, Host Home/credential/socket access, Host
fallback, or direct mutation of the real workspace.

## Scope and expected ownership

- add `services/sandbox.py` for capability probe, snapshot, manifest, Diff, and cleanup;
- add `adapters/local/sandbox.py` plus platform-specific macOS/Linux backends;
- reuse `services/process.py` and CommandResult rather than create a second process model;
- extend bootstrap/CLI mode activation and application tool policy;
- add deterministic fake-backend tests and real claimed-platform isolation/escape tests.

No Docker, network/loopback capability, automatic OS package install, Full Access, durable
Artifact store, raw Host auto mode, or public event change.

## Executable tasks

### S3.33.1 — Revalidate Gate P0 as the definitive platform probe

- Record why Pi's inherited Host authority is rejected for automatic execution; reuse only
  its bounded process/result ergonomics, not its security assumptions.
- Re-run the exact current-host/backend/toolchain/CoW probes recorded by Gate P0 after the
  intervening implementation; any regression fails closed before CLI activation.
- Record executable/version/feature evidence without installing or changing Host state.
- Prove the candidate can deny outside file read/write and network while running a basic
  project command with required read-only system/toolchain access.
- Define `SandboxCapability` with supported/unavailable/degraded reason; degraded cannot
  authorize Auto Sandboxed.
- If the locked contract cannot be proven, stop this platform slice and request a user
  decision; never substitute Docker or Host.

### S3.33.2 — Implement a bounded project snapshot

- Build a task-private temporary snapshot from the current workspace, including current
  uncommitted source changes.
- Prefer APFS `clonefile` or a proven equivalent CoW primitive. Never use plain hard links for
  writable files. Permit bounded streaming copy only for admitted small fixtures when CoW is
  unavailable; otherwise return `sandbox_unavailable`.
- Never follow external symlinks or copy special files; preserve safe internal symlinks and
  file modes where supported.
- Exclude every SensitiveResourcePolicy-protected credential/private-key file and Morrow
  state path; project commands receive no real secrets merely because they are in the workspace.
- Exclude VCS object storage and bounded known cache/build directories according to a tested
  internal snapshot policy; do not read user ignore files as authorization to escape.
- Enforce file count/byte limits and a 15-second preparation budget; return a controlled failure
  before sandbox launch rather than attempting a slow whole-tree copy.
- Capture baseline revisions for every copied file needed to compute post-command Diff.
- Validate the exact temp root before cleanup and never traverse cleanup symlinks.

### S3.33.3 — Implement platform-independent sandbox contract

- Define one injected backend Port: probe, prepare rules/mounts, launch through the existing
  process adapter, classify violation, and cleanup.
- Generate a minimal environment with private HOME/TMP/cache directories.
- Expose snapshot writable, required executable/runtime/system paths read-only, and nothing
  else except backend-required virtual system paths.
- Make the mandatory acceptance fixture standard-library-only with the resolved system
  interpreter. If supporting a project `.venv`, expose only that resolved subtree read-only and
  redirect bytecode/cache/temp writes to private paths; never bind all of the real workspace.
- Remove credentials, proxy variables, SSH/GPG agents, Docker socket, keychains, and Morrow
  state paths.
- Deny all network including loopback and inherited sockets.
- Backend/rule/start failure returns `sandbox_unavailable`/`sandbox_violation`; no Host retry.
- Enforce phase budgets inside the unchanged outer timeout: prepare 15s, command 75s, Diff 15s,
  cleanup 10s, and 5s slack. Timeout/cancellation in any phase still performs bounded cleanup.

### S3.33.4 — Add macOS and Linux adapters

- macOS: generate a bounded Seatbelt-class profile from resolved trusted paths; default deny,
  explicit read/write/process allowances, and network deny.
- Linux: construct fixed bubblewrap argv with new namespaces, read-only system/toolchain binds,
  writable snapshot/private temp, hidden Home/state/socket paths, and unshared network.
- Do not invoke either backend through a model-controlled shell string.
- Unit-test rule/argv construction for macOS and Linux on every platform. The current macOS host
  must run the real escape suite; Linux is claimed only after a real Linux runner passes and is
  otherwise explicitly unsupported, not a macOS completion blocker. Windows is a declared
  Auto Sandboxed non-target for this Stage 3 release.

### S3.33.5 — Collect snapshot Diff and promote safely

- After command exit, compare snapshot against its baseline with bounded hashes/Diff.
- Return sandbox ChangeSet/Artifact facts separately from real-workspace ChangeSet facts.
- Extract the bounded current-run SandboxPromotionBundle (opaque id, eligible text payloads,
  hashes, and captured real-workspace revisions), then discard the snapshot. Retain the bundle
  only in task-private memory/temp until `run_task()` settles and clean it on every terminal path.
- Add strict `promote_sandbox_changes(change_set_id, paths[])`. It can reference only the current
  run's recorded sandbox ChangeSet and only a validated subset of eligible text create/modify
  paths; the model supplies no replacement content.
- Promotion always requires user approval in every preset, displays the master-plan bounded
  actual Diff, and delegates each selected path to Subplan 31's conflict-safe mutation service
  using captured real-workspace revisions and cumulative mutation facts.
- Never promote delete/rename/chmod/link or binary changes in Stage 3.
- A conflict during promotion preserves both the real workspace and the reported sandbox Diff.

### S3.33.6 — Enable and accept Auto Sandboxed

- Enable the CLI preset only when probe reports a supported backend.
- Auto Sandboxed routes process intent only to NativeSandboxProcessAdapter; Host execution is
  a policy denial, not a fallback.
- Snapshot-local create/modify/delete is allowed because the snapshot is disposable; only
  eligible text create/modify facts can be proposed for ordinary conflict-safe promotion.
- Exercise automatic test/build commands, shell strings, timeout/cancel, output truncation,
  snapshot modification, and optional safe promotion through Fake Provider and real terminal.
- Rebuild/snapshot the capability-derived system boundary with Auto Sandboxed and
  `promote_sandbox_changes`; no internal mode/policy metadata enters Provider schemas.
- Escape suite attempts outside/Home/credential/socket reads/writes, network/loopback, external
  symlinks, child processes, inherited environment, and real-workspace mutation.
- Verify every attempt is denied/unavailable and sentinel state remains unchanged.

## Completion criteria

- Auto Sandboxed is enabled only after a real backend probe succeeds.
- Commands run in a writable temporary snapshot; the real workspace is not writable by them.
- Home, credentials, agent/Docker sockets, Morrow state, external filesystem, network, and
  loopback are unavailable under real claimed-platform tests.
- Missing/broken backends fail closed with no Host fallback.
- Snapshot changes are bounded facts, discarded by default, and promoted only through the
  current-run subset tool and ordinary conflict-safe service after approval.
- CoW/copy admission, standard-library toolchain, phase budgets, and current-host real escape
  suite are green; Linux support is claimed only if separately real-tested.
- No Docker, network grant, Full Access, global permission persistence, or raw auto mode enters.
- Focused/unit/real-platform tests, full offline suite, quality gates, CLI help, and
  `git diff --check` pass.

## Acceptance evidence — 2026-08-18

- Gate P0 was revalidated on the current host: macOS 26.6.1 / Darwin 25.6.0 / arm64,
  `/usr/bin/sandbox-exec`, the system Python 3.14 runtime, APFS `clonefile` success with a
  distinct inode and unchanged source, and a restricted probe that blocked workspace/Home/
  `.ssh`/loopback/external writes or reads as specified. No Docker, helper installation,
  repository write, or persistent Host change was used.
- The real host-level macOS escape suite passed both production isolation cases: the Seatbelt
  command could write only its private snapshot, while real workspace, Home, protected `.env`,
  and network/loopback attempts remained blocked; Auto Sandboxed production composition exposed
  the native command and approval-required promotion path without changing the real workspace.
- Native backend builder tests cover default-deny Seatbelt rules, fixed bubblewrap argv, snapshot
  exclusion/CoW-or-copy admission, internal/external symlinks, bounded Diff, protected content,
  and promotion through Subplan 31 mutation services. Host process tests cover timeout and
  descendant cleanup without wall-clock assertions.
- `UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest -m 'not live'`: 387 passed, 2 skipped
  only because the nested Codex sandbox cannot run host-level Seatbelt tests, 1 deselected.
  Host-level rerun of the two skipped macOS tests: 2 passed. Ruff format/check, compileall,
  `morrow --help`, and `git diff --check` all passed.
- Linux rule construction is tested, but Linux runtime support is explicitly unsupported until a
  real Linux runner passes; Windows remains outside this Stage 3 target.

## Delivered result

Morrow can automatically run project commands inside a native, no-network, copy-isolated
workspace while protecting the user's real project and Host data.
