# Progress Tracker

## Current status

Subplan 94 removed repository-only compatibility surfaces and historical evaluation data. The
change deletes 3,135 net lines. Focused suites passed, followed by the complete offline gate at
`1338 passed, 2 deselected`; Ruff format/check, compileall, CLI help and diff checks also passed.

## Active task

Commit and integrate the verified Subplan 94 checkpoint.

## Next action

Retire the topic branch, then activate the generic Preference current-format slice from the latest
verified `main`.

## Blockers

None. Existing external user data has not yet been transformed; that work belongs to the next
persisted-state slice.
