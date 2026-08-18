# Subplan 31 — Stage 3C Conflict-Safe File Mutation and Diff

> Status: pending; not active
> Depends on: Subplan 30 completed and accepted

## Goal

Add exact patching, controlled create/replace, atomic publication, actual Diff, and task-local
ChangeSet facts without delete/rename/chmod/link capability or silent overwrite.

## Scope and expected ownership

- extend `core/local_tools.py` with FileRevision, edit, mutation, Diff, and ChangeSet models;
- extend `services/files.py` and add `services/changes.py` for mutation and task facts;
- extend `adapters/local/filesystem.py` for safe publication and a simple per-target lock;
- extend `application/local_tools.py` with mutation/show tools;
- extend ToolRunContext/handler-result wiring only generically if evidence requires;
- focused mutation/diff/policy/approval/end-to-end tests.

No delete, rename, mode/link change, Host process, sandbox, Git write, or Full Access.

## Executable tasks

### S3.31.1 — Lock revision and edit contracts

- Compare Pi `edit`/`write`/`file-mutation-queue` at the fixed commit and record the exact
  behaviors retained, strengthened with revisions/atomicity, or rejected.
- Return SHA-256, size, and `mtime_ns` from `read_file`; SHA-256 is the mutation authority.
- Define strict exact edits `{old_text,new_text}` and bounded edit count/input size.
- Compute policy facts exactly as the master plan defines: edit count, inserted+deleted lines,
  replaced+replacement UTF-8 bytes, affected-file ratio, and per-run cumulative mutation use.
- Require expected SHA-256 for every patch and replace; create requires target absence.
- Compute all matches against the same original content; require one unique match per
  non-empty old text and reject overlap.
- Preserve BOM, newline convention, final newline, and existing file mode where supported.
- Return conflict, never fallback to fuzzy matching or full-file overwrite.
- Reject creates, replacements, and patches targeting SensitiveResourcePolicy-protected paths;
  credential material must not enter Provider tool arguments or task-local Diff facts.

### S3.31.2 — Implement bounded parent creation and atomic publication

- Use a simple async lock keyed by canonical target path and hold it until filesystem work and
  cleanup settle; do not design a general lease/concurrent-executor protocol.
- Revalidate parent/target and expected revision immediately before publication.
- Reject any mutation path with a symlink component.
- For `write_file(create)` only, create at most four missing parent directories after listing
  them in intent/preview; record them as auxiliary ChangeSet creates. Patch/replace never create
  parents. On failure/cancel, remove only still-empty directories created by this call in reverse.
- Write a same-directory temporary regular file with exclusive creation, apply safe mode,
  flush/`fsync`, atomic replace, and parent `fsync` where supported.
- On every injected failure, preserve original content/metadata and remove only the exact
  validated temporary path created by Morrow.
- Test external editor changes, parent swaps, same-path contention, cancellation, disk/write/
  fsync/replace failure, and cleanup failure without broad deletion primitives.

### S3.31.3 — Generate actual Diff and ChangeSet facts

- Use stdlib `difflib` for display/unified Diff against actual before/after text.
- Bound Diff while retaining path, operation, revision, line stats, and truncation.
- Return one ChangeSet entry from every successful create/modify; unchanged is explicit and
  performs no write.
- Accumulate current-run ChangeSet facts through generic ToolRunContext in original order.
- Add `show_changes` to return only the current run's bounded actual facts; never derive the
  file list from Assistant prose.
- Re-apply SensitiveResourcePolicy before emitting Diff/ChangeSet content so a path that
  becomes protected during a race cannot leak through a stale preflight.
- Keep previous user changes distinct from Morrow mutation facts.

### S3.31.4 — Add mutation tools and mode policy

- Add strict `apply_patch` and `write_file` schemas plus thin handlers.
- Manual: prompt for create/patch/replace, with bounded relative path, operation, diff stats,
  risk reason, and actual bounded unified Diff in the preview. Mutation preview may use up to
  40 sanitized lines/4 KiB with an explicit truncation marker; stats-only approval is forbidden.
- Auto Safe/Auto Sandboxed automatically allow only the exact master-plan thresholds: one file,
  ≤8 edits, ≤64 changed lines, ≤4 KiB changed UTF-8 bytes, ≤25% of an existing file and not all
  non-empty content; create is ≤64 lines/4 KiB. Per-run automatic totals are ≤4 files, 16 edits,
  128 changed lines, and 8 KiB. Every threshold excess and all replace operations require approval.
- All modes deny delete/rename/chmod/link, outside paths, and missing/stale authority.
- Approval refusal/unavailable/timeout closes the ToolCycle and performs no filesystem write.
- Keep existing configuration approvals unchanged.
- Rebuild/snapshot the capability-derived system boundary for the mutation inventory.

### S3.31.5 — Accept a mutation-only repair path

- Fake Provider searches, reads revision, applies an exact patch, calls `show_changes`, and
  produces a final answer matching actual ChangeSet facts.
- Inject an external modification between read and patch; prove conflict, reread, corrected
  patch, and truthful recovery.
- Exercise Manual approval and Auto Safe automatic small patch through production composition.
- Test immediately below/at/above every per-call and cumulative Auto Safe threshold, plus parent
  depth 0/4/5, symlink-parent escape, failure/cancel cleanup, and Diff-preview truncation.
- Verify no user pre-existing/unrelated file is modified or attributed to Morrow.

## Completion criteria

- Exact patch/create/replace semantics, SHA-256 conflicts, unique matching, and overlap rules
  are directly tested.
- Same-file operations serialize and atomic publication preserves original content on every
  tested failure/cancel path.
- Successful mutations return actual bounded Diff/ChangeSet facts and `show_changes` reflects
  only the current run.
- Manual and Auto Safe behavior matches the fixed policy matrix.
- Parent creation is limited to four safe levels and every auxiliary directory is visible in facts.
- Manual approval sees bounded actual Diff; threshold crossing cannot bypass approval with many
  unique exact edits or repeated tool calls.
- Delete/rename/chmod/link, process, Git write, network, Full Access, and fuzzy edit remain absent.
- Protected credential/private-key paths cannot be mutated or exposed in previews/Diff/facts.
- Focused tests, full offline suite, quality gates, CLI help, and `git diff --check` pass.

## Delivered result

Morrow can make visible, conflict-safe, recoverable project edits without silently overwriting
external work.
