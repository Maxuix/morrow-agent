"""Production composition, real AgentLoop and decoded request images with a fake device."""

import base64
import io
import json
from datetime import UTC, datetime

import pytest
from PIL import Image

from morrow.application.computer_requests import ComputerUseSelection
from morrow.bootstrap import build_session_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import (
    PermissionPreset,
    PermissionProfile,
    ToolCallContext,
    ToolRunContext,
)
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ActionOutcome,
    AxElement,
    ClickAction,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseImageShare,
    CoordinateFrame,
    DiscoverResult,
    Observation,
    ObservedWindow,
    RunSession,
    TargetRef,
    TransientCapture,
)
from morrow.core.domain import sha256_digest
from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState
from morrow.core.image_tokens import iter_image_parts
from morrow.core.models import AssistantMessage, FunctionToolCall, ToolApprovalDecision
from morrow.core.permissions import IsolationLabel
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings, RuntimePolicyOverrides
from morrow.testing import ScriptedModelProvider
from test_agent_run_preparation import _app, _configure_active, _dispatch_prepared


def tool(call_id, name, arguments):
    return AssistantMessage(
        tool_calls=(FunctionToolCall(id=call_id, name=name, arguments=json.dumps(arguments)),)
    )


class Device:
    def __init__(self, scope, *, status="completed"):
        self.scope, self.status = scope, status
        self.actions = []
        self.reads = []
        self.target = TargetRef(
            target_ref="ctarget_loop",
            agent_run_id=scope.agent_run_id,
            generation=scope.generation,
            app=scope.apps[0],
            process_identity="cproc_loop",
            window_identity="cwin_loop",
        )

    async def discover(self, request):
        assert request.scope == self.scope
        return DiscoverResult(targets=(self.target,))

    async def observe(self, request, *, settings):
        self.reads.append(request)
        after = bool(self.actions)
        buffer = io.BytesIO()
        Image.new("RGB", (8, 6), "blue" if after else "red").save(buffer, format="PNG")
        capture = TransientCapture(buffer.getvalue(), "image/png", 8, 6)
        observation = Observation(
            observation_id="cobs_after" if after else "cobs_before",
            target_ref=self.target.target_ref,
            agent_run_id=self.scope.agent_run_id,
            generation=self.scope.generation,
            bundle_id=self.scope.apps[0].bundle_id,
            process_identity=self.target.process_identity,
            window_identity=self.target.window_identity,
            capture_digest=sha256_digest(capture.content),
            captured_at=datetime.now(UTC),
            frame=CoordinateFrame(width=8, height=6),
            elements=(
                AxElement(
                    element_ref="celem_after" if after else "celem_before",
                    depth=1,
                    role="axbutton",
                    label="After" if after else "Before",
                ),
            ),
        )
        return ObservedWindow(
            observation,
            capture if request.include_image else None,
            image_error="image_missing" if after and self.status == "image_failed" else None,
        )

    async def execute_one(self, request, *, settings, authority):
        authority()
        assert request.observation.observation_id == "cobs_before"
        assert request.action.element_ref == "celem_before"
        self.actions.append(request)
        return ActionOutcome(
            status="completed" if self.status == "image_failed" else self.status,
            delivery=request.delivery,
        )

    def invalidate(self):
        pass


class Lifecycle:
    def __init__(self, status):
        self.status, self.device = status, None
        self.calls = []

    async def open_run_session(self, request):
        self.calls.append("open")
        self.device = Device(request.scope, status=self.status)
        return RunSession(
            run_session_id="crun_loop",
            agent_run_id=request.agent_run_id,
            generation=request.scope.generation,
        )

    def session_for(self, run):
        return self.device

    async def close_run_session(self, request):
        self.calls.append("close")


class Approval:
    def __init__(self):
        self.requests = []
        self.before_decision = lambda: None

    async def request(self, request):
        self.requests.append(request)
        self.before_decision()
        return ToolApprovalDecision(approved=True)


class ImageProvider(ScriptedModelProvider):
    def __init__(self):
        super().__init__(
            [
                tool("discover", "computer_observe", {"operation": "discover"}),
                tool(
                    "observe",
                    "computer_observe",
                    {"operation": "window", "target_ref": "ctarget_loop"},
                ),
                tool(
                    "action",
                    "computer_action",
                    {
                        "observation_id": "cobs_before",
                        "action": {"type": "click", "element_ref": "celem_before"},
                    },
                ),
                "The controlled button now shows After.",
            ]
        )
        self.image_pixels = []
        self.before_response = lambda: None

    async def stream(self, model, messages, tools=(), generation=None):
        self.before_response()
        pixels = []
        for part in iter_image_parts(messages):
            with Image.open(io.BytesIO(base64.b64decode(part.data))) as image:
                assert image.size == (8, 6)
                pixels.append(image.convert("RGB").getpixel((0, 0)))
        self.image_pixels.append(pixels)
        async for event in super().stream(model, messages, tools, generation):
            yield event


@pytest.mark.parametrize(
    "status", ["completed", "unknown", "revoked", "revoked_before_intent", "stale", "image_failed"]
)
async def test_real_loop_observes_approves_actions_and_hydrates_fresh_png(tmp_path, status):
    app = _app(tmp_path)
    app.registry.register(
        "fake-adapter",
        lambda config, credential: ImageProvider(),
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", input_types=("text", "image")
        ),
    )
    _configure_active(app)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
                )
            }
        ),
        expected_revision=config.revision,
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    lifecycle, approval = (
        Lifecycle(status if status in {"unknown", "image_failed"} else "completed"),
        Approval(),
    )
    products = build_session_application(
        app,
        identity,
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        computer_use_lifecycle=lifecycle,
        approval_port=approval,
    )
    try:
        preparation = products.orchestrator.preparation
        factory = preparation.computer_factory
        selected = factory.select(
            ComputerUseSelection(
                apps=(ComputerUseAppIdentity(bundle_id="com.example.Controlled"),),
                image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
            ),
            products.session,
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        )
        prepared = preparation.prepare_new(agent_run_id="arun_loop", computer_request=selected)
        if status == "image_failed":
            prepared.provider.responses[-1] = (
                "The action returned, but I could not verify its screen."
            )
        if status == "completed":

            def reject_cross_context_preview():
                calls = (len(lifecycle.device.actions), len(lifecycle.device.reads))
                for run_id, session_id in [
                    ("arun_other", products.session.session_id),
                    ("arun_loop", "ses_other"),
                ]:
                    context = ToolCallContext(
                        run=ToolRunContext(run_id=run_id, session_id=session_id),
                        call_id="untrusted_preview",
                        tool_name="computer_action",
                        ordinal=3,
                        total=3,
                        result_limit=65536,
                    )
                    with pytest.raises(ComputerUseContractError, match="execution_not_authorized"):
                        prepared.computer_run.observations.action_preview(
                            "cobs_before",
                            ClickAction(type="click", element_ref="celem_before"),
                            context,
                        )
                assert calls == (len(lifecycle.device.actions), len(lifecycle.device.reads))

            approval.before_decision = reject_cross_context_preview
        if status in {"revoked", "revoked_before_intent"}:
            prepared.provider.responses[-1] = "The desktop permission was revoked; I did not click."

            def revoke():
                snapshot = factory.journal.get_permission_snapshot_for_run(
                    factory.workspace_id, "arun_loop"
                )
                products.api.revoke_grant(
                    snapshot.grant_id, reason="local stop before action", expected_row_version=1
                )

            if status == "revoked":
                approval.before_decision = revoke
            else:

                def revoke_before_intent():
                    if len(prepared.provider.image_pixels) == 2:
                        revoke()

                prepared.provider.before_response = revoke_before_intent
        if status == "stale":
            prepared.provider.responses.insert(
                3,
                tool(
                    "stale_action",
                    "computer_action",
                    {
                        "observation_id": "cobs_before",
                        "action": {"type": "click", "element_ref": "celem_before"},
                    },
                ),
            )
        events = await _dispatch_prepared(
            products.orchestrator, "Click the controlled button", prepared
        )
        assert events[-1].payload["finish_reason"] == "stop"
        expected_images = [[], [], [(255, 0, 0)], [(255, 0, 0), (0, 0, 255)]]
        if status in {"revoked", "revoked_before_intent", "image_failed"}:
            expected_images[-1] = [(255, 0, 0)]
        if status == "stale":
            expected_images.append(expected_images[-1])
        assert prepared.provider.image_pixels == expected_images
        assert lifecycle.calls == ["open", "close"]
        assert len(lifecycle.device.actions) == int(
            status not in {"revoked", "revoked_before_intent"}
        )
        assert len(lifecycle.device.reads) == (
            1 if status in {"revoked", "revoked_before_intent"} else 2
        )
        assert len(approval.requests) == (
            0 if status == "revoked_before_intent" else 2 if status == "stale" else 1
        )
        if approval.requests:
            preview = "\n".join(approval.requests[0].preview)
            assert "动作：左键单击" in preview
            assert "应用：com.example.Controlled" in preview
            assert "投递：前台" in preview and "分享受控窗口图像" in preview
            assert "独立桌面窗口授权" in preview
            assert "cobs_" not in preview and "celem_" not in preview and "cproc_" not in preview
        executions = factory.journal.list_session_executions(
            factory.workspace_id, products.session.session_id
        )
        assert len(executions) == (4 if status == "stale" else 3)
        snapshot = factory.journal.get_permission_snapshot_for_run(
            factory.workspace_id, "arun_loop"
        )
        assert all(
            row.grant_id == snapshot.grant_id and row.isolation is IsolationLabel.COMPUTER_USE_HOST
            for row in executions
        )
        assert products.persistence.permissions.active_tool_grant_evidence(
            snapshot, "run_command", now=factory.clock.now()
        ) == (None, None)
        action = (
            next(
                row
                for row in executions
                if row.tool_name == "computer_action" and row.result_envelope.visual_refs
            )
            if status not in {"revoked", "revoked_before_intent", "image_failed"}
            else next(row for row in executions if row.tool_name == "computer_action")
        )
        assert action.state is ToolExecutionState.CLOSED
        assert action.disposition is (
            ToolExecutionDisposition.UNKNOWN
            if status == "unknown"
            else ToolExecutionDisposition.DENIED
            if status in {"revoked", "revoked_before_intent"}
            else ToolExecutionDisposition.SUCCEEDED
        )
        consumed = factory.journal.get_approval_for_execution(
            factory.workspace_id, action.tool_execution_id
        )
        if status not in {"revoked", "revoked_before_intent"}:
            assert consumed.consumed_at is not None
        if status == "revoked_before_intent":
            assert consumed is None
        if status in {"revoked", "revoked_before_intent"}:
            assert action.result_envelope is None
        else:
            assert len(action.result_envelope.visual_refs) == int(status != "image_failed")
        if status == "stale":
            rejected = next(
                row
                for row in executions
                if row.tool_name == "computer_action" and not row.result_envelope.visual_refs
            )
            assert rejected.disposition is ToolExecutionDisposition.FAILED
        messages = products.session.log.messages_view()
        replies = [message for message in messages if message.role == "tool"]
        assert len(replies) == (4 if status == "stale" else 3)
        assert len(replies[1].visual_refs) == 1
        assert len(replies[2].visual_refs) == int(
            status not in {"revoked", "revoked_before_intent", "image_failed"}
        )
        if status == "image_failed":
            payload = json.loads(replies[2].content)["result"]
            assert payload["outcome"]["status"] == "completed"
            assert payload["observation_error"] == "image_missing"
            assert payload["observation"] is None
        if status == "stale":
            assert not replies[-1].visual_refs
        assert all(not message.input_parts for message in replies)
        for row in executions:
            if row.result_envelope is not None and row.result_envelope.visual_refs:
                ref = row.result_envelope.visual_refs[0]
                assert (
                    ref.agent_run_id == "arun_loop"
                    and ref.tool_execution_id == row.tool_execution_id
                )
                assert factory.visuals.read(ref, session_id=products.session.session_id).width == 8
    finally:
        products.persistence.store_session.close()


@pytest.mark.parametrize("approval_mode", ["absent", "headless"])
async def test_desktop_action_without_interactive_approval_is_not_dispatched(
    tmp_path, approval_mode
):
    from morrow.interfaces.cli import HeadlessApprovalPort

    app = _app(tmp_path)
    app.registry.register(
        "fake-adapter",
        lambda config, credential: ImageProvider(),
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", input_types=("text", "image")
        ),
    )
    _configure_active(app)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
                )
            }
        ),
        expected_revision=config.revision,
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    lifecycle = Lifecycle("completed")
    port = HeadlessApprovalPort() if approval_mode == "headless" else None
    products = build_session_application(
        app,
        identity,
        computer_use_lifecycle=lifecycle,
        approval_port=port,
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
    )
    try:
        preparation = products.orchestrator.preparation
        factory = preparation.computer_factory
        selected = factory.select(
            ComputerUseSelection(
                apps=(ComputerUseAppIdentity(bundle_id="com.example.Controlled"),),
                image_share=ComputerUseImageShare.CONTROLLED_WINDOW,
            ),
            products.session,
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        )
        prepared = preparation.prepare_new(agent_run_id="arun_headless", computer_request=selected)
        prepared.provider.responses[-1] = "The action needs interactive approval."
        await _dispatch_prepared(products.orchestrator, "Click the controlled button", prepared)
        assert lifecycle.device.actions == []
        assert len(lifecycle.device.reads) == 1
        assert lifecycle.calls == ["open", "close"]
        replies = [
            message for message in products.session.log.messages_view() if message.role == "tool"
        ]
        assert json.loads(replies[-1].content)["error"]["code"] == "needs_approval"
        assert replies[-1].visual_refs == ()
        rows = factory.journal.list_session_executions(
            factory.workspace_id, products.session.session_id
        )
        action = next(row for row in rows if row.tool_name == "computer_action")
        assert action.state is ToolExecutionState.CLOSED
        assert action.disposition is ToolExecutionDisposition.DENIED
        approval = factory.journal.get_approval_for_execution(
            factory.workspace_id, action.tool_execution_id
        )
        assert approval.consumed_at is None
        if port is not None:
            assert port.needs_approval
    finally:
        products.persistence.store_session.close()
