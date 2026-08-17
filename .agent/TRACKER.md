# Progress Tracker

## Current status

Stage 2 is complete after S2.20.12 closed the remaining review defects and the offline suite stayed green.

## Last completed task

S2.20.12 fixed NL config `ContextBudgetError` crashing the REPL, reused per-turn tool-call IDs being reported as `internal`, the Provider deadline wrapping public yields, and missing `GeneratorExit` turn cleanup.

## Next action

No Stage 3 work is active. Start a new plan only when Stage 3 is explicitly requested.

## Active task

None.

## Blockers

None.

## Active boundary

- Complete: Subplans 17–19 and S2.20.1–S2.20.10.
- Complete: Subplan 20 including S2.20.11 review remediation.
- The production bootstrap enables only the two approved in-memory tools when Adapter capability permits; unsupported Adapters remain plain chat.
- All operational limits now come from resolved RunPolicy; no numeric compatibility bridge remains.
- Stage 3/4/5 capabilities remain out of scope.
