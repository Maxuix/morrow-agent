# Subplan 5 — Generic Workflow Foundation

> Status: completed 2026-09-04; fast-forward integrated into local `main`
> Branch: `refactor/general-workflow-runtime`
> Activation base: verified local `main` at `c4fd731`
> Prerequisite: Stage 8 Subplans 1–4 verified
> Authority: user design correction of 2026-09-04; current code and validation

## Objective

Make the packaged multi-Agent workflow a small, editable suggestion instead of a role-specific
protocol, and stop turning guessed Provider-request counts or wall-clock durations into default
task termination conditions.

## Deliverables

- Use one role-neutral `TextResult@1` output/input path between every node in the packaged
  Explore → Implement → Verify starter. Nodes, edges, roles and access modes remain ordinary
  `WorkflowDefinitionSource` data; users can insert, replace or remove any node after cloning.
- Keep historical structured contracts (`EvidenceBundle`, `PlanArtifact`, `ImplementationPatch`,
  `TestReport`, `ReviewReport`, `SynthesisReport`) readable and explicitly available to advanced
  user-authored definitions, but remove them from the starter's required transfer path and from
  built-in role instructions.
- Reduce packaged Workflow suggestions to Direct and the one minimal multi-Agent starter. Existing
  published immutable revisions remain runnable and inspectable.
- Add a public `workflow clone` command/application operation that copies a read-only built-in or
  user definition to a new `origin=user` desired source under OCC, ready for arbitrary YAML edits.
- Make workflow-wide request cap, per-node default request cap and admission timeout nullable,
  opt-in guardrails. Packaged templates omit all three. Explicit positive limits retain durable,
  lineage-aware enforcement; usage/request accounting remains available even without a cap.
- Keep user-driven stop/control (`Ctrl+C`, Pause/Drain, Resume and host stop hooks) as the runtime
  control path. Do not add another guessed automatic turn count or repeated-cycle kill switch; the
  generic final-result path removes the starter's structured-submission repair loop.
- Update current README, architecture, roadmap and Stage 8 planning language so optional limits
  and template-as-suggestion semantics are the single current specification.

## Compatibility

- No stored revision, artifact kind or structured contract is deleted or rewritten.
- Sources and revisions that contain explicit positive limits preserve their current behavior.
- Nullable fields serialize explicitly in frozen evidence; old positive values still validate.
- The compiler version changes because effective-cap resolution and packaged source hashes change.

## Validation

- Focused domain/compiler/management/CLI/scheduler/journal/GUI tests for omitted and explicit
  limits, clone/edit freedom, generic node transport and historical structured-contract support.
- Full offline pytest gate, Ruff format/check, compileall, GUI typecheck/test/build and
  `git diff --check`.
- Fresh public-surface user simulation with disposable state: clone the starter, insert a custom
  node, publish/run, inspect request accounting, exercise user stop/control, and run the configured
  Provider-backed lane when a compatible existing credential is available. Never expose secrets.

## Out of scope

The visual graph editor, automatic GraphPlanner, general parallel execution and deletion of legacy
structured contract support remain in their later Stage 8 subplans.

## Acceptance

- Implementation checkpoint: `c4b1c4e`.
- Evidence: `docs/acceptance/stage-8-subplan-5-generic-workflow-foundation.md`.
- Gates: focused 158 passed; full offline 1609 passed / 2 deselected; Ruff, compileall, CLI help,
  GUI typecheck/31 tests/build/bundle budget and diff check passed.
- Real Provider: configurable four-node graph completed with a 15-request node; hidden-oracle
  correction recovered from one `invalid_response` through explicit failed-node rerun and then
  passed all checked/hidden oracles. Credentials stayed in CredentialStore.
