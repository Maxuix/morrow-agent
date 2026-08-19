"""Focused tests for Stage 4 Subplan 38 execution and approval contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from morrow.core.capabilities import ProcessIsolation
from morrow.core.domain import (
    AGENT_RUN_SNAPSHOT_MAX_BYTES,
    CONVERSATION_RECORD_MAX_BYTES,
    ERROR_DETAIL_MAX_BYTES,
    sha256_digest,
)
from morrow.core.execution import (
    APPLICATION_EVENT_MAX_BYTES,
    APPROVAL_RECORD_MAX_BYTES,
    DURABLE_PAYLOAD_BUDGETS,
    PREPARED_INTENT_MAX_BYTES,
    PRODUCTION_TOOL_NAMES,
    RECOVERY_REPORT_MAX_BYTES,
    STRUCTURED_TOOL_FACTS_MAX_BYTES,
    TASK_OUTCOME_MAX_BYTES,
    TOOL_CALL_ARGUMENTS_MAX_BYTES,
    TOOL_RESULT_ENVELOPE_MAX_BYTES,
    ApprovalDecisionError,
    ApprovalResolution,
    DurableApproval,
    DurableToolExecution,
    DurableToolFacts,
    EffectClass,
    ExecutionTransitionError,
    FileMutationEvidence,
    HandlerResultEnvelope,
    MissingCompletionPolicy,
    PreparedIntent,
    RecoveryClassification,
    StaleRowVersionError,
    ToolExecutionDisposition,
    ToolExecutionState,
    UnknownToolDeclarationError,
    approval_preview_digest,
    assert_handler_may_enter,
    consume_approval,
    intent_hash,
    missing_declarations,
    require_tool_call_arguments_budget,
    resolve_approval,
    tool_declaration,
    transition_execution,
)
from morrow.core.faults import (
    REQUIRED_FAULT_POINTS,
    FaultPoint,
    InjectedFault,
    NoOpFaultInjector,
    OnceFaultInjector,
)
from morrow.core.models import ToolEffect


def _digest(label: str = "x") -> str:
    return sha256_digest(label)


def _intent(**overrides) -> PreparedIntent:
    values = {
        "tool_name": "read_file",
        "call_id": "call1",
        "ordinal": 1,
        "arguments_digest": _digest("args"),
        "schema_digest": _digest("schema"),
        "permission_context_digest": _digest("perms"),
        "effect_class": EffectClass.BOUNDED_READ,
    }
    values.update(overrides)
    return PreparedIntent(**values)


def _execution(**overrides) -> DurableToolExecution:
    intent = overrides.pop("intent", None) or _intent()
    values = {
        "tool_execution_id": "tex_1",
        "workspace_id": "ws_1",
        "session_id": "ses_1",
        "task_run_id": "task_1",
        "turn_id": "turn_1",
        "agent_run_id": "arun_1",
        "call_id": intent.call_id,
        "ordinal": intent.ordinal,
        "tool_name": intent.tool_name,
        "intent": intent,
    }
    values.update(overrides)
    return DurableToolExecution(**values)


def _approval(**overrides) -> DurableApproval:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    preview = overrides.pop("preview", ("write README.md",))
    values = {
        "approval_id": "apr_1",
        "tool_execution_id": "tex_1",
        "intent_hash": intent_hash(_intent()),
        "tool_schema_digest": _digest("schema"),
        "permission_context_digest": _digest("perms"),
        "requested_scope": "workspace_write:write_file",
        "preview": preview,
        "preview_digest": approval_preview_digest(preview),
        "created_at": created,
        "expires_at": created + timedelta(minutes=5),
    }
    values.update(overrides)
    return DurableApproval(**values)


def test_payload_budgets_match_the_execution_adr():
    assert DURABLE_PAYLOAD_BUDGETS == {
        "conversation_record": 256 * 1024,
        "prepared_intent": 32 * 1024,
        "tool_call_arguments": 128 * 1024,
        "tool_result_envelope": 16 * 1024,
        "structured_tool_facts": 32 * 1024,
        "approval_record": 16 * 1024,
        "error_detail": 4 * 1024,
        "agent_run_snapshot": 64 * 1024,
        "recovery_report": 64 * 1024,
        "task_outcome": 64 * 1024,
        "application_event": 8 * 1024,
    }
    assert CONVERSATION_RECORD_MAX_BYTES == DURABLE_PAYLOAD_BUDGETS["conversation_record"]
    assert AGENT_RUN_SNAPSHOT_MAX_BYTES == DURABLE_PAYLOAD_BUDGETS["agent_run_snapshot"]
    assert ERROR_DETAIL_MAX_BYTES == DURABLE_PAYLOAD_BUDGETS["error_detail"]
    assert PREPARED_INTENT_MAX_BYTES == 32 * 1024
    assert TOOL_CALL_ARGUMENTS_MAX_BYTES == 128 * 1024
    assert TOOL_RESULT_ENVELOPE_MAX_BYTES == 16 * 1024
    assert STRUCTURED_TOOL_FACTS_MAX_BYTES == 32 * 1024
    assert APPROVAL_RECORD_MAX_BYTES == 16 * 1024
    assert RECOVERY_REPORT_MAX_BYTES == 64 * 1024
    assert TASK_OUTCOME_MAX_BYTES == 64 * 1024
    assert APPLICATION_EVENT_MAX_BYTES == 8 * 1024


def test_effect_class_is_independent_of_tool_effect():
    assert {item.value for item in ToolEffect}.isdisjoint({item.value for item in EffectClass})
    assert ToolEffect.NONE.value == "none"
    host = tool_declaration("run_command", process_isolation=ProcessIsolation.HOST)
    assert host.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
    assert host.missing_handler_completed is MissingCompletionPolicy.OUTCOME_UNKNOWN


def test_production_declarations_cover_the_frozen_inventory():
    assert PRODUCTION_TOOL_NAMES == {
        "update_configuration",
        "list_directory",
        "read_file",
        "find_files",
        "search_text",
        "apply_patch",
        "write_file",
        "show_changes",
        "run_command",
        "git_status",
        "git_diff",
        "promote_sandbox_changes",
    }
    assert "calculate" not in PRODUCTION_TOOL_NAMES
    assert "lookup_record" not in PRODUCTION_TOOL_NAMES
    assert tool_declaration("calculate").effect_class is EffectClass.PURE
    assert tool_declaration("show_changes").effect_class is EffectClass.DURABLE_STATE_READ
    git = tool_declaration("git_status")
    assert git.effect_class is EffectClass.BOUNDED_EXTERNAL_READ
    assert git.requires_frozen_confinement is True
    write = tool_declaration("write_file")
    assert write.missing_handler_completed is MissingCompletionPolicy.REQUIRES_RECONCILIATION
    sandbox = tool_declaration("run_command", process_isolation=ProcessIsolation.NATIVE_SANDBOX)
    assert sandbox.effect_class is EffectClass.PROCESS_EFFECT_NON_DURABLE
    assert sandbox.missing_handler_completed is MissingCompletionPolicy.OUTCOME_UNKNOWN
    with pytest.raises(UnknownToolDeclarationError, match="process isolation"):
        tool_declaration("run_command")
    with pytest.raises(UnknownToolDeclarationError, match="no durable declaration"):
        tool_declaration("invented_tool")
    assert missing_declarations(("read_file", "invented_tool")) == ("invented_tool",)


def test_prepared_intent_enforces_budget_and_redaction():
    with pytest.raises(ValidationError, match="budget"):
        _intent(redacted_arguments={"blob": "x" * (PREPARED_INTENT_MAX_BYTES + 1)})
    with pytest.raises(ValidationError, match="secret"):
        _intent(redacted_arguments={"token": "api_key=secret"})
    with pytest.raises(ValueError, match="budget"):
        require_tool_call_arguments_budget("x" * (TOOL_CALL_ARGUMENTS_MAX_BYTES + 1))


def test_facts_and_result_envelope_are_bounded():
    evidence = FileMutationEvidence(
        relative_path="README.md",
        operation="modify",
        existed_before=True,
        before_sha256=_digest("before"),
        expected_after_sha256=_digest("after"),
        expected_size=12,
        policy_version="files-v1",
        conflict_input_digest=_digest("conflict"),
    )
    facts = DurableToolFacts(files=(evidence,))
    assert facts.files[0].expected_after_sha256 == _digest("after")
    with pytest.raises(ValidationError, match="budget"):
        HandlerResultEnvelope(ok=True, summary={"blob": "y" * (TOOL_RESULT_ENVELOPE_MAX_BYTES + 1)})
    with pytest.raises(ValidationError, match="secret"):
        HandlerResultEnvelope(ok=False, error_message="password leaked")


def test_execution_transitions_are_explicit_and_versioned():
    prepared = _execution()
    awaiting = transition_execution(
        prepared,
        ToolExecutionState.AWAITING_APPROVAL,
        expected_row_version=1,
    )
    assert awaiting.state is ToolExecutionState.AWAITING_APPROVAL
    assert awaiting.row_version == 2
    executing = transition_execution(
        awaiting,
        ToolExecutionState.EXECUTING,
        expected_row_version=2,
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        approval_id="apr_1",
    )
    assert executing.executing_at is not None
    completed = transition_execution(
        executing,
        ToolExecutionState.HANDLER_COMPLETED,
        expected_row_version=3,
        disposition=ToolExecutionDisposition.SUCCEEDED,
        result_envelope=HandlerResultEnvelope(ok=True, summary={"bytes": 12}),
    )
    closed = transition_execution(
        completed,
        ToolExecutionState.CLOSED,
        expected_row_version=4,
    )
    assert closed.state is ToolExecutionState.CLOSED
    assert closed.disposition is ToolExecutionDisposition.SUCCEEDED
    with pytest.raises(ExecutionTransitionError, match="illegal"):
        transition_execution(
            prepared,
            ToolExecutionState.HANDLER_COMPLETED,
            expected_row_version=1,
            disposition=ToolExecutionDisposition.SUCCEEDED,
        )
    with pytest.raises(StaleRowVersionError, match="stale"):
        transition_execution(
            prepared,
            ToolExecutionState.EXECUTING,
            expected_row_version=2,
        )
    denied = transition_execution(
        prepared,
        ToolExecutionState.CLOSED,
        expected_row_version=1,
        disposition=ToolExecutionDisposition.DENIED,
    )
    assert denied.disposition is ToolExecutionDisposition.DENIED
    assert RecoveryClassification.NEVER_STARTED.value == "never_started"


def test_retry_creates_a_linked_execution_identity():
    retry = _execution(tool_execution_id="tex_2", retry_of_execution_id="tex_1")
    assert retry.retry_of_execution_id == "tex_1"
    with pytest.raises(ValidationError):
        _execution(tool_execution_id="call_1")


def test_approval_resolve_consume_and_expiry_are_deterministic():
    created = datetime(2026, 1, 1, tzinfo=UTC)
    pending = _approval()
    approved = resolve_approval(
        pending,
        approved=True,
        expected_row_version=1,
        now=created + timedelta(seconds=1),
        command_id="cmd_1",
    )
    assert approved.resolution is ApprovalResolution.APPROVED
    assert approved.granted_scope == pending.requested_scope
    same = resolve_approval(
        approved,
        approved=True,
        expected_row_version=2,
        now=created + timedelta(seconds=2),
    )
    assert same is approved
    with pytest.raises(ApprovalDecisionError, match="mismatch"):
        resolve_approval(
            approved,
            approved=False,
            expected_row_version=2,
            now=created + timedelta(seconds=2),
        )
    consumed = consume_approval(
        approved,
        expected_row_version=2,
        now=created + timedelta(seconds=2),
    )
    assert consumed.consumed_at is not None
    with pytest.raises(ApprovalDecisionError, match="already consumed"):
        consume_approval(consumed, expected_row_version=3, now=created + timedelta(seconds=3))
    expired = resolve_approval(
        pending,
        approved=True,
        expected_row_version=1,
        now=created + timedelta(minutes=6),
    )
    assert expired.resolution is ApprovalResolution.EXPIRED
    with pytest.raises(ApprovalDecisionError, match="expired"):
        consume_approval(
            approved,
            expected_row_version=2,
            now=created + timedelta(minutes=6),
        )


def test_handler_entry_requires_executing_and_consumed_approval():
    intent = _intent(
        tool_name="write_file",
        requires_approval=True,
        effect_class=EffectClass.RECONCILEABLE_FILE_WRITE,
    )
    execution = transition_execution(
        _execution(intent=intent, tool_name="write_file", call_id="call1"),
        ToolExecutionState.AWAITING_APPROVAL,
        expected_row_version=1,
    )
    now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    pending = _approval(intent_hash=intent_hash(intent), tool_execution_id="tex_1")
    with pytest.raises(ExecutionTransitionError, match="executing"):
        assert_handler_may_enter(execution, pending, now=now)
    executing = transition_execution(
        execution,
        ToolExecutionState.EXECUTING,
        expected_row_version=2,
        now=now,
        approval_id="apr_1",
    )
    with pytest.raises(ApprovalDecisionError, match="not consumed"):
        assert_handler_may_enter(executing, pending, now=now)
    approved = resolve_approval(pending, approved=True, expected_row_version=1, now=now)
    consumed = consume_approval(approved, expected_row_version=2, now=now)
    assert_handler_may_enter(executing, consumed, now=now)
    mismatched = consumed.model_copy(update={"intent_hash": _digest("other")})
    with pytest.raises(ApprovalDecisionError, match="intent hash"):
        assert_handler_may_enter(executing, mismatched, now=now)


def test_fault_injector_inventory_and_one_shot_behavior():
    assert {point.value for point in REQUIRED_FAULT_POINTS} == {
        "conversation.before_commit",
        "conversation.after_commit",
        "execution.intent_after_commit",
        "approval.after_create",
        "approval.after_consume",
        "handler.before_enter",
        "handler.after_return",
        "execution.after_handler_completed",
        "conversation.before_tool_message_commit",
        "conversation.after_tool_message_commit",
        "turn.before_terminal_commit",
        "turn.after_terminal_commit",
        "artifact.after_reserve",
        "artifact.after_temp_create",
        "artifact.file_fsync",
        "artifact.before_rename",
        "artifact.after_rename",
        "artifact.after_parent_fsync",
        "artifact.before_mark_available",
        "artifact.after_mark_available",
    }
    NoOpFaultInjector().check(FaultPoint.HANDLER_BEFORE_ENTER)
    injector = OnceFaultInjector(FaultPoint.HANDLER_BEFORE_ENTER)
    with pytest.raises(InjectedFault, match="handler.before_enter"):
        injector.check(FaultPoint.HANDLER_BEFORE_ENTER)
    injector.check(FaultPoint.HANDLER_BEFORE_ENTER)
    injector.check(FaultPoint.HANDLER_AFTER_RETURN)
    assert injector.fired is True
    seen: list[FaultPoint] = []
    custom = OnceFaultInjector(FaultPoint.APPROVAL_AFTER_CONSUME, action=seen.append)
    custom.check(FaultPoint.APPROVAL_AFTER_CONSUME)
    assert seen == [FaultPoint.APPROVAL_AFTER_CONSUME]
    with pytest.raises(ValueError):
        OnceFaultInjector(FaultPoint("not.a.point"))
