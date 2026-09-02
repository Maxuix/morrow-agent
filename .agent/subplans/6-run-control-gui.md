# Subplan 6 — Run Control GUI

> Status: pending activation
> Branch: `feat/stage8-run-control-gui`
> Activation base: latest verified `main` with Subplan 5 integrated
> Prerequisite: Subplan 5 verified; Subplans 1–2 runtime semantics already integrated
> Roadmap authority: stage-8 §8.3, §8.5, §13, §8C (GUI portion), §16.1–16.2

## Objective

Wire the Pause/Drain/Patch/continuation runtime (Subplans 1–2) into the GUI and CLI equally, so
users can observe, pause, edit pending graphs, resume, retry failed nodes and rerun — with full
parent/child lineage and inherited-Artifact visibility.

## Deliverables

- Run-control actions in GUI and CLI parity: Start, Pause, Resume, Cancel, Resolve Approval,
  Retry failed node (explicit root resume + `run_relation=rerun` child), Full rerun (new budget
  root, clearly labelled as new budget), Edit pending graph, Accept/Correct TaskOutcome.
- Workflow panel upgrades: current Revision, parent/child Run lineage, inherited Artifact
  provenance projection, per-node budget consumed/remaining, retries/failures, approval surface per
  §8.5 (requesting Task/Workflow/Node/Agent, operation type, affected objects, risk level, redacted
  preview, allow/deny/limited-session choices — never a bare "Agent wants to run a tool").
- Edit-pending flow: Pause → drain → edit Future nodes in the editor (Subplan 5) → patch preview
  with diff and risk classification → user confirmation → continuation child starts; the old Run
  shows terminal `superseded`.
- Cost feedback per §14.1–14.2: pre-run node count/models/max budget/parallelism/writers; live
  used/remaining budget and per-node consumption.
- CLI parity for every action and projection above.

## Key semantics

- GUI triggers the same commands as CLI; identical revision/event/runtime resolution results on
  both surfaces.
- Pause during in-flight Approval shows running + approval-pending, never mislabelled blocked.
- A rerun that establishes a new budget root is explicitly marked as such.

## Validation

- GUI–CLI conflict tests (concurrent edits → OCC conflict, no lost update), Core-restart recovery
  observed in the GUI, stale snapshot resync during a paused run.
- End-to-end browser flows: pause → edit pending → continue; failed-node retry lineage; full rerun
  labelling.
- Standard offline/static gates.

## Out of scope

Automatic Draft generation (Subplan 7), Agent-proposed Replan (Subplan 8), feedback capture UI
(Subplan 10).
