"""Private local Core discovery and authenticated CLI transport."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from morrow.core.domain import validate_prefixed_id


class CoreConnectionError(RuntimeError):
    pass


def _directory(root):
    path = Path(root) / "core-connections"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise CoreConnectionError("Core discovery directory must be private (0700)")
    return path


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 8192
        ):
            raise CoreConnectionError("Core connection file must be private (0600)")
        return json.load(handle)


@contextmanager
def publish_connection(root, workspace_id, base_url, token):
    validate_prefixed_id(workspace_id, "ws")
    directory = _directory(root)
    target = directory / (workspace_id + ".json")
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(
                {"version": 1, "workspace_id": workspace_id, "base_url": base_url, "token": token},
                handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        yield
    finally:
        Path(temporary).unlink(missing_ok=True)
        try:
            if _read(target).get("token") == token:
                target.unlink()
        except (OSError, ValueError, CoreConnectionError):
            pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise CoreConnectionError("Core redirects are refused")


class CoreClient:
    def __init__(self, connection):
        url = urlsplit(connection["base_url"])
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not url.port
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise CoreConnectionError("Only an explicit loopback Core address is supported")
        self.base_url = connection["base_url"].rstrip("/")
        self.token = connection["token"]
        self.workspace_id = connection["workspace_id"]
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    @classmethod
    def discover(cls, root, core_workspace_id=None):
        directory = _directory(root)
        if core_workspace_id:
            validate_prefixed_id(core_workspace_id, "ws")
            candidates = [directory / (core_workspace_id + ".json")]
        else:
            candidates = list(directory.glob("*.json"))[:33]
        if len(candidates) > 32:
            raise CoreConnectionError("Choose --core-workspace-id to identify the Core")
        found = []
        for path in candidates:
            try:
                client = cls(_read(path))
                meta = client.request("GET", "/v1/meta")
                if meta["workspace_id"] == client.workspace_id:
                    found.append(client)
            except (OSError, ValueError, KeyError, CoreConnectionError):
                continue
        if len(found) != 1:
            raise CoreConnectionError(
                "Start morrow serve/gui, or select one running Core with --core-workspace-id"
            )
        return found[0]

    def request(self, method, path, body=None):
        if not path.startswith("/v1/") or path.startswith("//"):
            raise CoreConnectionError("Invalid Core API path")
        request = Request(
            self.base_url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise CoreConnectionError("Core response exceeds client capacity")
                return json.loads(data)
        except HTTPError as exc:
            # Do not echo request headers, URL credentials or raw error bodies.
            raise CoreConnectionError(
                f"Core rejected this operation (HTTP {exc.code}); refresh state and retry"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise CoreConnectionError(
                "Core connection unavailable; keep the same client message ID when retrying"
            ) from None
