# Subplan 02 — Core Contracts and Test Doubles

> Stage: 1A  
> Status: pending  
> Parent: [Stage 1 implementation plan](../PLAN.md)

## Objective

Define the small set of domain contracts required by Stage 1, together with deterministic test doubles that keep default verification offline.

## Prerequisites

- Subplan 01 is complete.

## Tasks

1. Define typed identifiers and value objects for messages, `ModelRef`, model events, agent events, turn/session IDs, finish reasons, and model error codes.
2. Fix the Stage 1 public event envelope and the five allowed event types without naming future tool events.
3. Define ports for `ModelProvider`, `ProviderFactory`, `CredentialStore`, `WorkspaceResolver`, `GlobalConfigStore`, `WorkspaceIndexStore`, and the Stage 1 `ProjectStateStore` facade.
4. Keep global configuration and the workspace index outside `ProjectStateStore`. Ensure all `ProjectStateStore` methods explicitly require `workspace_id` and that future session/message storage is excluded.
5. Implement dynamic adapter registration keyed by `adapter_id`; presets remain data and no Provider name branch is permitted.
6. Implement `ScriptedModelProvider`, `MemoryCredentialStore`, fixed clock/ID sources, and temporary state-root support.
7. Add a default-test network guard that fails unexpected socket access while allowing explicitly marked Live tests to opt out.
8. Add contract tests for event ordering, Provider registration, credential operations, and unknown event-field tolerance.
9. Add a narrow architecture test ensuring `core` does not import SDK, CLI, rendering, YAML, keyring, or locking libraries.

## Verification

- A second Fake Adapter/Provider can be registered without changing core code.
- Every accepted scripted turn can satisfy exactly one start and one completion with strictly increasing sequence values.
- Cancellation is represented only by `finish_reason=cancelled`.
- No public event contains raw exceptions, SDK objects, reasoning, or a credential sentinel.
- Default tests fail if they attempt network access.
- Relevant unit, contract, Ruff, and formatting checks pass.

## Completion criteria

- Later subplans can implement adapters and application services solely against the accepted ports.
- Core is framework-independent and contains no speculative tool, memory, Skill, or scheduler contracts.
- All contract tests pass.

## Deliverables

- Core models, events, ports, and error taxonomy.
- Adapter registry.
- Deterministic reusable test-support layer.
