# Subplan 23 — Handoff Documentation and Historical Reconciliation

> Status: completed
> Depends on: Subplan 22

## Goal

Make every current product, architecture, roadmap, command, state, and acceptance statement
match the Handoff-free code while preserving completed Stage 1/2 records as explicit
history. Every repository occurrence must be classified in an auditable artifact; unrelated
mentions of handoff in external-system research/reviews must not be mechanically rewritten.

## Executable tasks

### HR.23.1 — Reconcile current user documentation

- Rewrite README product wording so it does not claim current cross-process continuation.
- Remove `/handoff` and Handoff-backed `/continue` commands and examples.
- Document exact `/new`, `/exit`, clean EOF, and confirmation-EOF behavior.
- State the current boundary explicitly: workspace identity, Profile, Preferences, Provider
  configuration, and credential references persist; ConversationLog is process-local;
  restart recovery and resume are unavailable.
- Document that legacy Handoff files are ignored and not automatically read, migrated, or
  deleted.

### HR.23.2 — Reconcile the architecture baseline

- Remove Handoff service/state/context/startup/exit-save/read-only flows from architecture
  diagrams and prose.
- Update ownership to Profile, Preferences, Provider/global config, workspace index, and
  Session-owned process-local ConversationLog.
- Update corruption isolation and document-envelope descriptions to the actual Profile and
  Preferences documents.
- Document the named `SessionApplication` composition root and the simplified command/
  terminal lifecycle where appropriate.
- Keep Stage 3/4/5 exclusions explicit.

### HR.23.3 — Separate current and future roadmap claims

- Rewrite present-tense continuity claims in `docs/ROADMAP.md` into two explicit sections:
  - **current:** process-local ConversationLog with no restart recovery; persisted identity,
    Profile, Preferences, Provider configuration, and credential references;
  - **long-term Stage 4:** persistent Sessions, resume/list/archive/delete, Fork, summaries/
    checkpoints, and memory, subject to a later approved design.
- Keep Stage 1's historical result truthful but mark Handoff transitional and removed after
  Stage 2.
- Update `docs/roadmap/stage-4-sessions-context-and-memory.md` so Stage 4 starts from
  Profile/Preferences plus process-local ConversationLog; remove Handoff as an authority,
  prerequisite, or automatic migration source.
- Make no future migration promise. Any explicit import of legacy data requires a separate
  product/data decision and plan.
- Do not choose Stage 4 storage technology or begin its implementation.

### HR.23.4 — Create an auditable reference-classification artifact

- Add `docs/acceptance/handoff-reference-classification.md`.
- Record each remaining repository occurrence with path, category, owning action, and
  rationale. Categories are:
  - current product/source/test reference to remove;
  - historical Stage 1/2 evidence to retain and label;
  - legacy-data non-destruction documentation/test to retain;
  - unrelated external-system/research use to retain unchanged.
- Audit README, architecture, roadmaps, proposals, reviews, acceptance evidence,
  `.agent/`, and linked Git-history references.
- Do not rewrite old acceptance results, transcripts, or design rationale as if Handoff
  never existed; add a concise document-level historical marker where confusion is likely.

### HR.23.5 — Publish removal acceptance evidence skeleton

- Add `docs/acceptance/handoff-removal-evidence.md` with requirement-to-test mapping,
  observed-result placeholders, legacy byte/non-read evidence, degraded-state contracts,
  command/exit-code matrix, package/source scans, and final gates.
- Record the temporary lack of cross-process resume and the exact Stage 4 deferral.
- Link the active plan/tracker and current roadmap to the new evidence without treating
  placeholders as passes.

### HR.23.6 — Audit references, links, help, and the integrated tree

- Run repository-wide case-insensitive reference inventory and reconcile every result with
  the classification artifact.
- Fail if a current command, architecture claim, product promise, source implementation, or
  active test expects Handoff as supported behavior.
- Allow only classified historical, legacy-data sentinel, or unrelated external-system
  occurrences.
- Check local Markdown links and references affected by deleted/renamed tests.
- Run CLI help, the full offline suite, all quality gates, and `git diff --check`; a docs
  slice must not hand a red tree to final acceptance.

## Completion criteria

- README and ARCHITECTURE describe only supported current behavior.
- ROADMAP cleanly separates current process-local behavior from future Stage 4 persistence.
- Stage 4 has no Handoff authority or implicit migration promise.
- Historical evidence remains truthful and visibly historical.
- Legacy ignored-file behavior is documented without promising import or deletion.
- `docs/acceptance/handoff-reference-classification.md` accounts for every occurrence.
- The removal evidence matrix is ready for observed final results.
- The complete offline and quality gates remain green.

## Validation

```bash
rg -n -i 'handoff|/continue|loaded_handoff|is_continuation' README.md docs .agent
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```
