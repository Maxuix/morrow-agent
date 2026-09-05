# Subplan 6 — Workflow Editor and Agent Module Inspector

> Status: completed 2026-09-04
> Branch: `feat/stage8-editor`
> Activation base: latest verified `main` with Subplan 5 integrated
> Prerequisite: Subplan 5 verified
> Roadmap authority: stage-8 §6.1–6.2, §6.4, §7, §8.3, §8B, §16.2 (edit cases)

## Objective

Let users create and edit valid Workflows and Agent definitions through the GUI: a node graph with
an inspector, pre-freeze Draft editing, compile-error visualization, and Definition/Revision diffs.
An illegal graph cannot be run.

## Deliverables

- Node-graph editor (approved node-based UI library) with add/delete/replace of Pending nodes, edge
  and input/output binding editing, and a Node inspector.
- AgentDefinition editing per roadmap §7.1 (name/description, role prompt, Provider/Model, Skills,
  Tool/Capability policy, ContextPolicy, contracts, optional guardrails, read-only/Writer flag), with §7.2
  non-overridable boundaries enforced and §7.3 Node override resolution shown with its source.
  Definition copy/derivative flow per §7.4 (new ID/Version, parent/source recorded, diff view,
  built-in updates never silently overwrite user copies).
- Read-only Catalogs and pickers the editor needs (Provider/Model/Skill/Tool/Artifact), plus Budget
  configuration — consuming the Catalog Query APIs delivered by Subplan 3, not new backend work
  here.
- Draft lifecycle per §6.1: the GUI Draft is a durable OCC object owned by Core (fields:
  `draft_id`, base revision/head references with their row versions, source body, status,
  timestamps), so refresh, multi-tab editing and long sessions cannot drift; every edit
  re-validates through the pure Compiler (debounced); only explicit freeze/run publishes an
  immutable Revision through the sole publication service. Catalog/head staleness surfaces as a
  warning, not silent divergence.
- Compile errors surfaced as actionable GUI diagnostics (per §6.4: downstream dependency, removed
  required Artifact, broken approval/review gate, no termination path); the GUI never guesses
  reconnections itself. To make canvas highlighting precise, the backend `CompileDiagnostic`
  (currently `severity/code/message` only) gains additive optional structured locator fields
  (`node_id` / `edge_id` where applicable), and the canvas highlights the exact node or edge.
- Editor validation is debounced (300–500 ms) so React Flow dragging cannot generate a request per
  pointer move; validation still always round-trips the Core API — no frontend compiler copy.
- Definition/Revision diff views.
- Editing-while-running protection at this layer: Running/Completed nodes render locked; edits to
  them are refused with the future-only explanation (runtime enforcement lives in Subplan 2).

## Key semantics

- The frontend does not re-implement the Compiler; validation round-trips through the Core API.
- Publication remains the only Revision-creating path; dragging produces zero Revisions.

## Validation

- Editor contract tests: valid graph creation, each §6.4 illegal-edit rejection with actionable
  error, Provider/Skill disabled mid-edit handling, Revision diff correctness.
- Browser-level flows: create a legal Workflow, run it; confirm an illegal graph cannot be frozen.
- Standard offline/static gates.

## Out of scope

Run-control actions (Subplan 7), automatic Draft generation (Subplan 8), Context/Learning/Skill
management beyond the editor's read-only pickers (Subplan 10).
