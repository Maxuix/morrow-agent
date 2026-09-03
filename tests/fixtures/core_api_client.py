"""Scripted in-process verification client for the Core API contract.

This is a test fixture, not a product: it speaks raw ASGI to the app, so no
socket, network or third-party HTTP client is involved. It doubles as the
reference event-stream consumer — initial snapshot under one cursor, durable
``/v1/events?after=`` pulls, WebSocket cursor hints, gap detection and resync
— so every §16.1 behavior is exercised exactly the way a GUI client must do it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


class WSSession:
    """One scripted WebSocket connection over raw ASGI messages."""

    def __init__(self, app, path: str, *, headers: list[tuple[bytes, bytes]]) -> None:
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": path.split("?", 1)[0],
            "raw_path": path.split("?", 1)[0].encode(),
            "query_string": (path.split("?", 1)[1] if "?" in path else "").encode(),
            "headers": headers,
            "subprotocols": [],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 80),
        }
        self._task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))
        self.closed_code: int | None = None

    async def accept(self) -> dict:
        await self._to_app.put({"type": "websocket.connect"})
        message = await self._next()
        assert message["type"] in ("websocket.accept", "websocket.close")
        if message["type"] == "websocket.close":
            self.closed_code = message.get("code")
        return message

    async def receive_json(self) -> dict:
        while True:
            message = await self._next()
            if message["type"] == "websocket.close":
                self.closed_code = message.get("code")
                raise ConnectionError(f"websocket closed: {self.closed_code}")
            if message["type"] == "websocket.send":
                return json.loads(message["text"])

    async def close(self, code: int = 1000) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": code})
        await asyncio.gather(self._task, return_exceptions=True)

    async def _next(self) -> dict:
        for _ in range(2000):
            await asyncio.sleep(0)
            if not self._from_app.empty():
                return await self._from_app.get()
            if self._task.done():
                if self._from_app.empty():
                    raise ConnectionError("websocket app exited without a message")
        raise AssertionError("websocket message never arrived")


class CoreApiVerificationClient:
    """Reference consumer: snapshot + ordered pull + cursor hints + resync."""

    def __init__(self, app, *, token: str) -> None:
        self.app = app
        self.token = token
        self.last_cursor = 0
        self.seen_event_ids: set[str] = set()
        self.applied: list[dict] = []

    # HTTP -----------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        token: str | None = None,
        origin: str | None = None,
        content_type: str | None = None,
    ) -> ApiResponse:
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        headers = [(b"host", b"127.0.0.1")]
        auth = self.token if token is None else token
        if auth:
            headers.append((b"authorization", f"Bearer {auth}".encode()))
        if body is not None:
            headers.append((b"content-type", (content_type or "application/json").encode()))
            headers.append((b"content-length", str(len(raw)).encode()))
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        route, _, query = path.partition("?")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": route,
            "raw_path": route.encode(),
            "query_string": query.encode(),
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 80),
        }
        messages: list[dict] = []
        delivered = False

        async def receive() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(item for item in messages if item["type"] == "http.response.start")
        payload = b"".join(
            item.get("body", b"") for item in messages if item["type"] == "http.response.body"
        )
        return ApiResponse(
            status=start["status"],
            headers=tuple(
                (key.decode(), value.decode()) for key, value in start.get("headers", [])
            ),
            body=payload,
        )

    # Convenience verbs ------------------------------------------------------

    async def get(self, path: str, **kwargs) -> ApiResponse:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, body: Any = None, **kwargs) -> ApiResponse:
        return await self.request("POST", path, body=body, **kwargs)

    async def request_raw_json(self, path: str, raw: str) -> ApiResponse:
        """Send a pre-serialized JSON payload (for malformed-shape probes)."""

        headers = [
            (b"host", b"127.0.0.1"),
            (b"authorization", f"Bearer {self.token}".encode()),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw.encode())).encode()),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 80),
        }
        messages: list[dict] = []
        delivered = False

        async def receive() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw.encode(), "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(item for item in messages if item["type"] == "http.response.start")
        payload = b"".join(
            item.get("body", b"") for item in messages if item["type"] == "http.response.body"
        )
        return ApiResponse(
            status=start["status"],
            headers=tuple(
                (key.decode(), value.decode()) for key, value in start.get("headers", [])
            ),
            body=payload,
        )

    # Reference event-stream consumer -----------------------------------------

    async def take_snapshot(self) -> dict:
        """Initial snapshot; the recorded cursor anchors every later pull."""

        response = await self.get("/v1/snapshot")
        assert response.status == 200, response.body
        snapshot = response.json()
        self.last_cursor = snapshot["cursor"]
        return snapshot

    async def pull_events(self) -> list[dict]:
        """Durable ordered pull after the recorded cursor; duplicates re-skip."""

        response = await self.get(f"/v1/events?after={self.last_cursor}&limit=100")
        assert response.status == 200, response.body
        page = response.json()
        fresh = []
        for event in page["events"]:
            assert event["cursor"] > self.last_cursor, "event stream regressed"
            if event["event_id"] in self.seen_event_ids:
                continue
            self.seen_event_ids.add(event["event_id"])
            self.applied.append(event)
            fresh.append(event)
        self.last_cursor = page["latest_cursor"]
        return fresh

    def websocket(self, *, token: str | None = None, origin: str | None = None) -> WSSession:
        headers = [(b"host", b"127.0.0.1")]
        suffix = ""
        auth = self.token if token is None else token
        if auth:
            suffix = f"?token={auth}"
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        return WSSession(self.app, f"/v1/events/stream{suffix}", headers=headers)

    async def expect_cursor_hint(self, ws: WSSession) -> int:
        """A hint arrives; the consumer then pulls the gap from durable events."""

        message = await ws.receive_json()
        assert message["type"] in ("hello", "cursor")
        return int(message["latest_cursor"])

    async def resync(self) -> dict:
        """Gap recovery: a fresh snapshot re-anchors the cursor."""

        return await self.take_snapshot()
