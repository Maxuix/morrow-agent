# Subplan 65 — Skill Package and Catalog Foundation

> Status: completed; review repair applied locally
> Branch: `codex/feat/stage6-skill-catalog`
> Prerequisite: Subplan 64 complete

## Objective

Add immutable Skill identity/version contracts, safe managed package storage, deterministic discovery
and conflicts, and Operational Store v14. Do not enable Skills or inject context yet.

## Ownership

- `src/morrow/core/skills/` contracts for definition, version, manifest, provenance, validation and
  catalog projection
- `src/morrow/adapters/skills/` for package parsing, canonical tree hashing and managed storage
- `src/morrow/application/skills/catalog.py` and query projections
- `src/morrow/adapters/state/migrations_v14_skills.py`, `skill_journal.py`, thin registrations
- focused Skill fixtures and `tests/test_skill_catalog.py`, `tests/test_skill_migration_v14.py`

## Tasks

1. Implement strict namespaced manifest decoding while retaining basic Agent Skills `SKILL.md`
   compatibility. Requested Trust and permission declarations remain non-authoritative.
2. Implement safe IDs: stable `skill_id`, Morrow-generated `skv_` version ID, separate display
   version and normalized names. Reject separators, dot paths, NUL, reserved names and normalization
   collisions.
3. Build canonical package trees from regular files only, including relevant executable mode; reject
   symlink/hardlink ambiguity, devices/FIFOs/sockets, root escape, duplicate normalized paths,
   oversize files/count/tree and unsupported encodings. Hash and consume bytes from the same safe
   open instead of validating a path and reopening it later.
4. Write and verify immutable `managed-version.json` envelopes with per-file and tree digests,
   provenance and lifecycle evidence refs. Never trust a package-provided envelope.
5. Discover builtin/user/generated/imported and configured project-read-only roots without writing
   the user's project.
6. Implement exact conflict rules: same ID/same digest fold; same ID/different digest blocks;
   different ID/same name is explicit ambiguity; scope/source never silently overrides.
7. Compute effective Trust from local source/provenance only and expose requested/effective values
   separately.
8. Add v14 tables for Skill definitions, versions, catalog operations, AgentRun selections/contexts
   and their reference constraints. The domain API uses `None` for global scope; SQLite v14 stores
   that value as a non-null empty-string sentinel so composite uniqueness and foreign keys work.
   Workspace rows retain exact `ws_` IDs. Reserve later-use tables completely so v14 checksum never
   changes after release.
9. Keep SQL and row mapping in `skill_journal.py`; the existing aggregate Journal only delegates.

## Validation

- Manifest, package budget, path, Unicode/case collision, symlink, envelope spoof, tree drift,
  source/scope isolation and all conflict combinations.
- v13→v14 migration, repeat migration, interrupted migration, checksum/future schema refusal and
  empty rebuild.
- Focused Skill tests, operational-store migration tests, standard quality commands and full
  non-live suite at the migration gate.

## Review repair evidence

- v14 global scope rows use a non-null empty-string SQLite sentinel; domain APIs still expose
  scope_id=None for global and map it at the journal boundary.
- Discovery rejects directory symlinks at the source/skill/version/package roots, rejects hardlinks,
  parses manifests from the bytes captured during canonical hashing, validates envelope structure,
  recomputes local Trust, and checks version-directory identity.
- Skill versions are insert-only with identical retries accepted; definition Trust survives
  round-trip; preparation/rehydration failures yield ordered error events; model overrides cannot
  widen adapter capabilities.

## Exit criteria

- Catalog can truthfully show valid, invalid, conflicted and unavailable Skills without enabling
  anything.
- Every managed version is immutable, path-safe and digest-verifiable.
- v14 is fixed, doctor-decodable and contains no Draft/Usage or MCP tables.
