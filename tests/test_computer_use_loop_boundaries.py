"""Actual SDK adapter and owner in complete offline observation/action tool cycles."""

import asyncio
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.adapters.state.operational import SystemStoreClock
from morrow.application.agent_runs.preparation import AgentRunPreparationError
from morrow.application.computer_requests import ComputerUseSelection
from morrow.application.computer_settings import ComputerUseSettingsService
from morrow.application.computer_use import ComputerUseLifecycle
from morrow.bootstrap import build_session_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseImageShare,
    ComputerUsePreflight,
)
from morrow.core.execution import (
    RecoveryClassification,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.image_tokens import iter_image_parts
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings, RuntimePolicyOverrides
from morrow.testing import FixedIdSource, ScriptedModelProvider
from test_agent_run_preparation import _app, _configure_active, _dispatch_prepared
from test_computer_use_driver import _Native, _process_birth, _sdk
from test_computer_use_lifecycle import _Lease, _open
from test_computer_use_loop import Approval, tool


class ReferenceProvider(ScriptedModelProvider):
    """Use opaque references from committed tool replies, never native identities."""

    def __init__(self, *, allow_final=False):
        self.allow_final = allow_final
        super().__init__([tool("discover", "computer_observe", {"operation": "discover"})])
        self.request_count = 0
        self.image_pixels = []
        self.image_sizes = []

    async def stream(self, model, messages, tools=(), generation=None):
        pixels = []
        for part in iter_image_parts(messages):
            with Image.open(io.BytesIO(base64.b64decode(part.data))) as image:
                self.image_sizes.append(image.size)
                pixels.append(image.convert("RGB").getpixel((0, 0)))
        self.image_pixels.append(pixels)
        if self.request_count:
            reply = next(message for message in reversed(messages) if message.role == "tool")
            # Hydration appends image provenance after the original JSON tool result.
            result = json.JSONDecoder().raw_decode(reply.content)[0]["result"]
            if self.request_count == 1:
                self.responses.append(
                    tool(
                        "observe",
                        "computer_observe",
                        {
                            "operation": "window",
                            "target_ref": result["targets"][0]["target_ref"],
                        },
                    )
                )
            elif self.request_count == 2:
                self.responses.append(
                    tool(
                        "action",
                        "computer_action",
                        {
                            "observation_id": result["observation_id"],
                            "action": {
                                "type": "click",
                                "element_ref": result["elements"][0]["element_ref"],
                            },
                        },
                    )
                )
            elif self.allow_final:
                self.responses.append(
                    "The click returned and a fresh screen was observed."
                    if result["outcome"]["status"] == "completed"
                    else "The window changed; I need a new observation before clicking."
                )
            else:
                raise AssertionError("cancelled run must not request a final answer")
        self.request_count += 1
        async for event in super().stream(model, messages, tools, generation):
            yield event


@pytest.mark.parametrize(
    "boundary",
    [
        "inflight_cancel",
        "window_changed",
        "window_replaced",
        "process_replaced",
        "hybrid",
        "hybrid_continue",
        "hybrid_config",
        "hybrid_startup",
    ],
)
async def test_real_sdk_boundary_preserves_safe_tool_results(tmp_path, boundary):
    app = _app(tmp_path)
    app.registry.register(
        "fake-adapter",
        lambda config, credential: ReferenceProvider(allow_final=boundary != "inflight_cancel"),
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function",
            input_types=("text", "image") if boundary.startswith("hybrid") else ("text",),
        ),
    )
    _configure_active(app)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(
                        enabled=boundary != "hybrid_startup",
                        mode=ComputerUseMode.HYBRID
                        if boundary.startswith("hybrid")
                        else ComputerUseMode.SEMANTIC,
                    )
                ),
            }
        ),
        expected_revision=config.revision,
    )
    entered, release, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finished = []

    class Native(_Native):
        changed = False

        async def list_windows(self, payload):
            result = await super().list_windows(payload)
            if self.changed and boundary == "window_changed":
                result.windows[0].bounds.x = 100
            if self.changed and boundary == "window_replaced":
                result.windows[0].window_id = 9002
            return result

        async def get_window_state(self, payload):
            state = await super().get_window_state(payload)
            if boundary.startswith("hybrid"):
                buffer = io.BytesIO()
                Image.new("RGB", (20, 10), "blue" if finished else "red").save(buffer, format="PNG")
                state.images[0].data_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")
                state.elements = state.elements[:1]
                state.elements_complete, state.truncated, state.degraded = True, False, False
                state.total_element_count = state.returned_element_count = 1
                state.snapshot_id = "after" if finished else "before"
            return state

        async def click(self, payload):
            self.calls.append(("click", payload))
            call_timeouts.append(owner._session._calls._timeout)
            entered.set()
            if boundary.startswith("hybrid"):
                finished.append("effect")
                return SimpleNamespace(
                    effect=SimpleNamespace(name="CONFIRMED"),
                    delivery=SimpleNamespace(mode=SimpleNamespace(name="FOREGROUND")),
                    error=None,
                    verified=True,
                )
            await release.wait()
            finished.append("effect")
            return SimpleNamespace()

    class Owner(ComputerDriverOwner):
        async def close_run_session(self, request):
            closing.set()
            await super().close_run_session(request)

    native, lease = Native(), _Lease()
    session_settings, diagnosed_settings, call_timeouts = [], [], []

    def configured_session(driver, name, settings):
        session_settings.append(settings)
        return native

    def diagnose(settings):
        diagnosed_settings.append(settings)
        return ComputerUsePreflight(status="unavailable", reason="native_unverified")

    async def shutdown():
        pass

    owner = Owner(
        _sdk(),
        FixedIdSource(),
        SystemStoreClock(),
        session_factory=lambda driver, name: native,
        configured_session_factory=configured_session,
        driver_factory=lambda sdk: SimpleNamespace(shutdown=shutdown),
        lease=lease,
        process_reader=lambda pid: (
            ProcessBirth(2, 0)
            if native.changed and boundary == "process_replaced"
            else _process_birth(pid)
        ),
    )
    lifecycle = ComputerUseLifecycle(
        lambda: owner,
        lambda: ComputerUsePreflight(
            status="unavailable",
            reason="disabled" if boundary == "hybrid_startup" else "native_unverified",
        ),
        native_verified=True,
        run_diagnostic=diagnose,
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    approval = Approval()
    products = build_session_application(
        app,
        identity,
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        computer_use_lifecycle=lifecycle,
        approval_port=approval,
    )
    factory = products.orchestrator.preparation.computer_factory
    if boundary == "hybrid_startup":
        assert not factory.settings.enabled
        ComputerUseSettingsService(app, preflight=lambda settings: None).put(
            ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
            expected_revision=app.global_store.load().revision,
        )
    running = None
    try:
        request = factory.select(
            ComputerUseSelection(
                apps=(ComputerUseAppIdentity(bundle_id="com.example.Notes"),),
                image_share=(
                    ComputerUseImageShare.CONTROLLED_WINDOW
                    if boundary.startswith("hybrid")
                    else ComputerUseImageShare.NONE
                ),
            ),
            products.session,
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        )
        prepared = products.orchestrator.preparation.prepare_new(
            agent_run_id="arun_cancel", computer_request=request
        )
        if boundary.startswith("hybrid"):
            configuration = ComputerUseSettingsService(app, preflight=lambda settings: None)
            if boundary == "hybrid_config":
                configuration.put(
                    ComputerUseSettings(enabled=False),
                    expected_revision=app.global_store.load().revision,
                )
                with pytest.raises(ComputerUseContractError):
                    factory.select(
                        ComputerUseSelection(
                            apps=(ComputerUseAppIdentity(bundle_id="com.example.Notes"),)
                        ),
                        products.session,
                        authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                    )
                ordinary = products.orchestrator.preparation.prepare_new(
                    agent_run_id="arun_disabled"
                )
                assert ordinary.computer_run is None
                assert "computer_observe" not in {
                    tool.function.name for tool in ordinary.tool_executor.definitions
                }
                await ordinary.aclose()
            events = await _dispatch_prepared(products.orchestrator, "Click Save", prepared)
            assert events[-1].payload["finish_reason"] == "stop"
            assert finished == ["effect"] and not lease.held
            assert len(approval.requests) == 1
            assert prepared.provider.image_sizes == [(20, 10)] * 3
            assert prepared.provider.image_pixels == [
                [],
                [],
                [(255, 0, 0)],
                [(255, 0, 0), (0, 0, 255)],
            ]
            journal, ws = factory.journal, factory.workspace_id
            executions = journal.list_session_executions(ws, products.session.session_id)
            assert len(executions) == 3
            action = next(row for row in executions if row.tool_name == "computer_action")
            assert action.state is ToolExecutionState.CLOSED
            assert action.disposition is ToolExecutionDisposition.SUCCEEDED
            replies = [
                message
                for message in products.session.log.messages_view()
                if message.role == "tool"
            ]
            assert [len(message.visual_refs) for message in replies] == [0, 1, 1]
            assert not any(message.input_parts for message in replies)
            assert replies[1].visual_refs[0].sha256 != replies[2].visual_refs[0].sha256
            for row in executions:
                for reference in row.result_envelope.visual_refs:
                    assert reference.tool_execution_id == row.tool_execution_id
                    assert reference.agent_run_id == "arun_cancel"
                    capture = factory.visuals.read(
                        reference, session_id=products.session.session_id
                    )
                    assert (capture.width, capture.height) == (20, 10)
            assert len([name for name, _ in native.calls if name == "click"]) == 1
            assert len([name for name, _ in native.calls if name == "get_window_state"]) == 2
            if boundary in {"hybrid_continue", "hybrid_config"}:
                if boundary == "hybrid_config":
                    assert session_settings[0].mode is ComputerUseMode.HYBRID
                    assert session_settings[0].enabled
                    assert session_settings[0].max_call_seconds == 15
                    configuration.put(
                        ComputerUseSettings(
                            enabled=True,
                            mode=ComputerUseMode.SEMANTIC,
                            max_call_seconds=7,
                            max_run_seconds=120,
                            max_operations=7,
                        ),
                        expected_revision=app.global_store.load().revision,
                    )
                preparation = products.orchestrator.preparation
                frozen = journal.get_agent_run(ws, "arun_cancel").snapshot
                original = journal.get_permission_snapshot_for_run(ws, "arun_cancel")
                with pytest.raises(AgentRunPreparationError):
                    preparation.rehydrate(frozen, agent_run_id="arun_cancel")
                request = factory.select(
                    ComputerUseSelection(
                        apps=(ComputerUseAppIdentity(bundle_id="com.example.Notes"),),
                        image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
                    ),
                    products.session,
                    authority=TRUSTED_COMPUTER_USE_AUTHORITY,
                )
                second = preparation.rehydrate(
                    frozen, agent_run_id="arun_second", computer_request=request
                )
                events = [
                    event
                    async for event in products.orchestrator.runtime.run_turn(
                        products.session,
                        "Click Save again",
                        client_message_id="cmsg-second",
                        prepared=second,
                    )
                ]
                assert events[-1].payload["finish_reason"] == "stop"
                current = journal.get_permission_snapshot_for_run(ws, "arun_second")
                assert current.grant_id != original.grant_id
                assert (
                    current.computer_use_scope.generation > original.computer_use_scope.generation
                )
                assert current.tool_schema_digest == original.tool_schema_digest
                assert second.provider.image_pixels == (
                    [[], [], [], []]
                    if boundary == "hybrid_config"
                    else [[], [], [(0, 0, 255)], [(0, 0, 255), (0, 0, 255)]]
                )
                if boundary == "hybrid_config":
                    assert session_settings[1].mode is ComputerUseMode.SEMANTIC
                    assert session_settings[1].max_run_seconds == 120
                    assert session_settings[1].max_operations == 7
                    assert call_timeouts == [15, 7]
                    assert diagnosed_settings == session_settings
                second_rows = [
                    row
                    for row in journal.list_session_executions(ws, products.session.session_id)
                    if row.agent_run_id == "arun_second"
                ]
                assert len(second_rows) == 3
                assert all(row.grant_id == current.grant_id for row in second_rows)
                assert all(
                    reference.agent_run_id == "arun_second"
                    for row in second_rows
                    for reference in row.result_envelope.visual_refs
                )
                assert len([name for name, _ in native.calls if name == "start_session"]) == 2
                assert len([name for name, _ in native.calls if name == "end_session"]) == 2
                assert len([name for name, _ in native.calls if name == "click"]) == 2
                assert finished == ["effect", "effect"] and not lease.held
            return
        if boundary != "inflight_cancel":
            approval.before_decision = lambda: setattr(native, "changed", True)
            events = await _dispatch_prepared(products.orchestrator, "Click Save", prepared)
            assert events[-1].payload["finish_reason"] == "stop"
            assert not entered.is_set() and finished == [] and not lease.held
            assert len(approval.requests) == 1
            assert not any(name == "click" for name, _ in native.calls)
            journal, ws = factory.journal, factory.workspace_id
            executions = journal.list_session_executions(ws, products.session.session_id)
            action = next(row for row in executions if row.tool_name == "computer_action")
            assert action.state is ToolExecutionState.CLOSED
            assert action.disposition is ToolExecutionDisposition.FAILED
            assert (
                journal.get_approval_for_execution(ws, action.tool_execution_id).consumed_at
                is not None
            )
            reply = products.session.log.messages_view()[-2]
            result = json.loads(reply.content)["result"]
            assert result["outcome"]["status"] == "not_started"
            assert result["outcome"]["error_code"] == "stale_observation"
            assert result["observation"] is None and not reply.visual_refs
            assert prepared.provider.request_count == 4
            return
        running = asyncio.create_task(
            _dispatch_prepared(products.orchestrator, "Click Save", prepared)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        running.cancel()
        await asyncio.wait_for(closing.wait(), timeout=5)
        assert owner.quarantined and lease.held and not running.done()
        assert finished == []
        with pytest.raises(ComputerUseContractError, match="driver_not_activated"):
            await owner.open_run_session(_open())
        release.set()
        events = await running
        assert events[-1].payload["finish_reason"] == "cancelled"
        assert finished == ["effect"] and not lease.held
        assert len([name for name, _ in native.calls if name == "click"]) == 1
        assert len([name for name, _ in native.calls if name == "end_session"]) == 1
        journal, ws = factory.journal, factory.workspace_id
        executions = journal.list_session_executions(ws, products.session.session_id)
        action = next(row for row in executions if row.tool_name == "computer_action")
        assert action.state is ToolExecutionState.CLOSED
        assert action.disposition is ToolExecutionDisposition.UNKNOWN
        assert action.executing_at is not None
        assert not action.result_envelope.ok
        assert action.result_envelope.error_code == "cancelled"
        assert not action.result_envelope.visual_refs
        assert (
            journal.get_approval_for_execution(ws, action.tool_execution_id).consumed_at is not None
        )
        replies = [
            message for message in products.session.log.messages_view() if message.role == "tool"
        ]
        assert len(replies) == 3
        assert not any(message.visual_refs for message in replies)
        assert prepared.provider.request_count == 3
        report = products.persistence.recovery.discover(
            products.session.session_id, products.session.log
        )
        item = next(
            item for item in report.items if item.tool_execution_id == action.tool_execution_id
        )
        assert item.classification is RecoveryClassification.OUTCOME_UNKNOWN
        assert item.blocking
    finally:
        release.set()
        if running is not None and not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await lifecycle.shutdown()
        products.persistence.store_session.close()
