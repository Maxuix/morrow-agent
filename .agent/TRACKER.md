# Progress Tracker

## Current status

Subplan 91 is verified complete and committed as `9c6e2ba`: Morrow's default composition now exposes the Pi-aligned seven core
tools plus conditionally composed configuration/Skill capabilities. Full offline validation passed
`1344 passed, 2 deselected`; no live Provider request or credential access occurred. S7P-09 is
reactivated, but no pre-repair campaign/profile can be reused.

## Active task

S7P-09 is blocked before plan/run admission. Obtain a sufficient total token ceiling, then rebuild
the comparison plan from clean commit `9c6e2ba` and a new create-only evidence root.

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

## Next action

Do not create or admit a partial campaign. The known hard minimum is 58,754,419 total tokens and
still excludes six unknown-usage requests; 80M remains the recommended safe total ceiling.

## Blockers

- The unrelated `docs/notes/` remain preserved in the named stash and isolated from this worktree.
- Remote publication remains unauthorized.
- The approved 50M total token ceiling is insufficient for the immutable 28-run campaign.
