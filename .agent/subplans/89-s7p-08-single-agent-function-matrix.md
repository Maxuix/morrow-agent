# Subplan 89 — S7P-08 Single-Agent Basic Function Matrix Regression

> Status: verified; pending local integration; activated 2026-08-27 from verified local
> `main@d84ac0d`
> Branch: `chore/s7p-08-single-agent-matrix`
> Dependencies: S7P-01 through S7P-07 are complete and integrated locally
> Gate type: P0 pre-Stage-7 evidence gate; no live Provider/model/Pi/MCP/network/credential run

## 1. Outcome

Produce an evidence-backed, repeatable regression proof that the current Direct Agent preserves all
basic single-Agent paths required before Stage 7. Every one of the 18 capability cells in the
approved S7P-08 checklist must map to tests that actually execute on this branch; high-risk cells
must include a failure and a recovery path. Published Stage 1 through Stage 6 acceptance surfaces,
the S7P-00 through S7P-07 repairs, and the current host's real macOS Seatbelt boundary must pass.

This subplan may close a regression in an already-promised capability after a deterministic failing
test proves it. It does not add a new product capability merely to make the matrix larger.

## 2. Authority and current-contract interpretation

1. The current user request, current code and current automated behavior outrank older proposal
   wording. The matrix starts from `main@d84ac0d`; it does not replay historical implementations.
2. Subplan 87 superseded the S7P-05 runtime Outcome Contract and CompletionChecker. The validation
   cell therefore proves scoped `ValidationFact` truth (`passed`, `failed`, `not_run`, wrong scope)
   and truthful final-stop observation. It must not restore semantic completion gating, workspace
   baselines, model correction, or verifier authority to the runtime.
3. S7P-06 owns long-horizon policy, compaction, truncation and Provider retry. S7P-07 owns durable
   steering/follow-up. S7P-08 regresses those contracts but does not redesign them.
4. A reproducible AgentRun snapshot may contain bounded immutable values or stable version/hash
   references. It must never contain credentials, reasoning, full tool arguments/results, command
   output, SDK objects, tracebacks or mutable live service objects.
5. The old acceptance documents remain historical evidence. If a cited test selector or behavior
   is stale, the new acceptance record must identify its current replacement rather than claiming
   that an obsolete selector ran.

## 3. Ownership

Primary ownership:

- a machine-readable S7P-08 coverage ledger under `tests/acceptance/`;
- focused matrix-contract and missing integration regressions under `tests/acceptance/` or the
  nearest existing test module;
- narrow production fixes only where a current promised contract is reproducibly broken;
- `docs/acceptance/s7p-08-single-agent-function-matrix.md`;
- `.agent/` execution state for Subplan 89.

Existing tests remain the preferred evidence. Do not duplicate a strong production-composition
test solely to obtain an S7P-08-prefixed name. Any new test must add missing cross-layer, failure,
recovery, snapshot or entrypoint evidence.

## 4. Non-goals and hold points

- Do not run the S7P-09 repeated real-model evaluation or Pi comparison.
- Do not run a live Provider, real network MCP server, browser, credential or external service.
- Do not implement Workflow, AgentDefinition, Scheduler, multi-Agent or any Stage 7 domain model.
- Do not add dependencies, change bundled `runtime-policy.toml` defaults, or change the public
  event lifecycle without a new explicit user decision.
- Do not restore the removed runtime completion gate or treat a passing validator as proof of
  business correctness.
- Do not weaken, delete, xfail or broadly skip a failing P0 path to make the gate green.
- Do not change S7P-00 protocol thresholds or manufacture S7P-09 evaluation data.
- Do not update historical pass counts in old acceptance records. Publish current evidence in the
  S7P-08 record and repair only a factually stale current-contract statement.

If a failure can only be repaired by one of the held changes above, stop, record the exact blocker
and ask the user before expanding scope.

## 5. Evidence admission rules

A capability cell is `PASS` only when all applicable rules hold:

1. At least one deterministic offline automated test exercises the promised behavior through the
   production service/application boundary, not merely a copied algorithm or inert mock.
2. The exact referenced selector executed successfully in the current Subplan 89 run.
3. High-risk behavior has an explicit negative path and a legal recovery/next-run path. These may
   be separate selectors, but both must execute.
4. Persisted/replayed behavior is reconstructed through the real Operational Store or documented
   versioned fixture where persistence is part of the contract.
5. Security claims assert absence from public events, terminal/JSONL, model projection and durable
   state using sentinel material; a source scan alone is supporting evidence, not the sole proof.
6. A scripted Provider, fake clock, fake MCP stdio server or isolated Git repository is valid
   offline evidence when it enters through the production adapter boundary. A live service is not
   required and cannot replace deterministic evidence.
7. A meta-test that validates the coverage ledger does not itself satisfy a capability cell. The
   behavior selectors named by the ledger must also run.

Each ledger row records: stable cell ID, checklist text, risk level, positive selectors, required
failure/recovery selectors, stage acceptance sources, platform conditions, last execution result
and any superseded historical selector. Unknown selectors, duplicate cells, empty selector sets or
unexplained P0 skips fail the matrix-contract test.

## 6. Frozen capability matrix

| ID | Capability | Required current proof | Initial authoritative suites |
|---|---|---|---|
| `chat` | Ordinary conversation | no-tool answer, streaming, multi-turn context, cancel then healthy continuation | `test_conversation_and_loop.py`, `test_context_runtime.py`, `test_agent_guardrails.py`, `test_terminal.py` |
| `workspace_discovery` | Workspace discovery | list/find/search/read, ranged read and large-file/output bounds | `test_local_files.py`, `test_local_search.py`, `test_tool_contract_audit.py` |
| `structured_mutation` | Structured mutation | create/patch/replace/delete/move/rename; conflicts preserve user state and recover | `test_local_mutation.py`, `test_s7p04_workspace_change_lifecycle.py`, `test_stage3_product_acceptance.py` |
| `shell` | Shell | argv/shell, cwd, environment allowlist, timeout, cancel and non-zero exit | `test_process.py`, `test_capability_executor.py`, `test_agent_tool_loop.py` |
| `sandbox_approval` | Sandbox and approval | auto/manual/deny, preview/promotion, escape and symlink refusal, promotion conflict recovery | `test_sandbox.py`, `test_capability_policy.py`, `test_stage4_permissions.py` |
| `git_read_only` | Git read-only | status/diff, pre-existing dirty changes and non-repository result | `test_git.py`, `test_stage3_product_acceptance.py` |
| `validation_truth` | Validation and stop truth | pass/fail/not-run/wrong scope; model-owned stop remains distinct from validation telemetry | `test_capabilities.py`, `test_process.py`, `test_agent_run_observability.py`, `test_s7p05_completion_truth.py` |
| `provider_model` | Provider and model | local configuration/selection/probe, frozen ModelRef, usage, transient retry and permanent failure | `test_provider.py`, `test_provider_control.py`, `test_agent_run_preparation.py`, `test_s7p06_pi_parity.py` |
| `session_task` | Session and Task | create/recover/fork/continue/accept/cancel/checkpoint/idempotent submission | `test_stage4_application_api.py`, `test_stage4_context_fork.py`, `test_stage4_execution.py`, `test_stage4_recovery.py` |
| `persistence_artifact` | Persistence and Artifact | process rebuild, artifact references, missing/corrupt, backup/restore and migrations | `test_operational_store.py`, `test_stage4_artifacts.py`, `test_stage4_backup.py`, `test_stage4_doctor.py`, `test_stage4_recovery_crash.py` |
| `context` | Context | compaction, restart restore, long tool output, overflow/error recovery and pairing legality | `test_context_runtime.py`, `test_s7p06_pi_parity.py`, `test_conversation_and_loop.py` |
| `profile_memory` | Profile/Preference/Knowledge | minimal relevant injection, revision conflict, review/promotion/revert and inspectable provenance | `test_preferences_and_orchestration.py`, `test_preference_context.py`, `test_stage5_project_knowledge.py`, `test_stage5_configuration_promotion.py` |
| `skill` | Skill | discovery/selection, frozen version, context, restricted script, draft/usage/lifecycle | `test_skill_catalog.py`, `test_skill_selection.py`, `test_skill_context.py`, `test_skill_scripts.py`, `tests/acceptance/test_stage6_integrated.py` |
| `mcp` | MCP | desired state/catalog, frozen version/schema, schema error, fake-server crash, timeout/cancel and restart recovery | `test_mcp_control.py`, `test_mcp_runtime.py`, `spikes/test_mcp_stdio_spike.py` |
| `permission_grant` | Permission and Grant | capability snapshot, approve/deny, revoke/expiry and no permission expansion after recovery | `test_capabilities.py`, `test_capability_policy.py`, `test_stage4_permissions.py`, `test_stage4_recovery.py` |
| `runtime_control` | Runtime control | cancel, steering, follow-up, deadline, budget exhaustion and safe recovery/pairing | `test_runtime_control.py`, `test_agent_limits.py`, `test_agent_guardrails.py`, `test_s7p06_pi_parity.py` |
| `security` | Security | credentials/reasoning/full args/results/traceback absent from every public and durable surface | `test_stage2_product_acceptance.py`, `test_stage3_product_acceptance.py`, `test_headless_run.py`, Stage 4–6 doctor/backup tests |
| `entrypoint_consistency` | Entrypoint consistency | interactive CLI and headless execution share application preparation, AgentLoop, snapshots and terminal meaning | `test_headless_run.py`, `test_cli_commands.py`, `test_terminal.py`, `test_stage3_product_acceptance.py` |

The initial suites are discovery anchors, not automatic PASS claims. Phase A must replace each broad
file reference in the ledger with exact collected selectors and identify weak or absent evidence.

## 7. Published acceptance regression lanes

The full offline suite is the final umbrella gate, but it is insufficient for failure attribution.
Run and record these lanes separately using the current selectors:

### Lane A — Stage 1 product continuity

```bash
uv run pytest -q -m 'not live' \
  tests/test_cli_commands.py tests/test_context_runtime.py tests/test_provider.py \
  tests/test_state_and_workspace.py tests/test_structured.py \
  tests/test_preferences_and_orchestration.py tests/test_terminal.py
```

### Lane B — Stage 2 AgentLoop and tool protocol

```bash
uv run pytest -q -m 'not live' \
  tests/test_core_contracts.py tests/test_conversation_and_loop.py tests/test_tools.py \
  tests/test_agent_tool_loop.py tests/test_agent_limits.py tests/test_agent_guardrails.py \
  tests/test_stage2_e2e.py tests/test_stage2_product_acceptance.py tests/test_stage_boundary.py
```

### Lane C — Stage 3 local code-agent and safety surface

```bash
uv run pytest -q -m 'not live' \
  tests/test_local_files.py tests/test_local_search.py tests/test_local_mutation.py \
  tests/test_process.py tests/test_git.py tests/test_sandbox.py tests/test_capabilities.py \
  tests/test_capability_executor.py tests/test_capability_policy.py \
  tests/test_local_tool_factories.py tests/test_stage3_product_acceptance.py \
  tests/test_s7p04_workspace_change_lifecycle.py
```

### Lane D — Stage 4 durable runtime

```bash
uv run pytest -q -m 'not live' tests/test_operational_store.py tests/test_stage4_*.py
```

### Lane E — Stage 5 reviewable memory and preference

```bash
uv run pytest -q -m 'not live' \
  tests/test_stage5_*.py tests/test_preference_*.py \
  tests/test_preferences_and_orchestration.py tests/test_review_worker.py
```

### Lane F — Stage 6 Skill, Provider control and MCP

```bash
uv run pytest -q -m 'not live' \
  tests/acceptance/test_stage6_integrated.py tests/test_skill_*.py \
  tests/test_dynamic_tool_contracts.py tests/test_provider_control.py \
  tests/test_mcp_control.py tests/test_mcp_runtime.py tests/spikes/test_mcp_stdio_spike.py \
  tests/test_agent_run_preparation.py tests/test_configuration_tool.py tests/test_stage6_backup.py
```

### Lane G — S7P-00 through S7P-07 repairs

```bash
uv run pytest -q -m 'not live' \
  tests/test_code_agent_mini_eval.py tests/test_agent_run_observability.py \
  tests/test_headless_run.py tests/test_tool_contract_audit.py \
  tests/test_direct_coding_prompt.py tests/test_project_instructions.py \
  tests/test_s7p04_workspace_change_lifecycle.py tests/test_s7p05_completion_truth.py \
  tests/test_s7p05_audit_remediation.py tests/test_s7p06_pi_parity.py \
  tests/test_runtime_control.py
```

Overlaps are intentional: a cross-stage seam must be attributable in both its historical lane and
its current S7P lane. Exact results, skips and elapsed time go only into the new S7P-08 acceptance
record.

## 8. Real macOS Seatbelt gate

On the current macOS platform, run these existing tests from a genuine host-level process where
`CODEX_SANDBOX=seatbelt` is not already enclosing the test process:

```bash
uv run pytest -q \
  tests/test_sandbox.py::test_production_auto_sandbox_registers_only_native_tools_and_keeps_real_workspace_clean \
  tests/test_sandbox.py::test_macos_native_sandbox_blocks_real_workspace_home_and_network
```

Do not unset or spoof the environment variable merely to bypass the skip guard. The evidence must
record platform, backend probe result, exact selectors and pass/skip/fail status without exposing
host paths beyond the already-public bounded test names. A skip caused by nested execution is not
an acceptable P0 result; rerun at host level or mark S7P-08 blocked. Linux Bubblewrap remains a
fail-closed unit-tested non-current-platform path and is not represented as real Linux evidence.

## 9. Execution sequence

### Phase A — Freeze the coverage ledger

1. Collect all non-live node IDs and audit the Stage 1–6 acceptance documents plus S7P-00–07
   records against current test names.
2. Add the machine-readable 18-cell ledger and a strict contract test. Resolve broad anchors in
   section 6 to exact selectors and identify positive/failure/recovery evidence.
3. Add a snapshot-specific cross-reference for Provider/ModelRef, Skill, MCP, Preference,
   Knowledge, ToolSet, permission and context policy. A component is not green merely because its
   configuration CRUD works; the admitted AgentRun must freeze or reference its exact version.
4. Classify each cell as `covered`, `gap`, `stale_reference` or `host_required`. Do not change
   product code during this audit.

### Phase B — Close evidence gaps and confirmed regressions

5. Add the smallest failing integration tests for uncovered current promises, prioritizing
   entrypoint equivalence, complete AgentRun snapshot reproduction and high-risk recovery seams.
6. Run the focused matrix. For every failure, classify it as product defect, test defect, stale
   document, unavailable host condition or environmental blocker.
7. For a confirmed product regression, reproduce it in the nearest owning module, make the
   narrowest test-first repair, and rerun the affected cell plus its historical acceptance lane.
8. A flaky pass is not closure. Isolate it, remove nondeterministic wall-clock/scheduling reliance,
   and record the cause. Do not erase a failure with repeated reruns alone.

### Phase C — Execute the gate

9. Run the focused 18-cell matrix and Lanes A–G.
10. Run the real host-level Seatbelt gate.
11. Run the Code Agent Mini Eval offline self-check. Do not start or finalize any real Agent/Pi
    evaluation bundle.
12. Run the full non-live and static/CLI gates in section 11.

### Phase D — Publish and close

13. Publish `docs/acceptance/s7p-08-single-agent-function-matrix.md` with the exact cell-to-selector
    table, current results, snapshot evidence, host Seatbelt evidence, defect/remediation log,
    every skip and the final S7P-08 verdict.
14. Perform a final read-only audit of the complete base-to-tip diff for false coverage, mock-only
    claims, secret exposure, skip masking and changes outside this subplan.
15. Fix confirmed findings, rerun affected and final gates, commit coherent verified progress and
    update `.agent/` state.
16. Fast-forward verified work into `main` under the repository workflow. Do not start S7P-09
    automatically.

## 10. Defect and skip protocol

- Reproduce a failure with a minimal deterministic test before editing production code.
- Preserve user-owned dirty files and test conflicts, rollback and restart from isolated temp
  workspaces. Never normalize a destructive failure into success.
- Use fake SDK chunks, scripted Providers, fake MCP stdio servers, fixed clocks and isolated Git
  repositories. Do not assert timing with wall-clock sleeps.
- A platform skip is acceptable only for a genuinely non-current platform path and must state the
  residual Stage 7 risk. The two current-macOS Seatbelt selectors cannot be skipped.
- Live-marked tests remain deselected and are recorded as intentionally outside S7P-08. Their
  absence cannot fail this offline gate, and their historical results cannot be presented as a
  current run.
- If a migration or persisted fixture exposes a compatibility defect, add old-version read and
  backup/restore evidence; do not rewrite the fixture or database to hide it.

## 11. Validation

```bash
uv sync
uv run pytest --collect-only -q -m 'not live'
uv run pytest -q tests/acceptance/test_s7p08_single_agent_matrix.py
uv run python evals/code-agent-mini/eval.py self-check
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests evals/code-agent-mini
uv run morrow --help
uv run morrow run --help
git diff --check
```

Also run Lanes A–G and the two Seatbelt selectors exactly as defined above. If `uv` cannot use its
cache, use the already-synchronized `.venv/bin/` equivalents and record the restriction and exact
fallback. Do not claim any command passed unless it ran to completion in the recorded environment.

## 12. Completion and integration gate

Subplan 89 is complete only when:

- all 18 ledger cells pass with actual current selectors;
- every high-risk cell has executed failure and recovery evidence;
- all reproducible snapshot classes in the S7P-08 checklist pass;
- Lanes A–G, the full offline suite and all quality gates pass;
- both real macOS Seatbelt host selectors pass without skip;
- every skip and residual risk is explicit, and no P0 path is skipped;
- the acceptance record contains exact current evidence and no live-result implication;
- the final diff is reviewed, coherent, committed and clean.

After verification, fast-forward merge into `main`, verify the topic tip is contained by `main`,
and retire the clean topic branch/worktree. Push only when the remote is authorized; otherwise
record the local-only upstream state as a blocker rather than claiming remote completion. S7P-09
requires a separate explicit user request.
