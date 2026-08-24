# Subplan 63 — Stage 6 Dependency and Contract Spike

> Status: ready, not started
> Branch: `codex/feat/stage6-contract-spike`
> Prerequisite: Stage 5 accepted; Stage 6 final proposal approved as planning baseline

## Objective

Resolve the few decisions that would otherwise force later rewrites: MCP SDK/dependency choice,
JSON Schema dialect, protocol result coverage, Skill package canonicalization, context budgets and
the exact per-run preparation contract. This subplan changes no production behavior and adds no
permanent dependency.

## Ownership

- `docs/research/stage6-mcp-dependency-spike.md`
- `docs/reviews/stage-6-skills-and-extensions-final-proposal.md` only if measured facts require a
  correction
- contract fixtures/prototypes under `tests/spikes/` only when executable evidence is useful
- `.agent/` execution state

## Tasks

1. Record the current AgentRun, ToolExecutor, PermissionSnapshot, CapabilityPolicy, Process service,
   AdapterRegistry, migration and backup seams with exact source references.
2. Evaluate the official MCP Python SDK in a temporary environment: Python 3.12 support, stdio
   lifecycle, cancellation, timeouts, tool schemas, content/result types, error behavior, direct and
   transitive dependencies, license and offline Fake Server support.
3. Evaluate whether the SDK already supplies adequate JSON Schema validation. If not, compare a
   focused `jsonschema` dependency with a deliberately limited internal validator; select supported
   `$schema` dialects and fail-closed behavior.
4. Prototype only the narrowest connect/list/call/close path against a local Fake stdio server.
   Do not integrate it into Morrow or use credentials/networked MCP.
5. Measure serialized budgets for representative PreparedAgentRun refs, 1/4/8 Skill selections,
   bounded Skill contexts and 1/16/64 MCP tool snapshots. Lock reference-only AgentRun fields and
   dedicated context/result budgets.
6. Define canonical Skill package path normalization, tree hashing, symlink/non-regular-file rules,
   Unicode/case collision behavior and managed `skv_` directory envelope.
7. Confirm v14/v15/v16 migration ownership and backup v2 manifest versioning.
8. Write one ADR-style conclusion listing selected approach, rejected alternatives, dependency
   recommendation, exact package versions/ranges to consider, risks and the user-approval gate.

## Validation

- Any spike tests run locally and deterministically; no user state is touched.
- `git diff --check`
- If Python fixtures are added: `uv run ruff format --check .`, `uv run ruff check .`, and focused
  tests.
- Verify `pyproject.toml` and `uv.lock` are unchanged.

## Exit criteria

- Every decision above has measured evidence and one selected answer.
- The proposal/plan is corrected if a Spike fact contradicts it.
- No permanent dependency or production MCP code is present.
- If dependencies are recommended, the next MCP dependency change is explicitly marked as requiring
  user approval; Subplan 64 may proceed independently.
