"""预览静态 listener：独立的 loopback 端口，只服务已登记的固定字节集合。

这个端口不属于 Core API：它没有令牌、没有 cookie、没有 Core 路由，只按预览身份
提供构建时固定的文件。请求永远是 GET/HEAD，目录列举与未登记路径一律 404。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from morrow.application.html_preview import PreviewBundle, PreviewRegistry


def _preview_csp(origin: str, *, frame_ancestors: str | None) -> str:
    """只允许本预览 origin 的脚本/样式/媒体，禁止连接 Core 或其他 loopback 服务。"""

    parts = [
        "default-src 'none'",
        f"script-src {origin} 'unsafe-inline'",
        f"style-src {origin} 'unsafe-inline'",
        f"img-src {origin} data: blob:",
        f"font-src {origin}",
        f"media-src {origin}",
        f"connect-src {origin}",
        "worker-src 'none'",
        "child-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
    ]
    if frame_ancestors:
        parts.append(f"frame-ancestors {frame_ancestors}")
    return "; ".join(parts)


class _PreviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "MorrowPreview/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        # 预览标识与路径不进日志：它们是一次性的、限定集合的凭据。
        return

    def _resolve(self) -> tuple[PreviewBundle, str] | None:
        path = unquote(urlsplit(self.path).path)
        parts = [segment for segment in path.split("/") if segment]
        if not parts:
            return None
        preview_id = parts[0]
        bundle = self.server.registry.get(preview_id)  # type: ignore[attr-defined]
        if bundle is None:
            return None
        if len(parts) == 1:
            return bundle, bundle.entry_path
        relative = "/".join(parts[1:])
        if relative.startswith("..") or "/../" in relative or relative.startswith("/"):
            return None
        return bundle, relative

    def _respond(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self) -> None:
        resolved = self._resolve()
        if resolved is None:
            self._respond(
                404,
                {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
                "预览不可用".encode(),
            )
            return
        bundle, relative = resolved
        item = bundle.file(relative)
        if item is None:
            self._respond(
                404,
                {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
                "未包含的依赖".encode(),
            )
            return
        origin = self.server.origin  # type: ignore[attr-defined]
        headers = {
            "Content-Type": item.media_type,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            # 透明来源的 sandbox 文档读取 ES module 需要 CORS；这个端口没有 Core 权限。
            "Access-Control-Allow-Origin": "*",
            "Content-Security-Policy": _preview_csp(
                origin,
                frame_ancestors=self.server.frame_ancestors,  # type: ignore[attr-defined]
            ),
        }
        self._respond(200, headers, item.content)

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._handle()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._respond(405, {"Content-Type": "text/plain; charset=utf-8"}, b"")

    do_PUT = do_POST
    do_DELETE = do_POST


class PreviewHttpServer:
    """Core 生命周期内的预览 listener；未启动时 origin 为 None。"""

    def __init__(
        self,
        registry: PreviewRegistry,
        *,
        host: str = "127.0.0.1",
        frame_ancestors: str | None = None,
    ) -> None:
        self.registry = registry
        self.host = host
        self.frame_ancestors = frame_ancestors
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def origin(self) -> str | None:
        server = self._server
        if server is None:
            return None
        return f"http://{self.host}:{server.server_address[1]}"

    def start(self) -> str:
        if self._server is not None:
            return self.origin or ""
        server = ThreadingHTTPServer((self.host, 0), _PreviewRequestHandler)
        server.daemon_threads = True
        server.registry = self.registry  # type: ignore[attr-defined]
        server.origin = f"http://{self.host}:{server.server_address[1]}"  # type: ignore[attr-defined]
        server.frame_ancestors = self.frame_ancestors  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, name="morrow-preview", daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return server.origin  # type: ignore[attr-defined]

    def stop(self) -> None:
        server = self._server
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self.registry.clear()

    def url_for(self, preview_id: str, entry_path: str) -> str:
        origin = self.origin
        if origin is None:
            raise RuntimeError("preview listener is not running")
        return f"{origin}/{preview_id}/{entry_path}"
