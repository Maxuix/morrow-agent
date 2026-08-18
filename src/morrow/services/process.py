"""Host command classification, redaction, and bounded result projection."""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.local.process import HostProcessAdapter, ProcessAdapterError
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    RiskFlag,
    ToolRunContext,
)
from morrow.core.local_tools import CommandRequest, CommandResult, CommandStatus
from morrow.core.models import ToolEffect
from morrow.services.files import LocalFileError, WorkspaceFileService

MAX_COMMAND_OUTPUT_BYTES = 8 * 1024
MAX_COMMAND_RESULT_BYTES = 16 * 1024
MAX_COMMAND_PREVIEW_CHARS = 180
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_URL = re.compile(r"(?i)^(?:https?|ftp|ssh)://")
_LOOPBACK = re.compile(r"(?i)(?:localhost|127\.0\.0\.1|::1|0\.0\.0\.0)")
_REDACTION_TOKEN = re.compile(
    r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
)
_REDACTION_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "ksh", "dash", "fish"})
_SHELL_GIT_COMMAND = re.compile(
    r"(?im)(?:^|[;&|()`\n])\s*(?:(?:command|exec|env)\s+)*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+\s+)*(?:[^\s;&|()`]*/)?git(?:\s|$)"
)


class ProcessServiceError(RuntimeError):
    """Stable local process-service failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CommandPlan:
    request: CommandRequest
    cwd: Path
    cwd_relative: str
    argv: tuple[str, ...] | None
    shell: str | None
    command_class: str
    risk_flags: tuple[RiskFlag, ...]


class SecretRedactor:
    """Redact exact active credentials and conservative token-shaped output."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        unique = sorted({value for value in secrets if len(value) >= 4}, key=len, reverse=True)
        self._exact = tuple(unique)
        self.max_secret_length = max((len(value) for value in unique), default=0)

    def redact(self, raw: bytes) -> tuple[str, tuple[str, ...], int]:
        invalid_utf8 = False
        text = raw.decode("utf-8", errors="replace")
        if "\ufffd" in text:
            invalid_utf8 = True
        count = 0
        flags: list[str] = []
        for secret in self._exact:
            occurrences = text.count(secret)
            if occurrences:
                count += occurrences
                flags.append("exact_secret")
                text = text.replace(secret, "<redacted>")
        for pattern in (_REDACTION_TOKEN, _REDACTION_BEARER):
            text, replacements = pattern.subn("<redacted>", text)
            if replacements:
                count += replacements
                flags.append("token_pattern")
        if invalid_utf8:
            flags.append("invalid_utf8")
        return text, tuple(dict.fromkeys(flags)), count


class ProcessExecutionService:
    """Prepare and execute an explicitly requested, approval-gated Host command."""

    def __init__(
        self,
        files: WorkspaceFileService,
        *,
        adapter: HostProcessAdapter | None = None,
        secrets: tuple[str, ...] = (),
        environment: Mapping[str, str] | None = None,
        requires_host: bool = True,
        requires_sandbox: bool = False,
    ) -> None:
        self.files = files
        self.adapter = adapter or HostProcessAdapter()
        self.redactor = SecretRedactor(secrets)
        self.environment = dict(environment or os.environ)
        self.requires_host = requires_host
        self.requires_sandbox = requires_sandbox
        self._plans: dict[tuple[str, str], CommandPlan] = {}

    def preflight(self, request: CommandRequest) -> CommandPlan:
        try:
            resolved = self.files.preflight_directory(request.cwd)
        except LocalFileError as exc:
            raise ProcessServiceError(exc.code, exc.message) from exc
        try:
            tokens = (
                tuple(shlex.split(request.shell))
                if request.shell is not None
                else request.argv or ()
            )
        except ValueError as exc:
            raise ProcessServiceError("invalid_command", "shell 命令语法无效") from exc
        if not tokens or any(not token for token in tokens):
            raise ProcessServiceError("invalid_command", "命令不能为空")
        shell_script = _shell_script(tokens) if request.shell is None else request.shell
        shell_form = request.shell is not None or shell_script is not None
        command_class = _command_class(tokens[0], shell=shell_form)
        risk_flags = _risk_flags(
            tokens,
            self.files,
            shell=shell_form,
            shell_script=shell_script,
        )
        return CommandPlan(
            request=request,
            cwd=resolved.target,
            cwd_relative=resolved.relative_path,
            argv=request.argv,
            shell=request.shell,
            command_class=command_class,
            risk_flags=tuple(sorted(risk_flags, key=lambda flag: flag.value)),
        )

    def cache_plan(self, run_id: str, call_id: str, plan: CommandPlan) -> None:
        self._plans[(run_id, call_id)] = plan

    def cached_plan(self, run_id: str, call_id: str) -> CommandPlan | None:
        return self._plans.get((run_id, call_id))

    def approval_command(self, plan: CommandPlan) -> str:
        """Render one terminal-only bounded command preview with credential redaction."""

        rendered = shlex.join(plan.argv) if plan.argv is not None else plan.shell or ""
        redacted, _, _ = self.redactor.redact(rendered.encode("utf-8", errors="replace"))
        single_line = redacted.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        if len(single_line) > MAX_COMMAND_PREVIEW_CHARS:
            return single_line[: MAX_COMMAND_PREVIEW_CHARS - 1] + "…"
        return single_line

    def intent(self, plan: CommandPlan) -> OperationIntent:
        return OperationIntent(
            kind=OperationKind.PROCESS,
            effect=ToolEffect.NONE,
            relative_paths=(plan.cwd_relative,),
            command_class=plan.command_class,
            risk_flags=plan.risk_flags,
            requires_host=self.requires_host,
            requires_sandbox=self.requires_sandbox,
            preview_summary=(
                "原生沙箱进程（临时快照）" if self.requires_sandbox else "非沙箱宿主进程",
                f"命令类别：{plan.command_class}",
                f"工作目录：{plan.cwd_relative}",
                f"超时上限：{plan.request.timeout_seconds:g} 秒",
                "真实工作空间不会以可写方式暴露；命令修改仅保留在临时快照"
                if self.requires_sandbox
                else "批准后项目代码可能以当前用户权限访问工作空间外文件或网络",
            ),
        )

    async def execute(
        self,
        plan: CommandPlan,
        *,
        result_limit: int,
        run: ToolRunContext,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
    ) -> tuple[CommandResult, object]:
        environment = self._minimal_environment()
        output_limit = min(MAX_COMMAND_OUTPUT_BYTES, max(1, result_limit))
        try:
            if hasattr(self.adapter, "run_identity"):
                self.adapter.run_identity = (run.run_id, call_id)
            output = await self.adapter.run(
                argv=plan.argv,
                shell=plan.shell,
                cwd=plan.cwd,
                timeout_seconds=plan.request.timeout_seconds,
                environment=environment,
                output_limit=output_limit,
                redaction_overlap=self.redactor.max_secret_length,
            )
        except ProcessAdapterError as exc:
            raise ProcessServiceError(exc.code, exc.message) from exc
        stdout, stdout_flags, stdout_redactions = self.redactor.redact(output.stdout_tail)
        stderr, stderr_flags, stderr_redactions = self.redactor.redact(output.stderr_tail)
        sandbox_change_set = getattr(self.adapter, "last_change_set", None)
        if sandbox_change_set is not None:
            run.retain_change_set(sandbox_change_set.change_set_id, sandbox_change_set)
        stdout = _tail_text(stdout, output_limit)
        stderr = _tail_text(stderr, output_limit)
        flags = tuple(dict.fromkeys((*stdout_flags, *stderr_flags)))
        redaction_count = stdout_redactions + stderr_redactions
        result = CommandResult(
            status=output.status,
            exit_code=output.returncode if output.status is CommandStatus.EXITED else None,
            signal=-output.returncode if output.status is CommandStatus.SIGNALED else None,
            stdout=stdout,
            stderr=stderr,
            stdout_original_bytes=output.stdout_original_bytes,
            stdout_original_lines=output.stdout_original_lines,
            stderr_original_bytes=output.stderr_original_bytes,
            stderr_original_lines=output.stderr_original_lines,
            stdout_truncated=output.stdout_truncated,
            stderr_truncated=output.stderr_truncated,
            output_truncated=output.stdout_truncated or output.stderr_truncated,
            duration_ms=output.duration_ms,
            command_class=plan.command_class,
            cwd=plan.cwd_relative,
            redaction_flags=flags,
            redaction_count=redaction_count,
            sandbox_change_set_id=(
                sandbox_change_set.change_set_id if sandbox_change_set is not None else None
            ),
            sandbox_changed_paths=(
                sandbox_change_set.changed_paths if sandbox_change_set is not None else ()
            ),
            sandbox_changes_truncated=(
                sandbox_change_set.truncated if sandbox_change_set is not None else False
            ),
        )
        result = self._fit_result(result, result_limit)
        from morrow.core.capabilities import CommandToolFact

        fact = CommandToolFact(
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            relative_paths=(plan.cwd_relative,),
            approval_verdict=approval_verdict,
            command_class=plan.command_class,
            status=result.status.value,
            exit_code=result.exit_code,
            signal=result.signal,
            duration_ms=result.duration_ms,
            output_truncated=result.output_truncated,
            redaction_flags=result.redaction_flags,
            redaction_count=result.redaction_count,
        )
        return result, fact

    def _minimal_environment(self) -> dict[str, str]:
        allowed = {"PATH", "LANG", "LC_ALL", "TMPDIR", "SystemRoot", "ComSpec"}
        return {
            key: value
            for key, value in self.environment.items()
            if key in allowed and isinstance(value, str) and "\x00" not in value
        }

    @staticmethod
    def _fit_result(result: CommandResult, result_limit: int) -> CommandResult:
        if _json_size(result) <= result_limit:
            return result
        stdout_lines = result.stdout.splitlines(keepends=True)
        stderr_lines = result.stderr.splitlines(keepends=True)
        stdout_truncated = result.stdout_truncated
        stderr_truncated = result.stderr_truncated
        while _json_size(result) > result_limit:
            if stdout_lines:
                stdout_lines.pop(0)
                stdout_truncated = True
            elif stderr_lines:
                stderr_lines.pop(0)
                stderr_truncated = True
            else:
                break
            result = result.model_copy(
                update={
                    "stdout": "".join(stdout_lines),
                    "stderr": "".join(stderr_lines),
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                    "output_truncated": stdout_truncated or stderr_truncated,
                }
            )
        if _json_size(result) > result_limit:
            raise ProcessServiceError("output_budget", "进程结果无法放入当前预算")
        return result


def _command_class(executable: str, *, shell: bool) -> str:
    if shell:
        return "shell"
    name = Path(executable).name.casefold()
    if name in {"python", "python3", "python3.12", "python3.13", "node", "ruby", "perl"}:
        return "interpreter"
    if name in {"pytest", "ruff", "mypy", "uv", "npm", "pnpm", "yarn", "make", "cmake"}:
        return "project_command"
    if name == "git":
        return "git"
    if name in {"curl", "wget", "ssh", "scp", "ftp", "nc", "netcat"}:
        return "network"
    return "opaque"


def _shell_script(tokens: tuple[str, ...]) -> str | None:
    executable = Path(tokens[0]).name.casefold()
    if executable not in _SHELL_INTERPRETERS:
        return None
    for index, token in enumerate(tokens[1:-1], start=1):
        if token == "-c":
            return tokens[index + 1]
    return None


def _risk_flags(
    tokens: tuple[str, ...],
    files: WorkspaceFileService,
    *,
    shell: bool,
    shell_script: str | None = None,
) -> set[RiskFlag]:
    flags: set[RiskFlag] = set()
    lowered = tuple(token.casefold() for token in tokens)
    executable = Path(tokens[0]).name.casefold()
    destructive_commands = {
        "rm",
        "rmdir",
        "del",
        "erase",
        "unlink",
        "truncate",
        "mkfs",
        "dd",
        "shred",
        "kill",
        "pkill",
        "killall",
        "reboot",
        "shutdown",
        "chmod",
        "chown",
        "chgrp",
        "ln",
        "mv",
        "cp",
        "install",
    }
    privilege_commands = {"sudo", "su", "doas", "pkexec"}
    network_commands = {"curl", "wget", "ssh", "scp", "ftp", "nc", "netcat", "telnet"}
    if executable in destructive_commands or any(
        token in {">", ">>", "tee", "truncate"} for token in lowered
    ):
        flags.add(RiskFlag.DESTRUCTIVE)
    if executable in privilege_commands:
        flags.add(RiskFlag.PRIVILEGE_ESCALATION)
    if executable in network_commands or any(_URL.match(token) for token in lowered):
        flags.add(RiskFlag.NETWORK)
    if any(_LOOPBACK.search(token) for token in lowered):
        flags.add(RiskFlag.LOOPBACK)
    if executable in {"pip", "pip3", "uv", "npm", "pnpm", "yarn", "cargo", "brew", "apt"}:
        if any(
            token in {"install", "add", "update", "upgrade", "remove", "uninstall"}
            for token in lowered[1:]
        ):
            flags.add(RiskFlag.DESTRUCTIVE)
            if executable in {"pip", "pip3", "uv", "npm", "pnpm", "yarn", "cargo"}:
                flags.add(RiskFlag.NETWORK)
    if executable == "git" and not _git_read_only(lowered):
        flags.add(RiskFlag.GIT_WRITE)
    for index, token in enumerate(tokens):
        candidate = token.strip("\"'`;,")
        if "=" in candidate and (
            candidate.startswith("-") or candidate.split("=", 1)[0].isidentifier()
        ):
            candidate = candidate.split("=", 1)[1]
        if index > 0 and _outside_like(candidate):
            flags.add(RiskFlag.OUTSIDE_WORKSPACE)
        if candidate and not candidate.startswith("-") and not _outside_like(candidate):
            normalized = candidate.removeprefix("./")
            if "/" in normalized or normalized.startswith("."):
                if files.sensitive_policy.is_protected_path(normalized):
                    flags.add(RiskFlag.PROTECTED_RESOURCE)
                    flags.add(RiskFlag.CREDENTIAL_ACCESS)
    if shell and any(token in {">", ">>", "tee"} for token in lowered):
        flags.add(RiskFlag.DESTRUCTIVE)
    if executable == "git" and any(
        token in {"--git-dir", "--work-tree", "-c"} for token in lowered
    ):
        flags.add(RiskFlag.GIT_WRITE)
    if shell_script is not None:
        flags.update(_shell_script_risks(shell_script, files))
    return flags


def _shell_script_risks(script: str, files: WorkspaceFileService) -> set[RiskFlag]:
    flags: set[RiskFlag] = set()
    lowered = script.casefold()
    destructive_names = {
        "rm",
        "rmdir",
        "del",
        "erase",
        "unlink",
        "truncate",
        "mkfs",
        "dd",
        "shred",
        "kill",
        "pkill",
        "killall",
        "reboot",
        "shutdown",
        "chmod",
        "chown",
        "chgrp",
        "ln",
        "mv",
        "cp",
        "install",
    }
    network_names = {"curl", "wget", "ssh", "scp", "ftp", "nc", "netcat", "telnet"}
    privilege_names = {"sudo", "su", "doas", "pkexec"}

    def contains_command(names: set[str]) -> bool:
        pattern = r"(?<![A-Za-z0-9_./-])(?:" + "|".join(sorted(names)) + r")(?![A-Za-z0-9_./-])"
        return re.search(pattern, lowered) is not None

    if contains_command(destructive_names) or any(operator in script for operator in (">", ">>")):
        flags.add(RiskFlag.DESTRUCTIVE)
    if contains_command(network_names) or _URL.search(script):
        flags.add(RiskFlag.NETWORK)
    if _LOOPBACK.search(script):
        flags.add(RiskFlag.LOOPBACK)
    if contains_command(privilege_names):
        flags.add(RiskFlag.PRIVILEGE_ESCALATION)
    if _SHELL_GIT_COMMAND.search(script):
        flags.add(RiskFlag.GIT_WRITE)
    try:
        shell_tokens = tuple(shlex.split(script))
    except ValueError:
        return flags
    for index, token in enumerate(shell_tokens):
        candidate = token.strip("\"'`;,\n")
        if index > 0 and _outside_like(candidate):
            flags.add(RiskFlag.OUTSIDE_WORKSPACE)
        if candidate and not candidate.startswith("-"):
            normalized = candidate.removeprefix("./")
            if files.sensitive_policy.is_protected_path(normalized):
                flags.add(RiskFlag.PROTECTED_RESOURCE)
                flags.add(RiskFlag.CREDENTIAL_ACCESS)
    return flags


def _git_read_only(tokens: tuple[str, ...]) -> bool:
    if len(tokens) < 2:
        return False
    if any(token.startswith("--git-dir") or token.startswith("--work-tree") for token in tokens):
        return False
    command = tokens[1]
    if command in {"status", "diff", "log", "show", "rev-parse", "ls-files", "describe"}:
        return True
    return command == "branch" and any(flag in tokens[2:] for flag in {"--show-current", "-vv"})


def _outside_like(value: str) -> bool:
    return (
        value.startswith(("/", "~", "\\"))
        or _WINDOWS_PATH.match(value) is not None
        or value == ".."
        or value.startswith("../")
        or "/../" in value
    )


def _tail_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[-max_bytes:].decode("utf-8", errors="ignore")


def _json_size(value: CommandResult) -> int:
    return len(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))
