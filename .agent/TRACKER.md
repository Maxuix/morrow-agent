# Progress Tracker

## Current status

Subplan 92 implementation and validation are complete. The current seven-tool surface is the only
production callable coding interface; obsolete Provider contracts and factories are removed.
Historical recovery names remain data compatibility, not production tools.

## Active task

Commit and fast-forward the verified cleanup into the parent S7P-09 execution branch.

## Evidence

- Core model-visible names/fields match Pi: `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls`.
- Default inventory fell from 15 to 9 because configuration and Skill capabilities remain composed;
  Auto Sandboxed adds one promotion tool.
- Canonical tool-schema size fell from 11,951 to 5,923 bytes; the Pi-aligned seven-tool core is
  2,742 bytes versus Pi's 3,627 bytes in the retained frozen profile.
- `edit`/`write` infer revision/mode; absolute-inside paths normalize; unknown harmless fields are
  ignored. Outside paths, protected resources, stale plans and disallowed bash effects still fail
  execution-side before unsafe publication.
- Full gate: `1344 passed, 2 deselected in 87.69s`; Ruff format/check, compileall, CLI help and
  `git diff --check` passed.
- Retained formal attempts account for 16,754,419 known tokens plus six unavailable-usage requests.
  The approved 50M total leaves 33,245,581 known-token headroom before unknown-usage allowance.
- Removed 13 retired Provider schema/model/factory families from production local tools. Current
  production declarations no longer accept their names; a separate legacy table preserves old-row
  recovery classification.
- Focused migrated coverage passed `176`; final offline gate passed `1327 passed, 2 deselected in
  87.08s`. Ruff format/check, compileall, CLI help and `git diff --check` passed.

## Next action

Integrate the verified child commit, then return to the unchanged S7P-09 capacity hold.

## Blockers

- The unrelated `docs/notes/` remain preserved in the named stash and isolated from this worktree.
- Remote publication remains unauthorized.
- The approved 50M total token ceiling is insufficient for the immutable 28-run campaign.
