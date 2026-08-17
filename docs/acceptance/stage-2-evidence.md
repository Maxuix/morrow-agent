# Stage 2 Agent Core Acceptance Evidence

> Historical evidence: Handoff results describe the accepted Stage 2 tree at that time;
> the transitional feature was removed afterward and is not a current capability.

> Evidence date: 2026-08-17  
> Scope authority: `docs/roadmap/stage-2-agent-core.md` sections 15–17 and the approved proposal sections 21–22  
> Current status: all mandatory offline, package, product, boundary, and quality gates passed; optional Live was not run because no explicit credential was available.

## How to reproduce

All commands run from the repository root with the repository `NetworkGuard` enabled by
`tests/conftest.py` unless a row is explicitly marked Live or manual.

```sh
uv run pytest -m 'not live'
uv run pytest --collect-only -q
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run pytest tests/test_stage_boundary.py
git diff --check
```

The final-tree offline suite passed **300 tests** with the one explicit Live test
deselected. Strict collection found **301 tests**, including that Live marker. Every row
whose direct evidence is named below was exercised by this final suite; focused run
counts remain as diagnostic provenance.

## Protocol and OpenAI-compatible Adapter

S2.20.2 acceptance run:
`uv run pytest -q -m 'not live' tests/test_provider.py tests/test_core_contracts.py
tests/test_agent_guardrails.py::test_tool_fragment_progress_prevents_transient_retry
tests/test_agent_guardrails.py::test_zero_progress_transient_retries_but_auth_never_retries
tests/test_agent_guardrails.py::test_cancellation_after_text_progress_discards_partial_assistant_and_recovers`
→ **59 passed, 1 Live test deselected**. Missing call ID and invalid function-name
fake-chunk cases were added before this green run. Mandatory protocol acceptance is green
and was included in the 300-test final-tree suite.

| Requirement | Owner | Direct automated evidence | Observed result / remaining check |
|---|---|---|---|
| Tool-bearing request; text-only request omits tools and tool choice | Slice 1 | `test_adapter_request_whitelist_sends_tools_and_choice_only_when_present`; `test_adapter_omits_tools_and_choice_for_text_only_requests` | passed |
| Text, pure calls, and mixed content plus calls | Slice 1 | `test_adapter_accepts_only_explicit_stop_and_isolates_reasoning`; `test_adapter_assembles_split_pure_tool_call_stream`; `test_adapter_assembles_mixed_content_and_normalizes_stop_with_calls` | passed |
| One call and interleaved multiple calls preserve vendor ordering | Slice 1 | `test_adapter_assembles_split_pure_tool_call_stream`; `test_adapter_sorts_interleaved_calls_by_vendor_index` | passed |
| Usage-only chunks are ignored; exactly one logical choice is accepted | Slice 1 | `test_adapter_ignores_usage_only_chunks`; parameter cases in `test_adapter_rejects_malformed_tool_streams` | passed |
| Missing/conflicting IDs, duplicate IDs, invalid index/type/name/arguments are rejected | Slice 1 | fake-chunk parameter cases in `test_adapter_rejects_malformed_tool_streams`; `test_adapter_rejects_non_string_fragment_arguments`; Core validators in `test_core_contracts.py` | passed |
| Missing/conflicting/abnormal finish is rejected; explicit stop is accepted | Slices 1/3 | `test_adapter_rejects_missing_finish_signal`; `test_accumulator_rejects_conflicting_finish_and_reports_progress`; `test_adapter_rejects_non_normal_finish_as_invalid_response`; `test_adapter_normalizes_abnormal_finish_without_assembled_message` | passed |
| `stop` with calls normalizes to tool-calls completion | Slice 1 | `test_adapter_assembles_mixed_content_and_normalizes_stop_with_calls` | passed |
| Raw argument strings and `content: null` survive assembly | Slice 1 | `test_accumulator_preserves_argument_string_fidelity`; `test_adapter_assembles_split_pure_tool_call_stream`; `test_tool_call_arguments_stay_untouched_string` | passed |
| Explicit whitelist serializer round-trips an assembled Assistant | Slice 1 | `test_serialize_after_assemble_round_trip_is_deterministic`; `test_protocol_variants_reject_extras_and_are_immutable` | passed |
| Reasoning, SDK objects, and internal metadata do not enter public content/wire | Slice 1 | `test_adapter_accepts_only_explicit_stop_and_isolates_reasoning`; `test_adapter_reasoning_only_stream_has_no_visible_delta`; whitelist assertions above | passed |
| Transient errors distinguish zero progress from text/tool-fragment progress | Slice 3 | `test_zero_progress_transient_retries_but_auth_never_retries`; `test_tool_fragment_progress_prevents_transient_retry`; `test_cancellation_after_text_progress_discards_partial_assistant_and_recovers` | passed |
| No fixture claims support for an unimplemented native Provider | Boundary | Provider fixture inventory in `tests/test_provider.py`; `test_second_adapter_is_dynamic_and_does_not_change_core` | passed |

## ConversationLog and Context projections

S2.20.3 acceptance run:
`uv run pytest -q tests/test_conversation_and_loop.py tests/test_context_projections.py
tests/test_context_runtime.py` plus the focused structured/Handoff/config/restart projection
cases listed below → **53 passed**. Mandatory Conversation/Context acceptance is green
and was included in the 300-test final-tree suite.

| Requirement | Owner | Direct automated evidence | Observed result / remaining check |
|---|---|---|---|
| Single and multiple ToolCycles retain ordered call/result pairs | Slices 1/2 | `test_public_turn_views_derive_multiple_closed_cycles_and_final_assistant`; `test_multi_call_batch_results_arrive_in_original_order` | passed |
| Unknown, duplicate, missing, orphan, reused, and out-of-order results are rejected | Slice 2 | `test_log_enforces_ordered_results_and_no_terminal_while_cycle_open`; `test_log_rejects_assistant_crossing_open_cycle_and_missing_final`; parameter cases in `test_snapshot_strict_validation_rejects_malformed_record_order` | passed |
| An open Cycle excludes a new User, Assistant, or terminal record | Slice 2 | `test_log_enforces_single_active_turn_and_single_opening_user`; `test_log_enforces_ordered_results_and_no_terminal_while_cycle_open`; `test_log_rejects_assistant_crossing_open_cycle_and_missing_final` | passed |
| Success/cancel/failure/budget terminals are legal only after Cycle closure | Slices 1–3 | `test_cancelled_terminal_preserves_exact_runtime_interrupted_call_ids`; `test_unexpected_post_admission_exception_closes_with_internal`; `test_total_tool_call_limit_accepts_batch_and_closes_all_with_budget_envelopes`; E2E closure tests | passed |
| Terminal records never enter Provider payloads | Slices 1/2 | `test_two_tool_step_story_completes_with_legal_history`; `test_context_request_pack_and_source_snapshot_are_immutable_and_build_is_pure` | passed |
| Snapshot and public views are deeply immutable | Slice 2 | `test_open_cycle_view_is_immutable_and_reports_unresolved_ids`; `test_log_snapshot_and_messages_view_are_deeply_read_only`; `test_context_request_pack_and_source_snapshot_are_immutable_and_build_is_pure` | passed |
| Reset and `/new` clear process-local Log without changing persisted Handoff; restart restores no Log | Slice 2 | `test_session_reset_clears_log_and_session_state_without_touching_handoff`; `test_new_then_continue_clears_tool_history_and_loads_only_persisted_handoff`; `test_session_construction_and_restart_do_not_restore_conversation_log` | passed |
| Chat, Structured, and Handoff fallback projections are separated | Slice 2 | `test_explicit_projections_keep_tool_data_out_of_structured_and_fallback_views`; `test_structured_and_handoff_fallback_never_consume_tool_envelopes`; `test_config_extraction_with_tool_history_receives_only_structured_projection` | passed |
| All results in one old Cycle clear atomically | Slice 2 | `test_multi_result_cycle_is_cleared_atomically_without_touching_log` | passed |
| Hard trimming is oldest-first at whole-turn and whole-Cycle boundaries | Slice 2 | `test_hard_trim_drops_oldest_whole_turn_and_counts_source_records`; `test_hard_trim_drops_oldest_closed_cycle_but_preserves_current_user`; legacy boundary cases in `test_context_runtime.py` | passed |
| Current User, open Cycle, state, and tool definitions are protected and counted | Slices 2/3 | `test_hard_trim_drops_oldest_closed_cycle_but_preserves_current_user`; `test_protected_context_overflow_is_typed_context_budget_failure`; `test_canonical_estimator_counts_tool_schema_and_rejects_wire_oversize` | passed |
| Protected-set overflow fails before Provider dispatch | Slice 2 | `test_protected_context_overflow_is_typed_context_budget_failure`; `test_oversized_current_input_is_rejected_before_model_call`; `test_context_overflow_records_terminal_and_keeps_only_user` | passed |
| Final serialized request is rechecked for size and legal pairing | Slice 2 | `test_canonical_estimator_counts_tool_schema_and_rejects_wire_oversize`; `test_continuation_wire_is_rechecked_and_rejected_before_second_provider_dispatch` | passed |
| Every continuation rebuilds from the latest ConversationSnapshot | Review remediation | `test_each_continuation_rebuilds_context_from_the_latest_snapshot` | passed S2.20.11 |
| Building/clearing/trimming mutates no Log, Session, or Handoff and calls no summary model | Slice 2 | `test_context_request_pack_and_source_snapshot_are_immutable_and_build_is_pure`; `test_multi_result_cycle_is_cleared_atomically_without_touching_log`; `test_handoff_rejects_normalized_duplicate_decisions`; capability scans in `test_stage_boundary.py` | passed |

## ToolExecutor

S2.20.4 acceptance run:
`uv run pytest -q tests/test_tools.py tests/test_agent_limits.py
tests/test_agent_tool_loop.py` → **38 passed**. This run added direct tests for an
unrepresentable truncation envelope (`output_failed`) and exact equal per-call Cycle
allocation. Mandatory ToolExecutor acceptance is green and was included in the 300-test
final-tree suite.

| Requirement | Owner | Direct automated evidence | Observed result / remaining check |
|---|---|---|---|
| Pydantic Schema is generated and JSON/object/type/range/extra validation is strict | Slices 1/3 | `test_tool_definitions_are_generated_from_argument_models`; parameter cases in `test_invalid_arguments_are_rejected_before_handler`; `test_calculate_rejects_bounds_non_finite_and_strict_type_errors`; `test_validation_details_are_stable_and_capped_by_policy` | passed |
| `lookup_record` and `calculate` are deterministic and emit standard JSON only | Slice 1 / review remediation | `test_lookup_record_returns_injected_value_in_compact_envelope`; `test_lookup_record_not_found_is_deterministic`; `test_calculate_is_ordered_left_to_right`; `test_calculate_rejects_non_finite_results_from_finite_inputs` | passed S2.20.11 |
| Unknown tool, not-found, divide-by-zero, and malformed input return canonical errors | Slice 1 | `test_unknown_tool_is_bounded_without_leaking_call_details`; `test_lookup_record_not_found_is_deterministic`; `test_calculate_division_by_zero_is_deterministic`; validation tests above | passed |
| Timeout, execution failure, internal failure, and cancellation have distinct closure behavior | Slices 1/3 | `test_tool_timeout_becomes_one_bounded_result_and_loop_continues`; `test_handler_failure_is_bounded_and_never_auto_retried`; `test_unexpected_post_admission_exception_closes_with_internal`; `test_cancelled_error_is_reraised_to_the_loop` | passed |
| Validation and failure messages are bounded and omit secrets/tracebacks | Slices 1/3 | `test_validation_details_are_stable_and_capped_by_policy`; `test_error_envelope_is_compact_sorted_and_bounded`; `test_handler_failure_is_bounded_and_never_auto_retried`; `test_unknown_tool_is_bounded_without_leaking_call_details` | passed |
| Large success is valid truncated JSON with `original_chars`; too-small output budget fails safely | Slice 3 | `test_large_success_is_truncated_to_valid_json_with_original_size`; `test_success_that_cannot_fit_truncation_envelope_returns_output_failure` | passed |
| A Cycle receives equal canonical-wire-aware per-call allocation and minimum closure space is pre-admitted | Slice 3 / review remediation | `test_multi_call_cycle_allocates_the_same_bounded_result_limit_to_each_call`; `test_quote_heavy_result_stays_within_the_canonical_cycle_wire_budget`; `test_oversized_tool_call_cycle_is_rejected_before_history_admission`; `test_per_cycle_call_limit_rejects_assistant_before_history_admission` | passed S2.20.11 |
| Runtime never retries a tool | Slice 1 | `test_handler_failure_is_bounded_and_never_auto_retried`; `test_tool_failure_envelope_continues_loop_without_retry` | passed |
| Every accepted call gets exactly one bounded real or synthetic result | Slices 1–3 / review remediation | `test_multi_call_batch_results_arrive_in_original_order`; `test_cancelled_synthetic_result_respects_the_assigned_result_limit`; `test_total_tool_call_limit_accepts_batch_and_closes_all_with_budget_envelopes`; all cancellation/internal closure tests | passed S2.20.11 |

## AgentLoop, time, lifecycle, and terminal

S2.20.5 acceptance run:
`uv run pytest -q tests/test_agent_guardrails.py tests/test_agent_limits.py
tests/test_agent_tool_loop.py tests/test_stage2_e2e.py tests/test_conversation_and_loop.py
tests/test_terminal.py::test_terminal_segments_mixed_text_tool_and_final_text_without_replay_or_payload_leak`
→ **54 passed**. The run added an exact remaining-run-time cap assertion and configured
the longest AB×3 loop case at the six-round hard limit, proving loop detection wins at
Cycle completion before the next hard-cap check. Mandatory loop/time acceptance is green
and was included in the 300-test final-tree suite.

| Requirement | Owner | Direct automated evidence | Observed result / remaining check |
|---|---|---|---|
| One goal completes at least two dependent tool rounds before a final answer | Slice 1 | `test_two_tool_step_story_completes_with_legal_history` | passed |
| Multiple calls in one response execute in original order | Slice 1 | `test_multi_call_batch_results_arrive_in_original_order`; `test_two_tool_step_story_completes_with_legal_history` | passed |
| Model/tool-round/per-Cycle/total-call/run/context/result/Cycle limits stop safely | Slice 3 / review remediation | all tests in `test_agent_limits.py`, including `test_run_deadline_cancels_a_hanging_provider_stream`; `test_protected_context_overflow_is_typed_context_budget_failure`; `test_large_success_is_truncated_to_valid_json_with_original_size`; loop tests in `test_agent_guardrails.py` | passed S2.20.11 |
| Each serial tool timeout is bounded by remaining run time | Slice 3 | `test_effective_tool_timeout_is_capped_by_remaining_run_time`; `test_tool_timeout_becomes_one_bounded_result_and_loop_continues`; `test_deadline_between_tools_preserves_result_and_budget_closes_unstarted_call` | passed |
| Model retry occurs only for transient zero-progress failure | Slice 3 | `test_zero_progress_transient_retries_but_auth_never_retries`; `test_network_error_retries_once_before_visible_text` | passed |
| Text or tool-fragment progress prevents retry and partial streams do not enter history | Slices 1/3 | `test_tool_fragment_progress_prevents_transient_retry`; `test_cancellation_after_text_progress_discards_partial_assistant_and_recovers`; `test_provider_exception_always_completes_error_without_assistant_history` | passed |
| Cancellation at every commit boundary closes or discards the correct state | Slice 3 | all six cancellation tests in `test_agent_guardrails.py`; cancellation tests in `test_agent_tool_loop.py` and `test_stage2_e2e.py` | passed |
| Cancelled/budget synthetic results close all accepted unresolved calls and skipped statuses map correctly | Slice 3 | `test_cancellation_before_first_tool_marks_every_call_skipped`; parameter cases in `test_cancellation_after_a_tool_result_preserves_it_and_closes_remaining`; `test_total_tool_call_limit_accepts_batch_and_closes_all_with_budget_envelopes`; `test_deadline_between_tools_preserves_result_and_budget_closes_unstarted_call` | passed |
| Healthy user turn succeeds after cancellation/error/tool failure | Slices 1/3 | `test_cancelled_turn_records_cancelled_terminal_and_next_turn_succeeds`; `test_integrated_cancellation_mid_tool_batch_closes_and_recovers`; `test_integrated_internal_failure_mid_tool_batch_closes_and_recovers`; `test_tool_failure_envelope_continues_loop_without_retry` | passed |
| A×3 and AB×3 stop; changed arguments/results and near matches do not | Slice 3 | `test_repeated_single_cycle_stops_at_configured_repeat_limit`; `test_repeated_two_cycle_pattern_detects_but_near_match_does_not`; `test_changing_results_breaks_loop_equality_and_tool_events_are_secret_safe` | passed |
| Exactly one public start/completion and at most one fatal error | Slices 1/3 | `test_public_event_lifecycle_and_unknown_fields_are_tolerated`; `test_fatal_public_event_contract_is_exact_and_matches_completion`; lifecycle assertions in `test_stage2_e2e.py` | passed |
| Fatal error and completion share exact `stop_code`; obsolete public `code` is absent; mappings follow recovery contract | Slice 3 | `test_fatal_public_event_contract_is_exact_and_matches_completion`; error-path tests in `test_context_runtime.py` and `test_agent_limits.py` | passed |
| Finish reason and Assistant shape must agree before admission | Review remediation | parameter cases in `test_finish_reason_must_match_the_assistant_message_shape` | passed S2.20.11 |
| Combined precedence is deterministic and longest loop pattern precedes hard round cap | Slice 3 | `test_model_attempt_limit_precedes_tool_round_limit_when_both_are_exhausted`; six-round-cap `test_repeated_two_cycle_pattern_detects_but_near_match_does_not`; policy combination cases in `test_policy_rejects_invalid_values_and_combinations` | passed |
| Mixed text, tool status, final text are segmented, shown once, and hide payload/call IDs | Slice 3 | `test_terminal_segments_mixed_text_tool_and_final_text_without_replay_or_payload_leak`; `test_mixed_content_intermediate_text_persists_but_only_final_completes`; secret-safe tool event assertion in `test_changing_results_breaks_loop_equality_and_tool_events_are_secret_safe` | passed |

## Stage 1 regression surface

S2.20.6 acceptance run:
`uv run pytest -q -m 'not live' tests/test_cli_commands.py tests/test_context_runtime.py
tests/test_core_contracts.py tests/test_preferences_and_orchestration.py
tests/test_provider.py tests/test_state_and_workspace.py
tests/test_structured_and_handoff.py tests/test_terminal.py`
→ **186 passed, 1 Live test deselected**. The mandatory Stage 1 product surface is
green on the integrated Stage 2 tree and was included in the 300-test final-tree suite.

| Requirement | Direct automated evidence | Observed result / remaining check |
|---|---|---|
| Ten ordered chat turns, streaming deltas, empty/abnormal response, retry, Ctrl+C/EOF | `test_ten_turns_preserve_ordered_full_history_and_stream_deltas`; failure/retry/cancel tests in `test_context_runtime.py`; EOF/Ctrl+C tests in `test_terminal.py` | passed |
| Provider configure/test/show and credential precedence/rotation | onboarding/configuration tests in `test_provider.py`; CLI tests in `test_cli_commands.py` | passed |
| Natural-language configuration routing, repair, preview, confirmation, and fail-closed behavior | gate/extraction/orchestration tests in `test_preferences_and_orchestration.py` | passed |
| StructuredCompletion repair/deadline and Handoff model/fallback generation | all tests in `test_structured_and_handoff.py` | passed |
| `/continue`, `/handoff update`, `/new`, session switch, and `/exit` | transition/orchestration tests in `test_preferences_and_orchestration.py`; terminal exit tests in `test_terminal.py` | passed |
| Workspace identity/relink, revision/backup, locking, and degraded modes | all tests in `test_state_and_workspace.py`; degraded-mode tests in `test_preferences_and_orchestration.py` | passed |
| Dirty semantics after completed/cancelled/failed tool tasks | cancellation/E2E tests plus dirty transition and terminal tests | passed |
| Handoff/config model calls contain no tools or ToolMessage envelopes | `test_structured_and_handoff_fallback_never_consume_tool_envelopes`; `test_config_extraction_with_tool_history_receives_only_structured_projection` | passed |
| Stage 1 public errors use the approved visible text/`stop_code` contract without weakened classification | `test_fatal_public_event_contract_is_exact_and_matches_completion`; Stage 1 error tests in `test_context_runtime.py` | passed |

## Definition-of-done consolidation

The roadmap/proposal completion criteria are compound restatements of the rows above.
They are accepted only when every referenced row is green:

| Completion criterion | Evidence rows | Mandatory status |
|---|---|---|
| Two dependent tool steps reach a final answer | AgentLoop rows 1–2 | passed |
| Accepted calls/results are one-to-one; every exit closes the Cycle | Conversation rows 1–4; ToolExecutor row 9; AgentLoop rows 7–8 | passed |
| Partial/malformed streams never enter history; Provider wire is whitelisted | Protocol rows 4–10; AgentLoop row 6 | passed |
| Every specified tool failure is bounded and replayable to the model | ToolExecutor rows 1–8; AgentLoop row 9 | passed |
| Model/tool/time/context/output/loop limits stop with approved reasons | AgentLoop rows 3–4 and 10–13 | passed |
| Cancellation during model/tool activity permits a healthy next turn | AgentLoop rows 7–9 | passed |
| Context keeps recent legal history without mutating facts or summarizing | Conversation/Context rows 8–14 | passed |
| ConversationLog is process-local; Handoff/Structured paths exclude tool envelopes | Conversation/Context rows 7–8 and 14 | passed |
| Terminal segments are ordered, unique, distinguishable, and secret-safe | AgentLoop/terminal row 14 | passed |
| Stage 1 behavior and safety boundaries have no regression | Stage 1 table; boundary table below | passed |
| Default validation is offline; optional Live is reported truthfully | Final quality gates and Live status sections | passed |

## Product, package, and security evidence

S2.20.7 package run built
`/private/tmp/morrow-stage2-acceptance.71vYPt/dist/morrow_agent-0.1.0-py3-none-any.whl`
offline, then installed it with resolved dependencies into a fresh CPython 3.12 venv.
`import morrow`, `morrow --help`, `importlib.resources` policy discovery, and policy
loading (`max_tool_rounds=30`) passed. A first strictly offline dependency-resolution
attempt was inconclusive because the local uv cache lacked `keyring`; the subsequent
fresh-environment install resolved and installed 33 declared packages successfully.

Product acceptance run:
`uv run pytest -q tests/test_stage2_product_acceptance.py
tests/test_policy.py::test_missing_and_malformed_policy_fail_clearly
tests/test_stage_boundary.py tests/test_stage2_e2e.py
tests/test_agent_guardrails.py::test_cancellation_after_text_progress_discards_partial_assistant_and_recovers`
→ **17 passed** after correcting the Scripted Provider so mixed-content Assistant text
emits the same visible delta as the real Adapter. The actual `run_repl` path covered a
tool error followed by model recovery, healthy follow-up, Handoff update, `/new`, and
clean exit; cancellation during model/tool activity is covered by the two focused
integrated cases. Captured terminal/events/state/Handoff scans found none of the
credential/argument/result/traceback sentinels, and the Log was empty after `/new`.

| Gate | Automated/manual evidence | Status |
|---|---|---|
| Isolated wheel install, imports, CLI help, and bundled policy discovery | Fresh CPython 3.12 environment; `import morrow`; `morrow --help`; `importlib.resources` lookup | passed |
| Missing/malformed policy fails bootstrap clearly | `test_missing_and_malformed_policy_fail_clearly`; installed resource load | passed S2.20.7 |
| Default offline network guard and no persisted ConversationLog | `tests/conftest.py`; `test_plain_chat_turn_persists_no_state_document`; `test_session_construction_and_restart_do_not_restore_conversation_log` | passed S2.20.7 |
| Deterministic real terminal/product flow | `test_real_terminal_product_flow_is_ordered_recoverable_and_secret_safe`; integrated model/tool cancellation tests | passed S2.20.7 |
| Credentials, raw arguments/results, call IDs, tracebacks, reasoning, SDK metadata absent from public surfaces and persisted files | Product test captured event/terminal/state/Handoff sentinels plus focused tests cited above | passed |
| Optional real Provider function-calling smoke | Secret-safe environment presence check for `MORROW_OPENCODE_GO_API_KEY` | not run: credential absent on 2026-08-17; this is not a mandatory failure and no Live result is claimed |

## Stage 3/4/5 exclusions

| Excluded capability | Boundary evidence | Status |
|---|---|---|
| Stage 3 local project tools: filesystem writes, shell, Git, network/browser operations | `test_demo_tool_registry_names_are_exactly_lookup_record_and_calculate`; `test_demo_tools_leave_temporary_workspace_byte_identical`; `test_no_forbidden_tool_capability_is_registered_or_exposed` | passed |
| Stage 4 persistence/memory/summary/background work and ConversationLog recovery | `test_workspace_state_documents_stay_preferences_profile_handoff`; `test_state_store_api_has_no_conversation_or_summary_surface`; `test_plain_chat_turn_persists_no_state_document`; `test_session_construction_and_restart_do_not_restore_conversation_log` | passed |
| Stage 5 MCP, Skills, permission/approval system, third-party/native Provider tool fixtures | capability scans in `test_stage_boundary.py`; exact registry assertions; Provider fixture inventory | passed |
| Unsupported Adapter remains plain chat and exposes no tools | `test_unsupported_adapter_capability_preserves_plain_chat_without_tools`; `test_construction_stays_stage_1_compatible` | passed |

## Final quality-gate results

S2.20.11 review remediation added six regression cases covering a hanging Provider
stream, per-continuation context rebuilding, canonical outer-wire Cycle sizing,
bounded synthetic results, inconsistent finish/message pairs, and finite-input
calculator overflow. The focused runtime/tool suite passed **58 tests**. The package
gate was then rerun from a newly built wheel in a strict fail-fast shell using a fresh
uv-managed CPython 3.12.13 environment.

| Command/check | Final observed result |
|---|---|
| `uv run pytest -m 'not live' -q` | 300 passed, 1 Live test deselected, 1.65 s |
| `uv run pytest --collect-only -q` | 301 tests collected, including the opt-in Live marker, 0.17 s |
| `uv run ruff format --check .` | 70 files already formatted |
| `uv run ruff check .` | all checks passed |
| `uv run python -m compileall -q src tests` | passed with no output |
| Final offline wheel build | `morrow_agent-0.1.0-py3-none-any.whl`, 45 files |
| Fresh CPython 3.12.13 offline install | 33 declared packages installed from cache; success |
| Installed import/resource/policy smoke | `import morrow`; bundled TOML exists; policy loads; success |
| Installed CLI smoke | `morrow --help`; success |
| `uv run pytest -q tests/test_stage_boundary.py tests/test_stage2_product_acceptance.py` | 10 passed, 0.17 s |
| Production-source sentinel scan | no credential/argument/result/call-ID/traceback sentinel matches |
| `git diff --check` | passed with no output |

All mandatory Stage 2 gates are green. The only unrun check is the explicitly optional
real-Provider smoke; the secret-safe presence check found no compatible credential, so
no Live result is claimed.
