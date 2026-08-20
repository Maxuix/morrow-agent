"""Deterministic context checkpoint and immutable Session fork coverage."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

import morrow.bootstrap as bootstrap
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.checkpoints import (
    ContextBoundaryError,
    ContextCheckpointService,
    SessionForkService,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.artifacts import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRetention,
    ArtifactSensitivity,
    ArtifactState,
)
from morrow.core.context import CheckpointOmissionReason, ContextCheckpoint
from morrow.core.domain import ArtifactReference, DurableSession, TaskRunStatus, sha256_digest
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import AssistantMessage, FinishReason, ModelRef, UserMessage
from morrow.runtime.conversation import ConversationLog
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.session import Session
from morrow.testing import (
    FixedClock,
    FixedIdSource,
    ScriptedModelProvider,
    make_context_builder,
)


def _journal(tmp_path: Path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    opened = store.initialize()
    journal = SqliteOperationalJournal(opened)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    return store, opened, journal


def _closed_history(journal: SqliteOperationalJournal, *, session_id: str = "ses_1"):
    log = ConversationLog()
    writer = DurableConversationWriter(
        log,
        journal,
        workspace_id="ws_1",
        session_id=session_id,
        id_source=FixedIdSource(),
    )
    for index in range(2):
        writer.commit(log.plan_begin_turn(UserMessage(content=f"request-{index}")))
        writer.commit(log.plan_append_assistant(AssistantMessage(content=f"answer-{index}")))
        writer.commit(log.plan_finish_turn(FinishReason.STOP))
    return log


def test_checkpoint_is_bounded_deterministic_and_does_not_duplicate_history(tmp_path):
    _store, opened, journal = _journal(tmp_path)
    try:
        log = _closed_history(journal)
        service = ContextCheckpointService(
            journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock()
        )
        checkpoint = service.create("ses_1", retain_recent_turns=1, checkpoint_id="chk_1")
        writer = DurableConversationWriter(
            log,
            journal,
            workspace_id="ws_1",
            session_id="ses_1",
            id_source=FixedIdSource(),
        )
        writer.id_source.counts["rec"] = 6
        writer.commit(log.plan_begin_turn(UserMessage(content="later request")))
        writer.commit(log.plan_append_assistant(AssistantMessage(content="later answer")))
        writer.commit(log.plan_finish_turn(FinishReason.STOP))
        regenerated = service.regenerate("chk_1")
        assert service.projection_digest(checkpoint) == service.projection_digest(regenerated)
        assert checkpoint.source_end_position == 7
        assert checkpoint.omitted_sections[0].reason is CheckpointOmissionReason.OLDER_TURN
        assert len(journal.load_records("ws_1", "ses_1")) == 9
        assert journal.get_context_checkpoint("ws_1", "chk_1") == checkpoint
        assert "request-0" not in service.render_projection(checkpoint)
    finally:
        opened.close()


def test_checkpoint_requires_a_closed_boundary_and_fork_has_no_parent_copy(tmp_path):
    _store, opened, journal = _journal(tmp_path)
    try:
        log = _closed_history(journal)
        service = ContextCheckpointService(
            journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock()
        )
        checkpoint = service.create("ses_1", retain_recent_turns=1, checkpoint_id="chk_1")
        fork = SessionForkService(
            journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock()
        )
        child = fork.fork(
            "ses_1",
            checkpoint_id=checkpoint.checkpoint_id,
            reason="try branch",
            child_session_id="ses_child",
        )
        assert child.parent_session_id == "ses_1"
        assert child.current_task_run_id is None
        assert journal.load_records("ws_1", child.session_id) == ()
        assert journal.load_effective_records("ws_1", child.session_id) == journal.load_records(
            "ws_1", "ses_1"
        )

        child_log = restore_conversation_log(journal, "ws_1", child.session_id)
        child_writer = DurableConversationWriter(
            child_log,
            journal,
            workspace_id="ws_1",
            session_id=child.session_id,
            id_source=FixedIdSource(),
        )
        child_writer.id_source.counts["rec"] = 6
        child_writer.commit(child_log.plan_begin_turn(UserMessage(content="child request")))
        child_writer.commit(
            child_log.plan_append_assistant(AssistantMessage(content="child answer"))
        )
        child_writer.commit(child_log.plan_finish_turn(FinishReason.STOP))
        assert len(journal.load_records("ws_1", child.session_id)) == 3
        assert len(journal.load_effective_records("ws_1", child.session_id)) == 9
        assert len(journal.load_records("ws_1", "ses_1")) == 6
        assert log.snapshot().records[-1].sequence == 6
    finally:
        opened.close()


@pytest.mark.asyncio
async def test_fork_restored_through_production_bootstrap_can_complete_own_turn(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"
    model = ModelRef(provider_id="p", model_id="m")

    parent_app = build_application(
        state_root=state_root,
        credentials=MemoryCredentialStore(),
    )
    identity = parent_app.workspace_service.confirm(parent_app.workspace_service.resolve(project))
    parent = build_session_application(
        parent_app,
        identity,
        provider=ScriptedModelProvider(["parent answer"]),
        model=model,
    )
    parent_session_id = parent.session.session_id
    try:
        await parent.orchestrator.dispatch("parent request")
        parent_records = parent.persistence.journal.load_records(
            identity.workspace_id, parent_session_id
        )
        checkpoint = parent.api.create_checkpoint(
            parent_session_id,
            checkpoint_id="chk_restart",
            command_id="cmd_checkpoint",
        ).value
        child = parent.api.fork_session(
            parent_session_id,
            checkpoint_id=checkpoint.checkpoint_id,
            reason="continue independently",
            child_session_id="ses_child",
            command_id="cmd_fork",
        ).value
        assert child.current_task_run_id is None
        assert (
            parent.persistence.journal.load_records(identity.workspace_id, child.session_id) == ()
        )
    finally:
        parent.persistence.close()

    resumed_app = build_application(
        state_root=state_root,
        credentials=MemoryCredentialStore(),
    )
    resumed_identity = resumed_app.workspace_service.confirm(
        resumed_app.workspace_service.resolve(project)
    )
    resumed = build_session_application(
        resumed_app,
        resumed_identity,
        provider=ScriptedModelProvider(["child answer"]),
        model=model,
        resume_session_id=child.session_id,
    )
    try:
        assert [message.content for message in resumed.session.messages] == [
            "parent request",
            "parent answer",
        ]

        await resumed.orchestrator.dispatch("child request")

        child_row = resumed.persistence.journal.get_session(
            resumed_identity.workspace_id, child.session_id
        )
        assert child_row is not None
        assert child_row.current_task_run_id is not None
        child_task = resumed.tasks.get(child_row.current_task_run_id)
        assert child_task is not None
        assert child_task.session_id == child.session_id
        assert child_task.status is TaskRunStatus.READY_FOR_ACCEPTANCE
        assert [message.content for message in resumed.session.messages] == [
            "parent request",
            "parent answer",
            "child request",
            "child answer",
        ]
        assert (
            len(
                resumed.persistence.journal.load_records(
                    resumed_identity.workspace_id, child.session_id
                )
            )
            == 3
        )
        assert (
            resumed.persistence.journal.load_records(
                resumed_identity.workspace_id, parent_session_id
            )
            == parent_records
        )
    finally:
        resumed.persistence.close()


@pytest.mark.asyncio
async def test_production_composition_uses_store_clock_for_session_artifact_checkpoint_and_fork(
    tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"
    stamp = datetime(2040, 2, 3, 4, 5, 6, tzinfo=UTC)
    clock = FixedClock(stamp)
    store = OperationalStore(state_root, clock=clock, maintenance_timeout=0)
    handle = store.initialize()
    monkeypatch.setattr(bootstrap, "_open_operational_store", lambda _app: handle)

    app = build_application(
        state_root=state_root,
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    products = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["parent answer"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    try:
        session_id = products.session.session_id
        created = products.api.get_session(session_id)
        assert created.created_at == stamp
        assert created.updated_at == stamp

        artifact = products.artifacts.publish_bytes(
            b"clock evidence",
            kind=ArtifactKind.TEST_REPORT,
            session_id=session_id,
        )
        await products.orchestrator.dispatch("parent request")
        checkpoint = products.api.create_checkpoint(
            session_id,
            checkpoint_id="chk_clock",
            command_id="cmd_checkpoint_clock",
        ).value
        child = products.api.fork_session(
            session_id,
            checkpoint_id=checkpoint.checkpoint_id,
            child_session_id="ses_clock_child",
            command_id="cmd_fork_clock",
        ).value

        assert artifact.created_at == stamp
        assert artifact.updated_at == stamp
        assert checkpoint.created_at == stamp
        assert child.created_at == stamp
        assert child.updated_at == stamp
    finally:
        products.persistence.close()


def test_fork_rejects_an_open_turn_and_context_builder_projects_checkpoint(tmp_path):
    _store, opened, journal = _journal(tmp_path)
    try:
        log = _closed_history(journal)
        checkpoint = ContextCheckpointService(
            journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock()
        ).create("ses_1", retain_recent_turns=1, checkpoint_id="chk_1")
        writer = DurableConversationWriter(
            log,
            journal,
            workspace_id="ws_1",
            session_id="ses_1",
            id_source=FixedIdSource(),
        )
        writer.id_source.counts["rec"] = 6
        writer.commit(log.plan_begin_turn(UserMessage(content="current request")))
        with pytest.raises(ContextBoundaryError):
            SessionForkService(
                journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock()
            ).fork("ses_1", checkpoint_id=checkpoint.checkpoint_id)

        session = Session(session_id="runtime")
        session.log = restore_conversation_log(journal, "ws_1", "ses_1")
        context = make_context_builder().build(session, checkpoint=checkpoint)
        non_system = [message.content for message in context.messages if message.role != "system"]
        assert non_system == ["request-1", "answer-1", "current request"]
        assert context.checkpoint_id == "chk_1"
        assert "request-0" not in "\n".join(
            message.content for message in context.messages if message.role == "system"
        )
    finally:
        opened.close()


def test_checkpoint_projection_is_only_metadata_and_parent_is_workspace_scoped(tmp_path):
    _store, opened, journal = _journal(tmp_path)
    try:
        _closed_history(journal)
        checkpoint = ContextCheckpointService(
            journal, workspace_id="ws_1", id_source=FixedIdSource(), clock=FixedClock()
        ).create("ses_1", checkpoint_id="chk_1")
        assert checkpoint.artifact_refs == ()
        assert journal.list_context_checkpoints("ws_1", "ses_1") == (checkpoint,)
        assert journal.get_context_checkpoint("ws_2", "chk_1") is None
        assert journal.load_records("ws_1", "ses_1")[0].payload["content"] == "request-0"
    finally:
        opened.close()


def test_checkpoint_fault_boundaries_are_recoverable(tmp_path):
    _store, opened, journal = _journal(tmp_path)
    try:
        _closed_history(journal)
        before = ContextCheckpointService(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(),
            faults=OnceFaultInjector(FaultPoint.CHECKPOINT_BEFORE_COMMIT),
        )
        with pytest.raises(InjectedFault):
            before.create("ses_1", checkpoint_id="chk_before")
        assert journal.get_context_checkpoint("ws_1", "chk_before") is None

        after = ContextCheckpointService(
            journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock(),
            faults=OnceFaultInjector(FaultPoint.CHECKPOINT_AFTER_COMMIT),
        )
        with pytest.raises(InjectedFault):
            after.create("ses_1", checkpoint_id="chk_after")
        assert journal.get_context_checkpoint("ws_1", "chk_after") is not None
    finally:
        opened.close()


def test_checkpoint_keeps_a_missing_artifact_as_a_reference_without_fabricating_excerpt(tmp_path):
    _store, opened, journal = _journal(tmp_path)
    try:
        _closed_history(journal)
        metadata = ArtifactMetadata(
            artifact_id="art_missing",
            workspace_id="ws_1",
            kind=ArtifactKind.CONTEXT_SUMMARY,
            sensitivity=ArtifactSensitivity.REDACTED,
            state=ArtifactState.STAGING,
            retention=ArtifactRetention.STANDARD,
            sha256=sha256_digest(b"missing"),
            byte_size=7,
            excerpt="known bounded excerpt",
            created_at=FixedClock().value,
            updated_at=FixedClock().value,
        )
        journal.reserve_artifact("ws_1", metadata)
        journal.save_artifact(
            "ws_1",
            metadata.model_copy(
                update={
                    "state": ArtifactState.MISSING,
                    "row_version": 2,
                    "updated_at": FixedClock().value,
                }
            ),
            expected_row_version=1,
        )
        checkpoint = ContextCheckpoint(
            checkpoint_id="chk_missing",
            workspace_id="ws_1",
            session_id="ses_1",
            source_end_record_id="rec_6",
            source_end_position=7,
            artifact_refs=(ArtifactReference(artifact_id="art_missing", role="evidence"),),
        )
        journal.put_context_checkpoint("ws_1", checkpoint)
        loaded = journal.get_context_checkpoint("ws_1", "chk_missing")
        assert loaded is not None
        assert loaded.artifact_refs == checkpoint.artifact_refs
        assert loaded.source_end_record_id == checkpoint.source_end_record_id
        assert journal.list_artifact_references("ws_1", "art_missing") == (
            ("art_missing", "context_checkpoint", "chk_missing", "evidence"),
        )
    finally:
        opened.close()
