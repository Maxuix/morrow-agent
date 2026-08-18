# Subplan 34 — Stage 3F Read-Only Git and Final Acceptance

> Status: completed and post-implementation-review remediated on 2026-08-18; claimed platform: macOS
> Depends on: Subplan 33 completed and accepted

## Goal

Add structured read-only Git inspection, prove the full locate–modify–validate–report product
loop in multiple realistic fixtures and the real terminal composition, reconcile current
documentation, and close Stage 3 only with exact security/package/platform evidence.

## Scope and expected ownership

- add `services/git.py` and `adapters/local/git.py`;
- extend `core/local_tools.py` and `application/local_tools.py` with Git result/tool models;
- extend bootstrap exact inventory and terminal/product acceptance;
- add `docs/acceptance/stage-3-local-code-agent-evidence.md`;
- reconcile README, ARCHITECTURE, ROADMAP, Stage 3 roadmap, and execution-state documents;
- run full offline, package, security, and claimed-platform gates.

No Git writes, network, Full Access, persistent Session/Task/Artifact, Skills/MCP, browser,
background task, or multi-agent work.

## Executable tasks

### S3.34.1 — Implement safe Git inspection

- Resolve top-level, git-dir, and common-dir without changing state. Require top-level to equal
  the frozen workspace root and metadata/common directories to stay inside it; linked worktrees
  or external object stores return typed `external_git_metadata` rather than an outside-read grant.
- Implement parsed `git_status` from fixed porcelain-v2 NUL-delimited output.
- Implement bounded `git_diff` for unstaged/staged and validated relative path filters.
- Disable pager, color, external diff, textconv, prompts, credentials, optional index locks,
  global/system config, local fsmonitor, and other executable extension points with fixed argv,
  environment, and per-command config overrides.
- Suppress protected-file Diff hunks through SensitiveResourcePolicy while retaining a bounded
  metadata marker; Git inspection must not become a credential-reading bypass.
- Return branch/head/detached, staged/unstaged/untracked/conflict facts and truncation.
- Keep pre-existing user changes separate from task-local Morrow ChangeSets.
- Expose no general Git argv/tool and no write-capable subcommand.

### S3.34.2 — Lock the final production tool inventory

- Assert the exact common set for function-tool-capable Adapters:
  `update_configuration`, `list_directory`, `read_file`, `find_files`, `search_text`,
  `apply_patch`, `write_file`, `show_changes`, `run_command`, `git_status`, and `git_diff`;
  supported native-sandbox composition adds only `promote_sandbox_changes`.
- Assert `lookup_record` and `calculate` remain available only through explicit test-fixture
  registries and consume no production prompt/schema budget.
- Assert tool-free behavior for unsupported Adapters.
- Assert no delete/rename/chmod/link, Git write, network, browser, MCP, Skill, persistent
  Session, background, Full Access, or permission-elevation tool.
- Prove every Provider schema is strict and contains no local capability metadata.
- Snapshot the final capability-derived system boundary and replace substring-based forbidden
  keyword checks with exact inventory plus still-forbidden capability families.

### S3.34.3 — Run complete Fake Provider product stories

- Python fixture: search/read a failing implementation, make a first imperfect patch, run a
  failing test, correct it, rerun successfully, inspect changes/Git, and give a fact-matching
  final answer.
- Non-Python/text fixture: navigate nested files, preserve pre-existing user changes, patch a
  target, inspect Diff, and honestly report any unrun validation.
- Manual path includes approvals, rejection/recovery, Host timeout/cancel, and a subsequent
  healthy turn.
- Auto Safe path automatically applies only bounded structured edits and still prompts for
  Host project code.
- Auto Sandboxed path automatically validates in a temporary snapshot, proves real workspace
  protection, then exercises current-run subset promotion approval, success, conflict, and
  expiry without model-supplied replacement content.

### S3.34.4 — Run real terminal and security acceptance

- Exercise production bootstrap and `run_repl` with Scripted Provider, one shared terminal/
  PromptSession/ApprovalPort, and exact mode selection.
- Confirm previews are bounded and sanitized; secret sentinels are absent from terminal,
  events, ToolMessages, stored state, and diagnostics.
- Confirm Host approval and documentation state the lack of OS-level filesystem/network
  isolation; classifier coverage must not be reported as confinement evidence.
- Re-run the complete path/file/mutation/process/sandbox/Git attack matrix.
- Verify all success, denial, unavailable, conflict, timeout, cancellation, and internal
  failure paths close legal ToolCycles and recover where specified.
- Verify public event lifecycle/payload and ConversationLog ownership did not drift.
- After `turn.completed`, query latest ToolFacts and render one bounded terminal summary line;
  verify no Diff/secret enters the public event or summary.
- Derive the master-plan `RunMetricsSnapshot`, test enabled/disabled behavior and JSON-safe local
  export, and prove there is no upload or persistence.

### S3.34.5 — Create requirement-to-evidence matrix

- Map every master-plan definition-of-done clause to exact tests, source checks, product
  scenarios, platform probes, commands, and observed results.
- Include the fixed Pi per-slice comparison notes: borrowed behavior, deliberate Morrow
  hardening, rejected behavior, and license treatment.
- Record current Git identity, tool inventory, Pi reference commit, sandbox backend/version,
  platform coverage, skipped/unavailable platforms, and any optional Live status.
- Separate fake/injected adapter evidence from real filesystem/process/sandbox execution.
- Never label a missing backend, credential, network, or platform run as passed.
- Record the Stage 3 exception to crash-durable pre-side-effect intent/fact persistence and its
  explicit Stage 4 AgentRun/Artifact handoff.

### S3.34.6 — Reconcile product and architecture documentation

- Update README setup, permission modes, exact tool surface, approvals, path/mutation safety,
  Host warning, Auto Sandboxed behavior, sandbox availability/platform claims, Git external-
  metadata boundary, terminal summary/local metrics, and recovery.
- Update ARCHITECTURE actual dependency graph, policy/fact ownership, tool composition,
  service/adapter boundaries, and current runtime flow.
- Update ROADMAP/Stage 3 status only after all mandatory gates are green; retain Full Access
  in Stage 4 and all later-stage exclusions.
- Reconcile PLAN/TODO/TRACKER/LOG/subplan index without erasing historical evidence.

### S3.34.7 — Run final quality and package gates

- Run strict collection, full non-Live suite, Ruff format/check, compileall, CLI help,
  capability/source/secret scans, Markdown/reference audit, and `git diff --check`.
- Build a fresh wheel, inspect inventory/hash, install in a fresh environment from available
  offline dependencies, import Morrow, load bundled policy, and run installed CLI help.
- Run real sandbox isolation only on available claimed platforms and record exact evidence.
- Optional real-model evaluation remains separate, explicit, and non-blocking unless the user
  later makes it a release requirement.

## Completion criteria

- Structured Git status/diff is read-only, bounded, parsed, and protected from executable
  external diff/textconv/fsmonitor/prompt behavior and outside Git metadata.
- Protected credential/private-key content is absent from file/search/Diff/sandbox/process and
  all Provider, terminal, event, log, and state surfaces under the acceptance matrix.
- Exact production inventory and forbidden-capability scans pass.
- Final system boundary, ToolFact schema, terminal summary, and optional local metrics match the
  implemented inventory without public-event or persistence drift.
- Two distinct project fixtures complete the required product stories with truthful final facts.
- Manual, Auto Safe, and supported Auto Sandboxed behavior matches the fixed policy matrix.
- All path, mutation, process, sandbox, Git, approval, cancellation, and secret-isolation gates pass.
- Full offline and package acceptance is green with exact current evidence.
- README/ARCHITECTURE/ROADMAP and execution-state files describe only implemented behavior.
- Full Access remains unavailable and is handed to Stage 4 CapabilityGrant work.

## Delivered result

A fully accepted Stage 3 local Code Agent that can locate, modify, validate, inspect, and
truthfully report real project work under explicit workspace, approval, and native-sandbox
boundaries. The subsequent external 12-finding implementation review was verified against current
code, remediated without expanding Stage 3 scope, and accepted through refreshed offline,
host-level macOS sandbox, quality, and wheel gates.
