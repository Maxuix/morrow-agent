from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.preferences.context import PreferenceReviewContextError
from morrow.application.preferences.worker import ReviewWorker
from morrow.application.turns import SessionPersistence
from morrow.core.domain import DurableSession
from morrow.core.models import ModelRef
from morrow.core.preference_models import PreferenceOperation
from morrow.core.preference_review import PreferenceReviewOutput
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.testing import (
    FixedClock,
    FixedIdSource,
    ScriptedModelProvider,
    ScriptedPreferenceReviewer,
    make_context_builder,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MODEL = ModelRef(provider_id="test", model_id="reviewer")


def _open(tmp_path):
    clock = FixedClock(NOW)
    store = OperationalStore(tmp_path / "state", clock=clock, maintenance_timeout=0)
    handle = store.initialize()
    return store, handle, SqliteOperationalJournal(handle), clock


@pytest.mark.parametrize("timeout_seconds", (0, 121, float("nan"), float("inf"), True))
def test_worker_rejects_unbounded_timeout(timeout_seconds):
    with pytest.raises(ValueError):
        ReviewWorker(
            journal=None,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=lambda: NOW,
            timeout_seconds=timeout_seconds,
        )


def _persistence(journal, handle, *, workspace_id: str, session_id: str, ids):
    journal.create_session(DurableSession(session_id=session_id, workspace_id=workspace_id))
    session = Session(session_id=session_id)
    persistence = SessionPersistence(
        workspace_id=workspace_id,
        journal=journal,
        store_session=handle,
        id_source=ids,
        model=MODEL,
        run_policy=make_context_builder().run_policy,
        runtime_instance_id=f"worker-{session_id}",
        clock=journal,
    )
    persistence.attach(session)
    return session, persistence


async def _enqueue(
    journal,
    handle,
    *,
    workspace_id: str = "ws_1",
    session_id: str = "ses_1",
    ids=None,
    loop_ids=None,
):
    ids = ids or FixedIdSource()
    loop_ids = loop_ids or FixedIdSource()
    session, persistence = _persistence(
        journal,
        handle,
        workspace_id=workspace_id,
        session_id=session_id,
        ids=ids,
    )
    loop = AgentLoop(
        ScriptedModelProvider(["已完成。"]),
        MODEL,
        make_context_builder(),
        id_source=loop_ids,
    )
    [item async for item in loop.run_task(session, "请以后先给出可运行代码。")]
    turn_id = persistence.current_turn_id
    assert turn_id is not None
    job = journal.get_preference_review_job_for_turn(workspace_id, turn_id)
    assert job is not None
    return job, session, persistence


class BlockingReviewer:
    prompt_version = "blocking-v1"
    schema_version = "blocking-schema-v1"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def review(self, context, *, model, timeout_seconds):
        del context, model, timeout_seconds
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return PreferenceReviewOutput()


class SerialReviewer:
    prompt_version = "serial-v1"
    schema_version = "serial-schema-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def review(self, context, *, model, timeout_seconds):
        del context, model, timeout_seconds
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
        return PreferenceReviewOutput()


class RaisingRunner:
    reviewer = ScriptedPreferenceReviewer()
    model = MODEL

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def run(self, job_id: str):
        del job_id
        raise self.error


@pytest.mark.asyncio
async def test_worker_claims_runs_and_finalizes_proposals(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        ids = FixedIdSource()
        job, _session, _persistence = await _enqueue(
            journal, handle, ids=ids, loop_ids=FixedIdSource()
        )
        evidence = journal.get_preference_evidence_for_job("ws_1", job.job_id)
        assert evidence is not None
        reviewer = ScriptedPreferenceReviewer(
            [
                PreferenceReviewOutput(
                    operations=(
                        PreferenceOperation(
                            operation="add",
                            scope="workspace",
                            statement="先给出可运行代码。",
                            evidence_ids=(evidence.evidence_id,),
                        ),
                    )
                )
            ]
        )
        result = await ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            reviewer=reviewer,
            model=MODEL,
        ).drain_once()

        assert result.status == "completed"
        assert result.job_id == job.job_id
        assert result.proposal_count == 1
        assert result.duplicate_count == 0
        stored = journal.get_preference_review_job("ws_1", job.job_id)
        assert stored is not None
        assert stored.status.value == "completed"
        assert stored.attempt_count == 1
        assert stored.lease_id is None
        assert stored.reviewer_provider_id == MODEL.provider_id
        assert journal.list_preference_proposals("ws_1", job_id=job.job_id, limit=10)
        assert len(reviewer.calls) == 1
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_worker_emits_only_sanitized_proposal_notice(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        job, _session, _persistence = await _enqueue(journal, handle)
        evidence = journal.get_preference_evidence_for_job("ws_1", job.job_id)
        assert evidence is not None
        reviewer = ScriptedPreferenceReviewer(
            [
                PreferenceReviewOutput(
                    operations=(
                        PreferenceOperation(
                            operation="add",
                            scope="workspace",
                            statement="先给出可运行代码。",
                            evidence_ids=(evidence.evidence_id,),
                        ),
                    )
                )
            ]
        )
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            reviewer=reviewer,
            model=MODEL,
        )

        result = await worker.drain_once()

        assert result.status == "completed"
        assert worker.drain_notices()[0].kind == "proposals"
        assert worker.drain_notices() == ()
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_worker_stop_leaves_lease_for_expiry_reclaim(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        job, _session, _persistence = await _enqueue(journal, handle)
        blocking = BlockingReviewer()
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            reviewer=blocking,
            model=MODEL,
            lease_seconds=10,
        )
        await worker.start()
        await blocking.started.wait()
        assert worker.running
        running = journal.get_preference_review_job("ws_1", job.job_id)
        assert running is not None
        assert running.status.value == "running"
        await worker.stop()
        assert not worker.running
        assert journal.get_preference_review_job("ws_1", job.job_id).status.value == "running"

        clock.value += timedelta(seconds=11)
        replacement = ScriptedPreferenceReviewer([PreferenceReviewOutput()])
        result = await ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            reviewer=replacement,
            model=MODEL,
            lease_seconds=10,
        ).drain_once()

        assert result.status == "completed"
        stored = journal.get_preference_review_job("ws_1", job.job_id)
        assert stored is not None
        assert stored.attempt_count == 2
        assert stored.status.value == "completed"
        assert len(replacement.calls) == 1
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_worker_serializes_jobs_within_one_workspace(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        ids = FixedIdSource()
        loop_ids = FixedIdSource()
        first, _session, _persistence = await _enqueue(journal, handle, ids=ids, loop_ids=loop_ids)
        second, _session, _persistence = await _enqueue(
            journal,
            handle,
            session_id="ses_2",
            ids=ids,
            loop_ids=loop_ids,
        )
        reviewer = SerialReviewer()
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            reviewer=reviewer,
            model=MODEL,
        )
        first_task = asyncio.create_task(worker.drain_once())
        await reviewer.first_started.wait()
        second_task = asyncio.create_task(worker.drain_once())
        await asyncio.sleep(0)
        assert reviewer.calls == 1

        reviewer.release_first.set()
        first_result, second_result = await asyncio.gather(first_task, second_task)
        assert {first_result.job_id, second_result.job_id} == {first.job_id, second.job_id}
        assert first_result.status == "completed"
        assert second_result.status == "completed"
        assert reviewer.calls == 2
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_worker_retries_provider_failures_and_exhausts_after_three_attempts(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        job, _session, _persistence = await _enqueue(journal, handle)
        reviewer = ScriptedPreferenceReviewer(
            [
                RuntimeError("provider one"),
                RuntimeError("provider two"),
                RuntimeError("provider three"),
            ]
        )
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            reviewer=reviewer,
            model=MODEL,
            lease_seconds=30,
        )

        first = await worker.drain_once()
        assert first.status == "deferred"
        assert first.error_code == "provider_unavailable"
        stored = journal.get_preference_review_job("ws_1", job.job_id)
        assert stored is not None
        assert stored.status.value == "running"
        assert stored.attempt_count == 1
        assert stored.lease_expires_at == clock.value + timedelta(seconds=5)

        clock.value += timedelta(seconds=5)
        second = await worker.drain_once()
        assert second.status == "deferred"
        stored = journal.get_preference_review_job("ws_1", job.job_id)
        assert stored is not None
        assert stored.attempt_count == 2
        assert stored.lease_expires_at == clock.value + timedelta(seconds=15)

        clock.value += timedelta(seconds=15)
        third = await worker.drain_once()
        assert third.status == "exhausted"
        assert third.error_code == "provider_unavailable"
        stored = journal.get_preference_review_job("ws_1", job.job_id)
        assert stored is not None
        assert stored.status.value == "exhausted"
        assert stored.attempt_count == 3
        assert stored.failure_code.value == "provider_unavailable"
        assert stored.lease_id is None
        assert len(reviewer.calls) == 3
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_worker_marks_context_budget_as_terminal_without_retry(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        job, _session, _persistence = await _enqueue(journal, handle)
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            runner=RaisingRunner(
                PreferenceReviewContextError("context_budget", "bounded context is too large")
            ),
            model=MODEL,
        )

        result = await worker.drain_once()

        assert result.status == "failed"
        assert result.error_code == "context_budget"
        stored = journal.get_preference_review_job("ws_1", job.job_id)
        assert stored is not None
        assert stored.status.value == "failed"
        assert stored.failure_code.value == "context_budget"
        assert stored.attempt_count == 1
        assert stored.lease_id is None
        assert journal.list_claimable_preference_review_jobs("ws_1") == ()
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_worker_manual_retry_resets_only_retryable_terminal_jobs(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        job, _session, _persistence = await _enqueue(journal, handle)
        worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            runner=RaisingRunner(RuntimeError("provider unavailable")),
            model=MODEL,
            lease_seconds=30,
        )

        await worker.drain_once()
        clock.value += timedelta(seconds=5)
        await worker.drain_once()
        clock.value += timedelta(seconds=15)
        exhausted = await worker.drain_once()
        assert exhausted.status == "exhausted"
        assert worker.drain_notices()[0].kind == "exhausted"

        pending = worker.retry(job.job_id)
        assert pending.status.value == "pending"
        assert pending.attempt_count == 0
        assert pending.failure_code is None
        assert pending.completed_at is None

        terminal_worker = ReviewWorker(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
            runner=RaisingRunner(PreferenceReviewContextError("context_budget", "bounded")),
            model=MODEL,
        )
        failed = await terminal_worker.drain_once()
        assert failed.status == "failed"
        with pytest.raises(ValueError, match="not retryable"):
            terminal_worker.retry(job.job_id)
    finally:
        handle.close()
        assert store.layout.database.exists()


@pytest.mark.asyncio
async def test_preference_review_job_queries_omit_frozen_context_and_reviewer_details(tmp_path):
    store, handle, journal, clock = _open(tmp_path)
    try:
        job, _session, _persistence = await _enqueue(journal, handle)
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=clock.now,
        )

        page = api.list_preference_review_jobs(limit=10)
        view = page.items[0]
        shown = api.get_preference_review_job_view(job.job_id)
        status = api.preference_review_status()

        assert view.job_id == job.job_id
        assert shown is not None
        assert shown.evidence_id is not None
        assert not hasattr(view, "active_snapshot_json")
        assert not hasattr(view, "reviewer_provider_id")
        assert status.pending == 1
        assert status.total == 1
    finally:
        handle.close()
        assert store.layout.database.exists()
