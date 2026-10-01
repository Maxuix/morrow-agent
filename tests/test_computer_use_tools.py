"""Observe tool contract uses the existing ledger and durable visual outcome."""

import pytest
from pydantic import ValidationError

from morrow.application.computer_tools import ComputerObserveArguments, make_computer_observe_tool
from morrow.core.capabilities import OperationKind, ToolCallContext, ToolRunContext
from morrow.core.execution import EffectClass, MissingCompletionPolicy
from morrow.runtime.tools import ToolExecutionError, ToolRegistry
from test_computer_use_observer import _application
from test_computer_use_observer import environment as _observer_environment


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


def context(**values):
    fields = dict(
        run=ToolRunContext(run_id="arun_1", session_id="ses_1"),
        call_id="observe_call",
        tool_name="computer_observe",
        ordinal=2,
        total=2,
        result_limit=65536,
    )
    fields.update(values)
    return ToolCallContext(**fields)


@pytest.mark.parametrize(
    "arguments",
    [
        {"operation": "window"},
        {"operation": "window", "target_ref": "4242"},
        {"operation": "discover", "target_ref": "ctarget_1"},
        {"operation": "discover", "include_image": True},
        {"operation": "window", "target_ref": "ctarget_1", "include_image": "true"},
        {"operation": "discover", "pid": 4242},
        {"operation": "discover", "grant_id": "grt_1"},
    ],
)
def test_arguments_reject_native_identity_and_invalid_operation_shapes(arguments):
    with pytest.raises(ValidationError):
        ComputerObserveArguments.model_validate(arguments)


def test_tool_passes_closed_production_contract_audit(environment):
    application, _ = _application(environment)
    tool = make_computer_observe_tool(application, environment[0])
    registry = ToolRegistry()
    registry.register(tool)
    frozen = registry.snapshot(
        require_runtime_contract=True,
        require_closed_schema=True,
        require_production_declaration=True,
    )
    assert frozen.audit
    assert tool.runtime_contract.intent_kind is OperationKind.COMPUTER_OBSERVE
    assert tool.recovery_declaration.effect_class is EffectClass.BOUNDED_EXTERNAL_READ
    assert (
        tool.recovery_declaration.missing_handler_completed is MissingCompletionPolicy.SAFE_TO_RETRY
    )


async def test_handler_publishes_visual_reference_through_existing_outcome(environment):
    application, _ = _application(environment)
    tool = make_computer_observe_tool(application, environment[0])
    discovered = await tool.context_handler(
        ComputerObserveArguments(operation="discover"), context()
    )
    result = await tool.context_handler(
        ComputerObserveArguments(
            operation="window",
            target_ref=discovered.payload["targets"][0]["target_ref"],
        ),
        context(),
    )
    (reference,) = result.visual_refs
    assert result.payload["image"]["artifact_id"] == reference.artifact_id
    assert result.artifact_refs[0].artifact_id == reference.artifact_id
    assert reference.tool_execution_id == "tex_observe"
    assert "data_base64" not in str(result.payload)


@pytest.mark.parametrize(
    "override",
    [
        {"call_id": "other"},
        {"ordinal": 1},
        {"tool_name": "computer_action"},
        {"run": ToolRunContext(run_id="arun_1", session_id="ses_other")},
    ],
)
async def test_context_binding_rejection_never_opens_driver(environment, override):
    application, lifecycle = _application(environment)
    tool = make_computer_observe_tool(application, environment[0])
    with pytest.raises(ToolExecutionError, match="execution_not_authorized"):
        await tool.context_handler(
            ComputerObserveArguments(operation="discover"), context(**override)
        )
    assert lifecycle.calls == []
    assert lifecycle.device.calls == []


@pytest.mark.parametrize(
    "reason, recovery",
    [
        ("desktop_busy", "等待停稳"),
        ("stale_observation", "不重复旧动作"),
        ("tcc_missing", "实际运行宿主"),
        ("image_missing", "重新观察"),
        ("image_publish_failed", "不要自动重试"),
        ("model_image_tools_required", "同时支持图像与函数工具"),
    ],
)
async def test_refusal_returns_bounded_code_and_local_recovery_without_device_reads(
    reason, recovery
):
    from morrow.core.computer_use import ComputerUseContractError

    class Refused:
        def execution_for_context(self, context):
            raise ComputerUseContractError(reason)

    tool = make_computer_observe_tool(Refused(), None)
    with pytest.raises(ToolExecutionError) as raised:
        await tool.context_handler(ComputerObserveArguments(operation="discover"), context())
    assert raised.value.details == ({"reason": reason},)
    assert recovery in str(raised.value)
