# TODO

Active subplan: none. Subplan 7 (`7-run-control-gui`) completed 2026-09-05 on
`feat/stage8-run-control-gui` and was fast-forwarded into `main` (`8050fdc`).

- [x] Backend approval surface per roadmap §8.5: enriched projection, allow/deny/allow-session
  decisions, session-scope auto-approval precedent.
- [x] Backend patch preview: structural diff + C8 risk classification.
- [x] Backend usage projections: per-node request counts and the §14.1 pre-run summary.
- [x] CLI parity: `workflow cancel`, `approval list/resolve`, patch preview output, TaskOutcome
  accept/correct surfacing.
- [x] GUI run-control client, WorkflowPanel controls with lineage and new-budget-root labelling.
- [x] GUI approval dialog (full §8.5 surface) and TaskOutcome accept/correct actions.
- [x] GUI edit-pending flow: pause → editor → patch preview (diff + risk) → confirm →
  continuation; parent superseded.
- [x] GUI cost feedback: pre-run summary line and per-node usage.
- [x] Contract tests (15), GUI Vitest (81), browser end-to-end flows, GUI–CLI OCC conflict in both
  directions, Core restart recovery observed in the GUI.
- [x] Acceptance doc, full offline gate 1633 passed / 2 Live deselected, Ruff/compileall/GUI build
  budget/`git diff --check` all green.
- [x] Fast-forward merge into `main`, ancestry verification, topic branch retired. Remote push is
  not authorized.
