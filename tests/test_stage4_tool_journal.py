"""Focused tests for the Stage 4 v3 tool execution and approval journal."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SourceRevisionRef,
    sha256_digest,
)
from morrow.core.execution import (
    ApprovalResolution,
    DurableApproval,
    DurableToolExecution,
    EffectClass,
    PreparedIntent,
    ToolExecutionState,
    approval_preview_digest,
    consume_approval,
    intent_hash,
    resolve_approval,
    transition_execution,
)
from morrow.core.models import ModelRef, Preferences, Profile
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.testing import FixedClock


def _retry() -> BusyRetryPolicy:
    return BusyRetryPolicy(busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0))


def _open_journal(tmp_path: Path):
    root = tmp_path / "state"
    store = OperationalStore(
        root,
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
    )
    session = store.initialize()
    return store, session, SqliteOperationalJournal(session)


def _digest(label: str) -> str:
    return sha256_digest(label)


def _snapshot() -> AgentRunSnapshot:
    return AgentRunSnapshot(
        profile=Profile(name="demo"),
        preferences=Preferences(language="中文"),
        model=ModelRef(provider_id="p", model_id="m"),
        provider_id="p",
        source_revisions=(
            SourceRevisionRef(
                kind="workspace_profile", revision=1, content_sha256=_digest("profile")
            ),
        ),
        run_policy_digest=_digest("policy"),
        tool_schema_digest=_digest("tools"),
        permission_profile_digest=_digest("perms"),
        runtime_instance_id="host-1",
    )


def _seed_run(journal: SqliteOperationalJournal, workspace_id: str = "ws_a") -> None:
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id=workspace_id),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id=workspace_id),
    )
    journal.create_turn(
        workspace_id,
        DurableTurn(
            turn_id="turn_1",
            session_id="ses_1",
            task_run_id="task_1",
            client_message_id="client-1",
        ),
    )
    journal.create_agent_run(
        workspace_id,
        DurableAgentRun(
            agent_run_id="arun_1",
            turn_id="turn_1",
            session_id="ses_1",
            snapshot=_snapshot(),
        ),
    )


def _intent(**overrides) -> PreparedIntent:
    values = {
        "tool_name": "write_file",
        "call_id": "call1",
        "ordinal": 1,
        "arguments_digest": _digest("args"),
        "schema_digest": _digest("schema"),
        "permission_context_digest": _digest("perms"),
        "effect_class": EffectClass.RECONCILEABLE_FILE_WRITE,
        "requires_approval": True,
    }
    values.update(overrides)
    return PreparedIntent(**values)


def _execution(intent: PreparedIntent | None = None, **overrides) -> DurableToolExecution:
    prepared = intent or _intent()
    values = {
        "tool_execution_id": "tex_1",
        "workspace_id": "ws_a",
        "session_id": "ses_1",
        "task_run_id": "task_1",
        "turn_id": "turn_1",
        "agent_run_id": "arun_1",
        "call_id": prepared.call_id,
        "ordinal": prepared.ordinal,
        "tool_name": prepared.tool_name,
        "intent": prepared,
    }
    values.update(overrides)
    return DurableToolExecution(**values)


def _approval(intent: PreparedIntent, **overrides) -> DurableApproval:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    preview = overrides.pop("preview", ("write README.md",))
    values = {
        "approval_id": "apr_1",
        "tool_execution_id": "tex_1",
        "intent_hash": intent_hash(intent),
        "tool_schema_digest": intent.schema_digest,
        "permission_context_digest": intent.permission_context_digest,
        "requested_scope": "workspace_write:write_file",
        "preview": preview,
        "preview_digest": approval_preview_digest(preview),
        "created_at": created,
        "expires_at": created + timedelta(minutes=5),
    }
    values.update(overrides)
    return DurableApproval(**values)


def test_execution_and_approval_round_trip(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        intent = _intent()
        stored = journal.put_execution("ws_a", _execution(intent))
        assert stored.state is ToolExecutionState.PREPARED
        assert stored.intent.schema_digest == intent.schema_digest
        approval = journal.put_approval("ws_a", _approval(intent))
        assert approval.resolution is ApprovalResolution.PENDING
        assert journal.get_approval_for_execution("ws_a", "tex_1").approval_id == "apr_1"
        assert journal.get_execution("ws_b", "tex_1") is None
        assert journal.get_approval("ws_b", "apr_1") is None
    finally:
        session.close()


def test_ordered_executions_keep_provider_order(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        journal.append_records(
            "ws_a",
            (
                DurableConversationRecord(
                    record_id="rec_1",
                    session_id="ses_1",
                    conversation_position=1,
                    kind="message",
                    payload={"role": "assistant", "content": "", "tool_calls": []},
                ),
            ),
        )
        first = journal.put_execution(
            "ws_a", _execution(_intent(ordinal=1, call_id="call1"), assistant_record_id="rec_1")
        )
        second = journal.put_execution(
            "ws_a",
            _execution(
                _intent(ordinal=2, call_id="call2"),
                tool_execution_id="tex_2",
                call_id="call2",
                ordinal=2,
                assistant_record_id="rec_1",
            ),
        )
        listed = journal.list_executions("ws_a", agent_run_id="arun_1")
        assert [item.tool_execution_id for item in listed] == [first.tool_execution_id, "tex_2"]
        assert [item.ordinal for item in listed] == [1, 2]
        with pytest.raises(StorageError) as error:
            journal.put_execution(
                "ws_a",
                _execution(
                    _intent(ordinal=1, call_id="call3"),
                    tool_execution_id="tex_3",
                    call_id="call3",
                    ordinal=1,
                    assistant_record_id="rec_1",
                ),
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert second.ordinal == 2
    finally:
        session.close()


def test_approval_requires_matching_intent_digests(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        intent = _intent()
        journal.put_execution("ws_a", _execution(intent))
        mismatched = _approval(intent, intent_hash=_digest("other"))
        with pytest.raises(StorageError) as error:
            journal.put_approval("ws_a", mismatched)
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert journal.get_approval_for_execution("ws_a", "tex_1") is None
    finally:
        session.close()


def test_one_approval_per_execution_and_consume_is_versioned(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        intent = _intent()
        journal.put_execution("ws_a", _execution(intent))
        pending = journal.put_approval("ws_a", _approval(intent))
        with pytest.raises(StorageError):
            journal.put_approval(
                "ws_a",
                _approval(intent, approval_id="apr_2"),
            )
        now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        approved = resolve_approval(pending, approved=True, expected_row_version=1, now=now)
        stored_approved = journal.save_approval("ws_a", approved, expected_row_version=1)
        consumed = consume_approval(stored_approved, expected_row_version=2, now=now)
        stored_consumed = journal.save_approval("ws_a", consumed, expected_row_version=2)
        assert stored_consumed.consumed_at is not None
        with pytest.raises(StorageError) as stale:
            journal.save_approval("ws_a", consumed, expected_row_version=2)
        assert stale.value.code is StorageErrorCode.UNAVAILABLE
    finally:
        session.close()


def test_consume_and_executing_are_one_transaction(tmp_path):
    store, session, journal = _open_journal(tmp_path)
    root = store.layout.data_root
    try:
        _seed_run(journal)
        intent = _intent()
        prepared = journal.put_execution("ws_a", _execution(intent))
        awaiting = transition_execution(
            prepared, ToolExecutionState.AWAITING_APPROVAL, expected_row_version=1
        )
        journal.save_execution("ws_a", awaiting, expected_row_version=1)
        pending = journal.put_approval("ws_a", _approval(intent))
        now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        approved = resolve_approval(pending, approved=True, expected_row_version=1, now=now)
        journal.save_approval("ws_a", approved, expected_row_version=1)
    finally:
        session.close()

    def injector(point: str) -> None:
        if point == "before_commit":
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            )

    failing_store = OperationalStore(
        root,
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
        failure_injector=injector,
    )
    with failing_store.open(StoreOpenMode.READ_WRITE) as failing_session:
        failing = SqliteOperationalJournal(failing_session)
        current_approval = failing.get_approval("ws_a", "apr_1")
        current_execution = failing.get_execution("ws_a", "tex_1")
        now = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
        consumed = consume_approval(current_approval, expected_row_version=2, now=now)
        executing = transition_execution(
            current_execution,
            ToolExecutionState.EXECUTING,
            expected_row_version=2,
            now=now,
            approval_id="apr_1",
        )

        def work(inner: SqliteOperationalJournal) -> None:
            inner.save_approval("ws_a", consumed, expected_row_version=2)
            inner.save_execution("ws_a", executing, expected_row_version=2)

        with pytest.raises(StorageError):
            failing.transact(work)

    with OperationalStore(
        root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    ).open(StoreOpenMode.READ_WRITE) as reopened:
        journal = SqliteOperationalJournal(reopened)
        approval = journal.get_approval("ws_a", "apr_1")
        execution = journal.get_execution("ws_a", "tex_1")
        assert approval.resolution is ApprovalResolution.APPROVED
        assert approval.consumed_at is None
        assert execution.state is ToolExecutionState.AWAITING_APPROVAL


def test_sql_rejects_consumed_unapproved_approval(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        intent = _intent()
        journal.put_execution("ws_a", _execution(intent))
        now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
        denied = resolve_approval(
            _approval(intent), approved=False, expected_row_version=1, now=now
        )
        journal.put_approval("ws_a", denied)
        with pytest.raises(StorageError):
            session.run_write(
                lambda executor: executor.execute(
                    "UPDATE approvals SET consumed_at_unix = 1 WHERE approval_id = 'apr_1'"
                )
            )
        assert journal.get_approval("ws_a", "apr_1").consumed_at is None
    finally:
        session.close()
