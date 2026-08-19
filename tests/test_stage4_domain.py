"""Focused tests for Stage 4 Subplan 37 domain contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from morrow.core.domain import (
    AGENT_RUN_SNAPSHOT_MAX_BYTES,
    AgentRunSnapshot,
    ApplicationEventCursor,
    ConversationPosition,
    DurableSession,
    DurableTurn,
    SequenceNamespace,
    SessionHealth,
    SessionLifecycle,
    SourceRevisionRef,
    TaskRunStatus,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    require_payload_budget,
    sha256_digest,
    utf8_size,
    validate_prefixed_id,
)
from morrow.core.models import ModelRef, Preferences, Profile, UserMessage


def _digest(label: str = "x") -> str:
    return sha256_digest(label)


def _snapshot(**overrides) -> AgentRunSnapshot:
    values = {
        "profile": Profile(name="demo"),
        "preferences": Preferences(language="中文"),
        "model": ModelRef(provider_id="p", model_id="m"),
        "provider_id": "p",
        "source_revisions": (
            SourceRevisionRef(
                kind="workspace_profile",
                revision=1,
                content_sha256=_digest("profile"),
            ),
        ),
        "run_policy_digest": _digest("policy"),
        "tool_schema_digest": _digest("tools"),
        "permission_profile_digest": _digest("perms"),
        "runtime_instance_id": "host-1",
    }
    values.update(overrides)
    return AgentRunSnapshot(**values)


def test_prefixed_ids_are_opaque_and_owner_specific():
    assert validate_prefixed_id("ses_abc", "ses") == "ses_abc"
    with pytest.raises(ValueError, match="prefix"):
        validate_prefixed_id("turn_abc", "ses")
    with pytest.raises(ValueError, match="empty"):
        validate_prefixed_id("   ", "ses")
    with pytest.raises(ValueError, match="bounded"):
        validate_prefixed_id("ses_" + "a" * 200, "ses")


def test_session_lifecycle_and_health_are_independent():
    session = DurableSession(
        session_id="ses_1",
        workspace_id="ws_1",
        lifecycle=SessionLifecycle.ACTIVE,
        health=SessionHealth.QUARANTINED,
        current_task_run_id="task_1",
    )
    assert session.lifecycle is SessionLifecycle.ACTIVE
    assert session.health is SessionHealth.QUARANTINED
    tombstone = session.model_copy(update={"lifecycle": SessionLifecycle.DELETED})
    assert tombstone.lifecycle is SessionLifecycle.DELETED
    assert tombstone.health is SessionHealth.QUARANTINED


def test_task_run_status_is_open_only_in_this_slice():
    assert list(TaskRunStatus) == [TaskRunStatus.OPEN]


def test_sequence_namespaces_do_not_alias():
    conversation = ConversationPosition(session_id="ses_1", value=3)
    runtime = ConversationPosition.model_validate(
        {"namespace": SequenceNamespace.CONVERSATION_POSITION, "session_id": "ses_1", "value": 3}
    )
    assert conversation.namespace is not SequenceNamespace.RUNTIME_EVENT_SEQUENCE
    assert conversation.namespace is not SequenceNamespace.APPLICATION_EVENT_CURSOR
    assert runtime.namespace is SequenceNamespace.CONVERSATION_POSITION
    cursor = ApplicationEventCursor(value=0)
    assert cursor.namespace is SequenceNamespace.APPLICATION_EVENT_CURSOR
    assert conversation.value != cursor.namespace


def test_client_message_id_is_not_a_user_message_field():
    turn = DurableTurn(
        turn_id="turn_1",
        session_id="ses_1",
        task_run_id="task_1",
        client_message_id="client-msg-1",
    )
    assert "client_message_id" not in UserMessage.model_fields
    assert turn.client_message_id == "client-msg-1"
    with pytest.raises(ValidationError):
        DurableTurn(
            turn_id="turn_1",
            session_id="ses_1",
            task_run_id="task_1",
            client_message_id="has space",
        )


def test_agent_run_snapshot_is_immutable_and_budgeted():
    snapshot = _snapshot()
    with pytest.raises(ValidationError):
        snapshot.provider_id = "other"
    assert utf8_size(snapshot.model_dump_json()) <= AGENT_RUN_SNAPSHOT_MAX_BYTES
    with pytest.raises(ValidationError, match="secret"):
        _snapshot(runtime_instance_id="api_key=secret")
    with pytest.raises(ValueError, match="budget"):
        require_payload_budget(
            b"x" * (AGENT_RUN_SNAPSHOT_MAX_BYTES + 1),
            AGENT_RUN_SNAPSHOT_MAX_BYTES,
            label="AgentRun snapshot",
        )


def test_turn_submit_receipt_stores_command_idempotency():
    receipt = TurnSubmitReceipt(
        session_id="ses_1",
        client_message_id="client-msg-1",
        request_digest=_digest("hello"),
        disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
        turn_id="turn_1",
        command_id="cmd_1",
    )
    assert receipt.disposition is TurnSubmitDisposition.ACCEPTED_OPEN
    assert receipt.request_digest == _digest("hello")
