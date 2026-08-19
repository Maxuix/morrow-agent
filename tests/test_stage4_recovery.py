"""Focused tests for Stage 4 Subplan 39 recovery contracts and classifier."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.application.recovery import recovery_tool_envelope
from morrow.core.domain import sha256_digest
from morrow.core.execution import (
    RECOVERY_REPORT_MAX_BYTES,
    EffectClass,
    FileMutationEvidence,
    MissingCompletionPolicy,
    RecoveryClassification,
    ToolExecutionState,
    ToolRecoveryDeclaration,
)
from morrow.core.models import AssistantMessage, FinishReason, FunctionToolCall, UserMessage
from morrow.core.recovery import (
    FileObservation,
    RecoveryDecisionError,
    RecoveryEvidence,
    RecoveryItem,
    RecoveryReport,
    RecoveryReportStatus,
    RecoveryResolution,
    allowed_resolutions,
    apply_item_resolution,
    apply_report_resume,
    classify_execution,
    classify_file_observations,
    observe_config,
    observe_file,
)
from morrow.runtime.conversation import ConversationLog, ConversationLogError


def _digest(label: str) -> str:
    return sha256_digest(label)


def _declaration(
    name: str = "read_file",
    effect: EffectClass = EffectClass.BOUNDED_READ,
    missing: MissingCompletionPolicy = MissingCompletionPolicy.SAFE_TO_RETRY,
) -> ToolRecoveryDeclaration:
    return ToolRecoveryDeclaration(
        tool_name=name, effect_class=effect, missing_handler_completed=missing
    )


def _evidence(**overrides) -> FileMutationEvidence:
    values = {
        "relative_path": "notes.txt",
        "operation": "replace",
        "existed_before": True,
        "before_sha256": _digest("before"),
        "expected_after_sha256": _digest("after"),
        "expected_size": 4,
        "policy_version": "files-v1",
        "conflict_input_digest": _digest("conflict"),
    }
    values.update(overrides)
    return FileMutationEvidence(**values)


def _item(**overrides) -> RecoveryItem:
    values = {
        "item_id": "rit_1",
        "report_id": "rrp_1",
        "tool_execution_id": "tex_1",
        "tool_name": "read_file",
        "classification": RecoveryClassification.SAFE_TO_RETRY,
        "allowed_resolutions": (RecoveryResolution.ABORT,),
        "evidence": RecoveryEvidence(
            execution_state=ToolExecutionState.EXECUTING,
            effect_class=EffectClass.BOUNDED_READ,
            summary=("bounded read was interrupted",),
        ),
    }
    values.update(overrides)
    return RecoveryItem(**values)


def test_recovery_report_is_budgeted_and_refuses_secrets():
    report = RecoveryReport(
        report_id="rrp_1",
        workspace_id="ws_1",
        session_id="ses_1",
        turn_id="turn_1",
        agent_run_id="arun_1",
        items=(_item(),),
    )
    assert report.status is RecoveryReportStatus.OPEN
    assert report.blocking_open[0].item_id == "rit_1"
    with pytest.raises(ValidationError, match="secret"):
        RecoveryReport(
            report_id="rrp_1",
            workspace_id="ws_1",
            session_id="ses_1",
            items=(
                _item(
                    evidence=RecoveryEvidence(
                        execution_state=ToolExecutionState.EXECUTING,
                        effect_class=EffectClass.BOUNDED_READ,
                        summary=("api_key leaked",),
                    )
                ),
            ),
        )
    assert RECOVERY_REPORT_MAX_BYTES == 64 * 1024


def test_classifier_uses_declaration_not_tool_effect():
    read = _declaration()
    write = _declaration(
        "write_file",
        EffectClass.RECONCILEABLE_FILE_WRITE,
        MissingCompletionPolicy.REQUIRES_RECONCILIATION,
    )
    host = _declaration(
        "run_command",
        EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        MissingCompletionPolicy.OUTCOME_UNKNOWN,
    )
    sandbox = _declaration(
        "run_command",
        EffectClass.PROCESS_EFFECT_NON_DURABLE,
        MissingCompletionPolicy.OUTCOME_UNKNOWN,
    )
    assert (
        classify_execution(state=ToolExecutionState.PREPARED, declaration=write)
        is RecoveryClassification.NEVER_STARTED
    )
    assert (
        classify_execution(state=ToolExecutionState.EXECUTING, declaration=read)
        is RecoveryClassification.SAFE_TO_RETRY
    )
    assert (
        classify_execution(state=ToolExecutionState.EXECUTING, declaration=write)
        is RecoveryClassification.REQUIRES_RECONCILIATION
    )
    assert (
        classify_execution(state=ToolExecutionState.EXECUTING, declaration=host)
        is RecoveryClassification.OUTCOME_UNKNOWN
    )
    assert (
        classify_execution(state=ToolExecutionState.EXECUTING, declaration=sandbox)
        is RecoveryClassification.OUTCOME_UNKNOWN
    )
    assert (
        classify_execution(state=ToolExecutionState.HANDLER_COMPLETED, declaration=write)
        is RecoveryClassification.COMPLETED
    )


def test_host_and_sandbox_ignore_process_absence_hints():
    host = _declaration(
        "run_command",
        EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        MissingCompletionPolicy.OUTCOME_UNKNOWN,
    )
    classification = classify_execution(
        state=ToolExecutionState.EXECUTING,
        declaration=host,
        observations=(),
    )
    assert classification is RecoveryClassification.OUTCOME_UNKNOWN
    assert RecoveryResolution.RETRY not in allowed_resolutions(classification, host)


def test_file_observation_uses_hashes_not_mtime(tmp_path: Path):
    target = tmp_path / "notes.txt"
    before = b"old\n"
    after = b"new\n"
    target.write_bytes(before)
    evidence = _evidence(
        before_sha256=sha256_digest(before),
        expected_after_sha256=sha256_digest(after),
        expected_size=len(after),
    )
    assert observe_file(evidence, root=tmp_path) is FileObservation.MATCHES_BEFORE
    target.write_bytes(after)
    assert observe_file(evidence, root=tmp_path) is FileObservation.MATCHES_EXPECTED
    target.write_bytes(b"other\n")
    assert observe_file(evidence, root=tmp_path) is FileObservation.THIRD_PARTY
    target.unlink()
    assert observe_file(evidence, root=tmp_path) is FileObservation.MISSING
    create = _evidence(
        operation="create",
        existed_before=False,
        before_sha256=None,
        expected_after_sha256=sha256_digest(after),
        expected_size=len(after),
    )
    assert observe_file(create, root=tmp_path) is FileObservation.MATCHES_BEFORE


def test_file_observations_classify_independently():
    expected = (FileObservation.MATCHES_EXPECTED, FileObservation.MATCHES_EXPECTED)
    before = (FileObservation.MATCHES_BEFORE, FileObservation.MATCHES_BEFORE)
    mixed = (FileObservation.MATCHES_EXPECTED, FileObservation.THIRD_PARTY)
    assert classify_file_observations(expected) is RecoveryClassification.COMPLETED
    assert classify_file_observations(before) is RecoveryClassification.SAFE_TO_RETRY
    assert classify_file_observations(mixed) is RecoveryClassification.OUTCOME_UNKNOWN
    write = _declaration(
        "promote_sandbox_changes",
        EffectClass.RECONCILEABLE_FILE_WRITE,
        MissingCompletionPolicy.REQUIRES_RECONCILIATION,
    )
    assert (
        classify_execution(
            state=ToolExecutionState.EXECUTING, declaration=write, observations=mixed
        )
        is RecoveryClassification.OUTCOME_UNKNOWN
    )


def test_config_observation_uses_revisions():
    assert (
        observe_config(source_revision=1, expected_revision=2, actual_revision=2)
        is FileObservation.MATCHES_EXPECTED
    )
    assert (
        observe_config(source_revision=1, expected_revision=2, actual_revision=1)
        is FileObservation.MATCHES_BEFORE
    )
    assert (
        observe_config(source_revision=1, expected_revision=2, actual_revision=9)
        is FileObservation.THIRD_PARTY
    )


def test_item_resolution_is_idempotent_and_rejects_illegal_choices():
    item = _item()
    with pytest.raises(RecoveryDecisionError, match="linked retry"):
        apply_item_resolution(item, RecoveryResolution.RETRY)
    aborted = apply_item_resolution(item, RecoveryResolution.ABORT)
    assert aborted.resolution is RecoveryResolution.ABORT
    assert apply_item_resolution(aborted, RecoveryResolution.ABORT) is aborted
    with pytest.raises(RecoveryDecisionError, match="mismatch"):
        apply_item_resolution(aborted, RecoveryResolution.QUARANTINE)
    with pytest.raises(RecoveryDecisionError, match="not allowed"):
        apply_item_resolution(item, RecoveryResolution.ACKNOWLEDGE)
    with pytest.raises(RecoveryDecisionError, match="report-level"):
        apply_item_resolution(item, RecoveryResolution.RESUME)


def test_report_resume_requires_blocking_items_closed():
    report = RecoveryReport(
        report_id="rrp_1",
        workspace_id="ws_1",
        session_id="ses_1",
        items=(_item(),),
    )
    with pytest.raises(RecoveryDecisionError, match="blocking"):
        apply_report_resume(report)
    closed = report.model_copy(
        update={"items": (apply_item_resolution(_item(), RecoveryResolution.ABORT),)}
    )
    resolved = apply_report_resume(closed, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert resolved.status is RecoveryReportStatus.RESOLVED
    assert apply_report_resume(resolved) is resolved


def test_recovery_close_rejects_success_and_closes_in_order():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(id="c1", name="echo", arguments="{}"),
                FunctionToolCall(id="c2", name="echo", arguments="{}"),
            )
        )
    )
    with pytest.raises(ConversationLogError, match="successful"):
        log.plan_recovery_close((("c1", recovery_tool_envelope()),), FinishReason.STOP)
    with pytest.raises(ConversationLogError, match="interrupted or error"):
        log.plan_recovery_close((("c1", '{"ok":true,"result":{}}'),), FinishReason.ERROR)
    with pytest.raises(ConversationLogError, match="original call order"):
        log.plan_recovery_close(
            (("c2", recovery_tool_envelope()), ("c1", recovery_tool_envelope())),
            FinishReason.ERROR,
        )
    planned = log.plan_recovery_close(
        (("c1", recovery_tool_envelope()), ("c2", recovery_tool_envelope())),
        FinishReason.ERROR,
    )
    log.apply_committed(planned)
    assert log.has_active_turn is False
    roles = [message.role for message in log.messages_view()]
    assert roles == ["user", "assistant", "tool", "tool"]
    for message in log.messages_view()[-2:]:
        assert '"ok":false' in message.content.replace(" ", "")


def test_recovery_close_can_leave_the_turn_open_for_resume():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="go"))
    log.append_assistant(
        AssistantMessage(tool_calls=(FunctionToolCall(id="c1", name="echo", arguments="{}"),))
    )
    planned = log.plan_recovery_close((("c1", recovery_tool_envelope()),), reason=None)
    log.apply_committed(planned)
    assert log.has_active_turn is True
    assert log.unresolved_call_ids == ()
