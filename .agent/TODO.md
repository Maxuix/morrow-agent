# Subplan 7 — Direct Invoking-Session Adapter

- [x] Task 1: add `invoking_session` scope and enforce the one-node/no-edge compiler shape.
- [x] Task 2: add the Direct client-message binding and transactional Turn admission recheck.
- [x] Task 3: compose the same AgentLoop leaf without the ordinary steering/follow-up queue.
- [x] Task 4: delegate root terminal ownership to TurnLifecycle and add idempotent finalization and
  recovery for STOP/ERROR/CANCEL/committer failure.
- [x] Task 5: reuse NodeResultCommitter for TextResult and structured output slots.
- [x] Task 6: prove ordinary-Direct parity and document the TaskContract secret-shaped exception.
- [x] Task 7: add the focused Scripted Provider and regression matrix, then run the full offline
  validation gate.

Subplan 7 is complete and integrated into local `main` (implementation `cda0c0c`, closeout
`72767b1`). Full offline gate: 1507 passed, 2 Live deselected; Ruff
format/check, compileall, `morrow --help` and `git diff --check` passed. No Live tests were run.
