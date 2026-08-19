"""Focused tests for the Stage 4 v2 lifecycle and conversation journal."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.migrations import V1, V2, V3, MigrationRegistry
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.core.domain import (
    AgentRunSnapshot,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionHealth,
    SessionLifecycle,
    SourceRevisionRef,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    sha256_digest,
)
from morrow.core.models import ModelRef, Preferences, Profile
from morrow.core.store import (
    SUPPORTED_SCHEMA_VERSION,
    StorageError,
    StorageErrorCode,
    StoreOpenMode,
)
from morrow.testing import FixedClock


def _retry() -> BusyRetryPolicy:
    return BusyRetryPolicy(busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0))


def _open_journal(tmp_path: Path, *, registry: MigrationRegistry | None = None):
    root = tmp_path / "state"
    store = OperationalStore(
        root,
        retry_policy=_retry(),
        clock=FixedClock(),
        maintenance_timeout=0,
        registry=registry,
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


def _session(workspace_id: str = "ws_a", session_id: str = "ses_1") -> DurableSession:
    return DurableSession(session_id=session_id, workspace_id=workspace_id)


def _task(
    workspace_id: str = "ws_a", session_id: str = "ses_1", task_run_id: str = "task_1"
) -> DurableTaskRun:
    return DurableTaskRun(task_run_id=task_run_id, session_id=session_id, workspace_id=workspace_id)


def test_initialize_creates_v3_business_tables(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        assert session.schema_version == SUPPORTED_SCHEMA_VERSION
        names = {
            row[0]
            for row in session.run_read(
                lambda executor: executor.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            )
        }
        assert {
            "sessions",
            "task_runs",
            "turns",
            "agent_runs",
            "conversation_records",
            "turn_submit_receipts",
            "tool_executions",
            "approvals",
            "recovery_reports",
            "recovery_receipts",
        }.issubset(names)
        assert journal.list_sessions("ws_a") == ()
    finally:
        session.close()


def test_v1_store_migrates_to_supported_journal(tmp_path):
    v1 = MigrationRegistry(supported_version=1)
    v1.add(V1)
    store, session, _journal = _open_journal(tmp_path, registry=v1)
    root = store.layout.data_root
    session.close()
    assert store.classify().schema_version == 1
    upgraded = OperationalStore(
        root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    report = upgraded.migrate()
    assert report.from_version == 1
    assert report.to_version == SUPPORTED_SCHEMA_VERSION
    with upgraded.open(StoreOpenMode.READ_WRITE) as opened:
        journal = SqliteOperationalJournal(opened)
        created = journal.create_session(_session(), task=_task())
        assert created.current_task_run_id == "task_1"
        assert journal.get_task_run("ws_a", "task_1") is not None


def test_v2_store_migrates_to_v3_journal(tmp_path):
    v2 = MigrationRegistry(supported_version=2)
    v2.add(V1)
    v2.add(V2)
    store, session, _journal = _open_journal(tmp_path, registry=v2)
    root = store.layout.data_root
    session.close()
    assert store.classify().schema_version == 2
    upgraded = OperationalStore(
        root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    report = upgraded.migrate()
    assert report.from_version == 2
    assert report.to_version == SUPPORTED_SCHEMA_VERSION
    assert "tool_execution_approval" in report.applied
    with upgraded.open(StoreOpenMode.READ_WRITE) as opened:
        names = {
            row[0]
            for row in opened.run_read(
                lambda executor: executor.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            )
        }
        assert {"tool_executions", "approvals", "recovery_reports"}.issubset(names)


def test_v3_store_migrates_to_v4_recovery(tmp_path):
    v3 = MigrationRegistry(supported_version=3)
    v3.add(V1)
    v3.add(V2)
    v3.add(V3)
    store, session, _journal = _open_journal(tmp_path, registry=v3)
    root = store.layout.data_root
    session.close()
    assert store.classify().schema_version == 3
    upgraded = OperationalStore(
        root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    )
    report = upgraded.migrate()
    assert report.from_version == 3
    assert report.to_version == 4
    assert report.applied == ("recovery_reports",)


def test_sessions_are_workspace_scoped(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        journal.create_session(_session("ws_a", "ses_1"), task=_task("ws_a", "ses_1", "task_1"))
        journal.create_session(_session("ws_b", "ses_2"), task=_task("ws_b", "ses_2", "task_2"))
        listed = journal.list_sessions("ws_a")
        assert [item.session_id for item in listed] == ["ses_1"]
        assert journal.get_session("ws_b", "ses_1") is None
        assert journal.get_task_run("ws_b", "task_1") is None
        assert journal.get_session("ws_a", "ses_1") is not None
    finally:
        session.close()


def test_quarantine_changes_health_not_lifecycle(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        created = journal.create_session(_session())
        updated = journal.save_session(
            "ws_a",
            created.model_copy(update={"health": SessionHealth.QUARANTINED}),
        )
        assert updated.lifecycle is SessionLifecycle.ACTIVE
        assert updated.health is SessionHealth.QUARANTINED
        tombstone = journal.save_session(
            "ws_a",
            updated.model_copy(update={"lifecycle": SessionLifecycle.DELETED}),
        )
        assert tombstone.lifecycle is SessionLifecycle.DELETED
        assert tombstone.health is SessionHealth.QUARANTINED
        assert journal.list_sessions("ws_a") == ()
        assert journal.get_session("ws_a", "ses_1") is not None
    finally:
        session.close()


def test_turn_and_receipt_uniqueness_is_per_session(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        journal.create_session(_session("ws_a", "ses_1"), task=_task("ws_a", "ses_1", "task_1"))
        journal.create_session(_session("ws_a", "ses_2"), task=_task("ws_a", "ses_2", "task_2"))
        first = journal.create_turn(
            "ws_a",
            DurableTurn(
                turn_id="turn_1",
                session_id="ses_1",
                task_run_id="task_1",
                client_message_id="client-1",
            ),
        )
        journal.put_receipt(
            "ws_a",
            TurnSubmitReceipt(
                session_id="ses_1",
                client_message_id="client-1",
                request_digest=_digest("hello"),
                disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
                turn_id=first.turn_id,
                command_id="cmd_1",
            ),
        )
        journal.create_turn(
            "ws_a",
            DurableTurn(
                turn_id="turn_2",
                session_id="ses_2",
                task_run_id="task_2",
                client_message_id="client-1",
            ),
        )
        with pytest.raises(StorageError) as error:
            journal.create_turn(
                "ws_a",
                DurableTurn(
                    turn_id="turn_3",
                    session_id="ses_1",
                    task_run_id="task_1",
                    client_message_id="client-1",
                ),
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert journal.get_receipt("ws_b", "ses_1", "client-1") is None
        assert journal.get_receipt("ws_a", "ses_1", "client-1") is not None
    finally:
        session.close()


def test_conversation_position_is_monotonic_and_rolls_back(tmp_path):
    store, session, journal = _open_journal(tmp_path)
    root = store.layout.data_root
    try:
        journal.create_session(_session())
        first = DurableConversationRecord(
            record_id="rec_1",
            session_id="ses_1",
            conversation_position=1,
            kind="message",
            payload={"role": "user", "content": "hello"},
        )
        updated = journal.append_records("ws_a", (first,))
        assert updated.conversation_position == 1
        with pytest.raises(StorageError) as error:
            journal.append_records("ws_a", (first,))
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert journal.get_session("ws_a", "ses_1").conversation_position == 1
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
        skipped = DurableConversationRecord(
            record_id="rec_2",
            session_id="ses_1",
            conversation_position=2,
            kind="message",
            payload={"role": "assistant", "content": "hi"},
        )
        with pytest.raises(StorageError):
            failing.append_records("ws_a", (skipped,))

    with OperationalStore(
        root, retry_policy=_retry(), clock=FixedClock(), maintenance_timeout=0
    ).open(StoreOpenMode.READ_WRITE) as reopened:
        journal = SqliteOperationalJournal(reopened)
        assert journal.get_session("ws_a", "ses_1").conversation_position == 1
        assert [record.record_id for record in journal.load_records("ws_a", "ses_1")] == ["rec_1"]
        assert journal.load_records("ws_b", "ses_1") == ()


def test_foreign_keys_reject_orphan_turns_and_runs(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:
        journal.create_session(_session(), task=_task())
        with pytest.raises(StorageError):
            journal.create_turn(
                "ws_a",
                DurableTurn(
                    turn_id="turn_1",
                    session_id="ses_1",
                    task_run_id="task_missing",
                    client_message_id="client-1",
                ),
            )
        turn = journal.create_turn(
            "ws_a",
            DurableTurn(
                turn_id="turn_1",
                session_id="ses_1",
                task_run_id="task_1",
                client_message_id="client-1",
            ),
        )
        with pytest.raises(StorageError):
            journal.create_agent_run(
                "ws_a",
                DurableAgentRun(
                    agent_run_id="arun_1",
                    turn_id="turn_missing",
                    session_id="ses_1",
                    snapshot=_snapshot(),
                ),
            )
        run = journal.create_agent_run(
            "ws_a",
            DurableAgentRun(
                agent_run_id="arun_1",
                turn_id=turn.turn_id,
                session_id="ses_1",
                snapshot=_snapshot(),
            ),
        )
        assert run.snapshot.profile is not None
        assert run.snapshot.profile.name == "demo"
        assert journal.get_agent_run("ws_b", "arun_1") is None
        resumed = journal.create_agent_run(
            "ws_a",
            DurableAgentRun(
                agent_run_id="arun_2",
                turn_id=turn.turn_id,
                session_id="ses_1",
                resume_of_agent_run_id="arun_1",
                snapshot=_snapshot(),
            ),
        )
        assert resumed.resume_of_agent_run_id == "arun_1"
    finally:
        session.close()


def test_transact_commits_session_turn_and_records_together(tmp_path):
    _store, session, journal = _open_journal(tmp_path)
    try:

        def work(txn: SqliteOperationalJournal) -> DurableSession:
            created = txn.create_session(_session(), task=_task())
            turn = txn.create_turn(
                "ws_a",
                DurableTurn(
                    turn_id="turn_1",
                    session_id=created.session_id,
                    task_run_id="task_1",
                    client_message_id="client-1",
                ),
            )
            txn.put_receipt(
                "ws_a",
                TurnSubmitReceipt(
                    session_id=created.session_id,
                    client_message_id="client-1",
                    request_digest=_digest("hello"),
                    disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
                    turn_id=turn.turn_id,
                    command_id="cmd_1",
                ),
            )
            return txn.append_records(
                "ws_a",
                (
                    DurableConversationRecord(
                        record_id="rec_1",
                        session_id=created.session_id,
                        conversation_position=1,
                        kind="message",
                        payload={"role": "user", "content": "hello"},
                    ),
                ),
            )

        committed = journal.transact(work)
        assert committed.conversation_position == 1
        assert journal.get_turn("ws_a", "turn_1") is not None
        assert journal.get_receipt("ws_a", "ses_1", "client-1") is not None
        assert journal.load_records("ws_a", "ses_1")[0].payload["content"] == "hello"
    finally:
        session.close()
