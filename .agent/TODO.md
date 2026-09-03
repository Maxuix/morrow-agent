# TODO

Active subplan: `1-pause-drain-runtime` (branch `feat/stage8-pause-drain`).

- [>] Study contracts doc + source: migrations (`operational.py`), `core/workflows/runs.py`,
  `workflow_journal.py`, scheduler admission path, recovery, CLI workflow commands
- [ ] Generalize migration framework with per-migration metadata
  (`requires_foreign_keys_off`, `requires_legacy_alter_table`, post-rebuild checks + pragma
  restoration on failure)
- [ ] Schema migration v26: rebuild `workflow_runs` (status CHECK + `draining`/`paused`/
  `superseded`, add `pause_requested`/`run_relation`/`lineage_budget_root_run_id`/
  `parent_run_id`), rebuild `workflow_active_root` index, create
  `workflow_run_execution_nodes` + `workflow_run_artifact_imports`, backfill legacy rows
- [ ] Core model: `DRAINING`/`PAUSED` (nonterminal) + `SUPERSEDED` (terminal) in
  `WorkflowStatus`, `validate_run_transition`, run validators for new states and
  `pause_requested` (OCC row-version)
- [ ] Extend `active_for_root` in `workflow_journal.py` with the same status set
- [ ] Single admission transaction merging `_bind_node_inputs` + `_drive_node` with status/
  pause/execution-set/deadline/lineage-budget rechecks and no `await` inside
- [ ] Pause command (OCC `running,false -> draining,true`; blocked without user_cancel intent;
  reject on user_cancel-intent blocked), drain completion to `paused`, approval-pending drain
  projection, recovery interaction, resume, restart preserves pause
- [ ] CLI: `workflow pause|resume` with truthful output
- [ ] Deterministic tests per subplan Validation section (barrier-controlled races, no sleeps)
- [ ] Stage 7 workflow matrix, full offline gate, Ruff format/check, compileall, `git diff --check`
