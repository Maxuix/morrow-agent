from __future__ import annotations

import hashlib

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.preferences.context import PreferenceReviewContextBuilder
from morrow.application.turns import SessionPersistence
from morrow.core.domain import DurableSession, sha256_digest
from morrow.core.models import ModelRef
from morrow.core.preference_documents import PreferenceReviewSnapshot
from morrow.core.preference_persistence_models import PreferenceReviewJobStatus
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.testing import FixedClock, FixedIdSource, ScriptedModelProvider, make_context_builder


def _open(tmp_path):
    clock = FixedClock()
    store = OperationalStore(tmp_path / "state", clock=clock, maintenance_timeout=0)
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    return store, handle, journal, clock


@pytest.mark.asyncio
async def test_terminal_turn_commit_enqueues_one_frozen_preference_review(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        ids = FixedIdSource()
        session = Session(session_id="ses_1")
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=ids,
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            runtime_instance_id="review-test",
            clock=clock,
        )
        persistence.attach(session)
        loop = AgentLoop(
            ScriptedModelProvider(["请以后默认先给出可运行代码。"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
        )

        events = [
            item async for item in loop.run_task(session, "以后回答代码问题先给出可运行代码。")
        ]

        assert events[-1].type == "turn.completed"
        jobs = journal.list_preference_review_jobs("ws_1")
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status is PreferenceReviewJobStatus.PENDING
        assert job.turn_id == persistence.current_turn_id
        snapshot = PreferenceReviewSnapshot.model_validate_json(job.active_snapshot_json)
        encoded = snapshot.serialized_bytes
        assert job.active_snapshot_count == len(snapshot.entries)
        assert job.active_snapshot_bytes == len(encoded)
        assert job.active_snapshot_digest == hashlib.sha256(encoded).hexdigest()

        evidence = journal.get_preference_evidence_for_job("ws_1", job.job_id)
        assert evidence is not None
        assert evidence.content_digest == sha256_digest("以后回答代码问题先给出可运行代码。")
        assert evidence.excerpt_redacted == "以后回答代码问题先给出可运行代码。"

        context = PreferenceReviewContextBuilder(
            journal=journal,
            workspace_id="ws_1",
        ).build(job=job, evidence=evidence)
        assert context.current_user_message == "以后回答代码问题先给出可运行代码。"

        replay = journal.transact(
            lambda txn: persistence.preference_reviews.enqueue_terminal_turn(
                txn,
                session=session,
                turn_id=persistence.current_turn_id,
                conversation=session.log.snapshot(),
                terminal=session.log.snapshot().public_turns(require_closed=True)[-1].terminal,
            )
        )
        assert replay.reason == "replayed"
        assert replay.job == job
        assert journal.count_preference_review_jobs("ws_1") == 1
    finally:
        handle.close()
        store.layout.database.exists()


@pytest.mark.asyncio
async def test_terminal_enqueue_failure_rolls_back_history_and_job(tmp_path, monkeypatch):
    store, handle, journal, clock = _open(tmp_path)
    try:
        session = Session(session_id="ses_1")
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=FixedIdSource(),
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            runtime_instance_id="review-rollback-test",
            clock=clock,
        )
        persistence.attach(session)

        def fail(*_args, **_kwargs):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "injected enqueue failure")

        monkeypatch.setattr(persistence.preference_reviews, "enqueue_terminal_turn", fail)
        loop = AgentLoop(
            ScriptedModelProvider(["完成。"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
        )

        with pytest.raises(StorageError, match="injected enqueue failure"):
            [item async for item in loop.run_task(session, "请完成这个请求。")]

        assert len(journal.load_records("ws_1", "ses_1")) == 2
        assert journal.list_preference_review_jobs("ws_1") == ()
        assert session.log.has_active_turn
    finally:
        handle.close()
        store.layout.database.exists()


@pytest.mark.asyncio
async def test_oversized_snapshot_skips_review_without_rolling_back_turn(tmp_path, monkeypatch):
    store, handle, journal, clock = _open(tmp_path)
    try:
        session = Session(session_id="ses_1")
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=FixedIdSource(),
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            runtime_instance_id="review-budget-test",
            clock=clock,
        )
        persistence.attach(session)
        monkeypatch.setattr(
            "morrow.application.preferences.jobs.snapshot_from_documents",
            lambda *_args: (_ for _ in ()).throw(ValueError("snapshot too large")),
        )
        loop = AgentLoop(
            ScriptedModelProvider(["完成。"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
        )

        events = [item async for item in loop.run_task(session, "请完成这个请求。")]

        assert events[-1].type == "turn.completed"
        assert journal.list_preference_review_jobs("ws_1") == ()
        assert not session.log.has_active_turn
    finally:
        handle.close()
        store.layout.database.exists()


@pytest.mark.asyncio
async def test_invalid_evidence_excerpt_skips_review_without_rolling_back_turn(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        session = Session(session_id="ses_1")
        persistence = SessionPersistence(
            workspace_id="ws_1",
            journal=journal,
            store_session=handle,
            id_source=FixedIdSource(),
            model=ModelRef(provider_id="p", model_id="m"),
            run_policy=make_context_builder().run_policy,
            runtime_instance_id="review-evidence-validation-test",
            clock=clock,
        )
        persistence.attach(session)
        loop = AgentLoop(
            ScriptedModelProvider(["完成。"]),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            id_source=FixedIdSource(),
        )

        events = [item async for item in loop.run_task(session, "ordinary\u0001request")]

        assert events[-1].type == "turn.completed"
        assert journal.list_preference_review_jobs("ws_1") == ()
        assert not session.log.has_active_turn
        assert [record.kind for record in journal.load_records("ws_1", "ses_1")] == [
            "message",
            "message",
            "terminal",
        ]
    finally:
        handle.close()
        store.layout.database.exists()
