"""Safe tool observation references survive the single history and durable paths."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from morrow.application.recovery import RecoveryService
from morrow.application.tool_persistence import DurableToolExecutionCoordinator
from morrow.core.capabilities import ToolHandlerOutcome
from morrow.core.domain import ArtifactReference
from morrow.core.execution import ToolExecutionState
from morrow.core.faults import FaultPoint, InjectedFault, NoOpFaultInjector, OnceFaultInjector
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    ProviderInputPart,
    ToolMessage,
    ToolVisualRef,
    UserMessage,
)
from morrow.core.recovery import RecoveryResolution
from morrow.runtime.agent import AgentLoop
from morrow.runtime.conversation import ConversationLog
from morrow.runtime.durable_log import (
    conversation_record_from_durable,
    durable_call_id,
    durable_from_conversation_record,
)
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutionOutcome, ToolExecutor, ToolRegistry, make_tool
from morrow.testing import (
    FixedClock,
    FixedIdSource,
    ScriptedModelProvider,
    make_context_builder,
    make_run_policy,
)
from test_stage4_tool_journal import _execution, _intent, _open_journal, _seed_run


def _ref(**changes):
    values = dict(
        artifact_id="art_visual",
        sha256="a" * 64,
        mime="image/png",
        byte_size=100,
        width=20,
        height=10,
        tool_execution_id="tex_1",
        workspace_id="ws_a",
        session_id="ses_1",
        task_run_id="task_1",
        agent_run_id="arun_1",
        observation_id="cobs_1",
    )
    values.update(changes)
    return ToolVisualRef(**values)


@pytest.mark.parametrize(
    "change",
    [
        {"byte_size": True},
        {"width": "20"},
        {"width": 1921},
        {"height": 0},
        {"byte_size": 8 * 1024 * 1024 + 1},
        {"mime": "text/plain"},
        {"sha256": "bad"},
        {"data_base64": "secret"},
        {"native_token": "secret"},
        {"path": "/tmp/image.png"},
    ],
)
def test_visual_ref_rejects_unbounded_content_and_native_objects(change):
    with pytest.raises(ValidationError):
        _ref(**change)


def test_tool_parts_are_transient_and_old_tool_messages_remain_readable():
    old = ToolMessage.model_validate({"role": "tool", "tool_call_id": "one", "content": "{}"})
    assert old.visual_refs == ()
    ref = _ref()
    hydrated = old.model_copy(
        update={
            "visual_refs": (ref,),
            "input_parts": (ProviderInputPart(type="image", data="private-image-bytes"),),
        }
    )
    assert "private-image-bytes" not in repr(hydrated)
    assert "private-image-bytes" not in hydrated.model_dump_json()
    assert ToolMessage.model_validate_json(hydrated.model_dump_json()).visual_refs == (ref,)
    with pytest.raises(ValidationError):
        ToolMessage(tool_call_id="one", content="{}", visual_refs=(ref, ref))


def _log():
    log = ConversationLog()
    log.begin_turn(UserMessage(content="inspect fixture"))
    log.append_assistant(
        AssistantMessage(
            tool_calls=(FunctionToolCall(id="call1", name="write_file", arguments="{}"),)
        )
    )
    return log


def test_redacted_durable_history_keeps_only_safe_reference_and_original_pairing():
    log = _log()
    ref = _ref()
    log.append_tool_result("call1", "native text never retained", visual_refs=(ref,))
    record = log.snapshot().records[-1]
    durable = durable_from_conversation_record(record, record_id="rec_1", session_id="ses_1")
    assert "native text never retained" not in durable.model_dump_json()
    restored = conversation_record_from_durable(durable)
    assert restored.message.tool_call_id == durable_call_id("call1")
    assert restored.message.visual_refs == (ref,)
    assert restored.message.input_parts == ()


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def test_handler_executor_and_agent_loop_keep_refs_in_the_only_history_writer():
    ref = _ref()

    async def handler(arguments):
        return ToolHandlerOutcome(payload={"observed": True}, visual_refs=(ref,))

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="visual_probe",
            description="scripted visual metadata",
            arguments_model=_Arguments,
            handler=handler,
        )
    )
    executor = ToolExecutor(registry.snapshot(), make_run_policy())
    call = FunctionToolCall(id="call1", name="visual_probe", arguments="{}")
    outcome = await executor.execute(call)
    assert outcome.visual_refs == (ref,)
    assert (
        ArtifactReference(artifact_id=ref.artifact_id, role="computer_observation")
        in outcome.artifact_refs
    )
    budgeted = await executor.execute(call, result_limit=1)
    assert not budgeted.ok and budgeted.visual_refs == (ref,)
    provider = ScriptedModelProvider([AssistantMessage(tool_calls=(call,)), "done"])
    session = Session(session_id="ses_1")
    events = [
        event
        async for event in AgentLoop(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            tool_executor=executor,
        ).run_task(session, "inspect")
    ]
    assert events[-1].type == "turn.completed"
    tools = [message for message in session.log.messages_view() if isinstance(message, ToolMessage)]
    assert len(tools) == 1 and tools[0].visual_refs == (ref,)
    assert [message.role for message in session.log.messages_view()] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


def test_completed_visual_evidence_survives_crash_and_recovery_without_handler_replay(tmp_path):
    store, handle, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        call_id = durable_call_id("call1")
        execution = journal.put_execution(
            "ws_a",
            _execution(
                intent=_intent(call_id=call_id),
                state=ToolExecutionState.EXECUTING,
            ),
        )
        from morrow.adapters.state.artifacts import FilesystemArtifactStore
        from morrow.application.artifacts import ArtifactService
        from morrow.core.artifacts import ArtifactKind

        artifact = ArtifactService(
            journal=journal,
            filesystem=FilesystemArtifactStore(store.layout),
            workspace_id="ws_a",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
        ).publish_bytes(
            b"synthetic capture placeholder",
            kind=ArtifactKind.TEST_REPORT,
            session_id="ses_1",
            task_run_id="task_1",
            artifact_id="art_visual",
        )
        ref = _ref(sha256=artifact.sha256, byte_size=artifact.byte_size)
        coordinator = DurableToolExecutionCoordinator(
            journal,
            workspace_id="ws_a",
            id_source=FixedIdSource(),
            permissions=None,
            faults=OnceFaultInjector(FaultPoint.EXECUTION_AFTER_HANDLER_COMPLETED),
            clock=FixedClock().now,
        )
        result = ToolExecutionOutcome(
            call_id="call1",
            name="write_file",
            ok=True,
            envelope='{"ok":true}',
            visual_refs=(ref,),
        )
        with pytest.raises(InjectedFault):
            coordinator.record_handler_completed(execution, result)
        persisted = journal.get_execution("ws_a", "tex_1")
        assert persisted.result_envelope.visual_refs == (ref,)
        assert persisted.artifact_refs[0].artifact_id == ref.artifact_id
        handle.close()
        from morrow.core.store import StoreOpenMode

        handle = store.open(StoreOpenMode.READ_WRITE)
        from morrow.adapters.state.journal import SqliteOperationalJournal

        journal = SqliteOperationalJournal(handle)
        persisted = journal.get_execution("ws_a", "tex_1")
        assert persisted.result_envelope.visual_refs == (ref,)
        log = _log()
        recovery = RecoveryService(journal, workspace_id="ws_a", id_source=FixedIdSource())
        report = recovery.discover("ses_1", log)
        _, _, planned = recovery.decide(
            report,
            command_id="cmd_abort",
            resolution=RecoveryResolution.ABORT,
            item_id=None,
            log=log,
            now=FixedClock().now(),
        )
        log.apply_committed(planned)
        assert log.messages_view()[-1].visual_refs == (ref,)
        # Recovery closes the grammar with an error; it doesn't report a new action.
        assert '"ok":false' in log.messages_view()[-1].content
        assert journal.get_execution("ws_a", "tex_1").row_version == persisted.row_version
    finally:
        handle.close()


def test_durable_coordinator_rejects_wrong_source_before_writing(tmp_path):
    _, handle, journal = _open_journal(tmp_path)
    try:
        _seed_run(journal)
        execution = journal.put_execution("ws_a", _execution(state=ToolExecutionState.EXECUTING))
        coordinator = DurableToolExecutionCoordinator(
            journal,
            workspace_id="ws_a",
            id_source=FixedIdSource(),
            permissions=None,
            faults=NoOpFaultInjector(),
            clock=FixedClock().now,
        )
        result = ToolExecutionOutcome(call_id="call1", name="write_file", ok=True, envelope="{}")
        for changes in [
            dict(workspace_id="ws_other"),
            dict(session_id="ses_other"),
            dict(agent_run_id="arun_other"),
            dict(tool_execution_id="tex_other"),
        ]:
            with pytest.raises(ValueError, match="provenance"):
                coordinator.record_handler_completed(
                    execution, replace(result, visual_refs=(_ref(**changes),))
                )
        assert journal.get_execution("ws_a", "tex_1").state is ToolExecutionState.EXECUTING
    finally:
        handle.close()
