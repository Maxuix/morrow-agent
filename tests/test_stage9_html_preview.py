"""Stage 9: bounded local HTML preview collections, listener and routes."""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.request

import pytest

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.application.html_preview import (
    HtmlPreviewBuilder,
    PreviewRegistry,
    media_type_for,
    parse_import_map,
    resolve_reference,
)
from morrow.core.application import ApplicationError
from morrow.server.app import create_asgi_app
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from test_stage8_core_api import SERVE_TOKEN, ServerFixture, write_pair_source

# 离线守卫会拦掉所有 connect；预览 listener 是本进程的 loopback socket，
# 只有这一项验收需要真的连上去，所以单独恢复真实连接。
_REAL_CONNECT = socket.socket.connect
_REAL_CREATE_CONNECTION = socket.create_connection

INDEX = """<!doctype html>
<html><head>
<meta charset="utf-8">
<script type="importmap">{"imports": {"three": "/vendor/three.module.js"}}</script>
<link rel="stylesheet" href="style.css">
</head><body>
<div id="root"></div>
<img src="assets/bg.png" alt="">
<script type="module">
import { scene } from 'three'
import './app.js'
import 'leftpad'
</script>
</body></html>
"""


def _service(root):
    return WorkspaceFileService(WorkspacePathResolver(root))


def _write_collection(root):
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "vendor").mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "style.css").write_text(
        "@import 'theme.css';\nbody { background: url('assets/bg.png'); }\n", encoding="utf-8"
    )
    (root / "theme.css").write_text(":root { --ink: #111; }\n", encoding="utf-8")
    (root / "app.js").write_text(
        "import { scene } from './vendor/three.module.js'\n"
        "const points = await fetch('./data/points.json')\n"
        "const textureUrl = './assets/bg.png'\n"
        "export const ready = scene\n",
        encoding="utf-8",
    )
    (root / "vendor" / "three.module.js").write_text("export const scene = 1\n", encoding="utf-8")
    (root / "assets" / "bg.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 8)
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "points.json").write_text('{"points": []}\n', encoding="utf-8")


def test_html_preview_builds_a_bounded_static_collection(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _write_collection(root)
    builder = HtmlPreviewBuilder(_service(root), workspace_id="ws_local")
    bundle = builder.build("index.html")

    paths = {item.path for item in bundle.files}
    assert paths == {
        "index.html",
        "style.css",
        "theme.css",
        "assets/bg.png",
        "app.js",
        "vendor/three.module.js",
        # 字面量相对路径的本地资源也要收进集合，否则纹理/数据会在运行时 404。
        "data/points.json",
    }
    assert bundle.entry_path == "index.html"
    assert bundle.revision and len(bundle.revision) == 64
    assert bundle.total_bytes == sum(len(item.content) for item in bundle.files)
    assert [item for item in bundle.missing] == ["leftpad（导入映射未命中）"]
    assert media_type_for("vendor/three.module.js") == "text/javascript; charset=utf-8"
    assert media_type_for("assets/bg.png") == "image/png"
    assert media_type_for("unknown.xyz") == "application/octet-stream"


def test_html_preview_reports_references_outside_the_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "index.html").write_text(
        '<script src="../outside.js"></script><link href="/etc/passwd">', encoding="utf-8"
    )
    (root / "app.js").write_text("const missing = './assets/absent.bin'\n", encoding="utf-8")
    (root / "missing-ref.html").write_text('<script src="app.js"></script>', encoding="utf-8")
    (tmp_path / "outside.js").write_text("secret\n", encoding="utf-8")
    builder = HtmlPreviewBuilder(_service(root), workspace_id="ws_local")
    bundle = builder.build("index.html")

    assert {item.path for item in bundle.files} == {"index.html"}
    assert any("工作区外引用" in entry for entry in bundle.missing)

    literal = HtmlPreviewBuilder(_service(root), workspace_id="ws_local").build("missing-ref.html")
    assert {item.path for item in literal.files} == {"missing-ref.html", "app.js"}
    assert any("assets/absent.bin" in entry for entry in literal.missing)


def test_html_preview_refuses_an_entry_outside_the_workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")
    builder = HtmlPreviewBuilder(_service(root), workspace_id="ws_local")
    with pytest.raises(ApplicationError) as refused:
        builder.build(str(outside))
    assert refused.value.code.value in {"invalid", "cross_workspace"}
    assert str(root) not in refused.value.message


def test_html_preview_enforces_file_and_byte_limits(tmp_path, monkeypatch):
    from morrow.application import html_preview

    root = tmp_path / "workspace"
    root.mkdir()
    _write_collection(root)
    monkeypatch.setattr(html_preview, "MAX_PREVIEW_FILES", 2)
    bundle = HtmlPreviewBuilder(_service(root), workspace_id="ws_local").build("index.html")
    assert len(bundle.files) == 2
    assert any("文件上限" in entry for entry in bundle.missing)

    monkeypatch.setattr(html_preview, "MAX_PREVIEW_FILES", 128)
    monkeypatch.setattr(html_preview, "MAX_PREVIEW_BYTES", 64)
    with pytest.raises(ApplicationError) as refused:
        HtmlPreviewBuilder(_service(root), workspace_id="ws_local").build("index.html")
    assert "20 MiB" in refused.value.message


def test_preview_registry_bounds_capacity_expiry_and_release(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _write_collection(root)
    bundle = HtmlPreviewBuilder(_service(root), workspace_id="ws_local").build("index.html")

    registry = PreviewRegistry(ttl_seconds=60, max_previews=2)
    first = registry.register(bundle)
    second = registry.register(bundle)
    third = registry.register(bundle)
    assert registry.active() == 2
    assert registry.get(first) is None
    assert registry.get(second) is not None
    assert registry.release(second) is True
    assert registry.release(second) is False
    assert registry.get(third) is not None

    expiring = PreviewRegistry(ttl_seconds=0, max_previews=2)
    expired = expiring.register(bundle)
    assert expiring.get(expired) is None

    scoped = PreviewRegistry()
    scoped.register(bundle)
    assert scoped.release_workspace("ws_local") == 1
    assert scoped.active() == 0


def test_security_headers_name_only_the_actual_preview_origin():
    from morrow.server.app import _security_headers
    from morrow.server.preview_server import _preview_csp

    origin = "http://127.0.0.1:54321"
    csp = dict(_security_headers(origin))[b"content-security-policy"].decode()
    assert f"frame-src 'self' blob: {origin}" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "54321" not in dict(_security_headers())[b"content-security-policy"].decode()

    framed = _preview_csp(origin, frame_ancestors="http://127.0.0.1:59939")
    assert f"script-src {origin} 'unsafe-inline'" in framed
    assert f"connect-src {origin}" in framed
    assert "frame-ancestors http://127.0.0.1:59939" in framed
    assert "connect-src 'self'" not in framed


def test_reference_resolution_keeps_import_maps_local():
    assert resolve_reference("", "three", {"three": "vendor/three.js"}, bare_is_external=True) == (
        "vendor/three.js",
        "ok",
    )
    assert resolve_reference(
        "", "three/addons/x.js", {"three/": "vendor/three/"}, bare_is_external=True
    )[0] == ("vendor/three/addons/x.js")
    assert resolve_reference("src", "https://cdn.example/x.js", {}, bare_is_external=True) == (
        None,
        "external",
    )
    assert resolve_reference("src", "leftpad", {}, bare_is_external=True) == (None, "unmapped")
    assert resolve_reference("src", "app.js", {}, bare_is_external=False) == ("src/app.js", "ok")
    assert parse_import_map('<script type="importmap">{"imports":{"a":"./a.js"}}</script>') == {
        "a": "./a.js"
    }
    assert parse_import_map('<script type="importmap">{oops}</script>') == {}


def _fetch(url: str) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"Host": "127.0.0.1"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class PreviewFixture(ServerFixture):
    """ServerFixture whose ASGI app also serves the GUI and the preview listener."""

    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        self.gui_dir = tmp_path / "gui_static"
        self.gui_dir.mkdir(parents=True)
        (self.gui_dir / "index.html").write_bytes(b"<!doctype html><html></html>")
        self.client = CoreApiVerificationClient(
            create_asgi_app(
                self.host,
                auth_token=SERVE_TOKEN,
                ws_ping_seconds=7200,
                gui_static_dir=self.gui_dir,
            ),
            token=SERVE_TOKEN,
        )


async def test_preview_listener_serves_only_the_registered_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)
    fixture = PreviewFixture(tmp_path)
    try:
        _write_collection(fixture.workspace_dir)
        (fixture.workspace_dir / "secret.txt").write_text("private\n", encoding="utf-8")
        root = f"/v1/workspaces/{fixture.workspace_id}/previews"

        created = await fixture.client.request("POST", root, body={"path": "index.html"})
        assert created.status == 200, created.body
        payload = created.json()
        assert payload["revision"] and payload["entry_path"] == "index.html"
        url = payload["url"]
        origin = url.split("/index.html")[0].rsplit("/", 1)[0]

        status, headers, body = await asyncio.to_thread(_fetch, url)
        assert status == 200, body
        assert b'<div id="root">' in body
        assert headers["Content-Type"].startswith("text/html")
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert headers["Cache-Control"] == "no-store"
        csp = headers["Content-Security-Policy"]
        assert f"script-src {origin} 'unsafe-inline'" in csp
        assert f"connect-src {origin}" in csp
        assert "object-src 'none'" in csp
        assert "197.0.0.1" not in csp and "localhost" not in csp
        # 预览端口没有 Core 路由、没有令牌边界：任何未登记路径都不可达。
        assert "connect-src 'self'" not in csp
        assert "'unsafe-eval'" not in csp and "core" not in csp.lower()
        assert (await asyncio.to_thread(_fetch, f"{origin}/v1/meta"))[0] == 404
        assert (await asyncio.to_thread(_fetch, f"{origin}/"))[0] == 404

        module = await asyncio.to_thread(_fetch, f"{url.rsplit('/', 1)[0]}/app.js")
        assert module[0] == 200 and "javascript" in module[1]["Content-Type"]

        # 未登记路径、未知身份与穿越尝试都不提供服务。
        assert (await asyncio.to_thread(_fetch, f"{url.rsplit('/', 1)[0]}/secret.txt"))[0] == 404
        assert (await asyncio.to_thread(_fetch, f"{origin}/unknown-token/index.html"))[0] == 404
        assert (await asyncio.to_thread(_fetch, f"{origin}/unknown-token/../secret.txt"))[0] == 404
        post = urllib.request.Request(url, method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            await asyncio.to_thread(urllib.request.urlopen, post, timeout=10)
        assert refused.value.code == 405

        # 主页面 CSP 只为这个预览 origin 打开 frame-src。
        page = await fixture.client.get("/", token="")
        assert page.status == 200, page.body
        page_csp = dict(page.headers)["content-security-policy"]
        assert f"frame-src 'self' blob: {origin}" in page_csp
        assert "script-src 'self'" in page_csp

        released = await fixture.client.request(
            "DELETE", f"{root}/{payload['preview_id']}", body={}
        )
        assert released.status == 200, released.body
        assert released.json()["released"] is True
        assert (await asyncio.to_thread(_fetch, url))[0] == 404
    finally:
        fixture.close()


async def test_preview_creation_checks_the_entry_revision_and_workspace(tmp_path):
    fixture = PreviewFixture(tmp_path)
    try:
        _write_collection(fixture.workspace_dir)
        root = f"/v1/workspaces/{fixture.workspace_id}/previews"
        stale = await fixture.client.request(
            "POST", root, body={"path": "index.html", "revision": "0" * 64}
        )
        assert stale.status == 409, stale.body

        missing = await fixture.client.request("POST", root, body={"path": "nope.html"})
        assert missing.status == 404, missing.body

        traversal = await fixture.client.request("POST", root, body={"path": "../outside.html"})
        assert traversal.status in (400, 403), traversal.body

        other = await fixture.client.request(
            "POST", "/v1/workspaces/ws_unknown/previews", body={"path": "index.html"}
        )
        assert other.status in (403, 404), other.body
    finally:
        fixture.close()


async def test_historical_delivery_preview_runs_registered_snapshot_bytes(tmp_path, monkeypatch):
    """A past delivery runs from its snapshots, never from the current disk."""

    from morrow.core.agent_definitions import ToolRequirement
    from morrow.core.models import AssistantMessage
    from test_stage8_core_api import (
        agent_source,
        create_session_and_task,
        publish_pipeline,
        start_run,
        wait_for_run,
    )

    monkeypatch.setattr(socket.socket, "connect", _REAL_CONNECT)
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)
    fixture = PreviewFixture(tmp_path)
    try:
        agent = agent_source(
            access_mode_ceiling="write",
            tool_requirements=(ToolRequirement(name="write", requirement="required"),),
        )
        revision = await publish_pipeline(fixture, agent=agent, make_source=write_pair_source)
        html = '<!doctype html><div id="root">snapshot</div><script src="app.js"></script>'
        fixture.bank.scripts.extend(
            [
                [
                    AssistantMessage(
                        tool_calls=(
                            _write_call("call_h", "index.html", html),
                            _write_call("call_j", "app.js", "window.snapshot = 1"),
                        )
                    ),
                    _submit_call(
                        "call_s",
                        {"result": [{"path": "index.html", "label": "页面"}]},
                    ),
                    ["phase one"],
                ],
                ["phase three"],
            ]
        )
        session_id, task_id, task_version = await create_session_and_task(fixture.client)
        started = await start_run(
            fixture.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        await wait_for_run(fixture.client, run_id, "completed")
        listing = await fixture.client.get(
            f"/v1/workspaces/{fixture.workspace_id}/sessions/{session_id}/task-artifacts"
            f"?workflow_run_id={run_id}"
        )
        assert listing.status == 200, listing.body
        listed = listing.json()["artifacts"]
        delivery = next(
            (
                item
                for item in listed
                if item["kind"] == "deliverable" and item["path"] == "index.html"
            ),
            None,
        )
        assert delivery is not None, [
            (item["kind"], item["source"], item["name"]) for item in listed
        ]
        artifact_id = delivery["artifact_id"]
        assert delivery["name"] == "index.html" and delivery["path"] == "index.html"

        # The workspace changes after the delivery; the preview must not.
        (fixture.workspace_dir / "index.html").write_text("<div>newer</div>", encoding="utf-8")
        root = f"/v1/workspaces/{fixture.workspace_id}/previews"
        created = await fixture.client.request(
            "POST",
            root,
            body={
                "artifact_id": artifact_id,
                "session_id": session_id,
                "workflow_run_id": run_id,
            },
        )
        assert created.status == 200, created.body
        payload = created.json()
        assert payload["entry_path"] == "index.html"
        url = payload["url"]
        status, headers, body = await asyncio.to_thread(_fetch, url)
        assert status == 200, body
        assert b'<div id="root">snapshot</div>' in body
        assert b"newer" not in body
        assert headers["X-Content-Type-Options"] == "nosniff"
        module = await asyncio.to_thread(_fetch, f"{url.rsplit('/', 1)[0]}/app.js")
        assert module[0] == 200 and b"window.snapshot = 1" in module[2]

        released = await fixture.client.request(
            "DELETE", f"{root}/{payload['preview_id']}", body={}
        )
        assert released.status == 200 and released.json()["released"] is True
        assert (await asyncio.to_thread(_fetch, url))[0] == 404

        # A missing authorizing selector never builds a historical preview.
        refused = await fixture.client.request("POST", root, body={"artifact_id": artifact_id})
        assert refused.status in (400, 422), refused.body
        unknown = await fixture.client.request(
            "POST", root, body={"artifact_id": "art_missing", "session_id": session_id}
        )
        assert unknown.status == 404, unknown.body
    finally:
        fixture.close()


def _write_call(call_id: str, path: str, content: str):
    from morrow.core.models import FunctionToolCall

    return FunctionToolCall(
        id=call_id,
        name="write",
        arguments=json.dumps({"path": path, "content": content}),
    )


def _submit_call(call_id: str, deliverables: dict):
    from morrow.core.models import AssistantMessage, FunctionToolCall

    return AssistantMessage(
        tool_calls=(
            FunctionToolCall(
                id=call_id,
                name="submit_node_result",
                arguments=json.dumps({"deliverables": deliverables}),
            ),
        )
    )
