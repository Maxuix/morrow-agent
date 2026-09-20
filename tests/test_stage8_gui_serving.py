"""Stage 8 Subplan 4: GUI static serving and `morrow gui` wiring tests.

The prebuilt GUI bundle is served read-only from the same loopback origin as
the API: GET/HEAD only, confined to the asset root with an extension
allowlist, security headers on every response, and token-free local GUI API
access. Headless `/v1/*` keeps its full token gate.
"""

from __future__ import annotations

import webbrowser
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.core.capabilities import PermissionPreset
from morrow.interfaces import gui_cli
from morrow.interfaces.cli import app as cli_app
from morrow.interfaces.serve_cli import public_base_url
from morrow.server.app import create_asgi_app
from morrow.server.static import gui_assets_available
from test_stage8_core_api import SERVE_TOKEN, ServerFixture

INDEX_HTML = b'<!doctype html><html><head><title>Morrow</title></head><body><div id="root"></div></body></html>'
APP_JS = b"console.log('morrow gui');"


class GuiFixture(ServerFixture):
    """ServerFixture whose ASGI app also mounts a prebuilt GUI bundle."""

    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        self.gui_dir = tmp_path / "gui_static"
        (self.gui_dir / "assets").mkdir(parents=True)
        (self.gui_dir / "index.html").write_bytes(INDEX_HTML)
        (self.gui_dir / "assets" / "app.A1B2C3.js").write_bytes(APP_JS)
        (self.gui_dir / "assets" / "inter.woff2").write_bytes(b"wOF2fake")
        (self.gui_dir / "NOTICES.md").write_bytes(b"# notices\n")
        (self.gui_dir / "payload.sh").write_bytes(b"echo nope")
        self.client = CoreApiVerificationClient(
            create_asgi_app(
                self.host,
                auth_token=SERVE_TOKEN,
                ws_ping_seconds=7200,
                gui_static_dir=self.gui_dir,
            ),
            token=SERVE_TOKEN,
        )


@pytest.fixture
def gfx(tmp_path):
    fixture = GuiFixture(tmp_path)
    yield fixture
    fixture.close()


def _headers(response):
    return dict(response.headers)


async def test_static_index_served_without_token_and_hardened(gfx):
    response = await gfx.client.get("/", token="")
    assert response.status == 200
    assert response.body == INDEX_HTML
    headers = _headers(response)
    assert headers["content-type"].startswith("text/html")
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "script-src 'self'" in headers["content-security-policy"]
    assert "style-src 'self' 'unsafe-inline'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-cache"


async def test_hashed_assets_are_immutable_and_typed(gfx):
    response = await gfx.client.get("/assets/app.A1B2C3.js", token="")
    assert response.status == 200
    assert response.body == APP_JS
    headers = _headers(response)
    assert headers["content-type"].startswith("text/javascript")
    assert "immutable" in headers["cache-control"]
    font = await gfx.client.get("/assets/inter.woff2", token="")
    assert font.status == 200
    assert _headers(font)["content-type"] == "font/woff2"
    notices = await gfx.client.get("/NOTICES.md", token="")
    assert notices.status == 200


async def test_static_surface_is_confined_and_read_only(gfx):
    traversal = await gfx.client.get("/../pyproject.toml", token="")
    assert traversal.status == 404
    nested = await gfx.client.get("/assets/../../pyproject.toml", token="")
    assert nested.status == 404
    disallowed = await gfx.client.get("/payload.sh", token="")
    assert disallowed.status == 404
    missing = await gfx.client.get("/assets/nope.js", token="")
    assert missing.status == 404
    posted = await gfx.client.post("/", {})
    assert posted.status == 404
    evil = await gfx.client.get("/", token="", origin="https://evil.example")
    assert evil.status == 404
    head = await gfx.client.request("HEAD", "/", token="")
    assert head.status == 200
    assert head.body == b""
    assert _headers(head)["content-length"] == str(len(INDEX_HTML))


async def test_gui_api_does_not_require_token_but_keeps_origin_guard(gfx):
    missing = await gfx.client.get("/v1/meta", token="")
    assert missing.status == 200
    assert "content-security-policy" in _headers(missing)
    no_origin_mutation = await gfx.client.post("/v1/sessions", {}, token="")
    assert no_origin_mutation.status == 403
    local_mutation = await gfx.client.post("/v1/sessions", {}, token="", origin="http://127.0.0.1")
    assert local_mutation.status == 200
    evil_api = await gfx.client.get("/v1/meta", token="", origin="https://evil.example")
    assert evil_api.status == 403


async def test_serve_mode_without_gui_still_rejects_unknown_paths(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        response = await fixture.client.get("/", token="")
        assert response.status == 404
        assert response.json()["error"]["code"] == "not_found"
    finally:
        fixture.close()


def test_gui_url_is_token_free():
    url = gui_cli.gui_url("http://127.0.0.1:43123")
    assert url == "http://127.0.0.1:43123/"
    assert "token=" not in url


def test_gui_assets_availability(tmp_path):
    assert gui_assets_available(tmp_path / "nope") is False
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert gui_assets_available(bundle) is False
    (bundle / "index.html").write_bytes(INDEX_HTML)
    assert gui_assets_available(bundle) is True


def test_gui_refuses_to_start_without_assets(tmp_path):
    result = CliRunner().invoke(
        cli_app,
        ["gui", "--gui-dir", str(tmp_path / "missing"), "--state-root", str(tmp_path / "state")],
    )
    assert result.exit_code == 2
    assert "pnpm --dir gui build" in result.output


def test_gui_announce_opens_browser_without_token(monkeypatch, tmp_path):
    captured = {}
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index.html").write_bytes(INDEX_HTML)
    application = SimpleNamespace(data_root=object())
    identity = SimpleNamespace(workspace_id="ws_gui")

    def fake_serve_core(**kwargs):
        captured["gui_static_dir"] = kwargs["gui_static_dir"]
        kwargs["announce"]("http://127.0.0.1:43999", "tok-xyz")

    opened = []
    monkeypatch.setattr(gui_cli, "build_application", lambda **_kwargs: application)
    monkeypatch.setattr(gui_cli, "_identity", lambda *_args: identity)
    monkeypatch.setattr(gui_cli, "_serve_core", fake_serve_core)
    monkeypatch.setattr(webbrowser, "open", opened.append)

    gui_cli.gui(
        port=0,
        bind="127.0.0.1",
        workspace_id=None,
        directory=tmp_path,
        state_root=tmp_path / "state",
        permission_mode=PermissionPreset.MANUAL,
        no_browser=False,
        gui_dir=bundle,
    )

    assert captured["gui_static_dir"] == bundle
    assert opened == ["http://127.0.0.1:43999/"]


def test_public_base_url_formats_loopback_addresses():
    assert public_base_url("127.0.0.1", 8787) == "http://127.0.0.1:8787"
    assert public_base_url("::1", 8787) == "http://[::1]:8787"


def test_gui_no_browser_prints_plain_url(monkeypatch, tmp_path, capsys):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index.html").write_bytes(INDEX_HTML)
    application = SimpleNamespace(data_root=object())
    identity = SimpleNamespace(workspace_id="ws_gui")

    def fake_serve_core(**kwargs):
        kwargs["announce"]("http://127.0.0.1:43999", "tok-xyz")

    monkeypatch.setattr(gui_cli, "build_application", lambda **_kwargs: application)
    monkeypatch.setattr(gui_cli, "_identity", lambda *_args: identity)
    monkeypatch.setattr(gui_cli, "_serve_core", fake_serve_core)
    monkeypatch.setattr(webbrowser, "open", lambda _url: None)
    gui_cli.gui(
        port=0,
        bind="127.0.0.1",
        workspace_id=None,
        directory=tmp_path,
        state_root=tmp_path / "state",
        permission_mode=PermissionPreset.MANUAL,
        no_browser=True,
        gui_dir=bundle,
    )
    output = capsys.readouterr().out
    assert "GUI: http://127.0.0.1:43999/" in output
    assert "token=" not in output


def test_gui_help_smoke():
    result = CliRunner().invoke(cli_app, ["gui", "--help"])
    assert result.exit_code == 0
    assert "--no-browser" in result.output


def test_gui_first_launch_registers_selected_directory_once_without_provider(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index.html").write_bytes(INDEX_HTML)
    project = tmp_path / "project"
    project.mkdir()
    captured = []

    def serve(**kwargs):
        application, identity = kwargs["application"], kwargs["identity"]
        config = application.global_store.load()
        assert config.value is None or config.value.providers == {}
        assert identity.path == str(project.resolve())
        entries = application.workspace_service._entries()
        assert list(entries.workspaces) == [identity.workspace_id]
        captured.append((identity.workspace_id, entries.revision))

    monkeypatch.setattr(gui_cli, "_serve_core", serve)
    args = [
        "gui",
        "--gui-dir",
        str(bundle),
        "--dir",
        str(project),
        "--state-root",
        str(tmp_path / "state"),
        "--no-browser",
    ]
    for _ in range(2):
        result = CliRunner().invoke(cli_app, args)
        assert result.exit_code == 0, result.output
    assert captured[0] == captured[1]
    result = CliRunner().invoke(cli_app, [*args, "--workspace-id", "ws_missing"])
    assert result.exit_code == 2
    assert len(captured) == 2
