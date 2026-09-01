# Subplan 5 — Serial DAG Scheduler

- [ ] Task 1: Start precreates one queued NodeRun per frozen node (stable order), replay-safe and
  fault-atomic. (Largely present from Subplan 4; prove for multi-node.)
- [ ] Task 2: admission binds the exact queued row; one fresh isolated Session + `workflow_node`
  TaskRun per leaf; duplicate wake creates nothing new.
- [ ] Task 3: deterministic topological execution order; readiness derived from terminal
  predecessors + bound input Artifacts; per-node completion with whole-graph success only after
  every declared node completes; fixed failure mapping.
- [ ] Task 4: aggregate request/deadline budget across nodes (shrunken positive remainder cap,
  zero/deadline mappings, compaction excluded from counting).
- [ ] Task 5: multi-node cancellation mappings + multi-node evidence projection into root
  TaskOutcome.
- [ ] Task 6: restart recovery + resume revocation recheck + recovery-only `abandon` for an
  OCC-current blocked run.
- [ ] Task 7: no retry; rerun = new WorkflowRun after explicit root resume (test only).
- [ ] Task 8: `tests/test_stage7_serial_scheduler.py` deterministic Scripted Provider matrix.
- [ ] Full offline gate, ruff, compileall, commit, fast-forward integrate.
