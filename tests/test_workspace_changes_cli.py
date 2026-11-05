from typer.testing import CliRunner

from morrow.interfaces.cli import app
from morrow.interfaces.core_client import CoreClient


def test_cli_list_and_explicit_preview_digest(monkeypatch):
    calls = []

    class Client:
        workspace_id = "ws_one"

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            return {"preview_digest": "a" * 64, "items": []}

    monkeypatch.setattr(CoreClient, "discover", lambda *a: Client())
    runner = CliRunner()
    result = runner.invoke(app, ["changes", "list", "--session", "ses_one", "--task", "task_one"])
    assert result.exit_code == 0, result.output
    assert calls[0][1].endswith("/sessions/ses_one/changes?task_run_id=task_one")
    result = runner.invoke(
        app,
        [
            "changes",
            "restore",
            "--session",
            "ses_one",
            "--preview",
            "rst_one",
            "--preview-digest",
            "b" * 64,
            "--command-id",
            "cmd_one",
            "--yes",
        ],
    )
    assert result.exit_code == 2
    assert all(call[0] != "POST" for call in calls)
    result = runner.invoke(
        app,
        [
            "changes",
            "restore",
            "--session",
            "ses_one",
            "--preview",
            "rst_one",
            "--preview-digest",
            "a" * 64,
            "--command-id",
            "cmd_one",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1][2]["command_id"] == "cmd_one"
