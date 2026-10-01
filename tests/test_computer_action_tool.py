"""Frozen action tool fields, real journal admission and result provenance."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from morrow.application.computer_tools import ComputerActionArguments, make_computer_action_tool
from morrow.core.capabilities import OperationKind, ToolRunContext
from morrow.core.execution import EffectClass, MissingCompletionPolicy
from morrow.core.models import ToolEffect
from morrow.runtime.policy import ToolApproval
from morrow.runtime.tools import ToolExecutionError, ToolRegistry
from test_computer_use_after_action import setup
from test_computer_use_observer import environment as _observer_environment
from test_computer_use_tools import context


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


def action_context(**fields):
    return context(tool_name="computer_action", call_id="action_call", ordinal=3, total=3, **fields)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "observation_id": "cobs_1",
            "action": {"type": "click", "element_ref": "celem_1"},
            "grant_id": "grt_1",
        },
        {"observation_id": "cobs_1", "action": {"type": "click", "pid": 123}},
        {
            "observation_id": "cobs_1",
            "action": {"type": "click", "element_ref": "celem_1", "delivery_mode": "foreground"},
        },
        {"observation_id": "cobs_1", "action": {"type": "call_tool", "name": "clipboard_read"}},
        {"observation_id": "cobs_1", "action": [{"type": "click", "element_ref": "celem_1"}]},
        {
            "observation_id": "native_snapshot",
            "action": {"type": "click", "element_ref": "celem_1"},
        },
        {
            "observation_id": "cobs_1",
            "action": {"type": "type_text", "element_ref": "celem_1", "text": "sk-" + "A" * 40},
        },
    ],
)
def test_model_cannot_supply_authority_native_names_or_action_batches(payload):
    with pytest.raises(ValidationError):
        ComputerActionArguments.model_validate(payload)


async def test_action_passes_closed_schema_and_full_production_contract(environment):
    app, _, before = await setup(environment)
    tool = make_computer_action_tool(app, environment[0])
    registry = ToolRegistry()
    registry.register(tool)
    frozen = registry.snapshot(
        require_runtime_contract=True,
        require_closed_schema=True,
        require_production_declaration=True,
    )
    assert frozen.audit
    assert tool.runtime_contract.intent_kind is OperationKind.COMPUTER_ACTION
    assert tool.runtime_contract.intent_effect is ToolEffect.PERSISTENT_WRITE
    assert tool.execution_policy.effect is ToolEffect.PERSISTENT_WRITE
    assert tool.execution_policy.approval is ToolApproval.REQUIRED
    assert tool.recovery_declaration.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
    assert (
        tool.recovery_declaration.missing_handler_completed
        is MissingCompletionPolicy.OUTCOME_UNKNOWN
    )


async def test_handler_returns_safe_new_image_with_actual_action_execution_source(environment):
    app, device, before = await setup(environment)
    tool = make_computer_action_tool(app, environment[0])
    arguments = ComputerActionArguments.model_validate(
        {
            "observation_id": before.observation.observation_id,
            "action": {"type": "click", "element_ref": "celem_1"},
        }
    )
    outcome = await tool.context_handler(arguments, action_context())
    assert outcome.payload["outcome"]["status"] == "completed"
    assert outcome.payload["observation"]["observation_id"] == "cobs_after"
    assert len(outcome.visual_refs) == 1
    reference = outcome.visual_refs[0]
    assert reference.tool_execution_id == "tex_action"
    assert outcome.payload["observation"]["image"]["artifact_id"] == reference.artifact_id
    assert outcome.artifact_refs[0].artifact_id == reference.artifact_id
    assert len(device.actions) == 1
    assert "data_base64" not in str(outcome.payload) and "element_token" not in str(outcome.payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"call_id": "foreign"},
        {"ordinal": 2},
        {"tool_name": "computer_observe"},
        {"run": ToolRunContext(run_id="arun_foreign", session_id="ses_1")},
        {"run": ToolRunContext(run_id="arun_1", session_id="ses_foreign")},
    ],
)
async def test_action_context_mismatch_has_no_native_effect_or_followup(environment, changes):
    app, device, before = await setup(environment)
    tool = make_computer_action_tool(app, environment[0])
    arguments = ComputerActionArguments.model_validate(
        {
            "observation_id": before.observation.observation_id,
            "action": {"type": "click", "element_ref": "celem_1"},
        }
    )
    reads = len(device.calls)
    with pytest.raises(ToolExecutionError, match="execution_not_authorized"):
        await tool.context_handler(arguments, replace(action_context(), **changes))
    assert len(device.calls) == reads and device.actions == []


@pytest.mark.parametrize("status", ["completed", "unknown"])
@pytest.mark.parametrize("result_limit", [65536, 100])
@pytest.mark.parametrize("crash", [False, True])
async def test_executor_and_journal_preserve_completion_even_when_output_is_too_large(
    environment,
    status,
    result_limit,
    crash,
):
    import json

    from morrow.application.tool_persistence import DurableToolExecutionCoordinator
    from morrow.core.execution import ToolExecutionDisposition, ToolExecutionState
    from morrow.core.faults import FaultPoint, InjectedFault, NoOpFaultInjector, OnceFaultInjector
    from morrow.core.models import FunctionToolCall
    from morrow.runtime.tools import ToolExecutor
    from morrow.testing import FixedIdSource, make_run_policy

    app, device, before = await setup(environment, status)
    registry = ToolRegistry()
    registry.register(make_computer_action_tool(app, environment[0]))
    executor = ToolExecutor(
        registry.snapshot(
            require_runtime_contract=True,
            require_closed_schema=True,
            require_production_declaration=True,
        ),
        make_run_policy(),
    )
    call = FunctionToolCall(
        id="action_call",
        name="computer_action",
        arguments=json.dumps(
            {
                "observation_id": before.observation.observation_id,
                "action": {"type": "click", "element_ref": "celem_1"},
            }
        ),
    )
    # The real ledger already contains the exact consumed approval. This is the
    # same trusted executor flag used after the durable coordinator consumes it.
    outcome = await executor.execute_with_context(
        call,
        run_context=action_context().run,
        ordinal=3,
        total=3,
        skip_approval=True,
        result_limit=result_limit,
    )
    assert outcome.ok is (result_limit == 65536)
    assert len(device.actions) == 1 and len(outcome.visual_refs) == 1
    assert outcome.disposition is (
        ToolExecutionDisposition.UNKNOWN if status == "unknown" else None
    )
    journal = environment[1]
    execution = journal.get_execution("ws_a", "tex_action")
    coordinator = DurableToolExecutionCoordinator(
        journal,
        workspace_id="ws_a",
        id_source=FixedIdSource(),
        permissions=None,
        faults=(
            OnceFaultInjector(FaultPoint.EXECUTION_AFTER_HANDLER_COMPLETED)
            if crash
            else NoOpFaultInjector()
        ),
        clock=app._clock.now,
    )
    if crash:
        with pytest.raises(InjectedFault):
            coordinator.record_handler_completed(
                execution,
                outcome,
                disposition=ToolExecutionDisposition.SUCCEEDED if status == "unknown" else None,
            )
        recorded = journal.get_execution("ws_a", "tex_action")
    else:
        recorded = coordinator.record_handler_completed(
            execution,
            outcome,
            disposition=ToolExecutionDisposition.SUCCEEDED if status == "unknown" else None,
        )
    assert recorded.state is ToolExecutionState.HANDLER_COMPLETED
    assert recorded.result_envelope.visual_refs == outcome.visual_refs
    assert recorded.artifact_refs[0].artifact_id == outcome.visual_refs[0].artifact_id
    if status == "unknown":
        assert recorded.disposition is ToolExecutionDisposition.UNKNOWN
    assert journal.get_execution("ws_a", "tex_action") == recorded
    from morrow.application.computer_visuals import validate_visual_source
    from morrow.application.recovery import RecoveryService
    from morrow.core.execution import RecoveryClassification
    from morrow.core.models import AssistantMessage, UserMessage
    from morrow.core.recovery import RecoveryDecisionError, RecoveryResolution
    from morrow.runtime.conversation import ConversationLog

    validate_visual_source(journal, "ws_a", outcome.visual_refs[0])
    log = ConversationLog()
    log.begin_turn(UserMessage(content="controlled fixture action"))
    log.append_assistant(AssistantMessage(tool_calls=(call,)))
    recovery = RecoveryService(journal, workspace_id="ws_a", id_source=FixedIdSource())
    report = recovery.discover("ses_1", log)
    item = next(item for item in report.items if item.tool_execution_id == "tex_action")
    assert item.classification is (
        RecoveryClassification.OUTCOME_UNKNOWN
        if status == "unknown"
        else RecoveryClassification.COMPLETED
    )
    assert RecoveryResolution.RETRY not in item.allowed_resolutions
    with pytest.raises(RecoveryDecisionError):
        recovery.decide(
            report,
            command_id="cmd_retry",
            resolution=RecoveryResolution.RETRY,
            item_id=item.item_id,
            log=log,
            now=app._clock.now(),
        )
    assert len(device.actions) == 1
    # Reusing the same actual execution never re-enters SDK, even with a fresh image.
    again = await executor.execute_with_context(
        call,
        run_context=action_context().run,
        ordinal=3,
        total=3,
        skip_approval=True,
    )
    assert not again.ok and len(device.actions) == 1


async def test_trusted_executor_flag_cannot_bypass_actual_missing_consumed_approval(environment):
    import json

    from morrow.core.models import FunctionToolCall
    from morrow.runtime.tools import ToolExecutor
    from morrow.testing import make_run_policy
    from test_computer_use_action_service import action_ledger, app_ready

    action_ledger(environment, approved=False)
    app, lifecycle, before = await app_ready(environment)
    registry = ToolRegistry()
    registry.register(make_computer_action_tool(app, environment[0]))
    executor = ToolExecutor(registry.snapshot(), make_run_policy())
    call = FunctionToolCall(
        id="action_call",
        name="computer_action",
        arguments=json.dumps(
            {
                "observation_id": before.observation.observation_id,
                "action": {"type": "click", "element_ref": "celem_1"},
            }
        ),
    )
    reads = len(lifecycle.device.calls)
    result = await executor.execute_with_context(
        call,
        run_context=action_context().run,
        ordinal=3,
        total=3,
        skip_approval=True,
    )
    assert not result.ok and "execution_not_authorized" in result.envelope
    assert lifecycle.device.actions == [] and len(lifecycle.device.calls) == reads
