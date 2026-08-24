# Subplan 71 — Dynamic Tool Contracts

> Status: pending
> Branch: `codex/feat/stage6-dynamic-tools`
> Prerequisite: Subplan 70 complete; Subplan 63 selected Schema approach

## Objective

Add the two generic seams required by MCP—runtime arguments validation and declaration-owned recovery
metadata—without changing local tool behavior or introducing MCP-specific branches in ToolExecutor,
ToolCycle or recovery.

## Ownership

- new focused `src/morrow/runtime/tool_arguments.py`
- recovery declaration contracts in `core/execution.py` or a focused extracted module
- bounded edits to `runtime/tools.py` and recovery classification/composition
- migrations only if Spike proves stored ToolExecution lacks required frozen evidence; prefer existing
  prepared intent/schema digest fields when sufficient
- `tests/test_dynamic_tool_contracts.py` plus all local tool/recovery contract tests

## Tasks

1. Define `ToolArgumentsValidator` with a single bounded validate call and sanitized stable errors.
   Implement `PydanticArgumentsValidator` as the compatibility path for every existing local tool.
2. Implement the selected JSON Schema validator with explicit `$schema` dialect handling, depth/
   properties/array/number/string budgets and refusal of unsupported/remote references.
3. Change `RegisteredTool` to carry a validator. Preserve `arguments_model` through a temporary
   constructor adapter only if required for incremental migration; remove dual authority before
   closing the subplan.
4. Define `ToolRecoveryDeclaration` containing effect class, missing-completion policy and optional
   reconciliation strategy ID. Attach it to every RegisteredTool.
5. Freeze the declaration or its digest into durable execution evidence before handler entry.
   Recovery classifies from stored declaration, not a static tool-name table or current registry.
6. Assign explicit declarations to all existing local/configuration/preference tools and prove
   byte-for-byte equivalent ordinary outcomes.
7. Add a test-only dynamic tool whose name is unknown at startup to prove validation, approval,
   execution and recovery without special casing.

## Validation

- Pydantic compatibility, malformed JSON, Schema dialect/default, unsupported features, budgets,
  remote `$ref` refusal and sanitized errors.
- Every existing tool has one declaration; dynamic tool crash classifications are stable after the
  registry disappears or changes.
- Run local tool, ToolCycle, approval, journal, crash/recovery and architecture-boundary suites;
  standard quality commands and full non-live suite.

## Exit criteria

- ToolExecutor has one validator interface and one recovery declaration source.
- Existing tools have no behavior regression and MCP can register tools from runtime schemas.
- No `if name.startswith("mcp.")` or dynamic-name recovery table exists.
