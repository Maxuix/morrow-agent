# Subplan 4 — Isolated Workflow Vertical Slice

- [x] Task 1: idempotent `StartWorkflowCommand` with full Start admission transaction.
- [x] Task 2: NodeRun admission through AgentFactory/existing preparation with frozen model,
  effective request cap and deadline enforcement.
- [x] Task 3: role-neutral NodeResultCommitter (TextResult wrap) + four-case Artifact helper.
- [x] Task 4: WorkflowTransitionService terminal mapping, result snapshot, evidence carry-forward.
- [x] Task 5: cancellation/recovery integration incl. `pending_terminal_intent=user_cancel`.
- [x] Task 6: minimum Workflow/Node read projections.
- [x] Task 7: opt-in composition entry; ordinary Direct unchanged and default.
- [x] Task 8: Scripted-Provider end-to-end tests for the declared matrix.
- [x] Full offline gate (1438 passed, 2 Live deselected), commit, fast-forward integrate.

Subplan 4 is complete. Implementation commit: `d082b39` on
`feat/stage7-isolated-workflow-slice`. Remote publication remains blocked pending explicit
authorization; no Live tests were run.
