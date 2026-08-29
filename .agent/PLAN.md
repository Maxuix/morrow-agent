# Current-Version-Only Compatibility Removal

> Status: completed
> Active subplan: none
> Activation base: `main@ba1a028`
> Source authority: current user request and current code/tests

## Objective

Keep only the latest Morrow contracts and persisted formats. Migrate deterministic user-owned state
to the current representation when a safe transformation exists. Remove obsolete runtime readers,
aliases, facades, schema branches, historical evaluation fixtures and tests instead of retaining
decode-only or test-only compatibility.

## Decisions

- Current source models, YAML documents, SQLite schema and the complete Backup bundle are the only
  supported formats.
- Historical evaluation bundles and fixtures do not require migration and may be deleted.
- Compatibility that exists only for internal tests is removed; tests must use current public or
  owning-module contracts.
- Existing user-owned YAML/SQLite data receives an explicit deterministic current-format migration
  where possible. Unsupported data fails with a clear current-version error or is removed by the
  migration; the runtime does not keep a legacy reader afterward.
- External interoperability contracts (OpenAI-compatible transport, MCP, Agent Skills and current
  mainstream tool schemas) are not Morrow-version compatibility and remain supported.
- Paused S7P-09 evidence remains immutable and is not reinterpreted or used as an input to this work.

## Execution order

1. Remove unreferenced shims, aliases, obsolete entrypoints, unreachable branches and historical
   evaluation-only resources; migrate internal imports to their owning modules.
2. Collapse fixed-field and generic Preference state to the current generic documents, with one
   explicit migration path and no decode-only legacy model afterward.
3. Remove retired completion-truth, legacy tool recovery, permissive ToolExecutor and old AgentRun
   snapshot fields; consolidate the Operational Store around the current schema.
4. Make the complete Backup bundle the sole backup contract and remove versioned alternatives.
5. Update architecture, roadmap and human documentation to describe only the current baseline.
6. Run focused tests after each slice and the complete offline/static/CLI/diff gate before merge.

## Completion

- Repository searches find no Morrow-version `legacy`, compatibility alias/facade, old schema
  decoder, retired tool declaration or historical acceptance fixture in active product/test paths.
- Fresh and deterministically migrated current state load through one code path.
- The complete offline suite, Ruff, compileall, CLI help and diff checks pass.
- Verified commits are fast-forward integrated into local `main`; no Live request is run.

Completed by Subplans 94–95; final implementation commit `bb88a73` is integrated into local `main`.
