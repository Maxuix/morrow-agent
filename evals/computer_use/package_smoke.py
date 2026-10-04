"""Offline installed-package gate; never import the SDK or contact a Provider."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.abc
import importlib.metadata
import importlib.util
import json
import socket
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path


class GateFailure(ValueError):
    """A fixed verifier code, with no runtime payload or exception text."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GateFailure(code)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class RejectSdkImport(importlib.abc.MetaPathFinder):
    def __init__(self) -> None:
        self.attempts = 0

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "cua_driver" or fullname.startswith("cua_driver."):
            self.attempts += 1
            raise ImportError("unexpected_sdk_import")
        return None


async def ordinary_task(root: Path) -> dict:
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application, build_session_application
    from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef
    from morrow.testing import ScriptedModelProvider

    application = build_application(state_root=root / "state", credentials=MemoryCredentialStore())
    project = root / "project"
    project.mkdir()
    fixture_text = "offline package fixture\n"
    (project / "fixture.txt").write_text(fixture_text, encoding="utf-8")
    identity = application.workspace_service.confirm(application.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="package_read", name="read", arguments='{"path":"fixture.txt"}'
                    ),
                )
            ),
            ["fixture.txt contains: offline package fixture."],
        ]
    )
    products = build_session_application(
        application,
        identity,
        provider=provider,
        model=ModelRef(provider_id="fixture", model_id="m1"),
    )
    try:
        require(products.computer_use.runtime_status.state == "not_activated", "desktop_activated")
        result = await products.orchestrator.dispatch("Read fixture.txt and report its contents.")
        require(not result.degraded, "ordinary_task_degraded")
        require(
            result.events[-1].type == "turn.completed"
            and result.events[-1].payload.get("finish_reason") == "stop",
            "ordinary_task_not_completed",
        )
        require(len(provider.stream_calls) >= 2, "ordinary_tool_loop_not_executed")
        require(
            any(
                message.role == "tool" and fixture_text.strip() in message.content
                for message in provider.stream_calls[1]
            ),
            "ordinary_read_result_missing",
        )
        require(
            all(
                definition.function.name not in {"computer_observe", "computer_action"}
                for definitions in provider.stream_tools
                for definition in definitions
            ),
            "desktop_tools_enabled_by_default",
        )
        require(not products.session.log.has_active_turn, "conversation_turn_left_open")
        require(products.computer_use.runtime_status.state == "not_activated", "desktop_activated")
        return {"ordinary_task": "completed", "ordinary_read": True, "desktop": "not_activated"}
    finally:
        await products.computer_use.shutdown()
        products.persistence.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-sdk", choices=("absent", "present"), required=True)
    parser.add_argument(
        "--sdk-version", choices=("0.30.4", "0.30.4+morrow.3"), default="0.30.4+morrow.3"
    )
    parser.add_argument("--require-wheel", action="store_true")
    parser.add_argument("--gui-source", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    args = parser.parse_args()
    check = "installation"
    guard = RejectSdkImport()
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    network_attempts = []

    def reject_network(connection, address):
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            network_attempts.append(True)
            raise RuntimeError("unexpected_network_connection")
        return original_connect(connection, address)

    def reject_network_ex(connection, address):
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            network_attempts.append(True)
            raise RuntimeError("unexpected_network_connection")
        return original_connect_ex(connection, address)

    try:
        import morrow

        installed = importlib.util.find_spec("cua_driver") is not None
        require(installed == (args.expect_sdk == "present"), "wrong_sdk_installation")
        sdk_version = importlib.metadata.version("cua-driver") if installed else None
        require(not installed or sdk_version == args.sdk_version, "wrong_sdk_version")
        distribution = importlib.metadata.distribution("morrow-agent")
        if args.require_wheel:
            require("site-packages" in Path(morrow.__file__).parts, "source_tree_imported")
            direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
            require(not direct_url.get("dir_info", {}).get("editable"), "editable_installation")
        check = "gui_bundle"
        expected = file_manifest(args.gui_source)
        require("index.html" in expected and len(expected) > 1, "gui_source_missing")
        actual = file_manifest(Path(morrow.__file__).parent / "gui_static")
        require(actual == expected, "installed_gui_differs")
        if args.wheel:
            with zipfile.ZipFile(args.wheel) as archive:
                prefix = "morrow/gui_static/"
                files = {
                    name.removeprefix(prefix): digest(archive.read(name))
                    for name in archive.namelist()
                    if name.startswith(prefix) and not name.endswith("/")
                }
            require(files == expected, "wheel_gui_differs")
        if args.sdist:
            with tarfile.open(args.sdist) as archive:
                files = {}
                for member in archive.getmembers():
                    if member.isfile() and "/src/morrow/gui_static/" in member.name:
                        relative = member.name.split("/src/morrow/gui_static/", 1)[1]
                        stream = archive.extractfile(member)
                        require(stream is not None, "sdist_gui_unreadable")
                        files[relative] = digest(stream.read())
            require(files == expected, "sdist_gui_differs")
        check = "ordinary_task"
        sys.meta_path.insert(0, guard)
        socket.socket.connect, socket.socket.connect_ex = reject_network, reject_network_ex
        with tempfile.TemporaryDirectory(prefix="morrow-installed-smoke-") as directory:
            task = asyncio.run(ordinary_task(Path(directory)))
        require(guard.attempts == 0 and not network_attempts, "unexpected_external_activation")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "python": sys.version.split()[0],
                    "morrow": distribution.version,
                    "sdk_installed": installed,
                    "sdk_version": sdk_version,
                    "sdk_import_attempts": guard.attempts,
                    "network_attempts": len(network_attempts),
                    "gui_files": len(expected),
                    "gui_manifest_sha256": digest(json.dumps(expected, sort_keys=True).encode()),
                    **task,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "check": check,
                    "error_type": type(exc).__name__,
                    **({"code": str(exc)} if isinstance(exc, GateFailure) else {}),
                }
            )
        )
        return 1
    finally:
        if guard in sys.meta_path:
            sys.meta_path.remove(guard)
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex


if __name__ == "__main__":
    raise SystemExit(main())
