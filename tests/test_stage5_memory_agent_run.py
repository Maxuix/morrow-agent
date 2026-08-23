from __future__ import annotations

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api_context import ApplicationCommandContext
from morrow.application.api_recovery import RecoveryApplicationService
from morrow.application.learning.memory_run_projection import load_frozen_memory_selection
from morrow.application.learning.memory_terms import refresh_project_knowledge_terms
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskService
from morrow.application.turn_lifecycle import build_agent_run_snapshot
from morrow.application.turns import SessionPersistence
from morrow.core.domain import (
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    SessionHealth,
    sha256_digest,
)
from morrow.core.execution import (
    DurableToolExecution,
    EffectClass,
    PreparedIntent,
    ToolExecutionState,
    transition_execution,
)
from morrow.core.models import ModelRef, Preferences, Profile
from morrow.core.recovery import RecoveryResolution
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.durable_log import DurableConversationWriter
from morrow.runtime.session import Session
from morrow.testing import FixedClock, FixedIdSource, make_context_builder
from test_stage5_memory_selection import NOW, _knowledge_subject, _selection


def _open(tmp_path):
    clock = FixedClock(NOW)
    store = OperationalStore(tmp_path / "state", clock=clock, maintenance_timeout=0)
    handle = store.initialize()
    return handle, SqliteOperationalJournal(handle), clock


def _persistence(journal, handle, *, ids: FixedIdSource, clock: FixedClock, session: Session):
    persistence = SessionPersistence(
        workspace_id="ws_1",
        journal=journal,
        store_session=handle,
        id_source=ids,
        model=ModelRef(provider_id="p", model_id="m"),
        run_policy=make_context_builder().run_policy,
        runtime_instance_id="host-1",
        clock=clock,
    )
    persistence.attach(session)
    return persistence


def _seed_terms(journal: SqliteOperationalJournal) -> None:
    _knowledge_subject(journal)
    head = journal.get_project_knowledge_head("ws_1", "knw_1")
    assert head is not None
    journal.transact(lambda txn: refresh_project_knowledge_terms(txn, "ws_1", head))


def _session() -> Session:
    return Session(
        session_id="ses_1",
        profile=Profile(name="frozen profile"),
        global_preferences=Preferences(language="en", instructions=["global rule"]),
        workspace_preferences=Preferences(
            response_detail="detailed", instructions=["workspace rule"]
        ),
        preferences=Preferences(language="zh", instructions=["session rule"]),
        profile_revision=4,
        preferences_revision=7,
        global_preferences_revision=2,
    )


def _interrupted_execution(
    journal: SqliteOperationalJournal,
    *,
    task_run_id: str,
    turn_id: str,
    agent_run_id: str,
    tool_name: str = "read_file",
    effect_class: EffectClass = EffectClass.BOUNDED_READ,
) -> None:
    intent = PreparedIntent(
        tool_name=tool_name,
        call_id="call_memory",
        ordinal=1,
        arguments_digest=sha256_digest("arguments"),
        schema_digest=sha256_digest("schema"),
        permission_context_digest=sha256_digest("permissions"),
        effect_class=effect_class,
    )
    prepared = journal.put_execution(
        "ws_1",
        DurableToolExecution(
            tool_execution_id="tex_memory",
            workspace_id="ws_1",
            session_id="ses_1",
            task_run_id=task_run_id,
            turn_id=turn_id,
            agent_run_id=agent_run_id,
            call_id=intent.call_id,
            ordinal=intent.ordinal,
            tool_name=intent.tool_name,
            intent=intent,
        ),
    )
    executing = transition_execution(
        prepared,
        ToolExecutionState.EXECUTING,
        expected_row_version=prepared.row_version,
        now=NOW,
    )
    journal.save_execution("ws_1", executing, expected_row_version=prepared.row_version)


def test_new_turn_freezes_selection_and_effective_preferences_atomically(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        _seed_terms(journal)
        session = _session()
        ids = FixedIdSource()
        ids.counts["task"] = 1
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)

        result = persistence.submit_user(
            session,
            "Operational SQLite",
            "client_memory",
            turn_id="turn_memory",
            agent_run_id="arun_memory",
        )

        assert result.kind == "accepted"
        run = journal.get_agent_run("ws_1", "arun_memory")
        assert run is not None
        assert run.snapshot.memory_selection_id is not None
        selection = journal.get_memory_selection("ws_1", run.snapshot.memory_selection_id)
        assert selection is not None
        assert selection.item_count == 1
        assert selection.selected_items[0].record_revision_id == "krv_1"
        assert run.snapshot.memory_selection_digest == selection.selection_digest
        assert run.snapshot.memory_snapshot_revision == selection.source_memory_revision
        assert {item.statement for item in run.snapshot.frozen_preferences} == {
            "global rule",
            "回答时默认使用 en。",
            "workspace rule",
            "回答默认提供详细说明。",
            "session rule",
            "回答时默认使用 zh。",
        }
        revisions = {item.kind: item for item in run.snapshot.source_revisions}
        assert revisions["global_config"].revision == 2
        assert revisions["workspace_profile"].revision == 4
        assert revisions["workspace_preferences"].revision == 7
        assert revisions["session_preferences"].revision == 0
        assert len(journal.load_records("ws_1", "ses_1")) == 1
        assert session.messages[0].content == "Operational SQLite"
    finally:
        handle.close()


def test_selection_admission_rolls_back_before_user_message_is_published(tmp_path, monkeypatch):
    handle, journal, clock = _open(tmp_path)
    try:
        _seed_terms(journal)
        session = _session()
        ids = FixedIdSource()
        ids.counts["task"] = 1
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)

        def fail_create_agent_run(*_args, **_kwargs):
            raise RuntimeError("injected AgentRun admission failure")

        monkeypatch.setattr(journal, "create_agent_run", fail_create_agent_run)
        with pytest.raises(RuntimeError, match="admission failure"):
            persistence.submit_user(
                session,
                "Operational SQLite",
                "client_rollback",
                turn_id="turn_rollback",
                agent_run_id="arun_rollback",
            )

        assert journal.get_turn("ws_1", "turn_rollback") is None
        assert journal.get_agent_run("ws_1", "arun_rollback") is None
        assert journal.get_receipt("ws_1", "ses_1", "client_rollback") is None
        assert journal.get_task_run("ws_1", "task_2") is None
        assert journal.list_memory_selections("ws_1") == ()
        assert journal.load_records("ws_1", "ses_1") == ()
        assert session.messages == ()
    finally:
        handle.close()


def test_recovery_agent_run_reuses_selection_after_memory_revision_changes(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        _seed_terms(journal)
        session = _session()
        ids = FixedIdSource()
        ids.counts["task"] = 1
        persistence = _persistence(journal, handle, ids=ids, clock=clock, session=session)
        accepted = persistence.submit_user(
            session,
            "Operational SQLite",
            "client_recovery",
            turn_id="turn_recovery",
            agent_run_id="arun_recovery",
        )
        assert accepted.kind == "accepted"
        original = journal.get_agent_run("ws_1", "arun_recovery")
        assert original is not None
        original_selection_id = original.snapshot.memory_selection_id
        original_selection_digest = original.snapshot.memory_selection_digest
        original_memory_revision = original.snapshot.memory_snapshot_revision
        assert original_selection_id is not None

        state = journal.get_memory_workspace_state("ws_1")
        assert state is not None
        journal.save_memory_workspace_state(
            "ws_1",
            state.model_copy(
                update={
                    "memory_revision": state.memory_revision + 1,
                    "row_version": state.row_version + 1,
                }
            ),
            expected_row_version=state.row_version,
        )
        _interrupted_execution(
            journal,
            task_run_id=persistence.current_task_run_id or "task_2",
            turn_id="turn_recovery",
            agent_run_id="arun_recovery",
            tool_name="run_command",
            effect_class=EffectClass.UNCONFINED_EXTERNAL_EFFECT,
        )

        ids.counts["arun"] = 1
        recovery = RecoveryService(journal, workspace_id="ws_1", id_source=ids)
        context = ApplicationCommandContext(
            journal=journal,
            workspace_id="ws_1",
            id_source=ids,
            clock=clock.now,
            tasks=TaskService(journal=journal, workspace_id="ws_1", id_source=ids, clock=clock.now),
            recovery=recovery,
        )
        service = RecoveryApplicationService(context)
        writer = DurableConversationWriter(
            session.log,
            journal,
            workspace_id="ws_1",
            session_id="ses_1",
            id_source=ids,
        )
        report = recovery.discover("ses_1", session.log)
        assert report is not None
        service.resolve(
            report,
            command_id="cmd_ack_memory",
            resolution=RecoveryResolution.ACKNOWLEDGE,
            item_id=report.items[0].item_id,
            log=session.log,
            writer=writer,
        )
        open_report = journal.get_report("ws_1", report.report_id)
        assert open_report is not None
        resumed = service.resolve(
            open_report,
            command_id="cmd_resume_memory",
            resolution=RecoveryResolution.RESUME,
            log=session.log,
            writer=writer,
        )

        assert resumed.value.status.value == "resolved"
        new_run = journal.get_agent_run("ws_1", "arun_2")
        assert new_run is not None
        assert new_run.resume_of_agent_run_id == "arun_recovery"
        assert new_run.snapshot.memory_selection_id == original_selection_id
        assert new_run.snapshot.memory_selection_digest == original_selection_digest
        assert new_run.snapshot.memory_snapshot_revision == original_memory_revision
        assert journal.get_memory_selection("ws_1", original_selection_id) is not None
    finally:
        handle.close()


def test_restore_quarantines_run_with_missing_frozen_selection(tmp_path):
    handle, journal, clock = _open(tmp_path)
    try:
        session = _session()
        selection = _selection()
        snapshot = build_agent_run_snapshot(
            session,
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            tools=(),
            runtime_instance_id="host-1",
            memory_selection=selection,
        )
        journal.create_session(
            DurableSession(session_id="ses_1", workspace_id="ws_1"),
            task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_1"),
        )
        journal.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_missing",
                session_id="ses_1",
                task_run_id="task_1",
                client_message_id="client_missing",
            ),
        )
        journal.create_agent_run(
            "ws_1",
            DurableAgentRun(
                agent_run_id="arun_missing",
                turn_id="turn_missing",
                session_id="ses_1",
                snapshot=snapshot,
            ),
        )
        _interrupted_execution(
            journal,
            task_run_id="task_1",
            turn_id="turn_missing",
            agent_run_id="arun_missing",
        )

        restored = Session(session_id="ses_1")
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=FixedIdSource(),
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            runtime_instance_id="host-1",
            clock=clock,
        )
        persistence.restore_into(restored)

        assert restored.health is SessionHealth.QUARANTINED
        assert persistence.open_report is not None
        with pytest.raises(StorageError) as error:
            load_frozen_memory_selection(journal, "ws_1", snapshot)
        assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    finally:
        handle.close()
