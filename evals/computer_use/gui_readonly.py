"""Opt-in fixture read on the production GUI composition, then serve its local GUI.

The SDK probe is separate from ordinary application grants. It observes only
the independently identified fixture; production native activation stays closed.
No model request, real credential, raw SDK result or screenshot is published.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import secrets
import socket
import tempfile
from pathlib import Path

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionProfile
from morrow.core.computer_use import ComputerUseContractError
from morrow.core.models import CredentialRef, ModelRef, ProviderConfig, ProviderModelConfig
from morrow.server.app import create_asgi_app
from morrow.server.composition import make_context_builder
from morrow.server.host import CoreHost
from morrow.testing import ScriptedModelProvider

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def build_gui_probe(root: Path, *, gui_static_dir: Path, port: int):
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    application = build_application(
        state_root=root / "state-root", credentials=MemoryCredentialStore()
    )
    application.registry.register(
        "acceptance-scripted",
        lambda *_: ScriptedModelProvider(["Controlled offline acceptance host."]),
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", multiple_tool_calls=True, input_types=("text", "image")
        ),
    )
    credential = CredentialRef(ref="provider:fixture:synthetic", version=1)
    application.credentials.set(credential.ref, "synthetic-fixture-only")
    loaded = application.global_store.load()
    application.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "fixture": ProviderConfig(
                        adapter="acceptance-scripted",
                        base_url="https://offline.invalid",
                        credential_ref=credential,
                        models={"fixture": ProviderModelConfig(api_model_id="fixture")},
                    )
                },
                "active_model": ModelRef(provider_id="fixture", model_id="fixture"),
            }
        ),
        expected_revision=loaded.revision,
    )
    resolution = application.workspace_service.resolve(workspace)
    identity = (
        application.workspace_service.confirm(resolution)
        if resolution.status == "candidate"
        else resolution.identity
    )
    application.workspace_state_service.onboard(
        identity.workspace_id, display_name="Native read-only fixture", summary="Controlled fixture"
    )
    host = CoreHost(
        make_context_builder(application, identity, permission_profile=PermissionProfile())
    )
    host.start()
    try:
        api = create_asgi_app(
            host,
            auth_token=secrets.token_urlsafe(32),
            gui_static_dir=gui_static_dir,
            listen_port=port,
        )
    except Exception:
        host.stop()
        raise
    return host, api


async def inspect_gui_owner(host, *, fixture_window, inspect):
    async def on_owner():
        context = host.context
        result = await inspect(fixture_window=fixture_window)
        result.update(
            host_mode="gui_composition_probe",
            full_service_context=context.products is not None
            and not getattr(context.products, "_execution_proxy", False),
            production_desktop_active=context.computer_use.runtime_status.state != "not_activated",
        )
        if not result["full_service_context"] or result["production_desktop_active"]:
            result.update(status="failed", reason="gui_context_incomplete")
        elif result["status"] == "passed":
            prepared = result.get("prepared_capture") or {}
            if (
                result.get("image_share_error") is not None
                or type(prepared.get("byte_size")) is not int
                or prepared["byte_size"] < 1
                or not prepared.get("sha256")
            ):
                result.update(status="failed", reason="gui_fixture_image_unverified")
        return result

    return await host.execute_preparation(on_owner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--fixture-bundle-id", choices=(FIXTURE_BUNDLE_ID,), required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    native = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    host = None
    with (
        tempfile.TemporaryDirectory(prefix="morrow-native-gui-probe-") as temporary,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener,
    ):
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            window = native["read_fixture_window"](args.fixture_state_file)
            host, api = build_gui_probe(
                Path(temporary), gui_static_dir=Path("src/morrow/gui_static"), port=port
            )
            result = asyncio.run(
                inspect_gui_owner(host, fixture_window=window, inspect=native["inspect_fixture"])
            )
            result["gui_url"] = f"http://127.0.0.1:{port}"
            args.evidence_file.write_text(json.dumps(result, sort_keys=True) + "\n")
            print(json.dumps(result, sort_keys=True), flush=True)
            if result["status"] != "passed" or result["production_desktop_active"]:
                raise SystemExit(1)
            import uvicorn

            server = uvicorn.Server(uvicorn.Config(api, log_level="warning"))
            server.run(sockets=[listener])
        except ComputerUseContractError as exc:
            print(json.dumps({"status": "failed", "reason": exc.code}), flush=True)
            raise SystemExit(1) from None
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "reason": "gui_probe_error",
                        "exception_type": type(exc).__name__,
                    }
                ),
                flush=True,
            )
            raise SystemExit(1) from None
        finally:
            if host is not None:
                host.stop()


if __name__ == "__main__":
    main()
