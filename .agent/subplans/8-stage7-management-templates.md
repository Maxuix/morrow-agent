# Subplan 8 — Stage 7 Workflow Management and Templates

> Status: pending
> Branch: `feat/stage7-workflow-management`
> Prerequisite: Subplan 7 completed, verified and integrated

## Objective

Expose the implemented static Runtime through one application Command/Query surface and a focused
CLI, optionally adding client-facing events only after separate authorization. Publish four built-in
definitions without GUI/server/background behavior or template-specific runtime branches.

## Ownership

- Workflow application commands, queries and safe projections;
- an explicit authorization decision for any additive client-facing ApplicationEvent records;
- focused `morrow agent` and `morrow workflow` CLI modules;
- built-in Direct, Explore-Implement-Verify, Parallel Research and Planned Refactor definitions;
- Planner definition and PlanArtifact only because Planned Refactor consumes them here;
- Stage 7 human usage documentation, doctor/backup composition and focused interface tests.

## Tasks

1. Add application commands for Agent source create/update/validate/publish/enable/disable; Workflow
   source create/update/validate, compile/publish, enable/disable, foreground run,
   recovery-resume and recovery-only abandon actions. `abandon` accepts only an OCC-current durable
   `blocked`/outcome-unknown Run under Subplan 5's handle-release invariant; it does not require the
   unknown durable Tool fact to be reconciled. It rejects running/queued work or an exact live handle
   still held by this process, never guesses liveness from a missing terminal row/PID, and directs the
   user to the owning foreground Ctrl-C/same-process cancellation path. The owning run caller may invoke the same-process
   cancellation boundary (and CLI Ctrl-C does so), but Stage 7 exposes no standalone cross-process
   cancel command or durable cancellation request.
   Preserve desired/published separation, OCC and immutable revision rules. Enable/disable mutates
   only the relevant SQLite Head admission gate: Workflow disable rejects new Runs; Agent disable
   rejects new leaf admission; historical inspection and running AgentRun recovery remain valid.
   `create/update/edit/publish` mutate only workspace `origin=user` desired source. Packaged
   `origin=builtin` sources are list/show/validate/run capable and their published Head may still use
   operational enable/disable, but source update/edit is refused with an actionable instruction to
   create a new-ID user definition. Stage 7 does not add a copy/fork command.
2. Add read-only queries for definitions/revisions, run/node/Agent linkage, graph status, bound
   Artifacts, usage availability, terminal outcome and actionable blocked reason. Paginate/bound
   projections using existing application patterns.
3. Treat `ApplicationEvent` as a client-facing cursor contract. Before adding Workflow event types,
   determine whether this is a public event lifecycle change under `AGENTS.md` and obtain explicit
   user authorization if so. If authorization is absent/denied, keep Query/CLI polling complete and
   defer event additions; do not block the static Runtime or fabricate an internal event category.
4. Add one unambiguous CLI surface with separate Agent
   `list/show/create/edit/validate/publish/enable/disable` and Workflow
   `list/show/create/edit/validate/compile/enable/disable/run/status/resume/abandon` commands plus
   `workflow node show`. Workflow run requires exact `--revision`, `--session`, `--root-task` and
   `--expected-task-version`, and exactly one bounded input source: `--task TEXT` or `--stdin`.
   It accepts optional `--command-id`; for a Direct graph it also accepts optional
   `--client-message-id`. The CLI generates and echoes omitted IDs before dispatch, but never chooses,
   creates, abandons or resumes a root Task. `validate` is pure/read-only, `compile` publishes a
   Revision/head, `status` inspects a run, `resume` only continues a nonterminal recovery run, and
   `abandon` only closes an eligible blocked recovery Run—it is never a remote cancel alias.
   Ctrl-C on the foreground `run` invokes its owning cancellation handle. Durable remote/background
   cancel is deferred with background execution to Stage 9. All commands call application services
   and never read/write SQLite or YAML directly.
5. Publish versioned built-in Direct, Explore-Implement-Verify and Parallel Research definitions
   using the already proven generic runtime.
6. Add Planner and PlanArtifact only now, then publish Planned Refactor as
   Explorer -> Planner -> Coder -> Reviewer. Keep one Writer and no automatic repair loop.
7. Expose and verify the Workflow backup/doctor coverage established in Subplan 2; add only the
   inventory needed for records introduced after Subplan 2 plus human docs for revision/run
   inspection, current-format restore and common validation/blocking errors.
8. Add CLI/application parity, OCC conflict, safe projection, old-revision inspection, built-in edit/
   publish refusal with user-source create positive case, bad-definition isolation and template
   compile/run tests, including running/current-process-live-handle abandon rejection and blocked/
   no-local-handle abandon success with unreconciled unknown evidence preserved.

## Proportionality decisions

The interface exposes only implemented behavior. It does not predict Stage 8 edit/replan commands or
introduce a generic API framework. Public AgentEvent remains frozen; ApplicationEvent additions are
client-contract work behind the explicit authorization gate above.

Explicitly deferred:

- HTTP/WebSocket, GUI/React Flow, browser server, live graph editing and background worker;
- automatic task/template selection, template marketplace/import, learned orchestration policy and
  feedback promotion;
- arbitrary built-in role library or user-facing safety configuration beyond current owners.

Invalid definitions fail their validate/run command only. CLI/application startup, Direct and other
definitions remain available.

## Validation

```bash
uv run pytest -q tests/test_stage7_workflow_management.py tests/test_stage7_workflow_cli.py
uv run pytest -q tests/test_stage7_readonly_parallelism.py
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow agent --help
uv run morrow workflow --help
git diff --check
```

## Exit criteria

- CLI and application projections expose the same bounded authoritative state and never bypass
  repositories/services; Query/CLI works whether optional ApplicationEvent additions are approved
  or deferred.
- CLI run input maps one-to-one to StartWorkflowCommand, and a mismatched/current/disabled root or
  Definition is rejected without silently changing Task state.
- Four built-in definitions compile/run through generic Workflow data with no template-name branch.
- Old immutable revisions/runs remain inspectable after desired-state edits.
- Invalid/temporarily unavailable definitions are isolated and produce actionable diagnostics.
- Doctor/backup and human usage cover the current implemented Stage 7 surface.
- No GUI/server/background/adaptive orchestration work exists.
