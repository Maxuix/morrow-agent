# Subplan 76 — Runtime Policy Configuration

> Status: completed locally
> Branch: `fix/stage6-runtime-policy`
> Baseline: `main@24f8ae8`

## Objective

Replace scattered process-wide runtime tuning defaults with one packaged `runtime-policy.toml`,
then allow a strictly typed optional `runtime_policy` overlay in the user's `config.yaml`. Preserve
code-owned safety ceilings and all permission, secret, path, schema, payload and storage invariants.

## Ownership

- packaged runtime-policy contracts and loader;
- global `config.yaml` runtime-policy override contract and lossless persistence;
- AgentRun, Learning Review and Preference Review composition defaults;
- tests and documentation that classify configurable defaults versus fixed safety invariants;
- removal of the superseded packaged `agent-policy.toml` resource.

## Tasks

1. Inventory hardcoded production constants and classify each as runtime tuning, entity/request
   configuration, compatibility/schema value, or fixed safety invariant.
2. Add a versioned packaged `runtime-policy.toml` containing AgentRun and Review defaults.
3. Add strict partial user overrides under `config.yaml: runtime_policy`; reject unknown fields and
   values outside code-owned safety ceilings. Do not expose this path to Agent configuration tools.
4. Merge defaults and overrides at application bootstrap and freeze effective values into existing
   AgentRun/Review evidence.
5. Replace Learning Review's hardcoded 15-second timeout and review lease defaults with effective
   policy values; wire Preference Review timeout, lease and retry backoff through the same source.
6. Preserve per-MCP/per-script/per-command timeouts as request/entity configuration and preserve all
   security budgets, permission defaults, secret filters and fail-closed validators in code.
7. Add focused regression coverage for packaging, default resolution, safe overlays, invalid/future
   overlays, YAML preservation and review composition.
8. Update README, architecture and Stage 6 acceptance/roadmap evidence; run focused and full gates.

## Validation

- focused policy/config/review tests;
- `uv run pytest -m 'not live'`;
- Ruff format/check, compileall, CLI help and `git diff --check`;
- no live Provider, network, credential or user-state execution.

## Exit criteria

- packaged defaults have one authority: `runtime-policy.toml`;
- user `config.yaml` may omit overrides or safely override only declared runtime fields;
- invalid/unknown or over-ceiling overrides fail closed without leaking values;
- Learning Review no longer receives its production timeout from a constructor literal;
- fixed safety invariants remain code-owned and are documented;
- verified changes are committed and fast-forwarded to local `main`, then the topic branch is retired.

## Completion evidence

- focused policy/config/review matrix: `135 passed in 5.64s`;
- full non-live suite: `1074 passed, 2 skipped, 2 deselected in 42.11s`;
- Ruff format/check, compileall, five CLI help commands and `git diff --check`: passed;
- no live Provider, network, credential, user-state or remote-push path was used.
