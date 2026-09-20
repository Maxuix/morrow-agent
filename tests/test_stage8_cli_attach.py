import json
import os

import pytest
from typer.testing import CliRunner

from morrow.interfaces.cli import app
from morrow.interfaces.core_client import CoreClient, CoreConnectionError, _read, publish_connection


def test_private_discovery_permissions_cleanup_and_unsafe_address(tmp_path):
    with publish_connection(
        tmp_path, "ws_test", "http://127.0.0.1:12345", "synthetic-private-token"
    ):
        path = tmp_path / "core-connections/ws_test.json"
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert _read(path)["workspace_id"] == "ws_test"
        os.chmod(path, 0o644)
        with pytest.raises(CoreConnectionError, match="private"):
            _read(path)
        os.chmod(path, 0o600)
    assert not path.exists()
    for address in (
        "https://example.test",
        "http://example.test:80",
        "http://u:p@127.0.0.1:80",
        "http://127.0.0.1:80/bad",
    ):
        with pytest.raises(CoreConnectionError):
            CoreClient({"base_url": address, "token": "never-print", "workspace_id": "ws_test"})


def test_attach_uses_scoped_api_external_id_and_no_writer(monkeypatch):
    calls = []

    class FakeClient:
        workspace_id = "ws_default"

        def request(self, method, path, body=None):
            calls.append((method, path, body))
            if path.endswith("/interactions"):
                return {
                    "receipt": {"status": "queued", "client_message_id": body["client_message_id"]}
                }
            return {"session": {"session_id": "ses_test"}}

    monkeypatch.setattr(CoreClient, "discover", lambda *_: FakeClient())
    result = CliRunner().invoke(
        app,
        [
            "attach",
            "--workspace-id",
            "ws_second",
            "--session-id",
            "ses_test",
            "--message",
            "CLI hello",
            "--client-message-id",
            "cli.once",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "POST",
        "/v1/workspaces/ws_second/sessions/ses_test/interactions",
        {"client_message_id": "cli.once", "text": "CLI hello"},
    )
    assert json.loads(result.stdout)["receipt"]["status"] == "queued"
    assert "cli.once" in result.stderr
