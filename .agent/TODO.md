# Subplan 5 — Serial DAG Scheduler

- [x] Task 1: Start precreates one queued NodeRun per frozen node (stable order), replay-safe and
  fault-atomic. (Was already built in Subplan 4; proven for multi-node graphs.)
- [x] Task 2: admission binds the exact queued row; one fresh isolated Session + `workflow_node`
  TaskRun per leaf; duplicate wake creates nothing new.
- [x] Task 3: deterministic topological execution order (`stable_execution_order`); readiness
  derived from terminal predecessors + bound input Artifacts; per-node completion with
  whole-graph success only after every declared node completes; fixed failure mapping.
- [x] Task 4: aggregate request/deadline budget across nodes (shrunken positive remainder cap,
  zero/deadline mappings, compaction excluded from counting).
- [x] Task 5: multi-node cancellation mappings + multi-node evidence projection into root
  TaskOutcome.
- [x] Task 6: restart recovery + resume revocation recheck + recovery-only `abandon` for an
  OCC-current blocked run.
- [x] Task 7: no retry; rerun = new WorkflowRun after explicit root resume (pinned by test).
- [x] Task 8: `tests/test_stage7_serial_scheduler.py` deterministic Scripted Provider matrix
  (24 tests).
- [x] Full offline gate (1466 passed, 2 Live deselected), ruff, compileall, commit.

Subplan 5 is complete. Implementation commit: `4acddc6` on `feat/stage7-serial-scheduler`.
Remote publication remains blocked pending explicit authorization; no Live tests were run.
