# Subplan 74 — Backup v2 and Doctor

> Status: completed locally
> Branch: `codex/feat/stage6-backup-doctor`
> Prerequisite: Subplan 73 complete

## Objective

Make Stage 6 cross-storage state verifiable and recoverable without changing the meaning of existing
backup bundle v1. Add bundle v2 for Extension YAML and referenced managed Skill packages, and extend
doctor with bounded Stage 6 integrity checks.

## Ownership

- versioned contracts in `core/backup.py` or a focused Stage 6 backup contract module
- focused `application/skills/backup.py`, `application/mcp/backup.py` and thin composition in
  `application/backup.py`
- focused Stage 6 doctor verifiers and thin composition in `application/doctor.py`
- backup/restore CLI compatibility only where needed
- `tests/test_stage6_backup.py`, `tests/test_stage6_doctor.py`, migration restore acceptance

## Tasks

1. Freeze existing bundle v1 creation/verification behavior and add an explicit manifest/bundle
   version discriminator with backwards decoding.
2. Define bundle v2 contents: online SQLite backup, Artifact manifest/content, global/workspace
   Extension YAML, required current configuration docs and only managed Skill versions referenced by
   bindings, runs, Drafts, Usage or rollback evidence.
3. Exclude CredentialStore values, external read-only Skill roots, unmanaged user directories, raw
   script/MCP results, SDK payloads and diagnostic stderr. Include CredentialRef names only where
   already part of sanitized config.
4. Build canonical relative paths and per-file/tree/manifest digests. Refuse symlink, duplicate path,
   root escape, drift during copy, missing referenced package/Artifact or unsupported schema.
5. Acquire a bounded extension maintenance lock, capture YAML revisions/package digests, copy
   immutable packages and run the SQLite online backup, then recheck all revisions/digests. Create
   through a temporary directory and atomic publish; failure leaves no valid-looking partial bundle.
6. Verify and restore only into an isolated target root. Validate manifest first, then SQLite
   integrity/schema, YAML schemas/digests, package envelopes/tree digests, Artifact digests and all
   cross-store references before activation.
7. Keep v1 verifier able to validate historical bundles without requiring Stage 6 files. v2 verifier
   must reject a relabeled/incomplete v1.
8. Extend doctor for v14/v15/v16 table refs, selection/context digests/budgets, Binding↔version,
   Draft/Usage refs, MCP config/catalog/run snapshots, PermissionSnapshot evidence and package drift.
9. Ensure doctor/backup output contains bounded IDs/counts/error codes only and no Skill content,
   scripts, MCP results or credentials.

## Validation

- v1 compatibility fixture; v2 round trip in isolated roots; every file/ref tamper; missing package/
  Artifact; YAML/SQLite future schema; symlink/path traversal; interrupted create/restore.
- Restore historical run/context/MCP evidence without consulting the source root.
- Doctor healthy/degraded/failing cases and cross-workspace reference corruption.
- Focused backup/doctor/migration tests, standard quality commands and full non-live suite.

## Exit criteria

- v1 semantics are unchanged and v2 completely covers Stage 6 authoritative/referenced state except
  explicit exclusions.
- Restore never activates unverified partial state.
- Doctor detects all declared cross-store integrity failures without leaking content.

## Completion evidence

- Added explicit v1/v2 manifest contracts, atomic v2 creation/verification/restore, bounded YAML and
  managed Skill capture, Artifact integrity checks, cross-store Skill/MCP/reference validation and
  credentials exclusion.
- Added Stage 6 Doctor checks for v14/v15 Skill catalog, Binding, run evidence, Draft/Usage links and
  managed package drift, gated by the store schema so older stores remain diagnosable.
- Focused suite: `tests/test_stage6_backup.py` — 5 passed; affected backup/Doctor/MCP/Skill suite —
  32 passed. Full offline gate: `1056 passed, 2 skipped, 2 deselected`.
- Ruff format/check, `compileall`, required CLI help commands and `git diff --check` passed. No live,
  networked or credentialed path was run.
