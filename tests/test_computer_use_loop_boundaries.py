"""Cancel a real tool cycle while the scripted SDK retains an admitted action."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from morrow.adapters.computer_use.owner import ComputerDriverOwner
from morrow.adapters.computer_use.process_identity import ProcessBirth
from morrow.adapters.state.operational import SystemStoreClock
from morrow.application.computer_requests import ComputerUseSelection
from morrow.application.computer_use import ComputerUseLifecycle
from morrow.bootstrap import build_session_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUsePreflight,
)
from morrow.core.execution import (
    RecoveryClassification,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.runtime_policy import ComputerUseSettings, RuntimePolicyOverrides
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

    async def stream(self, model, messages, tools=(), generation=None):
        if self.request_count:
            reply = next(message for message in reversed(messages) if message.role == "tool")
            result = json.loads(reply.content)["result"]
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
                    "The window changed; I need a new observation before clicking."
                )
            else:
                raise AssertionError("cancelled run must not request a final answer")
        self.request_count += 1
        async for event in super().stream(model, messages, tools, generation):
            yield event


@pytest.mark.parametrize(
    "boundary", ["inflight_cancel", "window_changed", "window_replaced", "process_replaced"]
)
async def test_real_sdk_boundary_preserves_safe_tool_results(tmp_path, boundary):
    app = _app(tmp_path)
    app.registry.register(
        "fake-adapter",
        lambda config, credential: ReferenceProvider(allow_final=boundary != "inflight_cancel"),
        capabilities=ProviderCapabilities(tool_protocol="openai_function"),
    )
    _configure_active(app)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(enabled=True)
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

        async def click(self, payload):
            self.calls.append(("click", payload))
            entered.set()
            await release.wait()
            finished.append("effect")
            return SimpleNamespace()

    class Owner(ComputerDriverOwner):
        async def close_run_session(self, request):
            closing.set()
            await super().close_run_session(request)

    native, lease = Native(), _Lease()

    async def shutdown():
        pass

    owner = Owner(
        _sdk(),
        FixedIdSource(),
        SystemStoreClock(),
        session_factory=lambda driver, name: native,
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
        lambda: ComputerUsePreflight(status="unavailable", reason="native_unverified"),
        native_verified=True,
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
    running = None
    try:
        request = factory.select(
            ComputerUseSelection(apps=(ComputerUseAppIdentity(bundle_id="com.example.Notes"),)),
            products.session,
            authority=TRUSTED_COMPUTER_USE_AUTHORITY,
        )
        prepared = products.orchestrator.preparation.prepare_new(
            agent_run_id="arun_cancel", computer_request=request
        )
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
