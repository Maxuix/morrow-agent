# Agent Workflow

This repository uses a lightweight execution workflow.

## Project Files

Project context:

* `docs/ROADMAP.md` — high-level project direction and milestones
* `docs/ARCHITECTURE.md` — current repository structure and component responsibilities

Execution state:

* `.agent/PLAN.md` — current active implementation plan
* `.agent/TODO.md` — executable tasks and their status
* `.agent/TRACKER.md` — current progress and next action
* `.agent/LOG.md` — important execution history
* `.agent/subplans/` — ordered child plans for work that is too large for one active plan

## Start / Resume

Before working:

1. Read `.agent/PLAN.md`.
2. Read `.agent/TODO.md`.
3. Read `.agent/TRACKER.md`.
4. Read recent `.agent/LOG.md` entries only when additional context is needed.
5. Read `docs/ROADMAP.md` when broader project direction is relevant.
6. Read relevant parts of `docs/ARCHITECTURE.md` when the task touches project structure or component boundaries.
7. Inspect the relevant code before making changes.

Continue from the active task or next pending task.

Do not redo completed work unless verification shows it is necessary.

## Execute

Work on one logical task at a time.

Before implementation:

* mark the task `[>]` in `.agent/TODO.md`
* update the active task in `.agent/TRACKER.md`

Then:

1. Inspect relevant code.
2. Implement the task.
3. Run appropriate validation.
4. Fix issues caused by the change.
5. Mark the task complete only after validation succeeds.

## Update

Update files only when their state meaningfully changes.

* `PLAN.md` — update when the implementation approach materially changes
* `TODO.md` — update when tasks or task status change
* `TRACKER.md` — update when current progress, blockers, or next action change
* `LOG.md` — append important results, decisions, failures, validation results, or blockers
* `ROADMAP.md` — update only when project direction or milestones change
* `ARCHITECTURE.md` — update when the actual project structure or component responsibilities change

Do not log routine operations such as reading files, searching code, or listing directories.

## Task Status

* `[ ]` pending
* `[>]` in progress
* `[x]` completed
* `[!]` blocked

## Plan

`PLAN.md` represents the current active plan.

Treat it as a living document, not immutable instructions.

If repository evidence or execution results conflict with the plan, update the plan.

Do not preserve obsolete plan versions in the active file. Git history can be used for previous versions.

## Large Plans

If the current plan is too large to execute directly, split it into ordered subplans under `.agent/subplans/`.

`PLAN.md` should remain a high-level index of:

- the overall goal
- subplans
- dependencies
- completion status
- the currently active subplan

Work on one subplan at a time.

`TODO.md` should contain executable tasks for the active subplan, not the entire master plan.

When a subplan is completed:

1. verify its completion criteria
2. mark it complete in `PLAN.md`
3. record the result in `LOG.md`
4. activate the next subplan
5. update `TODO.md` and `TRACKER.md`

Avoid creating deeper planning hierarchies unless strictly necessary.

## Verify

Never mark work complete without appropriate validation.

Use the most relevant available validation, such as:

* tests
* type checks
* linting
* build checks
* direct behavior verification

Do not claim validation passed unless it was actually run.

## Finish

Before finishing work:

1. Verify completed tasks.
2. Update `.agent/TODO.md`.
3. Update `.agent/TRACKER.md`.
4. Update `docs/ARCHITECTURE.md` if the actual architecture changed.
5. Append important final results to `.agent/LOG.md`.

Actual code and execution results take precedence over outdated documentation.
