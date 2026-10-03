"""Opt-in real DeepSeek Provider + native controlled-fixture functional campaign.

Each case uses ordinary AgentLoop, an isolated store, memory-only credentials,
fixture PID/window selection and at most one approved SDK action. The model
chooses the tool arguments; this wrapper records metadata without scripting
responses. Raw evidence is not a pass claim: compare independent effects with
SDK status. Unknown is never retried. No user applications are granted.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import runpy
import sys
import tempfile
from getpass import getpass
from pathlib import Path
from uuid import uuid4

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
    CredentialRef,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    ToolApprovalDecision,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings, RuntimePolicyOverrides

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def require(condition: bool, code: str):
    if not condition:
        raise ComputerUseContractError(code)


class NativeProvider:
    def __init__(self, key):
        from morrow.adapters.models.openai_compatible import OpenAICompatibleProvider

        self.real = OpenAICompatibleProvider(
            "https://api.deepseek.com", key, api_model_ids={"fixture": "deepseek-flash"}
        )
        self.images = []
        self.calls = []
        self.outcomes = []
        self.failures = []
        self.observation_meta = []
        self.tool_diagnostics = []

    async def stream(self, model, messages, tools=(), *, generation=None):
        self.images.append(
            [
                hashlib.sha256(base64.b64decode(p.data)).hexdigest()
                for p in iter_image_parts(messages)
            ]
        )
        if len(self.images) > 10:
            raise ComputerUseContractError("provider_call_budget")
        for message in messages:
            if message.role == "tool":
                try:
                    envelope = json.JSONDecoder().raw_decode(message.content)[0]
                    result = envelope.get("result", {})
                    self.tool_diagnostics.append(
                        {
                            "envelope": {k: v for k, v in envelope.items() if k in {"ok", "error"}},
                            "result": {
                                k: v
                                for k, v in result.items()
                                if k
                                in {
                                    "error_code",
                                    "observation_error",
                                    "verification_error",
                                    "image_error",
                                }
                            },
                        }
                    )
                    if "elements" in result:
                        self.observation_meta.append(
                            {
                                "truncated": result.get("truncated"),
                                "image": bool(result.get("image")),
                                "element_count": len(result["elements"]),
                            }
                        )
                    outcome = result.get("outcome")
                    if outcome and outcome not in self.outcomes:
                        self.outcomes.append(outcome)
                except (ValueError, AttributeError):
                    pass
        async for event in self.real.stream(
            model,
            messages,
            tuple(t for t in tools if t.function.name in {"computer_observe", "computer_action"}),
            generation=generation,
        ):
            completion = getattr(event, "message", None)
            if completion:
                for call in completion.tool_calls:
                    args = json.loads(call.arguments)
                    self.calls.append(
                        {
                            "name": call.name,
                            "operation": args.get("operation"),
                            "action": args.get("action", {}).get("type"),
                            "key": args.get("action", {}).get("key"),
                            "keys": args.get("action", {}).get("keys"),
                            "direction": args.get("action", {}).get("direction"),
                            "amount": args.get("action", {}).get("amount"),
                            "coordinate_target": "x" in args.get("action", {}),
                            "element_target": bool(args.get("action", {}).get("element_ref")),
                            "include_image": args.get("include_image"),
                            "button": args.get("action", {}).get("button"),
                            "count": args.get("action", {}).get("count"),
                            "postcondition": args.get("action", {})
                            .get("postcondition", {})
                            .get("type"),
                        }
                    )
            failure = getattr(event, "failure", None)
            if failure:
                self.failures.append({"code": str(failure.code)})
            yield event


async def run_fixture(
    path: Path, sdk, root: Path, key: str, case: str, delivery: str, mode: str
) -> dict:
    helpers = runpy.run_path(str(Path(__file__).with_name("native_text.py")))
    counter = runpy.run_path(str(Path(__file__).with_name("native_counter.py")))
    readonly = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    before = helpers["text_oracle"](path, counter["counter_oracle"])
    marker = f"Morrow-loop-{uuid4().hex[:8]}中文🧭"
    require(marker not in before["text"], "fixture_marker_present")
    result = {
        "schema_version": 1,
        "scripted_provider": False,
        "provider_model": "deepseek-flash",
        "case": case,
        "mode": mode,
        "delivery": delivery,
        "sdk_input_entries": 0,
        "approval_count": 0,
        "raw_evidence_only": True,
    }
    result["baseline_json"] = path.read_text()
    app = build_application(state_root=root / "state", credentials=MemoryCredentialStore())
    provider = NativeProvider(key)
    app.registry.register(
        "acceptance-native-real-provider",
        lambda *_: provider,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function",
            input_types=("text", "image") if mode == "hybrid" else ("text",),
        ),
    )
    credential = CredentialRef(ref="provider:fixture:live-memory", version=1)
    app.credentials.set(credential.ref, key)
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode(mode))
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "fixture": ProviderConfig(
                        adapter="acceptance-native-real-provider",
                        base_url="https://api.deepseek.com",
                        credential_ref=credential,
                        models={"fixture": ProviderModelConfig(api_model_id="deepseek-flash")},
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
            result["sdk_input_entries"] += 1
            if result["sdk_input_entries"] > 1:
                raise ComputerUseContractError("fixture_action_repeated")
            result["sdk_method"] = name
            returned = await self._native.call_tool(name, content)
            from morrow.adapters.computer_use.session import outcome_from_tool

            result["sdk_action_outcome"] = outcome_from_tool(returned).model_dump(mode="json")
            return returned

        async def click(self, request):
            result["sdk_input_entries"] += 1
            if result["sdk_input_entries"] > 1:
                raise ComputerUseContractError("fixture_action_repeated")
            result["sdk_method"] = "click"
            return await self._native.click(request)

    owner = ComputerDriverOwner(
        sdk,
        app.id_source,
        SystemStoreClock(),
        session_factory=lambda driver, name, settings: Native(
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
            require(result["approval_count"] <= 1, "fixture_approval_repeated")
            current = helpers["text_oracle"](path, counter["counter_oracle"])
            require(current["sha256"] == before["sha256"], "fixture_changed_before_input")
            return ToolApprovalDecision(approved=case != "denied")

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
                delivery=ComputerUseDelivery(delivery),
                image_share=(
                    ComputerUseImageShare.CONTROLLED_WINDOW
                    if mode == "hybrid"
                    else ComputerUseImageShare.NONE
                ),
            ),
            products.session,
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        )
        products.orchestrator.prepare_options = lambda _: {"computer_request": request}
        instructions = {
            "observe": "Only discover then observe the selected window. Do not act.",
            "click": "Click the Increment button exactly once using its element_ref.",
            "double_click": "Double-click Increment once using element_ref, button left and count 2.",
            "right_click": "Right-click Increment once using element_ref, button right and count 1.",
            "postcondition_text": f"Insert {marker} once in Unicode field with type_text and postcondition {{type: text_appears, text: {marker}}}.",
            "postcondition_exists": "Click Increment once with postcondition {type: element_exists, selector: {role: axbutton, label: Increment}}.",
            "postcondition_attribute": "Click Increment once with postcondition {type: attribute_equals, selector: {role: axbutton, label: Increment}, attribute: enabled, value: true} where value is a string.",
            "denied": "Click Increment once. This test approval adapter will deny it. Accept denial and stop.",
            "coordinate_click": "Click Increment exactly once using image x,y coordinates.",
            "coordinate_scroll": "Scroll down by amount 3 once using image x,y coordinates at the center of the rows scroll area.",
            "type_text": f"Insert {marker} exactly once into the ordinary Unicode text field using type_text.",
            "press_key": "Send the q key once to the ordinary Unicode text field using press_key.",
            "hotkey": "Send shift+y once to the ordinary Unicode text field using hotkey.",
            "scroll": "Scroll down by amount 3 once targeting the scroll area using scroll.",
            "secure": "Insert SyntheticOnly2026 once into the synthetic secure field using type_text.",
        }
        prompt = (
            "This is an authorized local functional test. Use computer_observe discover, "
            "then computer_observe window to obtain current tokens. "
            + (
                "For every window observation set include_image=true. If screenshot fails, report the error and stop; do not switch to semantic. "
                if mode == "hybrid"
                else ""
            )
            + instructions[case]
            + " Use only computer tools. Execute at most ONE action, no retries even if unknown "
            "or not_started. Use fresh observation_id and element_ref. After the result, "
            "give a short factual summary and stop. Do not ask for confirmation."
        )
        completed = await products.orchestrator.dispatch(prompt)
        after = json.loads(path.read_text())
        baseline = json.loads(result.pop("baseline_json"))
        rows = factory.journal.list_session_executions(
            factory.workspace_id, products.session.session_id
        )
        result.update(
            fixture_identity_unchanged=all(after[k] == baseline[k] for k in ("instanceId", "pid")),
            status="tested",
            degraded=completed.degraded,
            finish_reason=completed.events[-1].payload.get("finish_reason"),
            provider_calls=provider.calls,
            provider_failures=provider.failures,
            observation_meta=provider.observation_meta,
            tool_diagnostics=provider.tool_diagnostics,
            provider_request_count=len(provider.images),
            provider_image_hashes=provider.images,
            action_outcomes=provider.outcomes,
            ledger_states=[r.state.value for r in rows],
            visual_count=sum(len(r.result_envelope.visual_refs) for r in rows if r.result_envelope),
            independent_effect={
                "counter_delta": after["count"] - baseline["count"],
                "text_changed": after["text"] != baseline["text"],
                "unicode_marker_present": marker in after["text"],
                "q_delta": after["text"].count("q") - baseline["text"].count("q"),
                "Y_delta": after["text"].count("Y") - baseline["text"].count("Y"),
                "scroll_delta": after["scrollOffset"] - baseline["scrollOffset"],
                "secure_before": baseline["secureFieldPopulated"],
                "secure_after": after["secureFieldPopulated"],
            },
            active_turn=products.session.log.has_active_turn,
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
    result.pop("baseline_json", None)
    if provider.real._client:
        await provider.real._client.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="Real DeepSeek + native Morrow fixture gate")
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--allow-real-provider", action="store_true", required=True)
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--cases", default="observe,click,type_text,press_key,hotkey,scroll,secure")
    parser.add_argument("--delivery", choices=("background", "foreground"), default="background")
    parser.add_argument("--mode", choices=("semantic", "hybrid"), default="semantic")
    args = parser.parse_args()
    key = (
        sys.stdin.readline().strip()
        if args.credential_stdin
        else os.environ.get("DEEPSEEK_API_KEY") or getpass("DeepSeek API key: ")
    )
    if not key:
        raise SystemExit("credential_missing")
    all_results = []

    async def campaign():
        sdk = load_sdk()
        for case in args.cases.split(","):
            with tempfile.TemporaryDirectory(prefix="morrow-real-provider-") as temporary:
                try:
                    result = await run_fixture(
                        args.fixture_state_file,
                        sdk,
                        Path(temporary),
                        key,
                        case,
                        args.delivery,
                        args.mode,
                    )
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "case": case,
                        "exception_type": type(exc).__name__,
                    }
            all_results.append(result)
            args.evidence_file.write_text(json.dumps(all_results, indent=2) + "\n")
            print(json.dumps(result), flush=True)

    asyncio.run(campaign())


if __name__ == "__main__":
    main()
