# Configuration Tooling Acceptance Evidence

> Date: 2026-08-17
> Authority: [active plan](../../.agent/PLAN.md), [tracker](../../.agent/TRACKER.md)
> Scope: the first Stage 3 stateful-tool slice only

This artifact records evidence from the final integrated working tree. Scripted Provider
cases prove deterministic plumbing, state ownership, authorization, history, and recovery;
they do not prove that a real model will infer every intent correctly. Optional Live intent
evaluation was not run because no compatible credential was supplied and no Live run was
requested.

## Tree and package identity

- `git rev-parse HEAD`: `e462e10fddf1f5ad41318cb2c18b26fe300444ca`; the acceptance changes are
  in the working tree on top of this commit and are not represented as a new commit.
- Baseline recorded by the plan: `cbc3d6d`; Subplans 25–27 are now implemented in the current
  tree.
- The fresh wheel is `dist/morrow_agent-0.1.0-py3-none-any.whl`, 45 entries, SHA-256
  `9a664e67b62173073da654aa131c5bb1c5822d3c0c532506ae2d190109a420a`.
- Wheel inventory contains `morrow/application/configuration.py`, the bundled
  `morrow/resources/agent-policy.toml`, and no Handoff module or Gate/extractor module.
- The wheel was installed into a new Python 3.13 virtual environment with
  `uv pip install --no-deps`; import, bundled-policy loading, configuration-module discovery,
  and installed `morrow --help` succeeded. Dependency resolution was intentionally offline;
  the smoke process reused the already verified workspace dependency set.

## Requirement-to-evidence matrix

| Definition-of-done requirement | Direct evidence | Observed result |
|---|---|---|
| Every non-Slash input uses one ordinary AgentLoop path | `SessionOrchestrator.stream` source; `test_production_composition_uses_one_agent_loop_and_refreshes_state_projection`; `test_stage_1a_orchestrator_has_zero_config_calls_without_gate` | No pre-router or second model call path; ordinary turns reach `AgentRuntime.run_turn` once. |
| One standard `update_configuration` surface | `test_no_forbidden_tool_capability_is_registered_or_exposed`; `test_update_configuration_arguments_are_flat_and_sensitive_targets_are_absent`; wheel scan | Function-tool-capable production inventory is exactly `lookup_record`, `calculate`, `update_configuration`; demo registry remains exactly two tools. |
| Old Gate/extractor/structured route absent from production/package | precise `rg` and `zipgrep` scans below | No old Gate/extractor symbols or package modules. Generic structured infrastructure remains, with zero production callers. |
| One Application Service authority shared with Slash Commands | configuration service tests; `test_command_service_routes_deterministic_edits_to_one_patch_path`; factory source | Tool and deterministic commands use typed commands and `ConfigPatchService`; handler is a thin adapter. |
| Approval, partial writes, null session revision, tombstones, no-op matrix | `tests/test_configuration_tool.py` service/tool/serial/cancellation tests; `tests/test_state_and_workspace.py` | Applied and unchanged results, per-call approval, deliberate earlier-call persistence, reset tombstones, revisions, and no-op behavior pass. |
| Handler satisfies the architecture boundary | `test_configuration_tool_approves_and_returns_only_bounded_result`; source inspection of `make_configuration_tool` | No complete state, paths, provider data, arguments, or raw exceptions cross the ToolMessage result. |
| Generic policy has no domain branch | `tests/test_tools.py`; source scan of Runtime/ToolExecutor/AgentLoop/Orchestrator | Approval uses local generic metadata; no configuration-name branch exists in generic runtime code. |
| AgentLoop, ToolCycle, context, wire, events, cancellation, budgets remain legal | `tests/test_agent_tool_loop.py`, `tests/test_context_projections.py`, `tests/test_stage2_e2e.py`, `tests/test_terminal.py` | Lifecycle, ordered ToolMessages, context refresh, bounded results, cancellation closure, and terminal segmentation pass. |
| Failure and cancellation outcomes are bounded and state-safe | `test_configuration_tool_denial_unavailability_and_preflight_failure_do_not_write`; sensitive-path, timeout, and cancellation tests; generic tool tests | Denial, unavailable approval, invalid args, read-only state, conflicts, execution failure, timeout, and cancellation close normally without unauthorized writes. |
| Sensitive Provider/credential/model/security/identity fields unavailable | schema assertions; `test_configuration_sensitive_path_fails_before_approval`; production source scan | Sensitive fields are absent from the flat schema and invalid calls fail before approval. |
| Unsupported Adapters have no hidden fallback | `test_unsupported_adapter_capability_preserves_plain_chat_without_tools`; `SessionApplication` composition | Unsupported Adapter is tool-free; explicit `/config` remains deterministic and available. |
| Intent-policy corpus is covered honestly | parameterized ordinary/one-turn/negative/quotation/hypothesis/ambiguous test; mixed-task test; terminal product test | Scripted cases exercise plumbing and no-write behavior. They are not reported as real-model semantic accuracy; Live is `not run`. |
| Plan/docs/evidence/package are reconciled | this artifact; README, architecture, roadmap; `.agent/PLAN.md`, `TODO.md`, `TRACKER.md`, `LOG.md`, subplan index | Current production claims name the configuration slice and keep file/search/edit/Shell work explicitly incomplete. |
| No unrelated Stage 3/4/5/6 capabilities entered | `tests/test_stage_boundary.py`; tool inventory; wheel/source scans | No file, Shell, Git, network, browser, MCP, Skill, persistent Session, memory, summary, background, or sub-agent capability was added. |
| All mandatory gates pass | final command record below | Green on the final tree; one Live test remains deselected by policy. |

## Integrated product scenarios

The direct configuration tests cover the following branches without a real network:

- approved session/workspace/profile writes and next-request state projection refresh;
- ordinary discussion, one-turn style, negation, quotation, hypothesis, and ambiguous scope
  remain ordinary Assistant responses with no write in the scripted cases;
- mixed lookup plus configuration in one public turn;
- multiple calls execute serially, one approval per call, with an applied first call preserved
  when a later call is rejected;
- approval EOF/Ctrl+C, task cancellation, terminal approval timeout, and synthetic ToolCycle
  closure followed by ordinary model recovery;
- repeated set, empty unset, duplicate append, zero-match remove, reset, missing Profile,
  cleared documents, read-only/future documents, revision conflict, write failure, and
  workspace isolation;
- `/config` and `/workspace` use the shared typed command path, while natural-language turns
  are logged and dirty and Slash commands remain outside ConversationLog;
- `/new`, dirty `/exit`, primary-prompt EOF, and confirmation EOF/Ctrl+C behavior remain
  covered by terminal tests.

## Precise scans and final command record

The following scans are required to remain empty or limited to the generic structured module:

```text
rg -n "ConfigIntentGate|GateDecision|ConfigExtractionResult|config_extractor|extraction_result" src
  -> no matches

rg -n "complete_structured\(" src --glob '*.py'
  -> definition only in src/morrow/runtime/structured.py; no production caller

zipgrep -n -E "ConfigIntentGate|GateDecision|ConfigExtractionResult|config_extractor|extraction_result" dist/morrow_agent-0.1.0-py3-none-any.whl
  -> no matches
```

Final command results on the integrated tree:

```text
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest -m 'not live'
  -> 299 passed, 1 deselected
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run pytest --collect-only -q
  -> 300 tests collected
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff format --check .
  -> all files formatted
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run ruff check .
  -> All checks passed
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run python -m compileall -q src tests
  -> passed
UV_CACHE_DIR=/private/tmp/morrow-uv-cache uv run morrow --help
  -> passed
git diff --check
  -> passed
```

The bundled policy still reports `tool_timeout_seconds = 120`. No Live Provider or real-model
intent evaluation was run. Stage 3 remains incomplete: file reading/search/editing, Shell/Git,
and other local project capabilities require a separate explicit scope and authorization.
