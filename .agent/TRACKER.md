# Progress Tracker

## Current status

S7P-01 is verified, fast-forward integrated into local `main@3f5c7cb`, and retired. S7P-02 is
active as Subplan 81. The root session reproduced the Provider/runtime mismatch using the actual
generated schemas and the same bounded local schema validator used for runtime-defined tools.

The main worktree contains only three user-owned untracked research documents. They are outside
Subplan 81 and must remain untouched.

## Active task

Create and commit the S7P-02 activation plan, then dispatch its dedicated `gpt-5.6-luna` / `max`
implementation task. That task owns test-first implementation, same-session read-only Luna Max
review, repair, full offline validation and topic-branch commits.

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

Commit the Subplan 81 activation state on `main`, create `codex/fix/s7p-02-tool-contracts`, then
start the dedicated Luna Max implementation task from that exact branch.

## Blockers

None.
