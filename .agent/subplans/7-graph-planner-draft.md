# Subplan 7 — Task-Specialized GraphPlanner Draft

> Status: pending activation
> Branch: `feat/stage8-graph-planner`
> Activation base: latest verified `main` with Subplan 6 integrated
> Prerequisite: Subplan 6 verified
> Roadmap authority: stage-8 §4.1–4.6, §12, §8D, §16.4

## Objective

Generate editable, compilable, task-specialized Workflow Drafts from structured task features and
bounded Catalogs — templates are priors/fallbacks, never the sole source of the graph; simple tasks
stay Direct.

## Deliverables

- `TaskFeatures` extraction (roadmap §4.2 fields) from local rules, project metadata and one
  constrained model classification.
- Optional single read-only Scout producing a `TaskBrief`.
- `NodeCatalog`/`ArtifactCatalog`/`CapabilityCatalog` as bounded read-only projections of existing
  AgentDefinitions, Artifact contracts and Capability authority — no second registry; and the
  minimal `GraphGrammar` reusing the Compiler schema (not persisted, not a DSL).
- Constrained GraphPlanner: template prior or minimal grammar composition → task-specialized
  `TaskGraphDraft` (nodes, dependencies, contracts, Artifact bindings, parameterized
  Agent/Model/Skill/Policy/Budget per §4.4) → Compiler validation → Direct fallback or user
  supplement request when information is insufficient.
- Draft explanation per §12, derived from structured features and policy, never from hidden model
  reasoning.
- Compile-failure path per §4.5: one deterministic repair or constrained regeneration, then Direct
  fallback or wait-for-user with the concrete error; an uncompiled graph never runs silently.
- Direct-first rules per §4.3; multi-agent only when complexity/risk/parallel-benefit thresholds
  are met.
- `OrchestrationPolicy` domain + storage per §5.1–5.2 (including `auto_run_mode` and
  `auto_replan_mode`, defaulting to approval-first), workspace override support; auto-run stays
  off without paired-evidence promotion per §4.6.
- GUI integration: Draft review/edit in the editor (Subplan 5), explanation panel, auto-run policy
  settings.

## Key semantics

- Parameterization never creates unregistered permissions, references disabled Skills, bypasses the
  Compiler, selects unauthorized Providers or modifies fixed system boundaries.
- Similar-but-different tasks must produce visibly different Drafts (roadmap §16.4), not mechanical
  template copies.
- No paired evidence → suggestion/approval mode only, without blocking manual runs.

## Validation

- Deterministic planner tests over scripted Providers: small task stays Direct, large task gains a
  Reviewer, budget shortage handled, user-excluded role respected, workspace policy overrides
  global defaults, task-differentiated Drafts, compile-failure fallback.
- Standard offline/static gates.

## Out of scope

ReplanSignal/Coordinator (Subplan 8), feedback-driven policy candidates (Subplan 10), parallelism
(Subplan 11).
