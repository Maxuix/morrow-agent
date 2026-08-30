# Subplan 92 — Remove Legacy Model-Facing Tool Adapters

> Status: completed, verified and fast-forward integrated into the parent S7P-09 branch
> Branch: `refactor/remove-legacy-tool-adapters`
> Stack base: S7P-09 execution branch `351cf8b`
> Return target: Subplan 90 capacity hold

## Outcome

Make the Pi-aligned tool layer the only production callable coding interface. Remove the obsolete
Provider schemas, argument models and factories for `list_directory`, `read_file`, `find_files`,
`search_text`, `apply_patch`, `write_file`, `run_command`, `delete_file`, `move_file`,
`rename_file`, `show_changes`, `git_status` and `git_diff`.

## Ownership

- `src/morrow/application/local_tools.py`;
- current-versus-legacy declaration and runtime-contract inventories;
- tests that import or instantiate retired adapters;
- current architecture, acceptance and human-facing tool inventories.

## Contract

1. Current production coding tools are `read`, `ls`, `find`, `grep`, `edit`, `write` and `bash`.
2. Conditional product tools, including sandbox promotion, configuration, Preference, Artifact and
   Skill capabilities, retain their existing composition rules.
3. Underlying file/search/mutation/process/Git services are not removed merely because their old
   model-facing wrappers are retired.
4. Historical durable executions using old names remain classifiable through a clearly labelled
   legacy recovery table. Old names are not accepted as current registered production tools.
5. Execution-side confinement, approvals, revision checks, redaction, atomic publication and
   recovery semantics do not weaken.

## Validation

- No retired schema, argument model or factory remains in production local-tool code.
- Default composition and Provider snapshots expose only the current names plus conditional product
  capabilities.
- Current tool journeys and backend safety/service regressions pass.
- Full offline tests, Ruff format/check, compileall, CLI help and `git diff --check` pass.

## Non-goals

- No live MiMo/Pi campaign request or credential access.
- No runtime-policy default, public event, protocol, task, Gold or verifier change.
- No removal of durable historical records or migration that makes old sessions unreadable.

## Completion evidence

- Retired production schema/model/factory symbols are absent from `local_tools.py` and all Python
  imports; current `PRODUCTION_TOOL_NAMES` contains only current and conditional tools.
- Focused migrated tool/process/mutation/recovery coverage passed `176`.
- Full offline suite passed `1327 passed, 2 deselected in 87.08s` after one unrelated transient
  context-compaction test failure passed alone and on the clean full rerun.
- Ruff format/check, compileall, CLI help and `git diff --check` passed.
- No live Provider request or credential access occurred.
