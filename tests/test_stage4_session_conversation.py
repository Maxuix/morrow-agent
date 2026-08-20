"""Subplan 37 durable no-tool Session, receipts, restore, and /new /exit."""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.stage4_v2 import write_v2_store
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.commands import CommandService
from morrow.bootstrap import build_application, build_session_application
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import (
    DurableConversationRecord,
    SessionHealth,
    SessionLifecycle,
    TurnSubmitDisposition,
)
from morrow.core.models import AgentEvent, FinishReason, ModelRef
from morrow.core.store import SUPPORTED_SCHEMA_VERSION
from morrow.testing import FixedIdSource, ScriptedModelProvider, seed_user_turn


def _session_app(tmp_path: Path, *, resume: str | None = None, texts: list[str] | None = None):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(texts or ["saved answer"])
    products = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        resume_session_id=resume,
    )
    return app, identity, products


@pytest.mark.asyncio
async def test_user_commit_precedes_turn_started_and_survives_restart(tmp_path):
    app, identity, products = _session_app(tmp_path)
    session_id = products.session.session_id
    events = [
        item
        async for item in products.orchestrator.stream("hello there")
        if isinstance(item, AgentEvent)
    ]
    assert events[0].type == "turn.started"
    assert products.session.messages[0].content == "hello there"
    assert events[0].turn_id
    assert products.session.persisted is True
    assert products.session.dirty is False

    resumed = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["ignored"]),
        model=ModelRef(provider_id="p", model_id="m"),
        resume_session_id=session_id,
    )
    assert [message.content for message in resumed.session.messages] == [
        "hello there",
        "saved answer",
    ]
    journal = resumed.session.committer.journal
    snapshot = journal.get_session(identity.workspace_id, session_id)
    assert snapshot.conversation_position == 3
    run_id = journal._read_one(
        "SELECT agent_run_id FROM agent_runs WHERE session_id = ?",
        (session_id,),
    )[0]
    run = journal.get_agent_run(identity.workspace_id, str(run_id))
    assert run is not None
    assert run.snapshot.provider_id == "p"
    assert "sk-" not in run.snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_client_message_id_replay_recovery_and_conflict(tmp_path):
    _app, identity, products = _session_app(tmp_path, texts=["first"])
    session = products.session
    runtime = products.orchestrator.runtime
    first = [
        event
        async for event in runtime.run_turn(session, "same text", client_message_id="client-1")
    ]
    assert first[-1].payload["finish_reason"] == FinishReason.STOP.value
    journal = session.committer.journal
    turns_before = journal._read_one(
        "SELECT COUNT(*) FROM turns WHERE session_id = ?",
        (session.session_id,),
    )[0]

    replay = [
        event
        async for event in runtime.run_turn(session, "same text", client_message_id="client-1")
    ]
    assert replay[-1].payload["finish_reason"] == FinishReason.STOP.value
    assert (
        journal._read_one(
            "SELECT COUNT(*) FROM turns WHERE session_id = ?",
            (session.session_id,),
        )[0]
        == turns_before
    )
    assert [message.content for message in session.messages].count("same text") == 1

    conflict = [
        event
        async for event in runtime.run_turn(session, "different text", client_message_id="client-1")
    ]
    assert conflict[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert [message.content for message in session.messages].count("different text") == 0

    message_count = len(session.messages)
    receipt = journal.get_receipt(identity.workspace_id, session.session_id, "client-1")
    journal.update_receipt(
        identity.workspace_id,
        receipt.model_copy(update={"disposition": TurnSubmitDisposition.ACCEPTED_OPEN}),
    )
    recovery = [
        event
        async for event in runtime.run_turn(session, "same text", client_message_id="client-1")
    ]
    assert session.health is SessionHealth.NEEDS_RECOVERY
    assert recovery[-1].payload["finish_reason"] == FinishReason.ERROR.value
    assert len(session.messages) == message_count
    assert products.commands.execute("/new").action is None


@pytest.mark.asyncio
async def test_new_keeps_old_session_and_exit_does_not_discard(tmp_path):
    _app, identity, products = _session_app(tmp_path)
    await products.orchestrator.dispatch("keep me")
    old_id = products.session.session_id
    assert products.commands.execute("/new").action == "new"
    products.orchestrator.reset_session()
    assert products.session.session_id != old_id
    assert products.session.messages == ()
    assert products.session.preferences.language is None
    loaded = products.session.committer.journal.get_session(identity.workspace_id, old_id)
    assert loaded is not None
    assert loaded.lifecycle is SessionLifecycle.ACTIVE
    records = products.session.committer.journal.load_records(identity.workspace_id, old_id)
    assert records
    assert products.commands.execute("/exit").action == "exit"
    assert "保留" in products.commands.execute("/exit").lines[0]


def test_process_local_dirty_new_still_requires_discard():
    from morrow.core.models import Profile
    from morrow.runtime.session import Session

    session = Session(session_id="ses_local")
    seed_user_turn(session, "unsaved")
    commands = CommandService(
        session=session,
        identity=type(
            "Id",
            (),
            {"display_name": "demo", "path": ".", "workspace_id": "ws_1"},
        )(),
        project_store=None,
    )
    assert commands.execute("/new").action == "discard_new"
    assert session.messages[0].content == "unsaved"
    assert Profile(name="x").name == "x"


def test_v2_fixture_opens_and_lists_workspace_session(tmp_path):
    root = tmp_path / "v2"
    root.mkdir()
    write_v2_store(root)
    app = build_application(state_root=root, credentials=MemoryCredentialStore())
    from morrow.adapters.state.journal import SqliteOperationalJournal
    from morrow.adapters.state.operational import OperationalStore
    from morrow.core.store import StoreOpenMode

    store = OperationalStore(root)
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        assert handle.schema_version == SUPPORTED_SCHEMA_VERSION
        journal = SqliteOperationalJournal(handle)
        listed = journal.list_sessions("ws_stage3")
        assert [item.session_id for item in listed] == ["ses_v2fixture"]
        assert journal.get_session("ws_other", "ses_v2fixture") is None
    assert app.data_root.store_path.name == "operational.sqlite"


def test_quarantine_rejects_invalid_sequence_without_rewriting_lifecycle(tmp_path):
    _app, identity, products = _session_app(tmp_path)
    journal = products.session.committer.journal
    journal.append_records(
        identity.workspace_id,
        (
            DurableConversationRecord(
                record_id="rec_orphan",
                session_id=products.session.session_id,
                conversation_position=1,
                kind="message",
                payload={"role": "assistant", "content": "orphan"},
            ),
        ),
    )
    products.session.committer.restore_into(products.session)
    assert products.session.health is SessionHealth.QUARANTINED
    row = journal.get_session(identity.workspace_id, products.session.session_id)
    assert row.lifecycle is SessionLifecycle.ACTIVE
    assert products.session.messages == ()


def test_duplicate_open_submit_commits_recovery_and_restores_active_projection(tmp_path):
    _app, identity, products = _session_app(tmp_path)
    session = products.session
    api = products.api
    assert api is not None

    accepted = api.submit_turn(
        session,
        user_input="in flight",
        client_message_id="client-open",
        turn_id="turn_open",
        agent_run_id="arun_open",
        command_id="cmd_open",
        persistence=products.persistence,
    )
    assert accepted.value.kind == "accepted"
    task_run_id = products.persistence.current_task_run_id

    with pytest.raises(ApplicationError) as error:
        api.submit_turn(
            session,
            user_input="in flight",
            client_message_id="client-open",
            turn_id="turn_duplicate",
            agent_run_id="arun_duplicate",
            command_id="cmd_duplicate",
            persistence=products.persistence,
        )

    assert error.value.code is ApplicationErrorCode.NEEDS_RECOVERY
    durable = products.persistence.journal.get_session(identity.workspace_id, session.session_id)
    assert durable is not None
    assert durable.health is SessionHealth.NEEDS_RECOVERY
    assert session.health is SessionHealth.NEEDS_RECOVERY
    assert products.persistence.current_turn_id == "turn_open"
    assert products.persistence.current_task_run_id == task_run_id
    assert products.persistence.current_agent_run_id == "arun_open"
    assert products.commands.execute("/new").action is None


def test_restore_quarantines_malformed_conversation_json(tmp_path):
    _app, identity, products = _session_app(tmp_path)
    journal = products.persistence.journal
    journal.append_records(
        identity.workspace_id,
        (
            DurableConversationRecord(
                record_id="rec_corrupt",
                session_id=products.session.session_id,
                conversation_position=1,
                kind="message",
                payload={"role": "user", "content": "hello"},
            ),
        ),
    )
    products.persistence.store_session.run_write(
        lambda executor: executor.execute(
            "UPDATE conversation_records SET payload_json = ? WHERE record_id = ?",
            ("{", "rec_corrupt"),
        )
    )

    products.persistence.restore_into(products.session)

    assert products.session.health is SessionHealth.QUARANTINED
    durable = journal.get_session(identity.workspace_id, products.session.session_id)
    assert durable is not None
    assert durable.health is SessionHealth.QUARANTINED
    assert products.session.messages == ()
