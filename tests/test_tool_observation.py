"""Tool lifecycle observation tests (master plan agent-transparency P4.1)."""

from pydantic import BaseModel, ConfigDict

from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.policy import ToolApproval
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import make_context_builder, make_run_policy
from test_agent_tool_loop import _DenyApproval


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class Collector:
    def __init__(self):
        self.facts = []
        self.fragments = []

    def reasoning_delta(self, *, turn_id, attempt_ordinal, fragment):
        self.fragments.append(fragment)

    def tool_observation(self, **fact):
        self.facts.append(fact)


def _echo_executor(*, approval=None):
    from morrow.runtime.policy import ToolExecutionPolicy

    async def handler(arguments: EchoArguments) -> object:
        return {"echo": arguments.value}

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="echo",
            description="echo",
            arguments_model=EchoArguments,
            handler=handler,
            execution_policy=ToolExecutionPolicy(approval=ToolApproval.REQUIRED)
            if approval
            else None,
            approval_preview=lambda _call: ("echo preview",),
        )
    )
    return ToolExecutor(registry.snapshot(), make_run_policy(), approval_port=approval)


def _loop(provider, executor, collector):
    return AgentLoop(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        tool_executor=executor,
        activity_observer=collector,
    )


async def _run(loop, text="go"):
    return [event async for event in loop.run_task(Session(session_id="s"), text)]


def _provider(*responses):
    from morrow.testing import ScriptedModelProvider

    return ScriptedModelProvider(list(responses))


async def test_tool_phases_observed_in_real_order():
    """Auto-approved tool: prepared → executing → terminal, from owner transitions."""

    collector = Collector()
    loop = _loop(
        _provider(
            AssistantMessage(
                tool_calls=(FunctionToolCall(id="c1", name="echo", arguments='{"value":"x"}'),)
            ),
            AssistantMessage(content="done"),
        ),
        _echo_executor(),
        collector,
    )
    events = await _run(loop)
    phases = [fact["phase"] for fact in collector.facts]
    assert phases == ["prepared", "executing", "terminal"], phases
    assert collector.facts[-1]["disposition"] == "succeeded"
    assert collector.facts[0]["call_id"] == "c1"
    assert events[-1].type == "turn.completed"


async def test_awaiting_approval_observed_and_denial_cancels():
    """Required approval surfaces the real waiting phase; denial has no handler run."""

    approval = _DenyApproval()
    collector = Collector()
    loop = _loop(
        _provider(
            AssistantMessage(
                tool_calls=(FunctionToolCall(id="c2", name="echo", arguments='{"value":"y"}'),)
            ),
            AssistantMessage(content="denied round done"),
        ),
        _echo_executor(approval=approval),
        collector,
    )
    await _run(loop)
    phases = [fact["phase"] for fact in collector.facts]
    # Executor-side denial never claims executing: prepared → terminal only.
    assert phases == ["prepared", "terminal"], phases
    assert collector.facts[-1]["disposition"] == "cancelled"
    assert len(approval.requests) == 1


async def test_observer_presence_does_not_change_execution_results():
    """P4.5: identical durable outcomes with and without an observer."""

    class _ProviderFactory:
        def __init__(self):
            self.counter = 0

        def __call__(self):
            from morrow.testing import ScriptedModelProvider

            self.counter += 1
            return ScriptedModelProvider(
                [
                    AssistantMessage(
                        tool_calls=(
                            FunctionToolCall(
                                id=f"c{self.counter}", name="echo", arguments='{"value":"z"}'
                            ),
                        )
                    ),
                    AssistantMessage(content="done"),
                ]
            )

    def run(observer):
        factory = _ProviderFactory()
        loop = AgentLoop(
            factory(),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            tool_executor=_echo_executor(),
            activity_observer=observer,
        )
        return _run(loop)

    with_observer = Collector()
    events_with = await run(with_observer)
    events_without = await run(None)
    assert [e.type for e in events_with] == [e.type for e in events_without]
    tool_status_with = [e.payload["status"] for e in events_with if e.type == "tool.status"]
    tool_status_without = [e.payload["status"] for e in events_without if e.type == "tool.status"]
    assert tool_status_with == tool_status_without
    assert len(with_observer.facts) == 3


async def test_steered_status_becomes_applied_control_receipt():
    """P6.1: the real steer consumption boundary yields an applied receipt."""
    from morrow.core.models import AgentEvent
    from morrow.server.activity_projection import SessionActivityProjector

    projector = SessionActivityProjector()
    items = projector.observe_event(
        AgentEvent(
            type="status.changed",
            event_id="evt_1",
            session_id="ses",
            turn_id="turn_9",
            sequence=1,
            payload={"status": "steered"},
        ),
        workspace_id="ws",
        session_id="ses",
        agent_run_id="arun_1",
    )
    assert items and items[0]["kind"] == "control"
    assert items[0]["payload"]["receipt"] == "applied"
    assert items[0]["payload"]["command"] == "steer"
    assert items[0]["activity_id"] == "act_control_turn_9"
