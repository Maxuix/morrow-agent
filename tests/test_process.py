from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.local_tools import RunCommandArguments, make_run_command_tool
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    ApprovalMode,
    PermissionProfile,
    PolicyVerdict,
    ProcessIsolation,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.local_tools import CommandRequest, CommandStatus
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolApprovalDecision
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import ProcessExecutionService, ProcessServiceError
from morrow.testing import ScriptedModelProvider, make_run_policy


def _service(tmp_path: Path, *, secrets: tuple[str, ...] = (), environment=None):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    return ProcessExecutionService(files, secrets=secrets, environment=environment)


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def _run() -> ToolRunContext:
    return ToolRunContext(run_id="run-1", session_id="session-1")


class _Approval:
    def __init__(self, approved: bool = True):
        self.approved = approved
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.approved)


def _call(name: str, payload: dict, call_id: str = "call-1") -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name=name,
        arguments=json.dumps(payload, ensure_ascii=False),
    )


def test_command_request_is_exactly_one_form_and_has_no_extra_authority():
    request = RunCommandArguments.model_validate(
        {"argv": (sys.executable, "-c", "print('ok')"), "cwd": "."}, strict=True
    )
    assert request.argv[0] == sys.executable
    assert "env" not in request.model_json_schema()["properties"]
    assert "stdin" not in request.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        RunCommandArguments.model_validate(
            {"argv": ["echo", "ok"], "shell": "echo ok"}, strict=True
        )
    with pytest.raises(ValidationError):
        RunCommandArguments.model_validate({"argv": []}, strict=True)


def test_process_preflight_classifies_forbidden_operations_before_approval(tmp_path):
    service = _service(tmp_path)
    network = service.preflight(CommandRequest(argv=("curl", "https://example.invalid")))
    assert "network" in {flag.value for flag in network.risk_flags}
    destructive = service.preflight(CommandRequest(argv=("rm", "file.txt")))
    assert "destructive" in {flag.value for flag in destructive.risk_flags}
    git_write = service.preflight(CommandRequest(argv=("git", "commit", "-m", "x")))
    assert "git_write" in {flag.value for flag in git_write.risk_flags}
    git_read = service.preflight(CommandRequest(argv=("git", "status")))
    assert git_read.risk_flags == ()
    sudo = service.preflight(CommandRequest(argv=("sudo", "echo", "no")))
    assert "privilege_escalation" in {flag.value for flag in sudo.risk_flags}
    shell_bypass = service.preflight(
        CommandRequest(argv=("sh", "-c", "echo ok; curl https://example.invalid"))
    )
    assert "network" in {flag.value for flag in shell_bypass.risk_flags}
    for request in (
        CommandRequest(argv=("sh", "-c", "git commit -am x")),
        CommandRequest(shell="cd . && git commit -am x"),
        CommandRequest(shell="$(git reset --hard)"),
    ):
        wrapped_git = service.preflight(request)
        assert "git_write" in {flag.value for flag in wrapped_git.risk_flags}
    with pytest.raises(ProcessServiceError) as error:
        service.preflight(CommandRequest(argv=_python("print('ok')"), cwd="../"))
    assert error.value.code in {"invalid_path", "outside_workspace"}


def test_protected_command_paths_and_invalid_shell_are_rejected(tmp_path):
    service = _service(tmp_path)
    protected = service.preflight(CommandRequest(argv=("cat", ".env")))
    assert {flag.value for flag in protected.risk_flags} >= {
        "protected_resource",
        "credential_access",
    }
    with pytest.raises(ProcessServiceError) as error:
        service.preflight(CommandRequest(shell="echo 'unterminated"))
    assert error.value.code == "invalid_command"


@pytest.mark.asyncio
async def test_host_process_returns_structured_nonzero_and_shell_results(tmp_path):
    service = _service(tmp_path)
    plan = service.preflight(CommandRequest(argv=_python("print('ok'); raise SystemExit(3)")))
    result, fact = await service.execute(
        plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
    )
    assert result.status is CommandStatus.EXITED
    assert result.exit_code == 3
    assert result.stdout.strip() == "ok"
    assert fact.status == "exited"
    shell_plan = service.preflight(CommandRequest(shell="printf 'shell-ok\\n'"))
    shell_result, _ = await service.execute(
        shell_plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="call-2",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
    )
    assert shell_result.status is CommandStatus.EXITED
    assert shell_result.stdout.strip() == "shell-ok"


@pytest.mark.asyncio
async def test_host_process_reports_signal_exit(tmp_path):
    service = _service(tmp_path)
    plan = service.preflight(
        CommandRequest(argv=_python("import os, signal; os.kill(os.getpid(), signal.SIGTERM)"))
    )
    result, _ = await service.execute(
        plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="signal",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
    )
    assert result.status is CommandStatus.SIGNALED
    assert result.signal == signal.SIGTERM


@pytest.mark.asyncio
async def test_output_is_bounded_redacted_and_invalid_utf8_is_deterministic(tmp_path):
    service = _service(
        tmp_path,
        secrets=("known-secret",),
        environment={"PATH": os.environ.get("PATH", ""), "SECRET_SENTINEL": "hidden"},
    )
    code = (
        "import os, sys; "
        "print(os.environ.get('SECRET_SENTINEL', 'missing')); "
        "sys.stdout.flush(); "
        "sys.stdout.buffer.write(b'x' * 100000); "
        "sys.stdout.buffer.write(b'\\xff\\n'); "
        "print('known-secret password=abc123 Bearer abcdefghijkl')"
    )
    plan = service.preflight(CommandRequest(argv=_python(code)))
    result, _ = await service.execute(
        plan,
        result_limit=800,
        run=_run(),
        call_id="output",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
    )
    rendered = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    assert "known-secret" not in rendered
    assert "abc123" not in rendered
    assert "hidden" not in rendered
    assert result.stdout_original_bytes > 8 * 1024
    assert result.output_truncated is True
    assert "invalid_utf8" in result.redaction_flags
    assert len(result.model_dump_json()) <= 800


@pytest.mark.asyncio
async def test_timeout_and_cancellation_clean_up_host_process(tmp_path):
    service = _service(tmp_path)
    timeout_plan = service.preflight(
        CommandRequest(argv=_python("import time; time.sleep(30)"), timeout_seconds=0.05)
    )
    timeout_result, _ = await service.execute(
        timeout_plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="timeout",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
    )
    assert timeout_result.status is CommandStatus.TIMED_OUT

    marker = tmp_path / "cancelled-started"
    cancel_plan = service.preflight(
        CommandRequest(
            argv=_python(
                "from pathlib import Path; import time; "
                f"Path({str(marker)!r}).write_text('started'); time.sleep(30)"
            )
        )
    )
    task = asyncio.create_task(
        service.execute(
            cancel_plan,
            result_limit=16 * 1024,
            run=_run(),
            call_id="cancel",
            tool_name="run_command",
            ordinal=1,
            approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        )
    )
    for _ in range(10_000):
        if marker.exists():
            break
        await asyncio.sleep(0)
    assert marker.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_timeout_terminates_descendant_process_group(tmp_path):
    service = _service(tmp_path)
    child_ready = tmp_path / "child-ready"
    child_terminated = tmp_path / "child-terminated"
    parent_ready = tmp_path / "parent-ready"
    child_pid = tmp_path / "child-pid"
    child_code = (
        "from pathlib import Path\n"
        "import signal\n"
        f"ready=Path({str(child_ready)!r})\n"
        f"terminated=Path({str(child_terminated)!r})\n"
        "def handler(signum, frame):\n"
        "    terminated.write_text('terminated')\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, handler)\n"
        "ready.write_text('ready')\n"
        "print('ready', flush=True)\n"
        "signal.pause()\n"
    )
    parent_code = (
        "from pathlib import Path; import signal, subprocess, sys; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}], "
        "stdout=subprocess.PIPE, text=True); "
        f"Path({str(child_pid)!r}).write_text(str(child.pid)); "
        "assert child.stdout is not None; child.stdout.readline(); "
        f"Path({str(parent_ready)!r}).write_text('ready'); signal.pause()"
    )
    plan = service.preflight(CommandRequest(argv=_python(parent_code), timeout_seconds=1.0))
    try:
        result, _ = await service.execute(
            plan,
            result_limit=16 * 1024,
            run=_run(),
            call_id="tree-timeout",
            tool_name="run_command",
            ordinal=1,
            approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        )
        assert result.status is CommandStatus.TIMED_OUT
        for _ in range(10_000):
            if child_ready.exists() and child_terminated.exists():
                break
            await asyncio.sleep(0)
        assert child_ready.exists()
        assert parent_ready.exists()
        assert child_pid.exists()
        assert child_terminated.exists()
    finally:
        # The adapter already cleans the group on normal timeout. This fallback
        # only protects the test process if setup fails before the command runs.
        if child_pid.exists() and not child_terminated.exists():
            try:
                os.kill(int(child_pid.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass


@pytest.mark.asyncio
async def test_process_adapter_spawn_failure_is_typed(tmp_path):
    service = _service(tmp_path)
    plan = service.preflight(CommandRequest(argv=("/does/not/exist",)))
    with pytest.raises(ProcessServiceError):
        await service.execute(
            plan,
            result_limit=16 * 1024,
            run=_run(),
            call_id="spawn",
            tool_name="run_command",
            ordinal=1,
            approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        )


@pytest.mark.asyncio
async def test_tool_executor_requires_approval_for_host_process_and_denies_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = _service(workspace, secrets=("approval-secret",))
    try:
        registry = ToolRegistry()
        registry.register(make_run_command_tool(service))
        approval = _Approval()
        executor = ToolExecutor(
            registry.snapshot(),
            make_run_policy(),
            approval_port=approval,
            capability_policy=CapabilityPolicy(
                PermissionProfile(), WorkspaceCapability(workspace_id="w1", root=workspace)
            ),
        )
        run = _run()
        outcome = await executor.execute_with_context(
            _call("run_command", {"argv": list(_python("print('approval-secret')"))}),
            run_context=run,
            ordinal=1,
            total=1,
        )
        assert outcome.ok is True
        assert len(approval.requests) == 1
        preview = "\n".join(approval.requests[0].preview)
        assert "命令：" in preview
        assert "非沙箱宿主进程" in preview
        assert "<redacted>" in preview
        assert "approval-secret" not in preview
        assert "approval-secret" not in outcome.envelope
        auto_safe = ToolExecutor(
            registry.snapshot(),
            make_run_policy(),
            approval_port=approval,
            capability_policy=CapabilityPolicy(
                PermissionProfile(approval_mode=ApprovalMode.AUTO_SAFE),
                WorkspaceCapability(workspace_id="w1", root=workspace),
            ),
        )
        auto_outcome = await auto_safe.execute_with_context(
            _call("run_command", {"argv": list(_python("print('auto-safe-approved')"))}, "auto"),
            run_context=run,
            ordinal=2,
            total=4,
        )
        assert auto_outcome.ok is True
        assert len(approval.requests) == 2
        sandboxed = ToolExecutor(
            registry.snapshot(),
            make_run_policy(),
            approval_port=approval,
            capability_policy=CapabilityPolicy(
                PermissionProfile(
                    approval_mode=ApprovalMode.AUTO,
                    process_isolation=ProcessIsolation.NATIVE_SANDBOX,
                ),
                WorkspaceCapability(workspace_id="w1", root=workspace),
                sandbox_available=True,
            ),
        )
        sandbox_outcome = await sandboxed.execute_with_context(
            _call("run_command", {"argv": list(_python("print('must-not-run')"))}, "sandbox"),
            run_context=run,
            ordinal=3,
            total=4,
        )
        assert sandbox_outcome.error_code is ToolErrorCode.PERMISSION_DENIED
        assert len(approval.requests) == 2
        forbidden = await executor.execute_with_context(
            _call("run_command", {"argv": ["curl", "https://example.invalid"]}, "network"),
            run_context=run,
            ordinal=4,
            total=4,
        )
        assert forbidden.error_code is ToolErrorCode.PERMISSION_DENIED
        assert len(approval.requests) == 2
    finally:
        workspace.rmdir()


@pytest.mark.asyncio
async def test_fake_provider_can_recover_after_host_command_failure(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _call(
                        "run_command",
                        {"argv": list(_python("print('first-failure'); raise SystemExit(1)"))},
                        "first",
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call("run_command", {"argv": list(_python("print('fixed')"))}, "second"),
                )
            ),
            AssistantMessage(content="第一次命令失败，修正后第二次命令成功。"),
        ]
    )
    approval = _Approval()
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )
    [item async for item in session_app.orchestrator.stream("执行校验并在失败后修正")]

    messages = [message for message in session_app.session.messages if message.role == "tool"]
    first = json.loads(messages[0].content)
    second = json.loads(messages[1].content)
    assert first["result"]["status"] == "exited"
    assert first["result"]["exit_code"] == 1
    assert second["result"]["exit_code"] == 0
    assert len(approval.requests) == 2
