import json
from datetime import UTC, datetime
from types import SimpleNamespace

from typer.testing import CliRunner

from morrow.application.preferences.jobs import PreferenceReviewJobView, PreferenceReviewStatusView
from morrow.application.preferences.worker import ReviewWorkerResult
from morrow.core.preference_persistence_models import PreferenceReviewJobStatus
from morrow.interfaces import cli as cli_module


def test_preference_inbox_typer_surface_is_separate_from_learning_inbox():
    result = CliRunner().invoke(cli_module.app, ["preferences", "inbox", "--help"])

    assert result.exit_code == 0
    assert "accept-many" in result.stdout
    assert "edit-and-accept" in result.stdout
    assert "review" in result.stdout
    assert "jobs" in result.stdout
    assert "run-pending" in result.stdout
    assert "retry" in result.stdout
    assert "learning" not in result.stdout


def test_preference_inbox_jobs_json_serializes_datetimes(monkeypatch):
    job = PreferenceReviewJobView(
        job_id="prjob_1",
        session_id="ses_1",
        turn_id="turn_1",
        review_version=1,
        status=PreferenceReviewJobStatus.PENDING,
        source_global_revision=0,
        source_workspace_revision=0,
        active_snapshot_count=0,
        active_snapshot_bytes=0,
        attempt_count=0,
        row_version=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_at=None,
        completed_at=None,
        lease_expires_at=None,
        failure_code=None,
        reviewer_provider_id=None,
        reviewer_model_id=None,
        reviewer_prompt_version="preference-v1",
        reviewer_schema_version="preference-v1",
    )

    class FakeApi:
        def list_preference_review_jobs(self, **_kwargs):
            return SimpleNamespace(items=(job,), next_cursor=None)

    monkeypatch.setattr(
        cli_module,
        "_state_services",
        lambda **_kwargs: (None, "handle", FakeApi(), None, None),
    )
    monkeypatch.setattr(cli_module, "_close_state", lambda _handle: None)

    result = CliRunner().invoke(
        cli_module.app,
        ["preferences", "inbox", "jobs", "--json", "--workspace-id", "ws_1"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["items"][0]["created_at"] == "2026-01-01T00:00:00+00:00"


def test_preference_inbox_run_pending_reports_durable_remaining_work(monkeypatch):
    class FakeApi:
        async def run_pending_preference_reviews(self, *, limit):
            assert limit == 1
            return (ReviewWorkerResult(status="deferred", error_code="provider_unavailable"),)

        def preference_review_status(self):
            return PreferenceReviewStatusView(pending=1)

    monkeypatch.setattr(
        cli_module,
        "_state_services",
        lambda **_kwargs: (None, "handle", FakeApi(), None, None),
    )
    monkeypatch.setattr(cli_module, "_close_state", lambda _handle: None)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "preferences",
            "inbox",
            "run-pending",
            "--limit",
            "1",
            "--json",
            "--workspace-id",
            "ws_1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["daemon"] is False
    assert payload["attempted"] == 1
    assert payload["results"][0]["status"] == "deferred"
    assert payload["status"]["pending"] == 1


def test_preference_inbox_review_reports_worker_result(monkeypatch):
    class FakeApi:
        async def run_preference_review(self, job_id):
            assert job_id == "prjob_1"
            return ReviewWorkerResult(status="completed", proposal_count=2)

    monkeypatch.setattr(
        cli_module,
        "_state_services",
        lambda **_kwargs: (None, "handle", FakeApi(), None, None),
    )
    monkeypatch.setattr(cli_module, "_close_state", lambda _handle: None)

    result = CliRunner().invoke(
        cli_module.app,
        [
            "preferences",
            "inbox",
            "review",
            "prjob_1",
            "--json",
            "--workspace-id",
            "ws_1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "completed"
    assert payload["proposal_count"] == 2
