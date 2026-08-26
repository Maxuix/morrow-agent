# Stage 7 Preflight Reliability Repairs — S7P-04 Workspace Change Lifecycle

> Status: active
> Active subplan: 83 — delete, move, rename and sandbox promotion lifecycle
> Branch: `codex/feat/s7p-04-workspace-change-lifecycle`
> Base: verified local `main@20e6ce3`
> Source authority: the user-requested S7P-04 checklist, current code and deterministic probes

## 1. Objective

Complete the basic Direct-agent workspace change lifecycle for regular files. Add structured,
revision-checked delete, move and rename operations beside existing create, patch and replace;
promote approved sandbox deletes and unambiguous moves; and preserve truthful durable recovery and
partial-failure evidence. Every effect must continue through the existing ToolExecutor,
CapabilityPolicy, approval, durable execution and ChangeSet boundaries.

S7P-04 does not add copy, directory mutation, recursive deletion, overwrite-on-move, chmod/link,
run-level undo, Git writes, completion verification, public-event changes, runtime-policy default
changes or dependencies.

## 2. Located defects

1. `MutationOperation` currently contains only `create`, `patch` and `replace`; production exposes
   only `apply_patch` and `write_file` for structured file mutation.
2. `WorkspaceMutationService` has no preflight/apply contract for an expected-revision deletion or
   a two-path move/rename, and `FileSystemAdapter` has no confined unlink or atomic no-replace move
   primitive.
3. A deterministic probe of a deleted sandbox file returns `operation='deleted'` with
   `eligible=False` and no content, so `promote_sandbox_changes` cannot select it.
4. Sandbox promotion assumes every eligible change is create/replace text and loops one mutation
   at a time. It neither represents source/destination identity nor describes partial publication
   truthfully if a later selected change fails.
5. Durable prepared intent freezes one `FileMutationEvidence` from `MutationPlan`. Recovery's
   `observe_file()` treats an expected absence as `MISSING`, so it cannot reconcile a completed
   delete or the source half of a move.
6. `MutationResult`/`MutationStatus`, ChangeToolFact and ChangeSet rendering have no deleted/moved/
   renamed result vocabulary or source/destination structural Diff.
7. Production tool inventory, recovery declarations, Provider schema audit and local acceptance
   tests contain no delete/move/rename contracts.

Reproduction at the activation baseline:

```text
operations ['create', 'patch', 'replace']
sandbox [('deleted', False, None)]
```

## 3. Frozen design decisions

### Operation surface

- Add three explicit Provider-visible tools: `delete_file`, `move_file` and `rename_file`. Keep
  `write_file` create/replace and `apply_patch` unchanged. Explicit tool names make approval,
  recovery and model-correction diagnostics unambiguous.
- `delete_file(path, expected_sha256)` deletes one existing regular file.
- `move_file(source_path, destination_path, expected_sha256)` moves one existing regular file to a
  different workspace-relative path. It may cross workspace directories but not filesystems.
- `rename_file(source_path, destination_path, expected_sha256)` is the same-parent form and rejects
  a destination outside the source parent. `move_file` may also target the same parent but remains
  recorded as `move`; the explicit rename tool is the semantic contract used when rename is asked.
- Source revision is mandatory. Destination must be absent. There is no `overwrite`, `force`,
  recursive or best-effort mode. Missing source/destination parent, existing destination, stale
  source, identity race or path change is a conflict, never success.
- Only regular files are admitted. Directories, symlinks, hard-to-classify special files and any
  symlink in either ancestry are rejected. Delete never removes a directory; move/rename never
  creates destination parents. Protected paths/content remain protected at both source and target.
- Copy is deliberately omitted: neither the checklist acceptance cases nor the current failures
  require a distinct copy operation, while create already represents explicit new content.

### Filesystem publication and conflicts

- Extend `MutationPlan` into a bounded operation plan capable of describing source and destination
  evidence without storing file content in durable state. Preserve existing write behavior and
  compatibility for create/patch/replace.
- Lock every affected path in stable lexical order, revalidate all parent/source/destination facts
  under lock, then publish. Delete uses confined directory-fd unlink. Move/rename uses an atomic
  no-replace primitive (`renameatx_np`/`renameat2` or an equivalently proven adapter operation) and
  fails closed when no atomic no-clobber primitive can be proven; plain overwrite-capable rename is
  not an allowed fallback.
- Same-filesystem atomic move is required. Cross-device errors are bounded failures and do not
  degrade into copy-plus-delete. After publication, verify expected absence/presence/hash and
  fsync affected parent directories where supported.
- Unified result facts add `deleted`, `moved` and `renamed` statuses, source/destination relative
  paths and bounded structural Diff. Actual ChangeSet entries are recorded immediately after each
  successful effect; assistant prose is never the change authority.
- User dirty changes remain visible and safe: every destructive source needs the exact current
  hash and every destination must be absent at both preflight and atomic publication. No Git reset,
  checkout, clean or implicit restoration is introduced.

### Approval, cancellation and composition

- Delete, move, rename and sandbox promotion of those effects are persistent workspace writes and
  always carry destructive-mutation approval risk. Ordinary, Auto Safe and Auto Sandboxed modes do
  not bypass approval for these operations.
- Intent resolution performs full bounded preflight and produces a value-safe preview before the
  durable assistant/tool-execution transaction. Handler entry occurs only after durable intent is
  visible and any approval has been consumed through the current coordinator.
- Approval denial or cancellation before handler entry has no filesystem effect. Cancellation
  after synchronous publication begins follows the existing shielded mutation rule: wait for the
  bounded filesystem operation to reach a truthful terminal observation; never pretend rollback.
- Register new tools through normal factories and production composition only. Do not add
  tool-name branches to AgentLoop, ToolExecutor or SessionOrchestrator, and do not change public
  event lifecycle or runtime-policy defaults.

### Durable evidence and crash recovery

- Generalize prepared file evidence to return an ordered tuple. Delete freezes one source item with
  `before_sha256` and expected kind `absent`. Move/rename freezes two items: source expected absent,
  destination expected the source digest/size and previously absent.
- `observe_file()` must treat expected absence as an expected result, while a still-present matching
  source is before-state. For a move, both observations expected means completed; both before means
  safe-to-retry; duplicated, mixed, missing-evidence or third-party states require reconciliation
  or outcome-unknown and never synthesize success.
- Keep `RECONCILEABLE_FILE_WRITE` with a frozen recovery declaration for all new tools. Add crash
  tests at prepared/awaiting-approval/executing/handler-completed boundaries. No full arguments,
  file contents, secrets, tracebacks or SDK objects enter durable intent or recovery reports.

### Sandbox delete/move promotion and multi-operation truth

- Make a deleted regular text file eligible when its baseline bytes/hash fit existing promotion
  budgets. Preview shows its deletion Diff and promotion calls the same delete mutation service.
- Detect a move only when one deleted baseline file and one created current file form an
  unambiguous one-to-one identity by hash, size and mode. Same-parent identity is `renamed`, other
  identity is `moved`. Ambiguous duplicate-content cases remain separate create/delete changes;
  the collector must not guess identity.
- A move selection is one logical change that carries both source and destination paths. Selection,
  approval preview, changed-path summary and ChangeSet preserve that pair deterministically.
- Preflight all selected promotion changes in stable order before any effect. Promotion is not
  advertised as run-level atomic. If a later effect fails after an earlier effect was applied, the
  tool returns a bounded partial-failure result, the already-applied ChangeSet remains visible, and
  durable disposition is failed/interrupted rather than success. No rollback overwrites user data.
- Sandbox preview and promoted real-tree result must match for created, modified, deleted and
  unambiguous moved/renamed files. Source/destination drift between snapshot and promotion is a
  conflict.

## 4. Test-first implementation sequence

1. Add failing domain/schema tests for the three operations, strict source/destination arguments,
   status/result vocabulary, production inventory and recovery declarations.
2. Add failing mutation-service matrices for success, stale hash, missing source, existing target,
   outside-root, symlink ancestry/leaf, directory/special-file rejection, protected resources,
   same-parent rename rule, no destination-parent creation and no-overwrite race.
3. Implement confined unlink and atomic no-replace move/rename in `FileSystemAdapter`; add fault and
   capability tests proving unsupported primitives fail closed and no outside/user file changes.
4. Implement explicit tool factories, previews, approval/cancellation behavior, ChangeToolFact and
   ChangeSet results. Update bootstrap, capability/tool inventory and static Provider-contract audit.
5. Generalize prepared file evidence and recovery observation/classification for expected absence
   and two-path moves. Add intent-before-effect, approval crash and restart reconciliation tests.
6. Extend sandbox change modelling/collection to eligible deletes and unambiguous identity moves;
   preserve ambiguity, selection and deterministic ordering. Route promotion through the same
   mutation service.
7. Add promotion tests for preview/result parity, deletion, move/rename, stale source/destination,
   approval denial, stable multi-change order and injected partial failure with visible prior facts.
8. Add a scripted Direct-agent acceptance task that deletes and renames files through production
   tools and verifies the final tree/ChangeSet without live Provider claims.
9. Update `docs/ARCHITECTURE.md` and the stale Stage-3 capability inventory to the actual structure,
   publish `docs/acceptance/s7p-04-workspace-change-lifecycle.md`, update execution state and run
   focused plus repository-wide offline gates.

## 5. Validation

```bash
uv run pytest -q tests/test_local_mutation.py tests/test_local_tool_factories.py
uv run pytest -q tests/test_sandbox.py tests/test_tool_contract_audit.py
uv run pytest -q tests/test_stage4_tool_persist.py tests/test_stage4_recovery.py tests/test_stage4_recovery_crash.py
uv run pytest -q tests/test_stage3_product_acceptance.py tests/test_code_agent_mini_eval.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

No live Provider/model/Pi/MCP/network/credential test is authorized. Temporary workspaces, fault
injection and scripted Providers supply deterministic offline evidence.

## 6. Completion, review and integration

- After coherent verified commits, the dedicated Luna Max implementation task must spawn one
  read-only `gpt-5.6-luna` / `max` subagent in that same task to review the complete activation-
  base...HEAD diff.
- The review must focus on no-clobber/TOCTOU, symlink and directory-fd confinement, delete/move
  recovery truth, two-path durable evidence, dirty user changes, sandbox identity ambiguity,
  promotion partial failure, approval/cancellation and false-positive tests.
- The implementation task reproduces and fixes every confirmed finding, reruns affected and full
  offline gates, commits acceptance/execution state and leaves a clean branch.
- It does not merge, push, delete its branch/worktree, touch the three user-owned research
  documents or start S7P-05. The root task verifies ancestry/cleanliness, fast-forward merges into
  local `main`, then retires clean task resources.
