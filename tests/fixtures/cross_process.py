"""Same-data-root cross-process fixtures (P01.3): real subprocess cold start.

Process A boots the real composition inside a subprocess, writes durable facts
through the public API, then exits cleanly or is SIGKILLed; the parent reopens
the same data root and asserts what must still be replayable. This is the
foundation for A16-A18. Rebuilding a React store or reopening SQLite inside
the parent process is not a substitute.

Open policy for P01: the parent closes its own ServerFixture before spawning a
worker, so exactly one process holds the data root at a time. Concurrent
double-open belongs to P11.

The readiness marker poll below is process-boundary synchronization between
two OS processes, not a timing assertion about product behavior.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

WORKER_BOOTSTRAP = r"""
import json
import os
import threading
from pathlib import Path

from fixtures.core_api_client import CoreApiVerificationClient
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application
from morrow.core.capabilities import PermissionProfile
from morrow.server.app import create_asgi_app
from morrow.server.composition import make_context_builder
from morrow.server.host import CoreHost


def main() -> int:
    settings = json.loads(os.environ["MORROW_CROSS_PROCESS_SETTINGS"])
    app = build_application(
        state_root=Path(settings["state_root"]),
        credentials=MemoryCredentialStore(),
    )
    resolution = app.workspace_service.resolve(Path(settings["workspace_dir"]))
    identity = (
        app.workspace_service.confirm(resolution)
        if resolution.status == "candidate"
        else resolution.identity
    )
    app.workspace_state_service.onboard(
        identity.workspace_id, display_name="Cross Process", summary="worker"
    )
    namespace = {
        "app": app,
        "workspace_id": identity.workspace_id,
    }
    scripts = settings.get("scripts")
    if scripts:
        # Mirror ServerFixture's scripted provider setup so the worker can run
        # real planning/runs inside its own process (kill-mid-flight scenes).
        from test_stage7_serial_scheduler import MODEL, ScriptBank

        from morrow.core.agent_runs import ProviderCapabilities
        from morrow.core.models import (
            CredentialRef,
            ProviderConfig,
            ProviderModelConfig,
        )

        bank = ScriptBank([["session composition"], *scripts])
        app.registry.register(
            "fake-adapter",
            bank,
            capabilities=ProviderCapabilities(
                tool_protocol="openai_function", multiple_tool_calls=True
            ),
        )
        credential_ref = CredentialRef(ref="provider:fake-provider:test", version=3)
        app.credentials.set(credential_ref.ref, "topsecret-value")
        config = app.global_store.load()
        app.global_store.update(
            lambda value: value.model_copy(
                update={
                    "providers": {
                        "fake-provider": ProviderConfig(
                            adapter="fake-adapter",
                            base_url="https://api.example.test/v1",
                            credential_ref=credential_ref,
                            models={
                                "m1": ProviderModelConfig(api_model_id="api-m1"),
                                "m2": ProviderModelConfig(api_model_id="api-m2"),
                            },
                        )
                    },
                    "active_model": MODEL,
                }
            ),
            expected_revision=config.revision,
        )
        namespace["bank"] = bank
    host = CoreHost(
        make_context_builder(app, identity, permission_profile=PermissionProfile()),
        command_queue_size=128,
    )
    host.start()
    token = settings.get("token", "cross-process-token")
    client = CoreApiVerificationClient(create_asgi_app(host, auth_token=token), token=token)
    namespace["host"] = host
    namespace["client"] = client
    namespace["settings"] = settings
    exec(settings["snippet"], namespace)  # noqa: S102 - scripted test worker only
    if settings.get("ready_marker"):
        Path(settings["ready_marker"]).write_text("ready", encoding="utf-8")
    if settings.get("exit_mode") == "clean":
        host.stop()
        return 0
    # kill mode: the parent SIGKILLs once the marker appears; park forever.
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


class BackendWorker:
    """One scripted backend subprocess bound to an existing data root."""

    def __init__(
        self,
        state_root: Path | str,
        workspace_dir: Path | str,
        snippet: str,
        *,
        exit_mode: str = "clean",
        ready_marker: Path | str | None = None,
        result_file: Path | str | None = None,
        scripts: list | None = None,
        token: str = "cross-process-token",
    ) -> None:
        if exit_mode not in {"clean", "kill"}:
            raise ValueError(f"unsupported exit mode: {exit_mode}")
        workspace_dir = Path(workspace_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self.ready_marker = Path(ready_marker) if ready_marker else None
        settings = {
            "state_root": str(state_root),
            "workspace_dir": str(workspace_dir),
            "snippet": snippet,
            "exit_mode": exit_mode,
            "token": token,
        }
        if self.ready_marker:
            settings["ready_marker"] = str(self.ready_marker)
        if result_file:
            settings["result_file"] = str(result_file)
        if scripts:
            settings["scripts"] = scripts
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "tests") + os.pathsep + env.get("PYTHONPATH", "")
        env["MORROW_CROSS_PROCESS_SETTINGS"] = json.dumps(settings)
        self.process = subprocess.Popen(
            [sys.executable, "-c", WORKER_BOOTSTRAP],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def wait_ready(self, *, timeout_seconds: float = 60.0) -> None:
        if self.ready_marker is None:
            raise ValueError("kill-mode workers need a ready_marker path")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.ready_marker.exists():
                return
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise AssertionError(f"worker exited before ready:\n{stderr}")
            time.sleep(0.05)
        raise AssertionError("worker never became ready")

    def kill(self) -> int:
        self.process.kill()
        return self.process.wait()

    def wait_clean(self, *, timeout_seconds: float = 120.0) -> subprocess.CompletedProcess:
        stdout, stderr = self.process.communicate(timeout=timeout_seconds)
        return subprocess.CompletedProcess(
            self.process.args, self.process.returncode, stdout, stderr
        )


def require_clean_exit(completed: subprocess.CompletedProcess) -> None:
    if completed.returncode != 0:
        raise AssertionError(
            f"cross-process worker failed ({completed.returncode}):\n{completed.stderr}"
        )
