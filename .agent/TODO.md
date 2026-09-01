# Subplan 4 — Isolated Workflow Vertical Slice

- [ ] Task 1: idempotent `StartWorkflowCommand` with full Start admission transaction.
- [ ] Task 2: NodeRun admission through AgentFactory/existing preparation with frozen model,
  effective request cap and deadline enforcement.
- [ ] Task 3: role-neutral NodeResultCommitter (TextResult wrap) + four-case Artifact helper.
- [ ] Task 4: WorkflowTransitionService terminal mapping, result snapshot, evidence carry-forward.
- [ ] Task 5: cancellation/recovery integration incl. `pending_terminal_intent=user_cancel`.
- [ ] Task 6: minimum Workflow/Node read projections.
- [ ] Task 7: opt-in composition entry; ordinary Direct unchanged and default.
- [ ] Task 8: Scripted-Provider end-to-end tests for the declared matrix.
- [ ] Full offline gate, commit, fast-forward integrate, retire branch.
