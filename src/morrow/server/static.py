"""Prebuilt GUI asset serving for the local Core server.

These handlers are only mounted when the server is started with a GUI asset
directory (`morrow gui`). The surface is read-only GET/HEAD, confined to the
asset root with an extension allowlist; the session token stays a URL fragment
that is never sent to the server, so the static surface needs no auth and
carries no state. Content-hashed assets under ``assets/`` are immutable;
everything else is served ``no-cache`` so a GUI upgrade is never masked by a
stale shell.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Package-data location: src/morrow/gui_static in a source checkout,
# site-packages/morrow/gui_static in an installed wheel.
DEFAULT_GUI_STATIC_DIR = Path(__file__).resolve().parents[1] / "gui_static"

_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def gui_assets_available(root: Path | None = None) -> bool:
    """Whether a usable prebuilt GUI bundle is present."""

    base = root if root is not None else DEFAULT_GUI_STATIC_DIR
    return (base / "index.html").is_file()


def make_gui_static_handler(root: Path):
    """Build the catch-all static handler confined to ``root``."""

    resolved_root = root.resolve()

    def _resolve(raw_path: str) -> Path | None:
        relative = raw_path.lstrip("/") or "index.html"
        candidate = (resolved_root / relative).resolve()
        if not candidate.is_relative_to(resolved_root):
            return None
        if not candidate.is_file():
            return None
        if candidate.suffix.lower() not in _CONTENT_TYPES:
            return None
        return candidate

    async def gui_static(request: Request) -> Response:
        candidate = _resolve(request.path_params.get("path", ""))
        if candidate is None:
            return JSONResponse(
                {"error": {"code": "not_found", "message": "unknown path"}},
                status_code=404,
            )
        body = candidate.read_bytes()
        headers = {
            "content-type": _CONTENT_TYPES[candidate.suffix.lower()],
            "content-length": str(len(body)),
        }
        relative = candidate.relative_to(resolved_root)
        if relative.parts and relative.parts[0] == "assets":
            headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            headers["cache-control"] = "no-cache"
        if request.method == "HEAD":
            return Response(content=b"", headers=headers)
        return Response(content=body, headers=headers)

    return gui_static
