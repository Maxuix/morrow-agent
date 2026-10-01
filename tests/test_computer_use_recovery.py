"""Actual action, PNG, tool reply transactions and reopened-store recovery."""

import json

import pytest

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.computer_tools import make_computer_action_tool
from morrow.application.computer_visuals import ComputerVisualService
from morrow.application.recovery import RecoveryService
from morrow.application.tool_persistence import (
    DurableToolExecutionCoordinator,
    ToolConversationPersistence,
)
from morrow.core.computer_use import ComputerUseContractError
from morrow.core.execution import (
    RecoveryClassification,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import AssistantMessage, FunctionToolCall, UserMessage
from morrow.core.recovery import RecoveryResolution
from morrow.core.store import StoreOpenMode
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.testing import FixedClock, FixedIdSource, make_run_policy
from test_computer_action_tool import action_context
from test_computer_use_after_action import setup
from test_computer_use_observer import environment as _observer_environment
from test_computer_use_permissions import NOW


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


@pytest.mark.parametrize("status", ["completed", "unknown"])
@pytest.mark.parametrize(
    "point",
    [
        FaultPoint.HANDLER_AFTER_RETURN,
        FaultPoint.EXECUTION_AFTER_HANDLER_COMPLETED,
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT,
        FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT,
    ],
)
async def test_reopened_tool_reply_and_recovery_keep_actual_image_and_never_replay(
    environment, tmp_path, status, point
):
    app, device, before = await setup(environment, status)
    journal = environment[1]
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
    ids = FixedIdSource()
    session = Session(session_id="ses_1")
    writer = DurableConversationWriter(
        session.log, journal, workspace_id="ws_a", session_id="ses_1", id_source=ids
    )
    writer.commit(session.log.plan_begin_turn(UserMessage(content="Controlled action")))
    writer.commit(session.log.plan_append_assistant(AssistantMessage(tool_calls=(call,))))
    outcome = await executor.execute_with_context(
        call, run_context=action_context().run, ordinal=3, total=3, skip_approval=True
    )
    assert len(device.actions) == 1 and len(outcome.visual_refs) == 1
    faults = OnceFaultInjector(point)
    coordinator = DurableToolExecutionCoordinator(
        journal,
        workspace_id="ws_a",
        id_source=ids,
        permissions=None,
        faults=faults,
        clock=FixedClock(NOW).now,
    )
    replies = ToolConversationPersistence(
        journal,
        workspace_id="ws_a",
        id_source=ids,
        mutation=None,
        faults=faults,
        clock=FixedClock(NOW).now,
    )
    with pytest.raises(InjectedFault):
        faults.check(FaultPoint.HANDLER_AFTER_RETURN)
        recorded = coordinator.record_handler_completed(
            journal.get_execution("ws_a", "tex_action"), outcome
        )
        replies.commit_tool_message(
            session.log.plan_append_tool_result(
                call.id, outcome.envelope, visual_refs=outcome.visual_refs
            ),
            recorded,
            session=session,
            writer=writer,
        )
    assert faults.fired and len(device.actions) == 1
    assert session.log.unresolved_call_ids == (call.id,)
    await app.close()
    journal._backend.session.close()

    store = OperationalStore(tmp_path / "state", clock=FixedClock(NOW))
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        reopened = SqliteOperationalJournal(handle)
        restored = restore_conversation_log(reopened, "ws_a", "ses_1")
        persisted = reopened.get_execution("ws_a", "tex_action")
        committed = point is FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT
        returned_only = point is FaultPoint.HANDLER_AFTER_RETURN
        assert persisted.state is (
            ToolExecutionState.CLOSED
            if committed
            else ToolExecutionState.EXECUTING
            if returned_only
            else ToolExecutionState.HANDLER_COMPLETED
        )
        if returned_only:
            assert persisted.result_envelope is None
        else:
            assert persisted.result_envelope.visual_refs == outcome.visual_refs
        assert len(restored.unresolved_call_ids) == int(not committed)
        recovery = RecoveryService(reopened, workspace_id="ws_a", id_source=ids)
        report = recovery.discover("ses_1", restored)
        item = next((item for item in report.items if item.tool_execution_id == "tex_action"), None)
        if not committed or status == "unknown":
            assert item is not None
            assert item.classification is (
                RecoveryClassification.OUTCOME_UNKNOWN
                if status == "unknown" or returned_only
                else RecoveryClassification.COMPLETED
            )
            assert RecoveryResolution.RETRY not in item.allowed_resolutions
            updated, receipt, planned = recovery.decide(
                report,
                command_id="cmd_ack",
                resolution=RecoveryResolution.ACKNOWLEDGE,
                item_id=item.item_id,
                log=restored,
                now=NOW,
            )
            recovered_writer = DurableConversationWriter(
                restored, reopened, workspace_id="ws_a", session_id="ses_1", id_source=ids
            )
            recovery.commit_decision(
                updated,
                receipt,
                planned=planned,
                log=restored,
                writer=recovered_writer,
                close_all=False,
            )
            _, replay, duplicate = recovery.decide(
                updated,
                command_id="cmd_ack",
                resolution=RecoveryResolution.ACKNOWLEDGE,
                item_id=item.item_id,
                log=restored,
                now=NOW,
            )
            assert replay.kind == "replay" and duplicate is None
        else:
            assert item is None
        closed = reopened.get_execution("ws_a", "tex_action")
        assert closed.state is ToolExecutionState.CLOSED
        if status == "unknown" or returned_only:
            assert closed.disposition is ToolExecutionDisposition.UNKNOWN
        tools = [message for message in restored.messages_view() if message.role == "tool"]
        assert len(tools) == 1
        assert tools[0].visual_refs == (() if returned_only else outcome.visual_refs)
        artifacts = ArtifactService(
            journal=reopened,
            filesystem=FilesystemArtifactStore(store.layout),
            workspace_id="ws_a",
            id_source=ids,
            clock=FixedClock(NOW).now,
        )
        visuals = ComputerVisualService(artifacts, reopened, clock=FixedClock(NOW).now)
        if returned_only:
            with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
                visuals.read(outcome.visual_refs[0], session_id="ses_1", agent_run_id="arun_1")
        else:
            capture = visuals.read(
                outcome.visual_refs[0], session_id="ses_1", agent_run_id="arun_1"
            )
            assert capture.width == 8 and capture.height == 6
            assert capture.content.startswith(b"\x89PNG")
        assert len(device.actions) == 1
        again = restore_conversation_log(reopened, "ws_a", "ses_1")
        assert len([message for message in again.messages_view() if message.role == "tool"]) == 1

        # Close the remaining fixture executions. Their accepted recovery
        # decisions must suppress rediscovery without erasing unknown effects.
        active = recovery.discover("ses_1", restored)
        if active is not None:
            updated, receipt, planned = recovery.decide(
                active,
                command_id="cmd_abort_all",
                resolution=RecoveryResolution.ABORT,
                item_id=None,
                log=restored,
                now=NOW,
            )
            recovered_writer = DurableConversationWriter(
                restored, reopened, workspace_id="ws_a", session_id="ses_1", id_source=ids
            )
            recovery.commit_decision(
                updated,
                receipt,
                planned=planned,
                log=restored,
                writer=recovered_writer,
                close_all=True,
            )
        assert recovery.discover("ses_1", restored) is None
        if status == "unknown" or returned_only:
            assert (
                reopened.get_execution("ws_a", "tex_action").disposition
                is ToolExecutionDisposition.UNKNOWN
            )
        assert len(device.actions) == 1
