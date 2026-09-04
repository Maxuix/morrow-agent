# TODO

Active subplan: `7-run-control-gui` on `feat/stage8-run-control-gui` (from latest verified
`main` `aadcfc1`).

- [ ] Backend approval surface per roadmap §8.5: enriched approval projection (requesting
  Task/Workflow/Node/Agent, operation type, affected objects, risk level), allow/deny/
  limited-session resolution choices, and an `approval.resolved` event.
- [ ] Backend patch preview: `patch_validate` gains a definition diff and C8 risk classification.
- [ ] Backend usage projections: per-node request usage/remaining allowance and a pre-run cost
  summary per §14.1 (node count/models/user-set limits/parallelism/writers).
- [ ] CLI parity: `workflow cancel`, approval list/resolve commands, patch preview diff/risk
  output, TaskOutcome accept/correct surfacing.
- [ ] GUI API client run-control methods and WorkflowPanel controls (Start/Pause/Resume/Cancel/
  Retry failed node/Full rerun with new-budget-root labelling/Edit pending).
- [ ] GUI approval resolution dialog (full §8.5 surface) and TaskOutcome accept/correct actions.
- [ ] GUI edit-pending flow: Pause → drain → editor → patch preview with diff and risk → user
  confirmation → continuation child; old run shows terminal `superseded`.
- [ ] GUI cost feedback: pre-run summary and live usage/remaining display.
- [ ] Contract/conflict/recovery tests, GUI Vitest, browser end-to-end flows (pause → edit →
  continue; failed-node retry lineage; full-rerun labelling).
- [ ] Acceptance doc, offline/static gates, GUI gates.
- [ ] Commit verified progress, fast-forward into `main`, verify ancestry, retire the topic
  branch. Remote push is not authorized.
