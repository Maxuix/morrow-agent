# Unified Model Failure Chain

> Status: completed
> Active subplan: none
> Activation base: `main@21d707f`
> Source authority: current user request and current runtime implementation

## Objective

Collapse model-call error handling into one typed failure chain. The Adapter produces one safe,
attributed failure fact; AgentLoop alone decides retry and terminal behavior; durable observations
consume that same fact without adding a new probe, gate, or content validator.

## Decisions

- Replace parallel `ModelEvent` error fields and `ModelProviderError` retry flags with one immutable
  `ModelFailure` value shared by streaming and non-streaming Provider boundaries.
- OpenAI-compatible streaming reports all expected failures as an error event; AgentLoop keeps one
  defensive exception normalizer for contract violations and unexpected implementations.
- Retryability is an Adapter classification fact, while retry execution and limits remain owned by
  AgentLoop and RunPolicy.
- Persist safe origin detail only where `internal` would otherwise be ambiguous; do not expand the
  database schema or expose SDK exceptions.
- Do not run another live proof as part of this refactor.

## Execution order

1. Introduce the unified failure value and migrate the Provider boundary.
2. Simplify ModelCallRunner and AgentLoop to consume that value once.
3. Update fake providers, reviewers and tests; remove obsolete retry helpers and split fields.
4. Run focused and full offline/static validation.
5. Commit, fast-forward into local `main` and retire the branch.

## Completion

- A streaming failure has one typed representation from Adapter through AgentLoop.
- AgentLoop contains the only retry-policy decision for AgentRun and compaction requests.
- Provider-versus-Adapter internal origin survives into safe terminal evidence.
- Relevant and full offline/static validation passes.

Completed by Subplan 99. A separately authorized current-code proof rerun is still required before
the S7P-10 gate can change from CONDITIONAL GO.
