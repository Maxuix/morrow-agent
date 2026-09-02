# Subplan 9 — Stage 7 Acceptance and Closeout

- [x] Task 1: freeze the deterministic integrated Stage 7 acceptance matrix and representative
  workspace fixtures.
- [x] Task 2: prove the master-plan invariants and four phase gates end to end.
- [x] Task 3: record the minimal ordinary-Direct versus Workflow comparison and full-rerun cost.
- [x] Task 4: keep Live evaluation separate unless explicitly authorized.
- [x] Task 5: apply the template promotion rule from available evidence.
- [x] Task 6: inspect the complete Stage 7 diff and repair confirmed findings only.
- [x] Task 7: reconcile acceptance, architecture, roadmap, Stage 8 entry conditions and usage docs;
  run the final offline/static/CLI gate.
- [x] Task 8: commit, fast-forward integrate, verify ancestry and retire the topic branch.

Subplan 9 is complete and integrated into local `main` (`506a276`). Focused acceptance: 3 passed;
Stage 7 matrix: 220 passed; full offline gate: 1523 passed, 2 Live deselected. Ruff format/check,
compileall, root/Agent/Workflow CLI help and `git diff --check` passed.
