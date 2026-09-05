# Stage 8 Subplan 10 — Context, Learning and Skill Management GUI

Date: 2026-09-05. Activation base: verified local `main` at `672ba90`.
Implementation branch: `feat/stage8-context-learning-skill-gui`; existing checkout, no extra worktree.
Implementation checkpoint: `5cd8ed3`.

## Delivered behavior

- Active Context Bar and a keyboard-accessible modal Drawer expose the selected Task/AgentRun's
  frozen Preferences, Profile, selected immutable Knowledge revisions and source revisions.
  A task without an admitted run stays unresolved; another task's last run is never substituted.
- Workflow leaf admission now receives the shared Preference loader and current Profile from
  production composition. Existing leaf recovery continues to restore its frozen snapshot.
- Preference management supports Workspace/Global add, replace, enable, disable and tombstone
  deletion, with current scope/revision/evidence and typed before/after Writer history. Profile
  supports its existing field operations and reset under document OCC.
- Learning has Proposed/History views for generic Preference proposals and existing Learning
  candidates, including source evidence, current target, conflicts, edit-and-accept, rejection and
  suppression. Knowledge retains its candidate/evidence promotion path, revision history and
  enable/disable/dispute/delete lifecycle. Lists have bounded navigable pages.
- Management GETs are read-only even on the writable Core, unlike the legacy Inbox's optional
  lazy expiry. Expired Learning candidates are shown as expired and excluded from the pending count.
- Skills expose source, version, scope, status, summary, requested tools/capabilities/MCP,
  scripts/trust and existing usage/validation evidence. Enable/Disable/Pin/Update/Rollback reuse
  the existing lifecycle. Update selects an already installed immutable version; package import
  continues through `morrow skill install`.
- Generated Skills appear first as Drafts, with bounded digest-verified SKILL.md text, text/file
  differences, evidence and validation. Editing creates a new revision. Acceptance publishes a
  version without enabling it. Package drift removes the publishable validation result; redacted
  document text cannot be used to overwrite its original.
- `/v1/management/*` and `morrow manage` share one application facade and strict command models.
  Mutations keep existing domain writers, OCC, receipts and file-publication sagas. GUI preserves
  command identity on uncertain retries, exposes conflicts, disables writes while disconnected,
  and refreshes projections without adding a public ApplicationEvent type.

## Calibration against current domain contracts

The generic Preference model has no language/verbosity-specific fields. The bar directs the user
to frozen rules instead of guessing from statements. Knowledge add/replace uses an evidenced
candidate and explicit conflict resolution; it does not invent a direct record writer. Existing
usage and validation results are displayed as evidence, not an inferred quality evaluation.
The later Stage 8 feedback/evaluation subplan remains inactive. Preferences/Skills remain bounded
by their existing document/catalog limits; Preference Writer history shows up to 500 batches.

## Deterministic acceptance

`tests/test_stage8_context_management.py` contains 30 tests covering these behaviors:

| Case | Evidence |
|---|---|
| GUI/CLI equivalence | Real Typer command entry and authenticated ASGI handlers over identical deterministic stores compare replies, canonical event bytes, revisions and all management projections |
| Preference lifecycle | Add/replace/disable/enable/remove plus replay, stale revision refusal and unchanged resolved history |
| Other mutation classes | Profile; Preference and Learning accept/edit/reject/suppress; Knowledge lifecycle; Skill enable/disable/pin/update; Draft create/edit/validate/accept/reject |
| Skill rollback | Two installed versions, binding digest OCC, rollback and repeated command without another event |
| Frozen runtime context | Real scripted Core Workflow with two leaves; original rules persist after current YAML edits; foreign task/run selection returns 404 |
| Immutable Knowledge | Real admitted MemorySelection still resolves the selected old revision after its Head is disabled |
| Read-only pagination | 105 distinct expired candidates across three pages; unchanged events and candidate rows; pending count excludes them |
| Draft integrity | Readable original document, edited text/file diff, drift failure, and no accidental active package |
| Boundary validation | Profile commands cannot target Provider/credential fields or global configuration |

The earlier focused management/Core API/Skill Draft matrix passed **51 tests**. Later management
additions passed 29 tests; its pagination fixture initially violated the existing fingerprint
uniqueness constraint. Distinct seeded proposals corrected the fixture, and its targeted test
passed. The final full regression includes all corrected tests.

## GUI and browser acceptance

- `pnpm --dir gui typecheck`: passed.
- `pnpm --dir gui test`: **93 passed**; Context tests cover frozen display, escaping, no inferred
  language/verbosity, generated Draft separation and authenticated stable command identity.
- `pnpm --dir gui build`: passed. JS **500.9 KiB / 700 KiB**, CSS **44.2 KiB / 120 KiB**;
  all assets **945.0 KiB**. Vite's generic chunk-size advisory is below the project's explicit budget.
- Ruff format/check, Python compileall, CLI/manage help and `git diff --check`: passed.

Browser acceptance used `scripts/gui_context_smoke_server.py` with provider-free fixtures under
`/tmp/morrow-context-smoke-2` and `/tmp/morrow-context-smoke-3`, listening only on 127.0.0.1:8810.

1. Opened the Context Drawer and verified an unstarted run does not display current YAML as
   actually resolved context.
2. Saved a Preference and observed document revision 1 → 2 plus its Active card.
3. Accepted a Learning candidate and observed the new active Knowledge revision and evidence entry.
4. Enabled an imported test Skill and observed it move from inactive to Active.
5. Inspected a generated Draft's complete SKILL.md, edited it, observed r2 and exact text/file diff,
   then published it and verified the generated version remained inactive.
6. Saved Profile and observed revision 1 → 2 with the new value. Escape closed the modal.
7. Inspected narrow-window screenshots of Active Skill and expanded Draft diff: navigation wraps,
   text wraps and the content area scrolls independently.

The created browser tab and both fixture servers were closed. No real Provider, MCP service,
external-network test, user credentials or production workspace data was used.

## Full regression, package and integration

Final full offline regression: **1709 passed, 2 skipped, 2 Live deselected in 934.01 seconds**,
exit 0. Command: `UV_CACHE_DIR=/tmp/morrow-subplan10-uv uv run --offline pytest -m 'not live' -q`.
Output: `/tmp/morrow-subplan10-final-gate.log`. Both skips are existing host-level Seatbelt tests,
which intentionally skip inside the nested Codex sandbox. All 30 management tests passed.
The earlier full attempt was interrupted after discovering the new fixture issue, so it is not
counted as a passing gate.

`uv build --offline` successfully built the sdist and wheel using the existing system uv cache.
The initial attempt with a temporary cache lacked Hatchling; no dependency or version was added.
Wheel inspection verified all 18 GUI asset entries byte-for-byte against the final build, as well
as the management application and CLI modules. Verified implementation and final refresh/acceptance were fast-forwarded into local `main` at
`1e7c215`. The topic contained zero commits absent from main, was deleted, and no extra worktree
remains. Main was 41 commits ahead of locally recorded origin/main and 0 behind before the final
documentation closure commit. No fetch or push was attempted.

Remote fetch/push and Live tests remain unauthorized. Subplan 11 is pending activation.
