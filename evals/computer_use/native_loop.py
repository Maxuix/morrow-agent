"""Opt-in one controlled native text action through the ordinary durable loop.

Uses a scripted Provider, temporary application/store and explicit fixture-only
authorization. No real Provider, credential or production activation is used.
Only hashes, counts and completion facts are exported; unknown is never retried.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import runpy
import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image

from morrow.adapters.computer_use.diagnostics import diagnose_host
from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.sdk_loader import (
    collect_host_probe,
    construct_run_session,
    load_sdk,
)
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.operational import SystemStoreClock
from morrow.application.computer_requests import ComputerUseSelection
from morrow.application.computer_use import ComputerUseLifecycle
from morrow.bootstrap import build_application, build_session_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
)
from morrow.core.image_tokens import iter_image_parts
from morrow.core.models import (
    AssistantMessage,
    CredentialRef,
    FunctionToolCall,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    ToolApprovalDecision,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings, RuntimePolicyOverrides
from morrow.testing import ScriptedModelProvider

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def require(condition: bool, code: str):
    if not condition:
        raise ComputerUseContractError(code)


def tool(call_id, name, arguments):
    return AssistantMessage(
        tool_calls=(FunctionToolCall(id=call_id, name=name, arguments=json.dumps(arguments)),)
    )


class NativeProvider(ScriptedModelProvider):
    def __init__(self, marker):
        super().__init__([tool("discover", "computer_observe", {"operation": "discover"})])
        self.marker = marker
        self.images = []
        self.action_outcome = None
        self.refusal_code = None
        self.exception_type = None

    async def stream(self, model, messages, tools=(), generation=None):
        try:
            async for event in self._stream(model, messages, tools, generation):
                yield event
        except ComputerUseContractError as exc:
            self.refusal_code = exc.code
            raise
        except Exception as exc:
            self.exception_type = type(exc).__name__
            raise

    async def _stream(self, model, messages, tools=(), generation=None):
        hashes = []
        for part in iter_image_parts(messages):
            content = base64.b64decode(part.data, validate=True)
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                require(image.size == (1040, 1024), "fixture_provider_image_invalid")
            hashes.append(hashlib.sha256(content).hexdigest())
        step = len(self.images)
        self.images.append(hashes)
        if step:
            reply = next(message for message in reversed(messages) if message.role == "tool")
            result = json.JSONDecoder().raw_decode(reply.content)[0]["result"]
            if step == 1:
                require(len(result["targets"]) == 1, "fixture_target_ambiguous")
                self.responses.append(
                    tool(
                        "observe",
                        "computer_observe",
                        {"operation": "window", "target_ref": result["targets"][0]["target_ref"]},
                    )
                )
            elif step == 2:
                require(len(hashes) == 1, "fixture_provider_image_missing")
                editable = [
                    item
                    for item in result["elements"]
                    if item["role"] in {"axtextfield", "axsecuretextfield"}
                ]
                require(1 <= len(editable) <= 2, "fixture_input_ambiguous")
                self.responses.append(
                    tool(
                        "input",
                        "computer_action",
                        {
                            "observation_id": result["observation_id"],
                            "action": {
                                "type": "type_text",
                                "element_ref": editable[0]["element_ref"],
                                "text": self.marker,
                            },
                        },
                    )
                )
            elif step == 3:
                self.action_outcome = result["outcome"]
                require(len(hashes) == 2, "fixture_provider_post_image_missing")
                self.responses.append(
                    "The single controlled action returned; no retry is requested."
                )
            else:
                raise ComputerUseContractError("fixture_provider_unexpected_request")
        async for event in super().stream(model, messages, tools, generation):
            yield event


async def run_fixture(path: Path, sdk, root: Path) -> dict:
    helpers = runpy.run_path(str(Path(__file__).with_name("native_text.py")))
    counter = runpy.run_path(str(Path(__file__).with_name("native_counter.py")))
    readonly = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    before = helpers["text_oracle"](path, counter["counter_oracle"])
    marker = f"Morrow-loop-{uuid4().hex[:8]}中文🧭"
    require(marker not in before["text"], "fixture_marker_present")
    result = {
        "schema_version": 1,
        "scripted_provider": True,
        "sdk_input_entries": 0,
        "approval_count": 0,
        "native_product_complete": False,
    }
    app = build_application(state_root=root / "state", credentials=MemoryCredentialStore())
    provider = NativeProvider(marker)
    app.registry.register(
        "acceptance-native-scripted",
        lambda *_: provider,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", input_types=("text", "image")
        ),
    )
    credential = CredentialRef(ref="provider:fixture:synthetic", version=1)
    app.credentials.set(credential.ref, "synthetic-fixture-only")
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "fixture": ProviderConfig(
                        adapter="acceptance-native-scripted",
                        base_url="https://offline.invalid",
                        credential_ref=credential,
                        models={"fixture": ProviderModelConfig(api_model_id="fixture")},
                    )
                },
                "active_model": ModelRef(provider_id="fixture", model_id="fixture"),
                "runtime_policy": RuntimePolicyOverrides(computer_use=settings),
            }
        ),
        expected_revision=config.revision,
    )

    class Native(readonly["_DiagnosedSession"]):
        async def call_tool(self, name, content):
            if name != "type_text":
                raise ComputerUseContractError("fixture_action_invalid")
            result["sdk_input_entries"] += 1
            if result["sdk_input_entries"] != 1:
                raise ComputerUseContractError("fixture_input_repeated")
            returned = await self._native.call_tool(name, content)
            from morrow.adapters.computer_use.session import outcome_from_tool

            result["sdk_action_outcome"] = outcome_from_tool(returned).model_dump(mode="json")
            return returned

    owner = ComputerDriverOwner(
        sdk,
        app.id_source,
        SystemStoreClock(),
        session_factory=lambda driver, name: Native(
            construct_run_session(sdk, driver, name), result
        ),
    )

    def diagnostic(current=settings):
        return diagnose_host(current, collect_host_probe(loader=lambda: sdk), images_required=True)

    # Explicit acceptance-only activation of this temporary composition.
    lifecycle = ComputerUseLifecycle(
        lambda: owner, diagnostic, native_verified=True, run_diagnostic=diagnostic
    )

    class Approval:
        async def request(self, request):
            result["approval_count"] += 1
            require(result["approval_count"] == 1, "fixture_approval_repeated")
            current = helpers["text_oracle"](path, counter["counter_oracle"])
            require(current["sha256"] == before["sha256"], "fixture_changed_before_input")
            return ToolApprovalDecision(approved=True)

    project = root / "workspace"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    products = build_session_application(
        app,
        identity,
        computer_use_lifecycle=lifecycle,
        approval_port=Approval(),
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
    )
    try:
        catalog = await lifecycle.discover_local_candidates(
            settings, authority=TRUSTED_COMPUTER_USE_AUTHORITY
        )
        candidates = [
            item
            for item in catalog.candidates
            if item.app.bundle_id == FIXTURE_BUNDLE_ID
            and (record := owner._candidates.resolve(item.candidate_id)).pid == before["pid"]
            and record.window_id == before["window_id"]
        ]
        require(len(candidates) == 1, "fixture_window_required")
        windows = lifecycle.select_local_candidates(
            (candidates[0].candidate_id,), authority=TRUSTED_COMPUTER_USE_AUTHORITY
        )
        factory = products.orchestrator.preparation.computer_factory
        request = factory.select(
            ComputerUseSelection(
                apps=(ComputerUseAppIdentity(bundle_id=FIXTURE_BUNDLE_ID),),
                windows=windows,
                delivery=ComputerUseDelivery.BACKGROUND,
                image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
            ),
            products.session,
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        )
        products.orchestrator.prepare_options = lambda _: {"computer_request": request}
        completed = await products.orchestrator.dispatch(
            "Insert one controlled fixture marker and observe the result."
        )
        result.update(
            provider_image_hashes=provider.images,
            provider_refusal=provider.refusal_code,
            provider_exception_type=provider.exception_type,
            action_outcome=provider.action_outcome,
        )
        require(
            not completed.degraded and completed.events[-1].payload.get("finish_reason") == "stop",
            "fixture_turn_incomplete",
        )
        after = helpers["text_oracle"](path, counter["counter_oracle"])
        require(
            helpers["independent_insert"](
                before, after, marker, counter["validate_counter_identity"]
            ),
            "fixture_input_unconfirmed",
        )
        outcome = provider.action_outcome or {}
        require(
            outcome.get("status") == "completed" and outcome.get("delivery") == "background",
            "fixture_native_unknown",
        )
        rows = factory.journal.list_session_executions(
            factory.workspace_id, products.session.session_id
        )
        require(
            len(rows) == 3 and all(row.state.value == "closed" for row in rows),
            "fixture_ledger_incomplete",
        )
        replies = [item for item in products.session.log.messages_view() if item.role == "tool"]
        require(
            [len(item.visual_refs) for item in replies] == [0, 1, 1],
            "fixture_visual_commit_incomplete",
        )
        hashes = [item.visual_refs[0].sha256 for item in replies if item.visual_refs]
        require(provider.images == [[], [], [hashes[0]], hashes], "fixture_provider_hash_mismatch")
        for row in rows:
            for ref in row.result_envelope.visual_refs:
                require(
                    ref.tool_execution_id == row.tool_execution_id, "fixture_visual_source_mismatch"
                )
                capture = factory.visuals.read(ref, session_id=products.session.session_id)
                require(
                    hashlib.sha256(capture.content).hexdigest() == ref.sha256,
                    "fixture_artifact_hash_mismatch",
                )
        require(
            hashes[0] != hashes[1]
            and result["sdk_input_entries"] == 1
            and result["approval_count"] == 1
            and not products.session.log.has_active_turn,
            "fixture_completion_unconfirmed",
        )
        result.update(
            status="passed",
            reason=None,
            ordinary_loop=True,
            independent_insert=True,
            action_status=outcome["status"],
            delivery=outcome["delivery"],
            provider_image_hashes=provider.images,
            source_execution_count=len(rows),
            before_state_sha256=before["sha256"],
            after_state_sha256=after["sha256"],
        )
    except ComputerUseContractError as exc:
        result.update(status="failed", reason=exc.code)
    except Exception as exc:
        result.update(
            status="failed", reason="fixture_loop_failed", exception_type=type(exc).__name__
        )
    finally:
        try:
            await lifecycle.shutdown()
        except Exception:
            result.update(status="failed", reason="shutdown_failed")
        finally:
            products.persistence.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--allow-one-text-insert", action="store_true", required=True)
    parser.add_argument("--fixture-bundle-id", choices=(FIXTURE_BUNDLE_ID,), required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="morrow-native-loop-") as temporary:
        try:
            sdk = load_sdk()
            result = asyncio.run(run_fixture(args.fixture_state_file, sdk, Path(temporary)))
        except ComputerUseContractError as exc:
            result = {"status": "failed", "reason": exc.code}
        except Exception as exc:
            result = {
                "status": "failed",
                "reason": "fixture_loop_failed",
                "exception_type": type(exc).__name__,
            }
    args.evidence_file.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
