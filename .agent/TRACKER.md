# Progress Tracker

## Current status

Subplan 78 is locally complete on `codex/feat/stage7-direct-baseline` from
`main@05e3603090dce7955d89f72f08f7df2ed2d7b120`.

The 10-task Code Agent Mini Eval is present and enumerable. The current public Direct Agent launch
path is `morrow --dir WORKSPACE`; the configured model is `opencode-go/mimo-v2.5`. Stage 7's roadmap
requires this single-Agent success/cost/rework baseline before Workflow work begins.

The starting worktree is intentionally dirty with user-owned changes to `docs/ROADMAP.md`,
`docs/IMPROVEMENT_OPPORTUNITIES.md`, `.agents/` and `evals/`. They must be preserved and excluded
from repair commits unless the final evidence report specifically belongs with the completed eval
dataset.

The dataset self-check passed all 10 items. Provider readiness passed outside the Codex filesystem
sandbox with the existing Keychain credential; the evaluation uses a disposable state root seeded
only with the current non-secret global configuration.

The public-interface `EXTERNAL-001` smoke run passed its external oracle (`13 passed`). Morrow's
terminal summary reported 13 tool calls, 10 successes, 3 failures, two modified files and validation
passed. Two write approvals were required. `phone_number.py` was the intended change; an untracked
`test_phone_number.py` remained even though the Agent said it had cleaned the temporary test. Record
this as an unexpected modification and possible delete/promotion workflow gap, not yet as a Stage 7
blocker.

## Active task

Integrate the verified repair and acceptance record into local `main`, then retire the topic
branch without absorbing the user's pre-existing roadmap, improvement-list, `.agents/` or `evals/`
changes.

The first full baseline is complete: `PASS 2`, `FAIL 8`, `BLOCKED 0`, `INCONCLUSIVE 0`. All six
Morrow-history tasks failed before any file modification after 32–49 tool calls and the 30-round
limit. `EXTERNAL-001` and `EXTERNAL-002` passed. `EXTERNAL-003` and `EXTERNAL-004` reached working
implementations but failed hidden callback/redefinition semantics and are classified as Agent
reasoning failures, not tool defects. All four external runs left Agent-created test files.

The two narrow repairs are complete. Durable prepared intents retain bounded policy reason codes
and expose safe recovery guidance; native sandbox execution exposes only exact read-only current
runtime/workspace `.venv` roots. `MORROW-001` and `MORROW-003` reruns reached viable project command
execution but remained FAIL because of Agent/round-budget behavior. No additional tool repair is
justified by the evidence.

The acceptance report is `docs/acceptance/stage7-direct-agent-baseline.md`. Focused tests passed
(`37 passed`), host-level native sandbox tests passed (`2 passed`), and the full offline gate passed
(`1081 passed, 2 deselected in 39.18s`). Ruff format/check, compileall, CLI help and diff check passed.

## Next action

Commit the closeout records, fast-forward local `main`, verify topic containment, and delete the
topic branch. Remote push remains out of scope.

## Blockers

None. The Direct baseline remains poor but is now a valid Stage 7 comparison baseline; the remaining
complex-task failures are Agent reasoning/round-budget evidence, not a missing execution tool.
