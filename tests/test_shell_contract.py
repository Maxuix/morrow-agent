"""Shell preflight, pinned interpreter, and executed-versus-refused results."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.adapters.local.process import HostProcessAdapter
from morrow.adapters.local.sandbox import (
    NativeSandboxProcessAdapter,
    SandboxBackend,
    SandboxCapability,
)
from morrow.adapters.local.shell import PinnedShell, probe_shell
from morrow.application.bash_tool import (
    BASH_PROVIDER_SCHEMA,
    bash_provider_schema,
    make_bash_tool,
)
from morrow.core.capabilities import (
    PermissionPreset,
    PermissionProfile,
    PolicyVerdict,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.local_tools import CommandRequest, CommandStatus
from morrow.core.models import FunctionToolCall, ToolApprovalDecision
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import (
    ProcessExecutionService,
    ProcessServiceError,
    command_fingerprint,
)
from morrow.services.sandbox import SandboxSnapshotService
from morrow.testing import make_run_policy

_HEREDOC = "cat <<'EOF'\n# don't parse this as shell syntax\nEOF\n"
_EMPTY_ARG = "printf '%s\\n' ''"
_NESTED_QUOTES = "printf '%s' \"$(printf '%s' \"a b\")\""
_COMMAND_SUBSTITUTION = "printf '%s' \"$(printf hi)\""
_UNICODE = "printf '%s' '你好'"
_BASH_ONLY = "printf '%s' \"$(cat <(printf hi))\""


def _service(tmp_path: Path, *, secrets: tuple[str, ...] = (), shell: PinnedShell | None = None):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    adapter = HostProcessAdapter(shell=shell) if shell is not None else None
    return ProcessExecutionService(files, adapter=adapter, secrets=secrets)


def _run() -> ToolRunContext:
    return ToolRunContext(run_id="run-1", session_id="session-1")


class _Approval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, approval_request):
        self.requests.append(approval_request)
        return ToolApprovalDecision(approved=True)


def _call(payload: dict, call_id: str = "call-1") -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="bash",
        arguments=json.dumps(payload, ensure_ascii=False),
    )


def _reference(shell: PinnedShell, script: str, cwd: Path, env: dict[str, str]):
    return subprocess.run(
        [shell.path, "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


async def _execute(service: ProcessExecutionService, request: CommandRequest):
    plan = service.preflight(request)
    result, _fact = await service.execute(
        plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="call-1",
        tool_name="bash",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    return plan, result


def test_probe_prefers_bash_and_falls_back_to_posix_sh():
    def exists(path: str) -> bool:
        return path in {"/bin/bash", "/bin/sh"}

    def run(path: str, script: str) -> str:
        del script
        return "3.2.57(1)-release" if path == "/bin/bash" else ""

    bash = probe_shell(exists=exists, which=lambda _name: None, run=run)
    assert bash == PinnedShell(path="/bin/bash", family="bash", version="3.2.57(1)-release")

    posix = probe_shell(
        exists=lambda path: path == "/bin/sh",
        which=lambda _name: None,
        run=lambda _path, _script: "",
    )
    assert posix == PinnedShell(path="/bin/sh", family="posix_sh", version="posix")

    ignored = probe_shell(exists=exists, which=lambda _name: None, run=lambda _path, _script: "")
    assert ignored.path == "/bin/sh"
    assert ignored.family == "posix_sh"

    custom = probe_shell(
        exists=lambda path: path == "/usr/local/bin/bash",
        which=lambda name: "/usr/local/bin/bash" if name == "bash" else None,
        run=lambda path, _script: "5.2.0" if path.endswith("bash") else "",
    )
    assert custom.path == "/usr/local/bin/bash"
    assert custom.family == "bash"

    missing = probe_shell(
        exists=lambda _path: False,
        which=lambda _name: None,
        run=lambda _path, _script: "",
    )
    assert missing.available is False


def test_argv_allows_empty_arguments_and_rejects_an_empty_command_name():
    request = CommandRequest.model_validate(
        {"argv": (sys.executable, "-c", "print('ok')", ""), "cwd": "."},
        strict=True,
    )
    assert request.argv[-1] == ""
    with pytest.raises(ValidationError):
        CommandRequest(argv=("", "ok"))
    with pytest.raises(ValidationError):
        CommandRequest(argv=("echo", "a\x00"))


def test_shell_boundary_and_classification_do_not_reject_legal_scripts(tmp_path):
    (tmp_path / "tests").mkdir()
    service = _service(tmp_path)
    empty = service.preflight(CommandRequest(shell=_EMPTY_ARG))
    heredoc = service.preflight(CommandRequest(shell=_HEREDOC))
    pipeline = service.preflight(CommandRequest(shell="cp a b && mv b c"))
    recognized = service.preflight(CommandRequest(shell="pytest tests"))
    unterminated = service.preflight(CommandRequest(shell="echo 'unterminated"))

    assert empty.command_class == "shell"
    assert empty.validation_kind is None
    assert heredoc.command_class == "unknown"
    assert heredoc.validation_kind is None
    assert pipeline.command_class == "shell"
    assert pipeline.validation_kind is None
    assert (recognized.validation_kind, recognized.validation_scope) == ("pytest", "tests")
    assert unterminated.command_class == "unknown"
    assert unterminated.validation_kind is None
    assert any(
        line.startswith("Shell：") and service.shell.path in line
        for line in service.intent(empty).preview_summary
    )
    argv_plan = service.preflight(CommandRequest(argv=("printf", "")))
    assert all(not line.startswith("Shell：") for line in service.intent(argv_plan).preview_summary)


def test_unparsed_shell_keeps_the_same_permission_and_redaction(tmp_path):
    service = _service(tmp_path, secrets=("known-secret",))
    script = "cat <<'EOF'\nknown-secret\n# don't parse this as shell syntax\nEOF\n"
    plan = service.preflight(CommandRequest(shell=script))
    simple = service.preflight(CommandRequest(shell="printf ok"))
    workspace = WorkspaceCapability(workspace_id="w1", root=tmp_path)
    ordinary = CapabilityPolicy(PermissionProfile(), workspace)
    full = CapabilityPolicy(
        PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        workspace,
    )

    assert plan.command_class == "unknown"
    assert plan.validation_kind is None
    assert ordinary.evaluate(service.intent(plan)).verdict is PolicyVerdict.ALLOW
    assert ordinary.evaluate(service.intent(simple)).verdict is PolicyVerdict.ALLOW
    assert full.evaluate(service.intent(plan)).verdict is PolicyVerdict.DENY
    assert full.evaluate(service.intent(simple)).verdict is PolicyVerdict.DENY
    preview = service.approval_command(plan)
    assert "known-secret" not in preview
    assert "<redacted>" in preview


def test_missing_shell_is_a_not_started_preflight_with_a_command_fingerprint(tmp_path):
    pinned = PinnedShell(path="", family="posix_sh", version="")
    service = _service(tmp_path, shell=pinned)
    request = CommandRequest(shell=_EMPTY_ARG)
    other = CommandRequest(shell="printf other")

    with pytest.raises(ProcessServiceError) as error:
        service.preflight(request)

    assert error.value.code == "invalid_command"
    assert error.value.message.startswith("命令未启动，进程没有执行")
    assert error.value.fingerprint == command_fingerprint(request)
    assert error.value.fingerprint != command_fingerprint(other)
    assert len(error.value.fingerprint) == 64


def test_tool_description_matches_the_pinned_shell(tmp_path):
    live = _service(tmp_path)
    live_tool = make_bash_tool(live)
    if live.shell.family == "bash":
        assert bash_provider_schema(live.shell) is BASH_PROVIDER_SCHEMA
        assert "Bash command to execute" in str(live_tool.definition.function.parameters)
    else:
        assert "POSIX sh" in live_tool.definition.function.description
        assert "Bash command to execute" not in str(live_tool.definition.function.parameters)

    posix = _service(
        tmp_path, shell=PinnedShell(path="/bin/sh", family="posix_sh", version="posix")
    )
    tool = make_bash_tool(posix)
    description = tool.definition.function.description
    command = tool.definition.function.parameters["properties"]["command"]["description"]
    assert "POSIX sh" in description
    assert "Bash-only syntax is not available" in description
    assert command.startswith("POSIX sh")
    assert "Bash command to execute" not in command


@pytest.mark.asyncio
async def test_preflight_failure_and_nonzero_exit_are_different_results(tmp_path):
    missing = _service(tmp_path, shell=PinnedShell(path="", family="posix_sh", version=""))
    registry = ToolRegistry()
    registry.register(make_bash_tool(missing))
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=_Approval(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    refused = await executor.execute_with_context(
        _call({"command": _EMPTY_ARG}),
        run_context=_run(),
        ordinal=1,
        total=1,
    )
    refused_body = json.loads(refused.envelope)
    assert refused.ok is False
    assert refused_body["ok"] is False
    assert refused_body["error"]["code"] == "invalid_command"
    assert "未启动" in refused_body["error"]["message"]
    assert "result" not in refused_body
    details = {item["key"]: item["value"] for item in refused_body["error"]["details"]}
    assert details["executed"] == "false"
    assert details["command_fingerprint"] == command_fingerprint(CommandRequest(shell=_EMPTY_ARG))

    service = _service(tmp_path)
    registry = ToolRegistry()
    registry.register(make_bash_tool(service))
    started = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=_Approval(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    outcome = await started.execute_with_context(
        _call({"command": "printf 'ran\\n'; exit 7"}, "started"),
        run_context=_run(),
        ordinal=1,
        total=1,
    )
    body = json.loads(outcome.envelope)
    assert outcome.ok is True
    assert body["result"]["executed"] is True
    assert body["result"]["status"] == "exited"
    assert body["result"]["exit_code"] == 7
    assert body["result"]["stdout"] == "ran\n"
    assert "未启动" not in outcome.envelope


@pytest.mark.asyncio
async def test_shell_scripts_match_the_pinned_shell(tmp_path):
    service = _service(tmp_path)
    shell = service.shell
    assert os.path.isabs(shell.path)
    assert os.access(shell.path, os.X_OK)
    assert shell.version
    env = service._minimal_environment()
    scripts = (
        _EMPTY_ARG,
        _HEREDOC,
        _NESTED_QUOTES,
        _COMMAND_SUBSTITUTION,
        _UNICODE,
        _BASH_ONLY,
        "echo 'unterminated",
    )
    for script in scripts:
        plan, result = await _execute(service, CommandRequest(shell=script))
        expected = _reference(shell, script, tmp_path, env)
        assert plan.command_class in {"shell", "unknown"}
        assert result.executed is True
        assert result.status is CommandStatus.EXITED
        assert result.exit_code == expected.returncode
        assert result.stdout == expected.stdout
        assert result.stderr == expected.stderr
    if shell.family == "bash":
        _plan, bash_result = await _execute(service, CommandRequest(shell=_BASH_ONLY))
        assert bash_result.exit_code == 0
        assert bash_result.stdout == "hi"
    _plan, empty_argv = await _execute(
        service,
        CommandRequest(
            argv=(
                sys.executable,
                "-c",
                "import sys; raise SystemExit(0 if sys.argv[1] == '' else 3)",
                "",
            )
        ),
    )
    assert empty_argv.executed is True
    assert empty_argv.exit_code == 0


@pytest.mark.asyncio
async def test_host_and_sandbox_invoke_the_pinned_shell_explicitly(tmp_path):
    log = tmp_path / "argv-log"
    wrapper = tmp_path / "pinned-shell"
    wrapper.write_text(
        f'#!/bin/bash\nprintf \'%s\\n\' "$0" "$@" > {str(log)!r}\nexec /bin/bash "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    pinned = PinnedShell(path=str(wrapper), family="bash", version="wrapper")
    adapter = HostProcessAdapter(shell=pinned)
    output = await adapter.run(
        argv=None,
        shell="printf 'via-wrapper\\n'",
        cwd=tmp_path,
        timeout_seconds=5,
        environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        output_limit=1024,
    )
    assert output.stdout_tail == b"via-wrapper\n"
    recorded = log.read_text(encoding="utf-8").splitlines()
    assert recorded[0] == str(wrapper)
    assert recorded[1] == "-c"
    assert recorded[2] == "printf 'via-wrapper\\n'"

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class _PassThrough(SandboxBackend):
        name = "test-pass-through"

        def __init__(self) -> None:
            self.last_build = None

        def probe(self) -> SandboxCapability:
            return SandboxCapability(
                platform="test",
                backend=self.name,
                supported=True,
                reason="test backend",
            )

        def build_command(self, **kwargs):
            self.last_build = kwargs
            return tuple(kwargs["argv"])

    backend = _PassThrough()
    sandbox = NativeSandboxProcessAdapter(
        workspace,
        SandboxSnapshotService(
            WorkspaceFileService(WorkspacePathResolver(workspace)),
            temp_parent=tmp_path,
        ),
        backend,
        process=HostProcessAdapter(shell=pinned),
    )
    sandbox_output = await sandbox.run(
        argv=None,
        shell="printf 'sandbox-shell\\n'",
        cwd=workspace,
        timeout_seconds=5,
        environment={"PATH": "/usr/bin:/bin"},
        output_limit=1024,
    )
    assert sandbox_output.stdout_tail == b"sandbox-shell\n"
    assert backend.last_build["argv"][:3] == (
        str(wrapper),
        "-c",
        "printf 'sandbox-shell\\n'",
    )
