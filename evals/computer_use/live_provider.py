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
import plistlib
import runpy
import signal
import subprocess
import sys
import tempfile
from getpass import getpass
from pathlib import Path
from uuid import uuid4

from campaign_verdict import (
    campaign_verdict,
    fixture_snapshot_evidence,
    recoverable_stale_attempt,
    wheel_inside_region,
)

from morrow.adapters.computer_use.action_inputs import FUNCTIONAL_SDK_VERSION
from morrow.adapters.computer_use.diagnostics import diagnose_host
from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.process_identity import read_process_birth
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


def sdk_frame_metadata(state):
    """Semantic SDK reads legitimately have no screenshot/window frame metadata."""
    bounds = state.window_bounds
    return {
        "width": state.screenshot_width,
        "height": state.screenshot_height,
        "backing_scale": state.screenshot_scale,
        "window_bounds": {k: getattr(bounds, k) for k in ("x", "y", "width", "height")}
        if bounds is not None
        else None,
    }


class NativeProvider:
    def __init__(self, key, *, transport=None):
        if transport is None:
            from morrow.adapters.models.openai_compatible import OpenAICompatibleProvider

            transport = OpenAICompatibleProvider(
                "https://api.deepseek.com", key, api_model_ids={"fixture": "deepseek-flash"}
            )
        self.real = transport
        self.request_budget = getattr(transport, "turn_budget", 10)
        self.images = []
        self.calls = []
        self.outcomes = []
        self.failures = []
        self.observation_meta = []
        self.tool_diagnostics = []
        self.action_arguments = {}
        self.observed_refs = {}

    async def stream(self, model, messages, tools=(), *, generation=None):
        self.images.append(
            [
                hashlib.sha256(base64.b64decode(p.data)).hexdigest()
                for p in iter_image_parts(messages)
            ]
        )
        if len(self.images) > self.request_budget:
            raise ComputerUseContractError("provider_call_budget")
        for message in messages:
            if message.role == "tool":
                try:
                    envelope = json.JSONDecoder().raw_decode(message.content)[0]
                    result = envelope.get("result", {})
                    self.tool_diagnostics.append(
                        {
                            "call_id": message.tool_call_id,
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
                                    "outcome",
                                }
                            },
                        }
                    )
                    if "elements" in result:
                        self.observed_refs[result.get("observation_id")] = {
                            e["element_ref"] for e in result["elements"] if e.get("element_ref")
                        }
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
                    if call.name == "computer_action":
                        self.action_arguments[call.id] = args.get("action", {})
                    self.calls.append(
                        {
                            "call_id": call.id,
                            "observation_id": args.get("observation_id"),
                            "name": call.name,
                            "operation": args.get("operation"),
                            "action": args.get("action", {}).get("type"),
                            "key": args.get("action", {}).get("key"),
                            "keys": args.get("action", {}).get("keys"),
                            "direction": args.get("action", {}).get("direction"),
                            "amount": args.get("action", {}).get("amount"),
                            "coordinate_target": args.get("action", {}).get("x") is not None,
                            "element_target": bool(args.get("action", {}).get("element_ref")),
                            "include_image": args.get("include_image"),
                            "button": args.get("action", {}).get("button", "left")
                            if args.get("action", {}).get("type") == "click"
                            else None,
                            "count": args.get("action", {}).get("count", 1)
                            if args.get("action", {}).get("type") == "click"
                            else None,
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
    path: Path,
    sdk,
    root: Path,
    key: str,
    case: str,
    delivery: str,
    mode: str,
    image_edge: int = 1920,
    controller_provider=None,
) -> dict:
    helpers = runpy.run_path(str(Path(__file__).with_name("native_text.py")))
    counter = runpy.run_path(str(Path(__file__).with_name("native_counter.py")))
    readonly = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    before = helpers["text_oracle"](path, counter["counter_oracle"])
    marker = f"Morrow-loop-{uuid4().hex[:8]}中文🧭"
    require(marker not in before["text"], "fixture_marker_present")
    result = {
        "schema_version": 2,
        "scripted_provider": bool(getattr(controller_provider, "scripted_provider", False)),
        "provider_model": (
            "scripted_fixture_transport"
            if getattr(controller_provider, "scripted_provider", False)
            else "external_live_controller"
            if controller_provider
            else "deepseek-flash"
        ),
        "case": case,
        "mode": mode,
        "delivery": delivery,
        "image_edge": image_edge,
        "shifted_window": os.environ.get("MORROW_FIXTURE_SHIFTED") == "1",
        "sdk_input_entries": 0,
        "approval_count": 0,
        "approval_decisions": [],
        "raw_evidence_only": False,
    }
    result["baseline_json"] = path.read_text()
    app = build_application(state_root=root / "state", credentials=MemoryCredentialStore())
    provider = NativeProvider(key, transport=controller_provider)
    app.registry.register(
        "acceptance-native-real-provider",
        lambda *_: provider,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function",
            input_types=("text", "image") if mode == "hybrid" else ("text",),
        ),
    )
    credential = CredentialRef(ref="provider:fixture:live-memory", version=1)
    # Preparation requires a binding; this synthetic value never leaves receipt mode.
    app.credentials.set(credential.ref, "receipt-only-no-network" if controller_provider else key)
    settings = ComputerUseSettings(
        enabled=True, mode=ComputerUseMode(mode), image_long_edge_px=image_edge
    )
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "fixture": ProviderConfig(
                        adapter="acceptance-native-real-provider",
                        base_url="https://controller.invalid"
                        if controller_provider
                        else "https://api.deepseek.com",
                        credential_ref=credential,
                        models={
                            "fixture": ProviderModelConfig(
                                api_model_id="external-live-controller"
                                if controller_provider
                                else "deepseek-flash"
                            )
                        },
                    )
                },
                "active_model": ModelRef(provider_id="fixture", model_id="fixture"),
                "runtime_policy": RuntimePolicyOverrides(computer_use=settings),
            }
        ),
        expected_revision=config.revision,
    )

    class Native(readonly["_DiagnosedSession"]):
        async def get_window_state(self, request):
            state = await super().get_window_state(request)
            result["sdk_fields"] = [
                {"label": e.label, "value": e.value}
                for e in state.elements
                if e.role in {"AXTextField", "AXSecureTextField"}
            ]
            self._node_meta = {
                e.element_token: {
                    "role": e.role,
                    "label": e.label,
                    "enabled": e.enabled,
                    "actions": e.actions,
                    "frame": (
                        {k: getattr(e.frame, k) for k in ("x", "y", "w", "h")} if e.frame else None
                    ),
                }
                for e in state.elements
                if e.element_token
            }
            self._frame_meta = sdk_frame_metadata(state)
            return state

        async def call_tool(self, name, content):
            if name == "read_element_attribute":
                result["sdk_attribute_reads"] = result.get("sdk_attribute_reads", 0) + 1
                payload = json.loads(content)
                proof = result.get("approved_reference", {})
                returned = await self._native.call_tool(name, content)
                try:
                    body = json.loads(returned.structured_json)
                except (ValueError, TypeError):
                    body = {}
                result.setdefault("attribute_read_proofs", []).append(
                    {
                        "native_token_matches_observed_sdk_token": proof.get(
                            "observed_token_sha256"
                        )
                        == hashlib.sha256(payload["element_token"].encode()).hexdigest(),
                        "attribute": payload["attribute"],
                        "identity": body.get("identity"),
                        "status": body.get("status"),
                        "value": body.get("value"),
                        "is_error": returned.is_error,
                        "degraded": returned.degraded,
                    }
                )
                return returned
            result["sdk_input_entries"] += 1
            if result["sdk_input_entries"] > 1:
                raise ComputerUseContractError("fixture_action_repeated")
            result["sdk_method"] = name
            payload = json.loads(content)
            if payload.get("element_token"):
                proof = result.get("approved_reference", {})
                result.setdefault("reference_proofs", []).append(
                    {
                        **proof,
                        "native_token_sha256": hashlib.sha256(
                            payload["element_token"].encode()
                        ).hexdigest(),
                        "native_token_matches_observed_sdk_token": proof.get(
                            "observed_token_sha256"
                        )
                        == hashlib.sha256(payload["element_token"].encode()).hexdigest(),
                    }
                )
            result["sdk_target"] = self._node_meta.get(payload.get("element_token"))
            result["sdk_coordinate"] = {k: payload.get(k) for k in ("x", "y")}
            returned = await self._native.call_tool(name, content)
            from morrow.adapters.computer_use.session import outcome_from_tool

            result["sdk_action_outcome"] = outcome_from_tool(returned).model_dump(mode="json")
            return returned

        async def click(self, request):
            result["sdk_input_entries"] += 1
            if result["sdk_input_entries"] > 1:
                raise ComputerUseContractError("fixture_action_repeated")
            result["sdk_method"] = "click"
            result["sdk_click"] = {
                "count": request.count,
                "button": request.button.name,
                "delivery": request.delivery_mode.name,
            }
            position = request.position
            result["sdk_target"] = self._node_meta.get(getattr(position, "element_token", None))
            result["sdk_coordinate"] = {k: getattr(position, k, None) for k in ("x", "y")}
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
            previous = [
                c
                for c in provider.calls
                if c.get("name") == "computer_action" and c.get("call_id") != request.call_id
            ]
            require(
                result["approval_count"] <= 3
                and result["sdk_input_entries"] == 0
                and all(recoverable_stale_attempt(c, provider.tool_diagnostics) for c in previous),
                "fixture_approval_repeated",
            )
            current = helpers["text_oracle"](path, counter["counter_oracle"])
            arguments = provider.action_arguments[request.call_id]
            session = owner._session
            result["selected_frame"] = session._native._frame_meta
            if reference := arguments.get("element_ref"):
                record = session._registry.element(reference)
                result["selected_node"] = session._native._node_meta.get(record.token)
                call = next(c for c in provider.calls if c.get("call_id") == request.call_id)
                observation_id = call.get("observation_id")
                result["approved_reference"] = {
                    "call_id": request.call_id,
                    "observation_id": observation_id,
                    "element_ref_sha256": hashlib.sha256(reference.encode()).hexdigest(),
                    "observed_token_sha256": hashlib.sha256(record.token.encode()).hexdigest(),
                    "same_ref_observed_then_dispatched": reference
                    in provider.observed_refs.get(observation_id, ()),
                }
            current_state = json.loads(path.read_bytes())
            initial_state = json.loads(result["baseline_json"])
            require(
                all(
                    current_state[k] == initial_state[k]
                    for k in (
                        "instanceId",
                        "pid",
                        "count",
                        "text",
                        "liveText",
                        "liveSecureText",
                        "scrollOffset",
                        "mouseClickCounts",
                        "rightMouseEvents",
                        "keyDownCharacters",
                        "keyUpEvents",
                        "pointerEvents",
                    )
                )
                and current["window_id"] == before["window_id"],
                "fixture_changed_before_input",
            )
            decision = ToolApprovalDecision(approved=case != "denied")
            result["approval_decisions"].append(
                {"call_id": request.call_id, "approved": decision.approved}
            )
            return decision

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
        async with asyncio.timeout(15):
            while True:
                catalog = await lifecycle.discover_local_candidates(
                    settings, authority=TRUSTED_COMPUTER_USE_AUTHORITY
                )
                candidates = [
                    item
                    for item in catalog.candidates
                    if item.app.bundle_id == FIXTURE_BUNDLE_ID
                    and (record := owner._candidates.resolve(item.candidate_id)).pid
                    == before["pid"]
                    and record.window_id == before["window_id"]
                ]
                if candidates:
                    break
                await asyncio.sleep(0.05)
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
            "double_click": "Double-click Increment once using element_ref, button left and count 2. Omit x and y entirely.",
            "right_click": "Right-click Increment once using element_ref, button right and count 1. Omit x and y entirely.",
            "postcondition_text": f"Insert {marker} once in Unicode field with type_text and postcondition {{type: text_appears, text: {marker}}}.",
            "postcondition_exists": "Click Increment once with postcondition {type: element_exists, selector: {role: axbutton, label: Increment}}.",
            "postcondition_attribute": "Click Increment once with postcondition {type: attribute_equals, element_ref: the SAME Increment reference, attribute: enabled, value: true} where value is a string.",
            "denied": "Click Increment once. This test approval adapter will deny it. Accept denial and stop.",
            "coordinate_click": "Click Increment exactly once using image x,y coordinates. Omit element_ref entirely.",
            "coordinate_scroll": "Scroll down by amount 3 once using image x,y coordinates at the center of the rows scroll area. Omit element_ref entirely.",
            "type_text": f"Insert {marker} exactly once into the ordinary Unicode text field using type_text.",
            "press_key": "Send the q key once to the ordinary Unicode text field using press_key.",
            "commit": 'Use press_key with key="enter" (lowercase) once in the ordinary text field with the live seed CommitSeed2026 to commit it. Do not insert or replace text.',
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
            + (
                " This deliberately tests unsupported foreground wheel delivery: call once "
                "to collect the bounded refusal. Do not change delivery, direction or amount."
                if case == "coordinate_scroll"
                and delivery == "foreground"
                and sdk.__version__ != FUNCTIONAL_SDK_VERSION
                else ""
            )
            + (
                " This deliberately tests the unsupported background double-click boundary: "
                "attempt it once to collect the bounded refusal. Do not change delivery, "
                "button or count."
                if case == "double_click"
                and delivery == "background"
                and sdk.__version__ != FUNCTIONAL_SDK_VERSION
                else ""
            )
            + " Use only computer tools. Deliver at most ONE native action. Never retry unknown "
            "or completed. A proven stale_observation/not_started may recover using a new "
            "observation and new refs, with at most three action attempts. After the result, "
            "give a short factual summary and stop. Do not ask for confirmation."
        )
        completed = await products.orchestrator.dispatch(prompt)
        after = json.loads(path.read_text())
        baseline = json.loads(result.pop("baseline_json"))
        result["fixture_snapshot_required"] = True
        result["fixture_snapshot"] = fixture_snapshot_evidence(baseline, after)
        rows = factory.journal.list_session_executions(
            factory.workspace_id, products.session.session_id
        )
        result.update(
            fixture_window=baseline["window"],
            fixture_foreground={
                "is_key": baseline.get("windowIsKey"),
                "active": baseline.get("appActive"),
            },
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
        normal = case != "secure"
        expected_text = {"press_key": "q", "hotkey": "Y", "secure": "SyntheticOnly2026"}.get(
            case, marker
        )
        live_key = "liveText" if normal else "liveSecureText"
        result["independent_effect"].update(
            exact_live_insert=after.get(live_key) == baseline.get(live_key, "") + expected_text,
            correct_field=after.get("lastEditedField")
            == ("fixture-text" if normal else "fixture-secure"),
            live_text_changed=after.get("liveText") != baseline.get("liveText"),
            committed_text_changed=after["text"] != baseline["text"],
            committed_seed=case == "commit"
            and baseline.get("liveText") == "CommitSeed2026"
            and baseline["text"] == ""
            and after["text"] == "CommitSeed2026",
            secure_live_before=bool(baseline.get("liveSecureText")),
            secure_live_after=bool(after.get("liveSecureText")),
            key_down_characters=after.get("keyDownCharacters", [])[
                len(baseline.get("keyDownCharacters", [])) :
            ],
            key_down_fields=after.get("keyDownFields", [])[
                len(baseline.get("keyDownFields", [])) :
            ],
            key_up_events=after.get("keyUpEvents", 0) - baseline.get("keyUpEvents", 0),
            text_change_events=after.get("textChangeEvents", 0)
            - baseline.get("textChangeEvents", 0),
            secure_change_events=after.get("secureChangeEvents", 0)
            - baseline.get("secureChangeEvents", 0),
            mouse_click_counts=after.get("mouseClickCounts", [])[
                len(baseline.get("mouseClickCounts", [])) :
            ],
            right_mouse_delta=after.get("rightMouseEvents", 0)
            - baseline.get("rightMouseEvents", 0),
            pointer_events=after.get("pointerEvents", [])[len(baseline.get("pointerEvents", [])) :],
            unchanged=all(
                after[k] == baseline[k]
                for k in (
                    "count",
                    "text",
                    "liveText",
                    "liveSecureText",
                    "scrollOffset",
                    "mouseClickCounts",
                    "rightMouseEvents",
                )
            ),
        )
        region = baseline.get("scrollRegion")
        wheels = [e for e in result["independent_effect"]["pointer_events"] if e["kind"] == "wheel"]
        result["fixture_scroll_region"] = region
        result["independent_effect"]["wheel_inside_scroll_region"] = wheel_inside_region(
            region, wheels
        )
        artifact_hashes = {
            ref.sha256
            for row in rows
            if row.result_envelope
            for ref in row.result_envelope.visual_refs
        }
        provider_hashes = {h for request_hashes in provider.images for h in request_hashes}
        result["artifact_image_hashes"] = sorted(artifact_hashes)
        result["image_hashes_match"] = bool(artifact_hashes) and artifact_hashes == provider_hashes
        result["raw_status"] = result["status"]
        result.update(campaign_verdict(result))
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
    if controller_provider is not None and hasattr(controller_provider, "decisions"):
        result["controller_decisions"] = controller_provider.decisions
    if client := getattr(provider.real, "_client", None):
        await client.close()
    return result


class FixtureProcess:
    """A LaunchServices child, bound to the actual fixture PID and its kernel birth."""

    def __init__(self, launcher, pid: int):
        self.launcher = launcher
        self.pid = pid
        self.birth = read_process_birth(pid)

    def poll(self):
        try:
            if read_process_birth(self.pid) != self.birth:
                return 0
        except ComputerUseContractError:
            return 0
        return None

    def terminate(self):
        if self.poll() is None:
            os.kill(self.pid, signal.SIGTERM)

    def wait(self, *, timeout: float):
        return self.launcher.wait(timeout=timeout)


async def start_fixture(
    app: Path, state_file: Path, *, edit_seed: str = "", foreground: bool = False
):
    """Launch only the explicit synthetic bundle; wait for its independent identity oracle."""
    metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    require(metadata.get("CFBundleIdentifier") == FIXTURE_BUNDLE_ID, "fixture_bundle_invalid")
    require(
        Path(metadata["MorrowFixtureStateDirectory"]).resolve() == state_file.parent.resolve(),
        "fixture_state_path_invalid",
    )
    state_file.unlink(missing_ok=True)
    binary = app / "Contents/MacOS" / metadata["CFBundleExecutable"]
    command = ["/usr/bin/open", "-W", "-n", "-a", str(app)]
    if not foreground:
        command.append("-g")
    command.extend(
        [
            "--env",
            f"MORROW_FIXTURE_EDIT_SEED={edit_seed}",
            "--env",
            f"MORROW_FIXTURE_FOREGROUND={'1' if foreground else '0'}",
        ]
    )
    launcher = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process = None
    try:
        async with asyncio.timeout(15):
            while launcher.poll() is None:
                try:
                    state = json.loads(state_file.read_bytes())
                    if process is None and type(state.get("pid")) is int and state["pid"] > 0:
                        executable = subprocess.check_output(
                            ["ps", "-p", str(state["pid"]), "-o", "comm="], text=True
                        ).strip()
                        require(
                            Path(executable).resolve() == binary.resolve(), "fixture_pid_invalid"
                        )
                        process = FixtureProcess(launcher, state["pid"])
                    if (
                        process is not None
                        and state["pid"] == process.pid
                        and state.get("window")
                        and state["revision"] > 0
                        and state.get("scrollRegion")
                        and (not foreground or state.get("windowIsKey") and state.get("appActive"))
                        and (not edit_seed or state.get("liveText") == edit_seed)
                    ):
                        return process
                except (OSError, ValueError, KeyError):
                    pass
                await asyncio.sleep(0.05)
        raise ComputerUseContractError("fixture_launch_failed")
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
        elif process is None and launcher.poll() is None:
            launcher.terminate()
        await asyncio.to_thread(launcher.wait, timeout=10)
        raise


def main():
    parser = argparse.ArgumentParser(description="Real DeepSeek + native Morrow fixture gate")
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--allow-real-provider", action="store_true", required=True)
    parser.add_argument("--credential-stdin", action="store_true")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--image-edge", type=int, default=1920)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument(
        "--fixture-app", type=Path, help="Launch a fresh controlled fixture for every case"
    )
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--cases", default="observe,click,type_text,press_key,hotkey,scroll,secure")
    parser.add_argument("--delivery", choices=("background", "foreground"), default="background")
    parser.add_argument("--mode", choices=("semantic", "hybrid"), default="semantic")
    args = parser.parse_args()
    if (args.matrix or len(args.cases.split(",")) > 1) and not args.fixture_app:
        parser.error("multiple cases require --fixture-app for fresh independent state")
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
        variants = [
            (args.mode, args.delivery, case, args.image_edge, False)
            for case in args.cases.split(",")
        ]
        if args.matrix:
            semantic = "observe,click,type_text,press_key,hotkey,commit,secure,postcondition_text,postcondition_exists,postcondition_attribute,denied,scroll".split(
                ","
            )
            visual = "observe,coordinate_click,coordinate_scroll,double_click,right_click".split(
                ","
            )
            variants = [
                (mode, delivery, case, 1920, False)
                for mode, cases in [("semantic", semantic), ("hybrid", visual)]
                for delivery in ("foreground", "background")
                for case in cases
            ]
            variants += [
                ("hybrid", "foreground", "coordinate_click", 640, False),
                ("hybrid", "background", "coordinate_scroll", 640, True),
            ]
        for mode, delivery, case, image_edge, shifted in variants:
            process = None
            with tempfile.TemporaryDirectory(prefix="morrow-real-provider-") as temporary:
                try:
                    if len(variants) > 1:
                        evidence = Path(temporary) / "case.json"
                        child = await asyncio.create_subprocess_exec(
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--allow-desktop",
                            "--allow-real-provider",
                            "--credential-stdin",
                            "--fixture-app",
                            str(args.fixture_app),
                            "--fixture-state-file",
                            str(args.fixture_state_file),
                            "--evidence-file",
                            str(evidence),
                            "--cases",
                            case,
                            "--delivery",
                            delivery,
                            "--mode",
                            mode,
                            "--image-edge",
                            str(image_edge),
                            env={
                                **os.environ,
                                "MORROW_FIXTURE_SHIFTED": "1" if shifted else "0",
                                "MORROW_FIXTURE_EDIT_SEED": "CommitSeed2026"
                                if case == "commit"
                                else "",
                            },
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await child.communicate((key + "\n").encode())
                        result = json.loads(evidence.read_bytes())[0]
                        all_results.append(result)
                        args.evidence_file.write_text(json.dumps(all_results, indent=2) + "\n")
                        print(
                            json.dumps(
                                {
                                    k: result.get(k)
                                    for k in (
                                        "case",
                                        "mode",
                                        "delivery",
                                        "status",
                                        "reason",
                                        "verdict_reason",
                                        "sdk_input_entries",
                                    )
                                }
                            ),
                            flush=True,
                        )
                        continue
                    if args.fixture_app:
                        process = await start_fixture(
                            args.fixture_app,
                            args.fixture_state_file,
                            edit_seed="CommitSeed2026" if case == "commit" else "",
                            foreground=delivery == "foreground",
                        )
                    result = await run_fixture(
                        args.fixture_state_file,
                        sdk,
                        Path(temporary),
                        key,
                        case,
                        delivery,
                        mode,
                        image_edge,
                    )
                except Exception as exc:
                    result = {
                        "status": "failed",
                        "case": case,
                        "exception_type": type(exc).__name__,
                    }
                finally:
                    if process is not None:
                        process.terminate()
                        await asyncio.to_thread(process.wait, timeout=10)
            all_results.append(result)
            args.evidence_file.write_text(json.dumps(all_results, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        k: result.get(k)
                        for k in (
                            "case",
                            "mode",
                            "delivery",
                            "status",
                            "reason",
                            "verdict_reason",
                            "sdk_input_entries",
                        )
                    }
                ),
                flush=True,
            )

    asyncio.run(campaign())
    if any(r.get("status") != "passed" for r in all_results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
