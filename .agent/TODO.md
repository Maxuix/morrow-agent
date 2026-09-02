# Subplan 8 — Stage 7 Workflow Management and Templates

- [x] Task 1: add the Agent and Workflow management command surfaces, including pure validation,
  publication, operational head controls, exact immutable revocation, foreground run/resume and
  recovery-only abandon.
- [x] Task 2: add bounded read-only management and run/node/Artifact queries.
- [x] Task 3: keep query/CLI polling complete and defer additive `ApplicationEvent` types because
  the current request did not separately authorize a public event-lifecycle change.
- [x] Task 4: add the focused `morrow agent` and `morrow workflow` CLI surfaces.
- [x] Task 5: add explicit, idempotently publishable built-in Agent and Workflow definitions.
- [x] Task 6: add Planner/PlanArtifact and Synthesizer/SynthesisReport plus Planned Refactor and
  serial Parallel Research templates through the generic runtime.
- [x] Task 7: finish Stage 7 backup/doctor coverage and human usage documentation.
- [x] Task 8: add the management/CLI/template regression matrix and run the declared validation
  gate.

Subplan 8 is complete and integrated into local `main` (implementation `ee68cb8`, closeout
`2054dde`). Full offline gate: 1517 passed, 2 Live deselected; Ruff
format/check, compileall, root/Agent/Workflow CLI help and `git diff --check` passed.
