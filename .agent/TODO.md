# TODO

## Current stage

Stage 6 implementation in progress.

## Active subplan

Subplan 65 — Skill Package and Catalog Foundation (completed; merged to `main`).

## Tasks (Subplan 65)

- `[x]` Strict namespaced manifest decoding (SKILL.md frontmatter + morrow.yaml) with requested
  Trust/permissions recorded as non-authoritative hints.
- `[x]` Safe IDs: stable skill_id, Morrow `skv_` version ids, display version; reject separators,
  dot paths, NUL, reserved names and normalization collisions.
- `[x]` Canonical package trees: regular files only, one safe open per file, exec mode, reject
  symlink/hardlink/device/FIFO/socket, root escape, collisions, reserved names, oversize, non-UTF-8.
- `[x]` Immutable `managed-version.json` envelopes with per-file/tree digests; package-provided
  envelopes never trusted; drift detection.
- `[x]` Discovery of builtin/user/generated/imported roots, scope isolation, bounded failures.
- `[x]` Exact conflict rules: same id/digest folds; same id/different digest blocks
  (identity_conflict); different id/same name is explicit ambiguity (name_conflict).
- `[x]` Effective Trust computed from local provenance only; requested vs effective exposed.
- `[x]` v14 tables (definitions, versions, operations, reserved AgentRun selections/contexts) with
  scope+scope_id composite identity; checksum fixed; thin journal delegation.
- `[x]` SQL and row mapping in `skill_journal.py`; aggregate journal only delegates.

## Boundaries

- Nothing is enabled, selected or injected; no Draft/Usage or MCP tables exist.
- Do not use real credentials, networked MCP Servers, live Providers or user state.
- Next: Subplan 66 (Skill lifecycle and Binding control) from latest `main`.
- Preserve AgentLoop/ConversationLog, permission policy defaults, public events and Stage 4/5 behavior.