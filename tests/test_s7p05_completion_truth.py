"""Validation telemetry and model-owned turn completion regressions."""

from __future__ import annotations

import json
import sys

import pytest
from pydantic import BaseModel, ConfigDict

from morrow.core.capabilities import (
    CommandToolFact,
    PolicyVerdict,
    ToolHandlerOutcome,
    ToolRunContext,
    ValidationFact,
)
from morrow.core.local_tools import CommandRequest, CommandResult, CommandStatus
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import ProcessExecutionService
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy


def _command(*, ordinal: int = 1, status: str = "exited", exit_code: int | None = 0):
    return CommandToolFact(
        call_id=f"call-{ordinal}",
        tool_name="run_command",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        command_class="project_command",
        status=status,
        exit_code=exit_code,
        duration_ms=1,
    )


def _validation(
    *,
    ordinal: int = 1,
    validator_kind: str = "pytest",
    scope: str = ".",
    status: str = "passed",
):
    return ValidationFact(
        call_id=f"call-{ordinal}",
        tool_name="run_command",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=(scope,),
        validator_kind=validator_kind,
        scope=scope,
        status=status,
        exit_code=0 if status == "passed" else 1,
        evidence_summary=status,
    )


def test_command_success_is_not_a_validation_fact_or_validation_outcome():
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record((_command(),))

    assert not run.validation_facts
    assert run.metrics("stop").validation_outcome == "not_run"


def test_latest_scoped_validation_fact_replaces_only_its_own_requirement():
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            _validation(ordinal=1, scope="tests"),
            _command(ordinal=2),
            _validation(ordinal=3, scope="tests", status="failed"),
            _validation(ordinal=4, scope="src"),
        )
    )

    assert {(fact.validator_kind, fact.scope): fact.status for fact in run.validation_facts} == {
        ("pytest", "tests"): "failed",
        ("pytest", "src"): "passed",
    }
    assert run.metrics("stop").validation_outcome == "failed"


def test_validator_recognition_is_strict_and_shell_control_flow_fails_closed(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    process = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))

    pytest_plan = process.preflight(CommandRequest(argv=(sys.executable, "-m", "pytest", "tests")))
    simple_shell_plan = process.preflight(CommandRequest(shell="pytest tests"))
    opaque_plan = process.preflight(CommandRequest(argv=("ls", "tests")))
    shell_plan = process.preflight(CommandRequest(shell="pytest tests && echo done"))
    mypy_plan = process.preflight(CommandRequest(argv=("mypy", "--strict", "src")))
    cargo_plan = process.preflight(CommandRequest(argv=("cargo", "test", "-q")))
    npm_plan = process.preflight(CommandRequest(argv=("npm", "test", "--", "--runInBand")))
    unittest_plan = process.preflight(CommandRequest(argv=(sys.executable, "-m", "unittest", "-v")))

    assert (pytest_plan.validation_kind, pytest_plan.validation_scope) == ("pytest", "tests")
    assert (simple_shell_plan.validation_kind, simple_shell_plan.validation_scope) == (
        "pytest",
        "tests",
    )
    assert opaque_plan.validation_kind is None
    assert shell_plan.validation_kind is None
    assert (mypy_plan.validation_kind, mypy_plan.validation_scope) == ("mypy", "src")
    assert (cargo_plan.validation_kind, cargo_plan.validation_scope) == ("cargo_test", ".")
    assert (npm_plan.validation_kind, npm_plan.validation_scope) == ("npm_test", ".")
    assert (unittest_plan.validation_kind, unittest_plan.validation_scope) == ("unittest", ".")


@pytest.mark.parametrize(
    "argv",
    (("./pytest", "tests"), ("/tmp/pytest", "tests"), ("./python", "-m", "pytest", "tests")),
)
def test_path_qualified_validator_names_fail_closed(tmp_path, argv):
    (tmp_path / "tests").mkdir()
    process = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))

    plan = process.preflight(CommandRequest(argv=argv))

    assert plan.validation_kind is None
    assert plan.validation_scope is None


def test_recognized_process_result_projects_only_a_validation_fact(tmp_path):
    (tmp_path / "tests").mkdir()
    process = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    result = CommandResult(
        status=CommandStatus.EXITED,
        exit_code=0,
        stdout="",
        stderr="",
        stdout_original_bytes=0,
        stdout_original_lines=0,
        stderr_original_bytes=0,
        stderr_original_lines=0,
        duration_ms=1,
        command_class="project_command",
        cwd=".",
    )
    recognized = process.preflight(CommandRequest(argv=("pytest", "tests")))
    opaque = process.preflight(CommandRequest(argv=("ls", "tests")))

    fact = process.validation_fact(
        recognized,
        result,
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
    )

    assert isinstance(fact, ValidationFact)
    assert (fact.validator_kind, fact.scope) == ("pytest", "tests")
    assert (
        process.validation_fact(
            opaque,
            result,
            call_id="call-2",
            tool_name="run_command",
            ordinal=2,
            approval_verdict=PolicyVerdict.ALLOW,
        )
        is None
    )


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


@pytest.mark.asyncio
async def test_failed_validation_telemetry_does_not_reject_model_stop():
    async def handler(arguments: _NoArguments):
        del arguments
        return ToolHandlerOutcome(
            payload={"observed": True},
            facts=(_validation(status="failed"),),
        )

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="record_failed_validation",
            description="Record one scripted failed validation fact.",
            arguments_model=_NoArguments,
            handler=handler,
        )
    )
    provider = ScriptedModelProvider(
        (
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="check-1",
                        name="record_failed_validation",
                        arguments=json.dumps({}),
                    ),
                )
            ),
            AssistantMessage(content="I am done despite the failed check."),
        )
    )
    loop = AgentLoop(
        provider,
        ModelRef(provider_id="demo", model_id="model"),
        make_context_builder(),
        tool_executor=ToolExecutor(registry.snapshot(), make_run_policy()),
    )
    session = Session(session_id="session-1")

    events = [event async for event in loop.run_task(session, "finish when you decide")]

    assert events[-1].type == "turn.completed"
    assert events[-1].payload["finish_reason"] == "stop"
    assert session.messages[-1].content == "I am done despite the failed check."
    assert session.latest_metrics is not None
    assert session.latest_metrics.validation_outcome == "failed"


@pytest.mark.asyncio
async def test_model_stop_is_not_preceded_by_an_intent_request_or_output_gate():
    provider = ScriptedModelProvider((AssistantMessage(content="Useful final answer."),))
    session = Session(session_id="session-1")
    loop = AgentLoop(
        provider,
        ModelRef(provider_id="demo", model_id="model"),
        make_context_builder(),
    )

    events = [event async for event in loop.run_task(session, "modify a file, or explain why not")]

    assert len(provider.stream_calls) == 1
    assert provider.complete_calls == []
    assert session.messages[-1].content == "Useful final answer."
    assert events[-1].payload["finish_reason"] == "stop"
