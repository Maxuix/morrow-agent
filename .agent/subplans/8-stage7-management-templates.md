# Subplan 8 — Stage 7 Workflow Management and Templates

> Status: completed and integrated
> Branch: `feat/stage7-workflow-management`
> Prerequisite: Subplan 7 completed, verified and integrated
> Revised 2026-08-31 per the conditional-GO plan review: `validate` is provably write-free and only
> explicit publication writes; the Parallel Research template runs serially on the Subplan 5
> Scheduler (concurrent fan-out is Stage 8).

## Objective

Expose the implemented static Runtime through one application Command/Query surface and a focused
CLI, optionally adding client-facing events only after separate authorization. Publish four built-in
definitions without GUI/server/background behavior or template-specific runtime branches.

## Ownership

- Workflow application commands, queries and safe projections;
- an explicit authorization decision for any additive client-facing ApplicationEvent records;
- focused `morrow agent` and `morrow workflow` CLI modules;
- built-in Direct (invoking-session), Explore-Implement-Verify, Parallel Research (serial fan-in)
  and Planned Refactor definitions;
- Planner/PlanArtifact and Synthesizer/SynthesisReport only because their templates consume them
  here;
- Stage 7 human usage documentation, doctor/backup completion and focused interface tests.

## Tasks

1. Add application commands for Agent source create/update/validate/publish/enable/disable/revoke;
   Workflow source create/update/validate, publish, enable/disable, revoke, foreground run,
   recovery-resume and recovery-only abandon actions. `abandon` accepts only an OCC-current durable
   `blocked`/outcome-unknown Run under Subplan 5's handle-release invariant; it does not require the
   unknown durable Tool fact to be reconciled. It rejects running/queued work or an exact live
   handle still held by this process, never guesses liveness from a missing terminal row/PID, and
   directs the user to the owning foreground Ctrl-C/same-process cancellation path. The owning run
   caller may invoke the same-process cancellation boundary (and CLI Ctrl-C does so), but Stage 7
   exposes no standalone cross-process cancel command or durable cancellation request.
   Preserve desired/published separation, OCC and immutable revision rules. `validate` is pure:
   it parses, resolves references and returns compiler diagnostics with zero writes — no
   Version/Revision row, no head movement, no built-in lazy publication — so CI and read-only
   contexts can call it freely. `publish` is the only command that creates an immutable
   Version/Revision and advances a head. `run` requires an exact already-published Revision ID; its
   single explicit `--ensure-published` opt-in publishes the current desired source first and echoes
   the chosen Revision, and without it an unpublished definition fails with an actionable publish
   instruction. Enable/disable mutates
   only the relevant SQLite Head admission gate: Workflow disable rejects new Runs; Agent disable
   rejects new admissions (new standalone runs and new Workflow Starts); admitted WorkflowRuns,
   historical inspection and running AgentRun recovery remain unaffected.
   `revoke` targets an exact immutable AgentDefinitionVersion or WorkflowRevision, is additive,
   audited (reason/timestamp/command) and one-way, and refuses to target a head selector rather
   than an exact immutable ID.
   `create/update/edit/publish` mutate only workspace `origin=user` desired source. Packaged
   `origin=builtin` sources are list/show/validate/run capable and their published Head may still use
   operational enable/disable, but source update/edit is refused with an actionable instruction to
   create a new-ID user definition. Stage 7 does not add a copy/fork command.
2. Add read-only queries for definitions/revisions (including revocation status), run/node/Agent
   linkage, graph status, bound Artifacts, usage availability, terminal outcome and actionable
   blocked reason. Paginate/bound projections using existing application patterns.
3. Treat `ApplicationEvent` as a client-facing cursor contract. Before adding Workflow event types,
   determine whether this is a public event lifecycle change under `AGENTS.md` and obtain explicit
   user authorization if so. If authorization is absent/denied, keep Query/CLI polling complete and
   defer event additions; do not block the static Runtime or fabricate an internal event category.
4. Add one unambiguous CLI surface with separate Agent
   `list/show/create/edit/validate/publish/enable/disable/revoke` and Workflow
   `list/show/create/edit/validate/publish/enable/disable/revoke/run/status/resume/abandon` commands
   plus `workflow node show`. Workflow run requires exact `--revision`, `--session`, `--root-task`
   and `--expected-task-version`, and exactly one bounded input source: `--task TEXT` or `--stdin`.
   It accepts optional `--command-id` and `--ensure-published`; for a Direct graph it also accepts
   optional `--client-message-id`. The CLI generates and echoes omitted IDs before dispatch, but
   never chooses, creates, abandons or resumes a root Task, and `--ensure-published` output states
   plainly that it wrote a new Revision. All commands call application services and never read/write
   SQLite or YAML directly. Ctrl-C on the foreground `run` invokes its owning cancellation handle.
   Durable remote/background cancel is deferred with background execution to Stage 9.
5. Publish versioned built-in definitions using the already proven generic runtime. Publication is
   explicit and idempotent: the first `publish` (or `run --ensure-published`) command targeting a
   packaged built-in compiles and publishes its immutable Revision and head through the ordinary
   compilation service, and the canonical content-hash no-op makes repeats free. `validate` and
   plain `run` never publish. No startup migration or background step publishes
   built-ins silently; an unpublished built-in is visible but not runnable.
6. Add Planner/PlanArtifact and Synthesizer/SynthesisReport now, with their real template consumers:
   publish Planned Refactor as Explorer -> Planner -> Coder -> Reviewer (one Writer, no automatic
   repair loop) and Parallel Research as a fixed Explorer fan-out -> Synthesizer fan-in that runs
   entirely serially on the Subplan 5 Scheduler — one node at a time in stable order. Fan-out leaves
   publish their EvidenceBundles as `required_for_node_completion=true` slots consumed by the
   Synthesizer's input bindings but absent from the Workflow `required_outputs` export list; only
   the single aggregate SynthesisReport is exported, so a large fan-out cannot approach the 64-ref
   compile bound. Both roles submit through `submit_node_result`. Publish the Direct template as the
   one-node `invoking_session` graph proven in Subplan 7, and Explore-Implement-Verify from Subplan
   6. No template-name branch exists anywhere in the runtime.
7. Complete the Workflow backup/doctor coverage established in Subplans 1–2 with the inventory
   needed for records introduced after Subplan 2 plus repair UX polish, and add human docs for
   revision/run inspection, current-format restore and common validation/blocking errors. The usage
   docs also carry the operational guidance implied by the fixed semantics: how to estimate the four
   mandatory budget fields (an under-sized cap/deadline fails the run and a rerun re-executes every
   node), why a run-level budget override is deliberately deferred, what ordinary disable versus
   emergency revoke mean operationally, and the cross-platform declaration strategy for built-in
   templates — a complete ImplementationPatch output contract is declared only where the native
   sandbox backend is available, otherwise the template declares the truthful structural result
   instead of failing whole graphs on unsupported platforms.
8. Add CLI/application parity, OCC conflict, safe projection, old-revision inspection, built-in
   edit/publish refusal with user-source create positive case, validate-write-free proof for both
   definition kinds (including CI-style repeated validate with zero Operational Store diff), plain
   `run` against an unpublished definition failing with the publish instruction,
   `run --ensure-published` writing exactly once and echoing the Revision, revoke CLI targeting an
   exact ID with audit output and head-selector refusal, bad-definition isolation and template
   publish/run tests, including running/current-process-live-handle abandon rejection and blocked/
   no-local-handle abandon success with unreconciled unknown evidence preserved.

## Proportionality decisions

The interface exposes only implemented behavior. It does not predict Stage 8 edit/replan/parallel
commands or introduce a generic API framework. Public AgentEvent remains frozen; ApplicationEvent
additions are client-contract work behind the explicit authorization gate above.

Explicitly deferred:

- HTTP/WebSocket, GUI/React Flow, browser server, live graph editing and background worker;
- automatic task/template selection, template marketplace/import, learned orchestration policy and
  feedback promotion;
- concurrent fan-out execution (Stage 8) and arbitrary built-in role library or user-facing safety
  configuration beyond current owners.

Invalid definitions fail their validate/run command only. CLI/application startup, Direct and other
definitions remain available.

## Validation

```bash
uv run pytest -q tests/test_stage7_workflow_management.py tests/test_stage7_workflow_cli.py
uv run pytest -q tests/test_stage7_direct_adapter.py
uv run pytest -m 'not live'
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
- `validate` is provably write-free; publication happens only through explicit `publish` or
  `run --ensure-published`.
- CLI run input maps one-to-one to StartWorkflowCommand, and a mismatched/current/disabled/revoked
  root or Definition is rejected without silently changing Task state.
- Four built-in definitions publish/run through generic Workflow data with no template-name branch,
  and Parallel Research demonstrates serial fan-in synthesis with only the SynthesisReport exported.
- Old immutable revisions/runs remain inspectable after desired-state edits; revocation is visible,
  audited and permanent.
- Invalid/temporarily unavailable definitions are isolated and produce actionable diagnostics.
- Doctor/backup and human usage cover the current implemented Stage 7 surface.
- No GUI/server/background/adaptive orchestration work exists.
