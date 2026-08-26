# Progress Tracker

## Current status

S7P-01 is verified, fast-forward integrated into local `main@3f5c7cb`, and retired. S7P-02 is
active as Subplan 81. The root session reproduced the Provider/runtime mismatch using the actual
generated schemas and the same bounded local schema validator used for runtime-defined tools.

The main worktree contains only three user-owned untracked research documents. They are outside
Subplan 81 and must remain untouched.

## Active task

The dedicated `gpt-5.6-luna` / `max` implementation task is active on
`codex/fix/s7p-02-tool-contracts`. Test-first final-wire mismatch coverage is in place and
currently fails against the activation baseline; the next action is to add the normalized
Provider-schema seam and fail-closed audit before repairing the static tools.

## Located evidence

- Final `RunCommandArguments.model_json_schema()` marks both `argv` and `shell` optional/nullable.
  Local schema validation accepts `{}` and both fields, while strict Pydantic validation rejects
  them through an after-validator.
- The existing test named `test_run_command_schema_requires_xor...` asserts description strings and
  never validates XOR behavior.
- `WriteFileArguments.model_json_schema()` cannot express that replace requires a revision and
  create forbids one; those rules also live only in an after-validator.
- `write_file.content` advertises 1 MiB, but the shared raw validator rejects strings beyond 64 KiB
  and JSON beyond 128 KiB before Pydantic/handler execution.
- Workspace lexical path rules and several operation shapes are runtime-only. `search_text` calls
  its text input `pattern`, which conflicts with the requested query vocabulary used by coding
  agents.
- `PydanticArgumentsValidator.schema` always returns raw Pydantic output. `make_tool()` and
  `serialize_tool()` provide no explicit conservative schema seam or complete contract audit.
- S7P-01 already carries bounded `{path,type}` diagnostics; S7P-02 can add a safe model-facing shape
  hint without expanding the durable projection.

## Next action

Implement and test the shared normalized Provider schema/audit seam, keeping the first three
contract tests failing until the runtime and final Provider wire use the same bounded schema.

## Blockers

None.
