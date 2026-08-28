# Subplan 91 — Mainstream Model-Facing Tool Interface

> Status: completed and verified; pending fast-forward into the parent S7P-09 execution branch
> Branch: `refactor/mainstream-tool-interface`
> Stack base: verified S7P-09 evaluator-environment repair `467c347`
> Return target: Subplan 90, with a new source/profile pin

## Outcome

Make the default coding surface familiar to models trained on Pi, Codex and Claude Code:
`read`, `ls`, `find`, `grep`, `edit`, `write`, and `bash`. Match Pi's field names and small schemas
where the existing backend can preserve semantics. Automatically translate these requests into
Morrow's confined, revision-checked and recoverable execution services.

## Ownership

- `src/morrow/application/local_tools.py` and default tool composition in `src/morrow/bootstrap.py`;
- durable declarations/runtime contract names needed for the seven aliases;
- narrow name-family checks in preparation, permission, persistence and tool-cycle code;
- focused local-tool/composition/recovery tests;
- `docs/ARCHITECTURE.md`, README usage inventory and new acceptance evidence;
- Subplan 90 profile generation only as needed to prove the new inventory offline.

## Contract

1. Provider-visible core schemas use Pi-compatible names and fields. They do not require SHA-256,
   create/replace mode, argv/shell XOR, relative-path regexes or nested schema-budget branches.
2. `edit` takes `path` plus `edits[{oldText,newText}]`; `write` takes `path` and `content`; `bash`
   takes `command` and optional `timeout`.
3. Relative paths, `./` paths and absolute paths inside the frozen workspace normalize to one
   internal relative identity. Paths resolving outside it fail before any effect.
4. `edit` and `write` read/freeze the current source revision during preflight. Publication still
   revalidates the frozen plan, so a concurrent change is a conflict rather than an overwrite.
5. Unknown harmless Provider fields may be ignored by the compatibility models. All consumed data
   remains bounded and validated by the backend before effect.
6. Redundant default tools (`delete_file`, `move_file`, `rename_file`, `show_changes`, `git_status`,
   `git_diff`) remain implemented/testable but leave the default coding inventory. Read-only Git
   inspection is expressible through confined `bash`; destructive shell commands remain subject to
   the existing permission profile and are denied unless that capability is explicitly enabled.
7. Configuration, Preference, Artifact and Skill tools remain conditionally composed and retain
   their own contracts. No runtime-policy default or public event lifecycle changes.

## Validation

- Provider-wire snapshots prove the seven names and Pi-compatible required fields.
- Scripted Provider journeys cover discover/read/edit/create/replace/bash using only simple fields.
- Negative tests prove absolute-outside paths, protected resources, dangerous commands and stale
  write plans remain denied/conflicted by the backend.
- Recovery and permission audits recognize `edit`, `write`, and both `bash` isolation variants.
- Touched tests, full `pytest -m 'not live'`, Ruff format/check, compileall, CLI help and
  `git diff --check` pass before completion.

## Non-goals

- No live MiMo or campaign request.
- No S7P-00 threshold/task/verifier change and no reinterpretation of retained results.
- No dependency, Workflow, multi-Agent, MCP, browser or S7P-10 work.

## Completion evidence

- Exact provider-field audit and compatibility journeys pass.
- Full offline suite: `1344 passed, 2 deselected in 87.69s`.
- Ruff format/check, compileall, CLI help and `git diff --check` pass.
- No live Provider request or credential access occurred.
