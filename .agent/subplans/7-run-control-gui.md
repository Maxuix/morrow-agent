# Subplan 7 — Run Control GUI

> Status: active (activated 2026-09-04)
> Branch: `feat/stage8-run-control-gui`
> Activation base: latest verified `main` with Subplan 6 integrated
> Prerequisite: Subplan 6 verified; Subplans 1–2 runtime semantics already integrated
> Roadmap authority: stage-8 §8.3, §8.5, §13, §8C (GUI portion), §16.1–16.2

## Objective

Wire the Pause/Drain/Patch/continuation runtime (Subplans 1–2) into the GUI and CLI equally, so
users can observe, pause, edit pending graphs, resume, retry failed nodes and rerun — with full
parent/child lineage and inherited-Artifact visibility.

## Deliverables

- Run-control actions in GUI and CLI parity: Start, Pause, Resume, Cancel, Resolve Approval,
  Retry failed node (explicit root resume + `run_relation=rerun` child), Full rerun (new accounting
  root, clearly labelled), Edit pending graph, Accept/Correct TaskOutcome.
- Workflow panel upgrades: current Revision, parent/child Run lineage, inherited Artifact
  provenance projection, per-node request usage and optional-limit remaining, retries/failures, approval surface per
  §8.5 (requesting Task/Workflow/Node/Agent, operation type, affected objects, risk level, redacted
  preview, allow/deny/limited-session choices — never a bare "Agent wants to run a tool").
- Edit-pending flow: Pause → drain → edit Future nodes in the editor (Subplan 6) → patch preview
  with diff and risk classification → user confirmation → continuation child starts; the old Run
  shows terminal `superseded`.
- Cost feedback per §14.1–14.2: pre-run node count/models/user-set limits/parallelism/writers; live
  request usage, optional remaining allowance and per-node consumption.
- CLI parity for every action and projection above.

## Key semantics

- GUI triggers the same commands as CLI; identical revision/event/runtime resolution results on
  both surfaces.
- Pause during in-flight Approval shows running + approval-pending, never mislabelled blocked.
- A rerun that establishes a new accounting root is explicitly marked as such.

## Validation

- GUI–CLI conflict tests (concurrent edits → OCC conflict, no lost update), Core-restart recovery
  observed in the GUI, stale snapshot resync during a paused run.
- End-to-end browser flows: pause → edit pending → continue; failed-node retry lineage; full rerun
  labelling.
- Standard offline/static gates.

## Out of scope

Automatic Draft generation (Subplan 8), Agent-proposed Replan (Subplan 9), feedback capture UI
(Subplan 11).
