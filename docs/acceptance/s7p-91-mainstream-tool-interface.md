# S7P-91 Mainstream Tool Interface Evidence

## Outcome

Morrow's default coding surface now uses the same seven core names and field families as the frozen
Pi 0.84.2 profile: `read`, `bash`, `edit`, `write`, `grep`, `find`, and `ls`. The ordinary default
composition exposes nine tools because Morrow also composes `update_configuration` and
`run_skill_script`; Auto Sandboxed additionally exposes `promote_sandbox_changes`.

The old dedicated factories remain in the codebase for explicit composition and recovery
compatibility, but `list_directory`, `read_file`, `find_files`, `search_text`, `apply_patch`,
`write_file`, `run_command`, `delete_file`, `move_file`, `rename_file`, `show_changes`, `git_status`,
and `git_diff` are no longer part of the default model-visible inventory.

Canonical compact JSON for the model-visible tool name/schema list fell from 11,951 bytes in r6 to
5,923 bytes in the new default. The seven core entries use 2,742 bytes, compared with 3,627 bytes
for the retained Pi profile; Morrow's extra bytes are now owned by its configuration/Skill product
capabilities rather than core coding protocol complexity.

## Model-facing contract

| Tool | Required fields | Optional fields |
|---|---|---|
| `read` | `path` | `offset`, `limit` |
| `bash` | `command` | `timeout` |
| `edit` | `path`, `edits[{oldText,newText}]` | none |
| `write` | `path`, `content` | none |
| `grep` | `pattern` | `path`, `glob`, `literal`, `ignoreCase`, `context`, `limit` |
| `find` | `pattern` | `path`, `limit` |
| `ls` | none | `path`, `limit` |

These schemas do not expose `expected_sha256`, create/replace mode, argv/shell XOR, path regexes,
`oneOf`, string/item maxima, timeout policy fields, or `additionalProperties: false`. Compatibility
models ignore harmless unknown fields. The bounded raw call envelope remains an execution contract,
not a field-level burden placed on the model.

## Adapter and safety evidence

- Relative, `./` and absolute paths inside the frozen workspace normalize to one internal relative
  path. Lexical normalization deliberately does not resolve away the visible symlink alias, so both
  alias and resolved-target sensitive-resource checks still run.
- Paths outside the workspace reach the execution adapter and return bounded `outside_workspace`
  results before effect; they are not rejected through a provider-visible regex.
- `edit` reads the current source revision during preflight and converts `oldText`/`newText` to the
  existing exact-edit service. `write` inspects the target, selects create or replace, and freezes
  the current revision automatically. Publication still revalidates the frozen plan under path
  locks, so existing stale-plan/concurrent-publication tests retain conflict behavior.
- `bash(command, timeout?)` converts to the existing command request and uses the unchanged process
  preflight, permission, approval, redaction, sandbox, artifact and recovery paths. Timeouts are
  bounded execution-side. Destructive, network, dependency-install and Git-write effects remain
  subject to capability policy; a scripted `rm`/`mv` call is denied before effect when destructive
  capability is not enabled.
- Durable declarations and runtime audits now cover all seven aliases, including Host and native
  sandbox variants of `bash`. Grant evidence, persisted command artifacts, turn permissions,
  recovery journal rules and tool-cycle behavior recognize both the new alias and retained legacy
  command name.

## Deterministic validation

- `tests/test_local_tool_factories.py` proves the exact seven schema field sets, absence of legacy
  protocol keywords, open extra-field compatibility, absolute-inside reads, outside denial, and an
  end-to-end read/edit/create/replace journey with no model-provided revision or mode.
- `tests/test_process.py`, `tests/test_sandbox.py`, `tests/test_stage4_execution.py` and
  `tests/test_tool_contract_audit.py` prove command recovery, sandbox composition, durable
  declarations and OpenAI-compatible wire/audit stability.
- Mutation and workspace-change suites retain exact edit, atomic publication, stale revision,
  concurrent publication, protected-resource and destructive-operation coverage for the backend
  services used by the compatibility layer.
- Full offline gate: `1344 passed, 2 deselected in 87.69s`. Ruff format/check, compileall, CLI help
  and `git diff --check` also passed.

No live Provider request or credential access was used for this repair. Every prior S7P-09 profile
and campaign remains immutable and non-comparable to this new interface; Subplan 90 must refreeze
from a new source pin.
