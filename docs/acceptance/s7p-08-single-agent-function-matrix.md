# S7P-08 Single-Agent Basic Function Matrix Evidence

> Verdict: **PASS**
> Date: 2026-08-27 (Asia/Shanghai)
> Branch baseline: `main@d84ac0d`
> Evidence commit: `8d8c6dd` plus this closeout record
> Scope: deterministic offline Direct Agent regression only

## 1. Verdict and boundaries

All 18 frozen single-Agent capability cells passed with current collected behavior selectors. Every
high-risk cell executed an explicit failure path and a legal recovery/next-run path. No product
regression was reproduced, so S7P-08 changed no production code.

The authoritative machine-readable mapping is
`tests/acceptance/s7p08_single_agent_matrix.json` (SHA-256
`8b8fe9eb416716dee76a1a0a340ac5405a1aaac2c8d099f2ed216092f91e75af`). Its
contract test rejects unknown selectors, duplicate cells or selectors, empty positive evidence,
missing high-risk failure/recovery evidence, absent acceptance sources, unexplained host conditions
and incomplete snapshot-class coverage.

S7P-09 real-model/Pi comparison did not run. No live Provider/model/MCP/network/credential test,
Workflow/multi-Agent feature, dependency, runtime-policy default or public event change is part of
this result. Validation remains scoped telemetry; model-owned `stop` was not replaced by a Runtime
completion gate.

## 2. Exact capability-to-selector mapping

The following selectors are the exact ledger entries executed by the focused matrix. Parameterized
selectors expand to their currently collected node IDs.

| Cell | Positive selectors | Failure selectors | Recovery selectors |
|---|---|---|---|
| `chat` | `tests/test_conversation_and_loop.py::test_plain_chat_uses_agent_loop_with_single_history_writer`<br>`tests/test_context_runtime.py::test_ten_turns_preserve_ordered_full_history_and_stream_deltas` | `tests/test_context_runtime.py::test_provider_exception_always_completes_error_without_assistant_history` | `tests/test_conversation_and_loop.py::test_cancelled_turn_records_cancelled_terminal_and_next_turn_succeeds` |
| `workspace_discovery` | `tests/test_local_tool_factories.py::test_fake_provider_can_list_search_read_continue_and_explain`<br>`tests/test_local_files.py::test_read_file_reports_revision_newline_and_actionable_continuation` | `tests/test_local_search.py::test_search_blocks_protected_symlink_targets_and_explicit_git_root` | Not required (medium risk) |
| `structured_mutation` | `tests/test_local_mutation.py::test_patch_preserves_bom_newline_and_mode_and_returns_actual_diff`<br>`tests/test_s7p04_workspace_change_lifecycle.py::test_delete_move_and_rename_publish_only_regular_files_without_overwrite` | `tests/test_local_mutation.py::test_stale_revision_conflicts_without_overwriting_external_change`<br>`tests/test_s7p04_workspace_change_lifecycle.py::test_move_no_replace_fails_closed_on_destination_race_and_unsupported_capability` | `tests/test_s7p04_workspace_change_lifecycle.py::test_sandbox_promotion_pair_and_delete_apply_and_preserve_changes` |
| `shell` | `tests/test_process.py::test_host_process_returns_structured_nonzero_and_shell_results` | `tests/test_process.py::test_timeout_and_cancellation_clean_up_host_process`<br>`tests/test_process.py::test_process_preflight_classifies_forbidden_operations_before_approval` | `tests/test_process.py::test_fake_provider_can_recover_after_host_command_failure` |
| `sandbox_approval` | `tests/test_sandbox.py::test_sandbox_text_change_requires_approval_and_promotes_safely`<br>`tests/test_sandbox.py::test_production_auto_sandbox_registers_only_native_tools_and_keeps_real_workspace_clean`<br>`tests/test_sandbox.py::test_macos_native_sandbox_blocks_real_workspace_home_and_network` | `tests/test_capability_policy.py::test_auto_sandboxed_process_fails_closed_until_backend_is_proven`<br>`tests/test_local_files.py::test_protected_symlink_target_and_git_metadata_are_metadata_only` | `tests/test_sandbox.py::test_sandbox_promotion_conflict_preserves_external_change_and_run_scope` |
| `git_read_only` | `tests/test_git.py::test_git_status_and_diff_are_bounded_read_only_and_protect_content` | `tests/test_git.py::test_git_adapter_timeout_is_typed` | `tests/test_git.py::test_git_non_repository_is_a_normal_bounded_result` |
| `validation_truth` | `tests/test_s7p05_completion_truth.py::test_recognized_process_result_projects_only_a_validation_fact` | `tests/test_s7p05_completion_truth.py::test_validator_recognition_is_strict_and_shell_control_flow_fails_closed`<br>`tests/test_s7p05_completion_truth.py::test_failed_validation_telemetry_does_not_reject_model_stop` | `tests/test_s7p05_completion_truth.py::test_latest_scoped_validation_fact_replaces_only_its_own_requirement` |
| `provider_model` | `tests/test_agent_run_preparation.py::test_prepared_submit_freezes_provider_evidence_in_snapshot`<br>`tests/test_provider_control.py::test_model_capability_override_is_persisted_and_narrows_the_next_run_snapshot` | `tests/test_agent_run_preparation.py::test_rehydrate_unavailable_frozen_credential_has_no_fallback`<br>`tests/test_agent_guardrails.py::test_zero_progress_transient_retries_but_auth_never_retries` | `tests/test_agent_run_preparation.py::test_rehydrate_rebuilds_exact_provider_from_frozen_evidence` |
| `session_task` | `tests/test_stage4_context_fork.py::test_fork_restored_through_production_bootstrap_can_complete_own_turn`<br>`tests/test_stage4_task_outcome.py::test_multi_turn_correction_and_acceptance_survive_restart` | `tests/test_stage4_context_fork.py::test_checkpoint_requires_a_closed_boundary_and_fork_has_no_parent_copy` | `tests/test_stage4_recovery_crash.py::test_application_recovery_updates_health_and_resume_run_atomically`<br>`tests/test_stage4_application_api.py::test_session_command_receipt_replay_conflict_and_same_transaction_event` |
| `persistence_artifact` | `tests/test_stage4_artifacts.py::test_artifact_publication_is_atomic_private_and_restart_readable`<br>`tests/test_stage4_backup.py::test_backup_contains_database_manifest_artifacts_and_detects_changed_restore_bytes`<br>`tests/test_operational_store.py::test_ordered_checksummed_migration_rolls_back_a_failed_step` | `tests/test_operational_store.py::test_valid_header_corruption_is_diagnose_repair_and_left_intact`<br>`tests/test_stage4_backup.py::test_backup_creation_fails_closed_when_an_available_artifact_is_missing` | `tests/test_stage4_artifacts.py::test_each_publication_fault_point_leaves_a_recoverable_truthful_state`<br>`tests/test_stage4_recovery_crash.py::test_file_reconciliation_after_restart` |
| `context` | `tests/test_stage4_context_fork.py::test_context_compression_checkpoint_restores_and_projects_after_restart`<br>`tests/test_s7p06_pi_parity.py::test_split_turn_compaction_keeps_user_anchor_and_tool_pairs_together` | `tests/test_conversation_and_loop.py::test_context_overflow_records_terminal_and_keeps_only_user` | `tests/test_s7p06_pi_parity.py::test_context_overflow_has_one_compaction_recovery_path` |
| `profile_memory` | `tests/test_stage5_memory_agent_run.py::test_new_turn_freezes_selection_and_effective_preferences_atomically`<br>`tests/test_preference_context.py::test_new_agent_run_reloads_yaml_sources_and_keeps_same_run_frozen` | `tests/test_stage5_memory_agent_run.py::test_selection_admission_rolls_back_before_user_message_is_published`<br>`tests/test_stage5_configuration_promotion.py::test_prepared_configuration_rejects_later_same_content_revision_as_drift` | `tests/test_stage5_memory_agent_run.py::test_recovery_agent_run_reuses_selection_after_memory_revision_changes`<br>`tests/test_stage5_configuration_promotion.py::test_configuration_saga_replays_after_sqlite_finalize_crash` |
| `skill` | `tests/test_skill_selection.py::test_admission_persists_skill_rows_and_injects_low_authority_context`<br>`tests/test_skill_scripts.py::test_skill_script_requires_sandbox_and_exact_frozen_package` | `tests/test_skill_scripts.py::test_skill_script_rejects_root_escape_symlink_and_input_mutation`<br>`tests/test_skill_selection.py::test_missing_dependencies_and_unknown_explicit_skill_are_omitted` | `tests/test_skill_lifecycle.py::test_recovery_finishes_after_yaml_publish_without_revision_drift` |
| `mcp` | `tests/test_mcp_control.py::test_fake_stdio_discovery_and_catalog_are_offline_and_deterministic`<br>`tests/spikes/test_mcp_stdio_spike.py::test_fake_stdio_connect_list_call_close` | `tests/test_mcp_control.py::test_invalid_tool_schema_isolated_from_valid_catalog`<br>`tests/test_mcp_runtime.py::test_timeout_is_terminal_for_one_server_and_never_retried` | `tests/test_mcp_runtime.py::test_one_degraded_server_does_not_poison_another`<br>`tests/test_mcp_runtime.py::test_rehydrate_rejects_launch_fact_drift` |
| `permission_grant` | `tests/test_stage4_permissions.py::test_permission_snapshot_must_match_the_frozen_agent_run_and_blocks_late_grants`<br>`tests/test_stage4_execution.py::test_approval_resolve_consume_and_expiry_are_deterministic` | `tests/test_stage4_permissions.py::test_grant_evidence_rejects_cross_scope_and_revoked_reuse` | `tests/test_stage4_recovery_crash.py::test_recovery_resume_creates_an_ungranted_agent_run` |
| `runtime_control` | `tests/test_runtime_control.py::test_midstream_steering_resubmits_as_durable_turn_and_consumes_atomically`<br>`tests/test_runtime_control.py::test_active_follow_up_drains_after_normal_stop_in_fifo_order` | `tests/test_agent_limits.py::test_tool_timeout_becomes_one_bounded_result_and_loop_continues`<br>`tests/test_runtime_control.py::test_pre_stop_steering_rejects_candidate_without_publishing_it` | `tests/test_runtime_control.py::test_cancel_preserves_pending_controls_for_the_next_user_run`<br>`tests/test_runtime_control.py::test_error_replay_uses_receipt_terminal_when_metrics_are_missing` |
| `security` | `tests/test_stage2_product_acceptance.py::test_real_terminal_product_flow_is_ordered_recoverable_and_secret_safe`<br>`tests/test_headless_run.py::test_run_emits_only_versioned_jsonl_and_terminal_safe_record` | `tests/test_conversation_and_loop.py::test_public_diagnostic_contract_rejects_secret_material_and_control_lines`<br>`tests/test_stage4_application_api.py::test_application_event_payload_rejects_secret_material` | `tests/test_stage6_backup.py::test_stage6_v2_round_trip_without_secret_authorities` |
| `entrypoint_consistency` | `tests/acceptance/test_s7p08_single_agent_matrix.py::test_interactive_and_headless_paths_share_run_preparation_and_terminal_truth`<br>`tests/test_headless_run.py::test_run_uses_the_real_session_builder_with_a_scripted_provider`<br>`tests/test_conversation_and_loop.py::test_runtime_run_turn_is_thin_delegate_of_loop_run_task` | `tests/test_headless_run.py::test_run_requires_explicit_prompt_and_workspace_without_reading_stdin` | `tests/test_terminal.py::test_clean_primary_eof_exits_once` |

Focused behavior execution expanded these selectors to `94 passed in 12.52s`. The ledger contract
and entrypoint slice passed `3 passed in 1.81s` after the final ledger update.

## 3. AgentRun reproduction evidence

The ledger requires and maps all eight snapshot classes:

| Snapshot class | Frozen/referenced evidence | Current proof |
|---|---|---|
| Provider/Model | `provider_runtime`, `model`, credential reference, configuration revision/digest | prepared submit, exact rehydrate, unavailable frozen credential failure |
| Skill | selection/context IDs and digests, selected/omitted counts, catalog/binding digests | Skill admission and journal-only projection rebuild |
| MCP | reference-only launch/tool snapshot IDs plus versioned v16 rows | fake stdio catalog, drift rejection, degraded-server isolation |
| Preference | frozen statements, source revisions/scopes and projection digest | atomic admission, same-run freeze, next-run refresh |
| Knowledge | memory selection ID/digest/revision and versioned selected record revisions | atomic selection, revision-change recovery reuse, rollback on admission failure |
| ToolSet | canonical provider schema digest and count | prepared snapshot, drift rejection and cross-entrypoint equality |
| Permission | permission-profile digest plus run-bound permission/grant evidence | late-grant rejection, revoke/expiry and ungranted recovery run |
| Context policy | frozen `RunPolicy` plus digest and prompt/project-instruction digests | exact rehydrate, compaction/overflow recovery and cross-entrypoint equality |

The new cross-entrypoint production-composition test dispatches the same prompt through terminal
consumption and headless JSONL consumption. Both use the same AgentLoop class, freeze equal
Provider/Model, RunPolicy, ToolSet, Permission, Preference, Skill catalog/binding and prompt/context
evidence, and settle with `stop` in durable terminal metrics. Mutable live Provider objects,
credentials, reasoning, tool payloads and output are not put in the snapshot.

## 4. Published acceptance reference audit

- Current acceptance documents contained 33 explicit `path::test` references; all collected
  successfully, expanding to 35 current node IDs where parameterized.
- The only referenced test file absent from the current tree was
  `tests/test_structured_and_handoff.py`. Its current behavior is split between
  `tests/test_structured.py` and `tests/test_preferences_and_orchestration.py`; the obsolete file
  was not counted as executed evidence.
- The historical Runtime completion-checker selector is superseded by
  `tests/test_s7p05_completion_truth.py::test_failed_validation_telemetry_does_not_reject_model_stop`
  after Subplan 87. S7P-08 did not revive verifier-owned completion.

## 5. Regression lanes

| Gate | Exact result |
|---|---|
| Lane A — Stage 1 | `157 passed, 1 deselected in 5.21s` |
| Lane B — Stage 2 | `132 passed in 2.06s` |
| Lane C — Stage 3 | `137 passed in 6.23s` |
| Lane D — Stage 4 | `243 passed in 30.37s` |
| Lane E — Stage 5 | `260 passed, 1 deselected in 22.07s` |
| Lane F — Stage 6 | `157 passed in 13.80s` |
| Lane G — S7P-00–07 | `198 passed in 25.22s` |

Overlaps are intentional. The deselections are live-marked tests and do not imply a current live
result.

## 6. Real macOS Seatbelt gate

The host reported `Darwin`; `CODEX_SANDBOX` was absent rather than unset or spoofed. The exact
current-platform gate ran:

```text
tests/test_sandbox.py::test_production_auto_sandbox_registers_only_native_tools_and_keeps_real_workspace_clean
tests/test_sandbox.py::test_macos_native_sandbox_blocks_real_workspace_home_and_network
```

Result: `2 passed in 0.75s`, zero skip. This is current-host macOS evidence only; Linux Bubblewrap
remains a fail-closed unit-tested path, not real Linux host evidence.

## 7. Final gates

| Command | Result |
|---|---|
| `uv sync` | resolved 65 packages; checked 59 packages |
| `uv run pytest --collect-only -q -m 'not live'` | `1300/1302` collected; 2 live deselected |
| `uv run pytest -q tests/acceptance/test_s7p08_single_agent_matrix.py` | `3 passed in 1.81s` |
| focused ledger selectors | `94 passed in 12.52s` |
| `uv run python evals/code-agent-mini/eval.py self-check` | 10/10 tasks passed |
| `uv run pytest -m 'not live'` | `1300 passed, 2 deselected in 91.64s` |
| `uv run ruff format --check .` | 491 files already formatted |
| `uv run ruff check .` | all checks passed |
| `uv run python -m compileall -q src tests evals/code-agent-mini` | passed with no output |
| `uv run morrow --help` | passed |
| `uv run morrow run --help` | passed |
| `git diff --check` | passed |

There were no offline skips and no P0 path was skipped. Two live tests were intentionally
deselected by the offline gate. No live result is claimed.

## 8. Defect and remediation log

The first ledger contract run rejected seven non-existent guessed selectors. This was a test-data
defect, not a product defect; each reference was replaced by an exact currently collected selector.
The coverage audit then identified one real evidence gap: no single test compared interactive and
headless frozen run evidence and terminal meaning. The new cross-entrypoint test closed that gap and
passed through production composition. No runtime or application source change was required.

Final S7P-08 verdict: **PASS**. The Direct Agent single-Agent baseline is ready for the separately
authorized S7P-09 evaluation decision; S7P-09 is not started by this record.
