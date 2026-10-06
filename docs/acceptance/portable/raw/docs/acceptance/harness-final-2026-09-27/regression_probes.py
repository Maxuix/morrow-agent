"""Negative acceptance probes: assert required invariants, fail on reviewed HEAD."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from morrow.adapters.local.process import _CursorBuffer
from morrow.core.local_tools import TrackedCommandStatus, TrackedCommandView, TrackedLifecycle
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelRef,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.services.process import SecretRedactor
from morrow.services.tracked_process import TrackedProcessRegistry
from morrow.testing import ScriptedModelProvider, make_context_builder

MODEL = ModelRef(provider_id="p", model_id="m")


@pytest.mark.parametrize("running", [True, False])
def test_tracked_poll_does_not_release_secret_fragments(running):
    secret = "SYNTHETIC_SECRET_1234"
    raw = b"x" * 12 + secret.encode() + b"y" * 12
    stdout, stderr = _CursorBuffer(1024), _CursorBuffer(1024)
    stdout.add(raw)
    execution = SimpleNamespace(
        execution_id="exec_" + "a" * 24,
        spawned=SimpleNamespace(stdout=stdout, stderr=stderr),
        status=TrackedCommandStatus.RUNNING if running else TrackedCommandStatus.EXITED,
        exit_code=None if running else 0,
        signal=None,
        lifecycle=TrackedLifecycle.TASK,
        cwd_relative=".",
        command_class="shell",
    )
    # Running: fixed hold cuts inside secret. Exited: polling chunk cuts inside it.
    limit = len(raw) if running else 20
    view = TrackedProcessRegistry()._view(
        execution,
        offset=0,
        stderr_offset=0,
        limit=limit,
        redactor=SecretRedactor((secret,)),
    )
    assert "SYNTHETI" not in view.stdout, "tracked output leaked synthetic secret prefix"


@pytest.mark.asyncio
async def test_overflow_compaction_does_not_start_after_deadline():
    clock = SimpleNamespace(value=0.0)
    entered = []

    class ProbeLoop(AgentLoop):
        async def _compact_context(self, *args, **kwargs):
            entered.append(clock.value)
            return False

    provider = ScriptedModelProvider(
        [
            ModelEvent(
                kind="error",
                failure=ModelFailure(
                    code=ModelErrorCode.CONTEXT_OVERFLOW,
                    origin=ModelFailureOrigin.PROVIDER,
                    message="synthetic overflow",
                ),
            )
        ]
    )
    loop = ProbeLoop(provider, MODEL, make_context_builder(), monotonic_clock=lambda: clock.value)
    async for event in loop.run_task(Session(session_id="s"), "work", run_timeout_seconds=10):
        if event.type == "status.changed" and event.payload.get("status") == "compacting":
            clock.value = 10.0
    assert not entered, "overflow recovery admitted compaction after work deadline"


@pytest.mark.asyncio
async def test_completion_review_prompt_survives_retry():
    class Arguments(BaseModel):
        pass

    async def handler(arguments):
        return "done"

    async def no_sleep(seconds):
        pass

    registry = ToolRegistry()
    registry.register(
        make_tool(name="work", description="work", arguments_model=Arguments, handler=handler)
    )
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(FunctionToolCall(id="c1", name="work", arguments="{}"),)),
            AssistantMessage(content="CANDIDATE_WITH_WRONG_UNITS"),
            ConnectionError("synthetic retry"),
            AssistantMessage(content="final"),
        ]
    )
    builder = make_context_builder()
    loop = AgentLoop(
        provider,
        MODEL,
        builder,
        retry_sleep=no_sleep,
        tool_executor=ToolExecutor(registry.snapshot(), builder.run_policy),
    )
    events = [
        event async for event in loop.run_task(Session(session_id="s"), "Write out/report.json")
    ]
    assert len(provider.stream_calls) == 4
    assert events[-1].payload["finish_reason"] == "stop"
    assert "CANDIDATE_WITH_WRONG_UNITS" in str(provider.stream_calls[2])
    assert "CANDIDATE_WITH_WRONG_UNITS" in str(provider.stream_calls[3]), (
        "retry lost review candidate"
    )


@pytest.mark.asyncio
async def test_tracked_mutation_invalidates_previous_validation(monkeypatch):
    from tempfile import TemporaryDirectory

    from morrow.core.capabilities import PolicyVerdict, ToolRunContext, ValidationFact
    from morrow.core.local_tools import CommandRequest
    from morrow.runtime.completion_check import check_completion
    from morrow.services import bash_execution
    from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
    from morrow.services.process import PreparedBash, ProcessExecutionService

    run = ToolRunContext(run_id="run", session_id="s")
    run.record(
        (
            ValidationFact(
                call_id="validate",
                tool_name="bash",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=(".",),
                validator_kind="pytest",
                scope=".",
                status="passed",
                exit_code=0,
                evidence_summary="passed",
            ),
        )
    )

    async def fake_start(service, prepared, **kwargs):
        return "exec_" + "b" * 24

    # No OS process is launched; isolate the fact projection after successful admission.
    monkeypatch.setattr(bash_execution, "_start", fake_start)
    monkeypatch.setattr(
        bash_execution,
        "_read",
        lambda *args, **kwargs: TrackedCommandView(
            execution_id="exec_" + "b" * 24,
            status=TrackedCommandStatus.RUNNING,
            output_offset=0,
            stderr_offset=0,
            lifecycle=TrackedLifecycle.TASK,
            cwd=".",
            command_class="shell",
        ),
    )
    with TemporaryDirectory() as directory:
        service = ProcessExecutionService(
            WorkspaceFileService(WorkspacePathResolver(Path(directory)))
        )
        prepared = PreparedBash(
            action="start",
            plan=service.preflight(CommandRequest(shell="printf changed > result.txt")),
        )
        result = await bash_execution.run_bash(
            service,
            prepared,
            session_id="s",
            task_id="t",
            result_limit=8192,
            run=run,
            call_id="change",
            tool_name="bash",
            ordinal=2,
            approval_verdict=PolicyVerdict.ALLOW,
        )
    run.record(result.facts)
    assert check_completion(run).validation_outcome != "passed", (
        "background command kept stale validation"
    )
