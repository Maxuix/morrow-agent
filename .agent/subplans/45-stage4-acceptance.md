# Subplan 45 — Stage 4 End-to-End Acceptance and Closeout

> Status: pending
> Prerequisite: Subplan 44 accepted
> Owns: integrated product evidence, documentation truth, packaging, and Stage 4 closure

## Objective

Prove the integrated Stage 4 product survives realistic foreground use and injected failure, remains
safe and operable after restart, preserves Stage 3 guarantees, and ships with truthful boundaries.

## In scope

- Integrated Fake/Scripted Provider product stories through the real CLI/REPL/application
  composition.
- Full logical and subprocess crash matrix for messages, tools, approvals, artifacts, checkpoints,
  recovery, grants, backup, and migration.
- Current-host Stage 3 security/sandbox regression gates relevant to the claimed platform.
- Upgrade fixture from the Stage 3/no-operational-store state and at least one prior Stage 4 schema
  fixture established and retained by Subplans 36/37.
- Wheel build/install smoke with isolated data root and durable Session recovery.
- Requirement-to-evidence matrix and accepted Stage 4 evidence document.
- Final README, ARCHITECTURE, ROADMAP, stage document, CLI help, and `.agent` execution-state update.

## Out of scope

- New capability implementation or scope expansion to fix an acceptance narrative.
- Live Provider/network tests unless separately requested with explicit compatible credentials.
- Stage 5 learning, Controlled Full Access Auto, background automation, GUI, and code rewind.

## Tasks

- [ ] S4.45.1 Freeze the final schema/tool/command/grant inventory and verify every Stage 4 contract
  has a production owner and test owner.
- [ ] S4.45.2 Run no-tool, file-edit/test, interrupted Host command, sandbox/promotion, correction/
  acceptance, long-context/fork, backup/restore, and Full Access Manual product stories.
- [ ] S4.45.3 Execute the full logical and subprocess fault matrix with deterministic IPC and record
  recovery classifications/evidence.
- [ ] S4.45.4 Run migration/future-schema/corruption/contention/disk/Artifact integrity and doctor
  acceptance fixtures.
- [ ] S4.45.5 Run the complete offline, Ruff, compileall, CLI help, diff, current-host security, and
  package build/install/recovery gates.
- [ ] S4.45.6 Create `docs/acceptance/stage-4-durable-agent-evidence.md` mapping every completion
  criterion to exact commands/tests/results and declared unsupported boundaries.
- [ ] S4.45.7 Reconcile all product/architecture/roadmap/reference/license docs with actual code;
  remove stale planning claims and do not claim unsupported platforms or Full Access Auto.
- [ ] S4.45.8 Commit verified progress, close Stage 4 execution state, and open only the Stage 5
  planning boundary—do not create or activate a Stage 5 plan without a user request.

## Required product stories

1. Create a Session, complete several no-tool Turns, exit, list, resume, and prove each accepted
   command creates one Turn/UserMessage; an interrupted Turn may resume with a linked AgentRun.
2. Perform a real Stage 3 code task with file edits and validation, crash at selected boundaries,
   reconcile safely, correct the answer, and explicitly accept the TaskRun.
3. Interrupt approved Host and native-sandbox commands without committed completion; restart
   reports unknown and never automatically replays either. Promotion reconciles per file.
4. Produce bounded redacted Artifacts, compact long context, restart, and fork without changing
   parent history or workspace files.
5. Back up an active installation, restore to an isolated target, verify hashes/integrity, and resume
   without credentials in the bundle.
6. Grant one Full Access Manual AgentRun, display/consume explicit unconfined Host approval, crash,
   prove the recovery AgentRun has no inherited grant, explicitly regrant if desired, revoke, and
   prove no silent elevation or Auto path.

## Final validation

At minimum:

```bash
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```

Also run the repository's relevant current-host native-sandbox acceptance and built-wheel smoke
commands recorded in the evidence document. Do not report a gate as passed unless it was actually
run, and record intentional skips with reasons.

## Completion gate

Every definition-of-done item in `.agent/PLAN.md` maps to reproducible evidence; all offline and
claimed-platform gates pass; packaged recovery works from an isolated data root; documents match
actual behavior; known limitations are explicit; and no unresolved high-severity durability,
security, migration, or recovery finding remains.

Acceptance cannot expand capability to make a story pass. In particular it must preserve one
Turn/UserMessage rather than one-model-call idempotency, non-terminal `ready_for_acceptance`,
Host/sandbox unknown-outcome classification without committed completion, and no grant inheritance.

## Deliverables

- Integrated Stage 4 implementation and acceptance fixtures.
- Stage 4 requirement-to-evidence report.
- Verified wheel/install/recovery evidence.
- Closed Stage 4 execution state with Stage 5 still unimplemented.
