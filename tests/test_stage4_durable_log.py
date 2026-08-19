"""ConversationLog candidate/commit tests for the durable append boundary."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.core.domain import DurableSession, DurableTaskRun
from morrow.core.models import AssistantMessage, FinishReason, FunctionToolCall, UserMessage
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.runtime.conversation import ConversationLog
from morrow.runtime.durable_log import (
    DurableConversationWriter,
    durable_call_id,
    restore_conversation_log,
)
from morrow.testing import FixedClock, FixedIdSource


def _retry() -> BusyRetryPolicy:
    return BusyRetryPolicy(busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0))


def _journal(tmp_path: Path):
    store = OperationalStore(
        tmp_path / "state",
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    session = store.initialize()
    journal = SqliteOperationalJournal(session)
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id="ws_a"),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_a"),
    )
    return store, session, journal


def test_plan_does_not_mutate_the_live_projection():
    log = ConversationLog()
    planned = log.plan_begin_turn(UserMessage(content="hello"))
    assert log.messages_view() == ()
    assert log.has_active_turn is False
    assert planned.added[0].sequence == 1
    log.apply_committed(planned)
    assert [message.content for message in log.messages_view()] == ["hello"]
    assert log.has_active_turn is True


def test_failed_persist_leaves_memory_projection_unchanged(tmp_path):
    store, session, journal = _journal(tmp_path)
    root = store.layout.data_root
    session.close()

    def injector(point: str) -> None:
        if point == "before_commit":
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            )

    failing = OperationalStore(
        root,
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
        failure_injector=injector,
    )
    with failing.open(StoreOpenMode.READ_WRITE) as opened:
        journal = SqliteOperationalJournal(opened)
        log = ConversationLog()
        writer = DurableConversationWriter(
            log,
            journal,
            workspace_id="ws_a",
            session_id="ses_1",
            id_source=FixedIdSource(),
        )
        planned = log.plan_begin_turn(UserMessage(content="hello"))
        with pytest.raises(StorageError):
            writer.commit(planned)
        assert log.messages_view() == ()
        assert log.has_active_turn is False

    with OperationalStore(
        root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    ).open(StoreOpenMode.READ_WRITE) as reopened:
        journal = SqliteOperationalJournal(reopened)
        assert journal.get_session("ws_a", "ses_1").conversation_position == 0
        restored = restore_conversation_log(journal, "ws_a", "ses_1")
        assert restored.messages_view() == ()


def test_committed_append_restores_identical_legal_records(tmp_path):
    _store, session, journal = _journal(tmp_path)
    try:
        log = ConversationLog()
        writer = DurableConversationWriter(
            log,
            journal,
            workspace_id="ws_a",
            session_id="ses_1",
            id_source=FixedIdSource(),
        )
        writer.commit(log.plan_begin_turn(UserMessage(content="hello")))
        writer.commit(log.plan_append_assistant(AssistantMessage(content="hi")))
        writer.commit(log.plan_finish_turn(FinishReason.STOP))
        assert log.has_active_turn is False
        restored = restore_conversation_log(journal, "ws_a", "ses_1")
        assert restored.snapshot() == log.snapshot()
        assert [message.content for message in restored.messages_view()] == ["hello", "hi"]
        assert restored.has_active_turn is False
    finally:
        session.close()


def test_tool_call_ids_have_stable_opaque_durable_correlations(tmp_path):
    _store, session, journal = _journal(tmp_path)
    try:
        log = ConversationLog()
        writer = DurableConversationWriter(
            log,
            journal,
            workspace_id="ws_a",
            session_id="ses_1",
            id_source=FixedIdSource(),
        )
        writer.commit(log.plan_begin_turn(UserMessage(content="read")))
        writer.commit(
            log.plan_append_assistant(
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="provider-call-42",
                            name="read_file",
                            arguments='{"path":"notes.txt"}',
                        ),
                    )
                )
            )
        )
        writer.commit(log.plan_append_tool_result("provider-call-42", '{"ok":true}'))
        writer.commit(log.plan_append_assistant(AssistantMessage(content="done")))
        writer.commit(log.plan_finish_turn(FinishReason.STOP))

        restored = restore_conversation_log(journal, "ws_a", "ses_1")
        cycle = restored.snapshot().public_turns()[0].cycles[0]
        durable_id = durable_call_id("provider-call-42")
        assert durable_id != "provider-call-42"
        assert cycle.assistant.message.tool_calls[0].id == durable_id
        assert cycle.results[0].message.tool_call_id == durable_id
    finally:
        session.close()
