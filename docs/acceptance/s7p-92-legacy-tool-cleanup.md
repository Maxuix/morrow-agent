# S7P-92 Legacy Tool Cleanup Evidence

## Outcome

The Pi-aligned surface is now Morrow's only production callable coding interface:
`read`, `ls`, `find`, `grep`, `edit`, `write`, and `bash`. Conditional configuration, Preference,
Artifact, Skill and sandbox-promotion tools retain their existing composition rules.

Production `local_tools.py` no longer contains Provider schemas, Pydantic argument models or
`RegisteredTool` factories for `list_directory`, `read_file`, `find_files`, `search_text`,
`apply_patch`, `write_file`, `run_command`, `delete_file`, `move_file`, `rename_file`,
`show_changes`, `git_status`, or `git_diff`.

## Compatibility boundary

- Underlying file, search, mutation, process, Git and ChangeSet services remain available to the
  current adapters, sandbox promotion, recovery and direct service tests.
- Old tool names were moved out of `PRODUCTION_TOOL_DECLARATIONS` into
  `LEGACY_TOOL_DECLARATIONS`. They can classify old durable rows that lack a frozen per-intent
  declaration, but `production_only=True` and the current composition gate reject them.
- Obsolete entries were removed from the current runtime static-contract inventory. Historical
  command-name checks remain only where stored `run_command` rows must still be interpreted.
- Destructive file service behavior remains covered directly and through
  `promote_sandbox_changes`; the current model-facing destructive path is policy-checked `bash`.

## Validation

- Focused migrated tool, process, mutation, sandbox, persistence, recovery and provider-contract
  tests: `176 passed`.
- Full offline gate: `1327 passed, 2 deselected in 87.08s`.
- No live Provider request or credential access was used.

Ruff format/check, compileall, CLI help and `git diff --check` passed at final closeout.
