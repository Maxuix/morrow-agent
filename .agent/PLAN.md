# Stage 7 Preflight Reliability Repairs — S7P-02 Tool Contracts

> Status: S7P-02 completed on its dedicated topic branch; root-owned merge remains pending
> Active subplan: 81 — Provider-visible tool schemas and recoverable argument contracts (completed)
> Branch: `codex/fix/s7p-02-tool-contracts`
> Base: activation baseline `36f3be18d2d9ce813a827a704ea911aa4baa502a`
> Source authority: the user-requested S7P-02 checklist, current code, and deterministic checks

## 1. Objective

Make the exact tool contract sent to the current OpenAI-compatible Provider conservative,
machine-checkable and consistent with Morrow's bounded argument validator, capability/recovery
declaration, handler preflight and error envelope. Repair the hidden `run_command` XOR contract and
the other reproduced cross-field/budget mismatches, then prove a scripted Agent can consume one
bounded invalid-argument response and correct its next call.

S7P-02 does not add workspace project instructions, delete/move/rename operations, completion
checking, round-budget changes, Provider retry/backoff, steering/follow-up, Workflow code, public
event changes, runtime-policy default changes or dependencies.

## 2. Located defects

1. The final `run_command` schema sent by `serialize_tool()` makes both `argv` and `shell` nullable
   optional properties. The schema accepts `{}` and accepts both fields while
   `CommandRequest.exactly_one_command_form()` rejects both shapes at execution time.
2. `tests/test_local_tool_factories.py::test_run_command_schema_requires_xor_and_forbids_install_or_network`
   is a false-positive contract test: it checks descriptions only and never validates the XOR.
3. `WriteFileArguments` requires `expected_sha256` for replace and forbids it for create only in an
   after-validator; the Provider schema does not express either branch.
4. `write_file.content` advertises 1 MiB, while `PydanticArgumentsValidator` first enforces the
   common 64 KiB string / 128 KiB JSON budget. Provider-visible and executable limits therefore
   disagree even before handler preflight.
5. Workspace path lexical rules are repeated as runtime validators and are mostly absent from the
   schema. `search_text` exposes the ambiguous field name `pattern` instead of the checklist's
   query vocabulary. Equivalent errors can therefore present different model-visible shapes.
6. `tool_parameters_from_model()` and `PydanticArgumentsValidator.schema` expose raw Pydantic
   output; there is no supported way to attach a conservative explicit Provider schema for
   cross-field rules while retaining the typed runtime model.
7. `make_tool()` copies a validator schema into `ToolDefinition`, but registration/snapshot and the
   Provider serializer do not audit later drift or prove the final wire schema is supported by the
   current Provider path. Capability/recovery names are checked, but the complete contract is not.
8. Pydantic failures return bounded `{path,type}` diagnostics, but cross-field failures commonly
   collapse to `$ / value_error`; the model does not receive a stable expected shape such as
   “exactly one of argv or shell”. No AgentLoop test proves correction on the next tool call.
9. Error-code separation exists in the enum/mapping, but no single contract matrix prevents
   `permission_denied`, `invalid_target`, `not_found`, `search_failed` and `execution_failed` from
   collapsing during this refactor.

## 3. Frozen implementation decisions

- Keep one public `run_command` tool. Do not split policy/recovery semantics across new command
  tools. Express its two legal shapes with the conservative Draft 2020-12 subset already supported
  by the OpenAI-compatible request path and Morrow's local schema validator.
- Allow a Pydantic-backed tool to supply an explicit Provider schema. The same compiled schema must
  validate raw JSON before Pydantic constructs the typed argument model; it is not documentation
  only. Schema-valid inputs must be a conservative subset of runtime-valid inputs.
- Add a fail-closed Tool Contract Audit at construction/registration or snapshot composition. It
  verifies schema support/bounds, validator-definition digest equality, names, recovery/capability
  declarations, resolver/handler presence and Provider wire stability. It stores or logs no
  arguments, results or secrets.
- Use one shared workspace-relative path annotation/helper for Provider-visible lexical bounds and
  typed runtime validation. Filesystem existence, symlink and workspace containment remain handler
  preflight facts with their existing distinct error codes.
- Rename the Provider-visible `search_text` text input to `query`; keep file discovery's glob-like
  `pattern` vocabulary. Update the handler and tests atomically; do not accept ambiguous duplicate
  query fields.
- Make Provider-visible content/array/string bounds no looser than the common raw-argument budget.
  Prefer a conservative usable limit over advertising values the executor always rejects.
- Encode `write_file` create/replace revision branches and `run_command` argv/shell branches in the
  final Provider schema. Preserve Pydantic after-validators as defense in depth.
- Model-facing invalid-argument feedback may add one bounded, value-free expected-shape hint.
  Durable S7P-01 observations continue to persist only the approved `{path,type}` projection; no
  raw values or full arguments are added to events, SQLite, YAML or terminal output.
- Dynamic MCP tools continue to use their frozen `JsonSchemaArgumentsValidator`; the common audit
  must accept the same bounded local-only dialect without widening MCP/network authority.
- The historical live invalid-argument percentage is not fabricated. S7P-02 publishes offline
  contract/repair evidence; the frozen repeated external threshold is measured in S7P-09.

## 4. Contract and schema work

### Provider schema authority

- Extend `PydanticArgumentsValidator` / `make_tool()` with an explicit schema seam whose default is
  the model schema and whose override is normalized and locally compiled.
- Ensure `ToolDefinition.function.parameters`, the validator's schema, the frozen schema digest and
  `OpenAICompatibleProvider.serialize_tool()` all use the same normalized object.
- Audit every production Direct inventory built by `build_session_application()`, including the
  auto-sandboxed promotion variant; separately retain dynamic MCP schema tests.

### `run_command`

- Provider schema accepts exactly one non-null command form: non-empty bounded `argv`, or non-blank
  bounded `shell`; it rejects neither/both/null/empty/whitespace forms and extra properties.
- Per-item and collection bounds must guarantee the existing total argument budget rather than
  relying on an invisible aggregate after-validator.
- `cwd` remains one bounded workspace-relative path and timeout keeps the existing positive 90s
  maximum. Policy prohibitions on network/install/Git/destructive behavior remain preflight/policy
  semantics, not misleading JSON Schema claims.

### Other static Direct tools

- Encode the `write_file` mode/revision relationship and align content size with executable raw
  argument limits.
- Normalize path/query/line/count/revision fields and add descriptions only where they convey
  executable constraints.
- Audit operation-dependent schemas such as configuration and preference management. Encode
  representable required/mutually-exclusive shapes; keep irreducible filesystem/state facts in
  deterministic preflight errors with contract cases.
- Do not weaken Skill Script, approval, capability, sandbox or recovery boundaries merely to make a
  schema easier for a model.

### Recoverable diagnostics

- Schema failures identify a bounded field path and stable keyword/type. Known cross-field rules
  return one concise safe expected shape.
- Repeated identical invalid-argument facts remain visible to the existing loop detector; S7P-06
  owns progress-based stopping, so S7P-02 does not add a competing loop state machine.
- Preserve distinct policy/target/existence/search/handler failure codes and prove them in one
  matrix.

## 5. Test-first implementation sequence

1. Add a new tool-contract test module that first proves the current final Provider wire accepts
   empty/both command forms, the write-file branch mismatch and the advertised/executable size
   mismatch.
2. Add explicit Provider-schema support plus local compilation/audit and fail-closed drift tests.
3. Repair `run_command`, `write_file`, shared paths and `search_text.query`; update factories,
   handlers, schema digests and focused tests together.
4. Add a full static inventory audit comparing definition, validator, recovery/capability and
   captured OpenAI-compatible request kwargs. Cover ordinary and auto-sandboxed inventories.
5. Add invalid-shape tables for empty/both/null command forms, wrong path/query/range/revision
   types, create/replace revision branches, extras and raw argument budgets.
6. Add a scripted Provider AgentLoop test: first call is invalid, the tool message contains bounded
   actionable feedback, the next call corrects the shape and succeeds, and no raw value leaks into
   durable diagnostics.
7. Add the error-code separation matrix and dynamic MCP compatibility regression.
8. Publish `docs/acceptance/s7p-02-tool-contracts.md`, update execution state and run all focused and
   repository-wide offline gates.

## 6. Validation

```bash
uv run pytest -q tests/test_tool_contract_audit.py tests/test_local_tool_factories.py
uv run pytest -q tests/test_tools.py tests/test_process.py tests/test_provider.py
uv run pytest -q tests/test_agent_run_observability.py tests/test_dynamic_tool_contracts.py
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
uv run morrow run --help
git diff --check
```

No live Provider/model/Pi/MCP/network/credential test is authorized. Scripted Providers, captured
request kwargs, temporary workspaces and local schema validation provide deterministic evidence.

## 7. Completion, review and integration

- Acceptance evidence maps every schema/validator/capability/handler/envelope contract to code and
  tests, records the offline invalid-argument repair scenario, and explicitly carries the <=1%
  repeated live-evaluation gate forward to S7P-09.
- After coherent verified commits, the Luna Max implementation session spawns a read-only Luna Max
  subagent to review the complete activation-base...HEAD diff. The reviewer checks schema/runtime
  equivalence, Provider compatibility, hidden constraints, diagnostic leaks, error-code collapse,
  recovery/capability drift and false-positive tests.
- The implementation session verifies and repairs every confirmed finding, reruns focused and full
  offline gates, updates acceptance/execution state and commits the final state.
- The implementation task does not merge, push, delete its branch/worktree, touch the three
  user-owned research documents or start S7P-03. This root task performs independent ancestry and
  cleanliness checks, fast-forward merges into local `main`, then retires the clean task resources.
