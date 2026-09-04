"""Stage 8 Subplan 4: GUI static serving and `morrow gui` wiring tests.

The prebuilt GUI bundle is served read-only from the same loopback origin as
the API: GET/HEAD only, confined to the asset root with an extension
allowlist, security headers on every response, and the session token never
required (it lives in the URL fragment). `/v1/*` keeps its full token gate.
"""

from __future__ import annotations

import webbrowser
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.migrations import MigrationRegistry, production_registry
from morrow.adapters.state.operational import OperationalStore
from morrow.bootstrap import _open_operational_store, build_application
from morrow.core.capabilities import PermissionPreset
from morrow.core.store import SUPPORTED_SCHEMA_VERSION
from morrow.interfaces import gui_cli
from morrow.interfaces.cli import app as cli_app
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


async def test_api_gate_unchanged_alongside_static(gfx):
    missing = await gfx.client.get("/v1/meta", token="")
    assert missing.status == 401
    wrong = await gfx.client.get("/v1/meta", token="wrong")
    assert wrong.status == 401
    ok = await gfx.client.get("/v1/meta")
    assert ok.status == 200
    assert "content-security-policy" in _headers(ok)
    evil_api = await gfx.client.get("/v1/meta", origin="https://evil.example")
    assert evil_api.status == 403


async def test_serve_mode_without_gui_still_rejects_unknown_paths(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        response = await fixture.client.get("/", token="")
        assert response.status == 404
        assert response.json()["error"]["code"] == "not_found"
    finally:
        fixture.close()


def test_gui_url_carries_token_in_fragment():
    url = gui_cli.gui_url("http://127.0.0.1:43123", "tok123")
    assert url == "http://127.0.0.1:43123/#token=tok123"
    assert "token=" not in url.split("#", 1)[0]


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


def test_gui_announce_opens_browser_with_fragment_token(monkeypatch, tmp_path):
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
    assert opened == ["http://127.0.0.1:43999/#token=tok-xyz"]


def test_gui_help_smoke():
    result = CliRunner().invoke(cli_app, ["gui", "--help"])
    assert result.exit_code == 0
    assert "--no-browser" in result.output


def test_core_composition_upgrades_an_older_supported_store_before_queries(tmp_path):
    state_root = tmp_path / "state"
    current = production_registry()
    legacy = MigrationRegistry(supported_version=22)
    for version in range(1, 23):
        legacy.add(current.get(version))
    OperationalStore(state_root, registry=legacy).initialize().close()

    application = build_application(
        state_root=state_root,
        credentials=MemoryCredentialStore(),
    )
    handle = _open_operational_store(application)
    try:
        assert handle.schema_version == SUPPORTED_SCHEMA_VERSION
        assert (
            SqliteOperationalJournal(handle).agent_definitions.get_head("ws_gui", "builtin_direct")
            is None
        )
    finally:
        handle.close()

    backups = tuple((state_root / "backups" / "operational").glob("*.sqlite"))
    assert len(backups) == 1
