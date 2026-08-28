"""Host command classification, redaction, and bounded result projection."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.local.process import HostProcessAdapter, ProcessAdapterError
from morrow.core.artifacts import ARTIFACT_MAX_BYTES
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    ToolRunContext,
)
from morrow.core.local_tools import CommandRequest, CommandResult, CommandStatus
from morrow.core.models import ToolEffect
from morrow.core.validation import (
    VALIDATION_FLAGS,
    VALIDATION_FORWARDED_ARG_FAMILIES,
    VALIDATION_OPTION_PREFIXES,
    match_validator_action,
)
from morrow.runtime.truncation import (
    PI_DEFAULT_MAX_BYTES,
    PI_DEFAULT_MAX_LINES,
    truncate_tail,
)
from morrow.services.files import LocalFileError, WorkspaceFileService

MAX_COMMAND_OUTPUT_BYTES = 8 * 1024
MAX_COMMAND_RESULT_BYTES = 16 * 1024
MAX_COMMAND_PREVIEW_CHARS = 180
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "ksh", "dash", "fish"})


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
    validation_kind: str | None = None
    validation_scope: str | None = None


class SecretRedactor:
    """Redact only exact credentials owned by Morrow."""

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
        parsed_shell_script = _shell_script(tokens) if request.shell is None else None
        shell_form = request.shell is not None or parsed_shell_script is not None
        command_class = _command_class(tokens[0], shell=shell_form)
        validation_kind, validation_scope = _recognized_validation(
            tokens,
            files=self.files,
            cwd_relative=resolved.relative_path,
            shell=request.shell is not None,
            shell_script=parsed_shell_script,
            shell_source=request.shell,
        )
        return CommandPlan(
            request=request,
            cwd=resolved.target,
            cwd_relative=resolved.relative_path,
            argv=request.argv,
            shell=request.shell,
            command_class=command_class,
            validation_kind=validation_kind,
            validation_scope=validation_scope,
        )

    def cache_plan(self, run_id: str, call_id: str, plan: CommandPlan) -> None:
        self._plans[(run_id, call_id)] = plan

    def cached_plan(self, run_id: str, call_id: str) -> CommandPlan | None:
        return self._plans.get((run_id, call_id))

    def discard_plan(self, run_id: str, call_id: str) -> None:
        self._plans.pop((run_id, call_id), None)

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
            requires_host=self.requires_host,
            requires_sandbox=self.requires_sandbox,
            preview_summary=(
                "原生沙箱进程（临时快照）" if self.requires_sandbox else "非沙箱宿主进程",
                f"命令类别：{plan.command_class}",
                f"工作目录：{plan.cwd_relative}",
                f"超时上限：{plan.request.timeout_seconds:g} 秒",
                "真实工作空间不会以可写方式暴露；命令修改仅保留在临时快照"
                if self.requires_sandbox
                else "命令以当前用户权限运行",
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
        truncation_max_bytes: int | None = None,
        truncation_max_lines: int | None = None,
    ) -> tuple[CommandResult, object]:
        result, fact, _ = await self.execute_with_artifact(
            plan,
            result_limit=result_limit,
            run=run,
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            approval_verdict=approval_verdict,
            truncation_max_bytes=truncation_max_bytes,
            truncation_max_lines=truncation_max_lines,
        )
        return result, fact

    async def execute_with_artifact(
        self,
        plan: CommandPlan,
        *,
        result_limit: int,
        run: ToolRunContext,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
        truncation_max_bytes: int | None = None,
        truncation_max_lines: int | None = None,
    ) -> tuple[CommandResult, object, bytes]:
        environment = self._minimal_environment()
        pi_truncation = truncation_max_bytes is not None or truncation_max_lines is not None
        if truncation_max_bytes is not None and (
            isinstance(truncation_max_bytes, bool)
            or not isinstance(truncation_max_bytes, int)
            or not 1 <= truncation_max_bytes <= PI_DEFAULT_MAX_BYTES
        ):
            raise ProcessServiceError("invalid_limit", "命令输出字节限制超出边界")
        if truncation_max_lines is not None and (
            isinstance(truncation_max_lines, bool)
            or not isinstance(truncation_max_lines, int)
            or not 1 <= truncation_max_lines <= PI_DEFAULT_MAX_LINES
        ):
            raise ProcessServiceError("invalid_limit", "命令输出行数限制超出边界")
        max_bytes = PI_DEFAULT_MAX_BYTES if truncation_max_bytes is None else truncation_max_bytes
        max_lines = PI_DEFAULT_MAX_LINES if truncation_max_lines is None else truncation_max_lines
        output_limit = min(
            max_bytes if pi_truncation else MAX_COMMAND_OUTPUT_BYTES, max(1, result_limit)
        )
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
        if pi_truncation:
            stdout_truncation = truncate_tail(stdout, max_lines=max_lines, max_bytes=output_limit)
            stderr_truncation = truncate_tail(stderr, max_lines=max_lines, max_bytes=output_limit)
            stdout = stdout_truncation.content
            stderr = stderr_truncation.content
        else:
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
            stdout_truncated=output.stdout_truncated
            or (pi_truncation and stdout_truncation.truncated),
            stderr_truncated=output.stderr_truncated
            or (pi_truncation and stderr_truncation.truncated),
            output_truncated=(
                output.stdout_truncated
                or output.stderr_truncated
                or (pi_truncation and stdout_truncation.truncated)
                or (pi_truncation and stderr_truncation.truncated)
            ),
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
        return result, fact, self._command_artifact(output)

    def _command_artifact(self, output) -> bytes:
        """Render a bounded, redacted command stream for the Artifact store."""

        stdout_raw = getattr(output, "stdout_full", None)
        stderr_raw = getattr(output, "stderr_full", None)
        stdout_raw = output.stdout_tail if stdout_raw is None else stdout_raw
        stderr_raw = output.stderr_tail if stderr_raw is None else stderr_raw
        stdout, _, _ = self.redactor.redact(stdout_raw)
        stderr, _, _ = self.redactor.redact(stderr_raw)
        retention_truncated = bool(getattr(output, "full_output_truncated", False))
        prefix = (
            "[morrow command output; Artifact retention is bounded and truncated]\n"
            if retention_truncated
            else ""
        )
        content = f"{prefix}[stdout]\n{stdout}\n[stderr]\n{stderr}"
        encoded = content.encode("utf-8")
        if len(encoded) <= ARTIFACT_MAX_BYTES:
            return encoded
        return encoded[:ARTIFACT_MAX_BYTES].decode("utf-8", errors="ignore").encode("utf-8")

    @staticmethod
    def validation_fact(
        plan: CommandPlan,
        result: CommandResult,
        *,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
    ):
        """Project a recognized process result into separate validation evidence."""

        if plan.validation_kind is None or plan.validation_scope is None:
            return None
        if result.status is CommandStatus.EXITED:
            status = "passed" if result.exit_code == 0 else "failed"
            evidence = "exit_zero" if status == "passed" else "exit_nonzero"
        elif result.status is CommandStatus.TIMED_OUT:
            status, evidence = "timeout", "timeout"
        elif result.status is CommandStatus.CANCELLED:
            status, evidence = "cancelled", "cancelled"
        else:
            status, evidence = "failed", "signaled"
        from morrow.core.capabilities import ValidationFact

        return ValidationFact(
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            relative_paths=(plan.validation_scope,),
            approval_verdict=approval_verdict,
            validator_kind=plan.validation_kind,
            scope=plan.validation_scope,
            status=status,
            exit_code=result.exit_code,
            evidence_summary=evidence,
        )

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


_SHELL_CONTROL = re.compile(r"[;&|<>$`(){}\[\]\n\r]")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_PYTHON_NAMES = frozenset({"python", "python3", "python3.12", "python3.13"})


def _recognized_validation(
    tokens: tuple[str, ...],
    *,
    files: WorkspaceFileService,
    cwd_relative: str,
    shell: bool,
    shell_script: str | None,
    shell_source: str | None = None,
) -> tuple[str | None, str | None]:
    """Recognize only a single, bounded validator invocation.

    The parser is intentionally conservative.  It never evaluates shell syntax,
    expands variables, follows wrappers, or treats a generic successful command
    as validation evidence.
    """

    if shell_script is not None:
        return None, None
    if shell:
        if shell_source is not None and _SHELL_CONTROL.search(shell_source):
            return None, None
        raw = " ".join(tokens)
        if _SHELL_CONTROL.search(raw) or not tokens:
            return None, None
        if Path(tokens[0]).name.casefold() in _SHELL_INTERPRETERS:
            return None, None
    command_tokens = tokens
    while command_tokens and _ENV_ASSIGNMENT.fullmatch(command_tokens[0]):
        command_tokens = command_tokens[1:]
    normalized = _unwrap_validation_tokens(command_tokens)
    if normalized is None:
        return None, None
    kind, operands, option_family = normalized
    operands = _safe_validation_options(operands, option_family)
    if operands is None:
        return None, None
    scopes: list[str] = []
    for operand in operands:
        scope = _validation_scope(operand, files=files, cwd_relative=cwd_relative)
        if scope is None:
            return None, None
        scopes.append(scope)
    # A validator may name several paths, but the completion contract records one
    # normalized scope.  Multiple disjoint operands are ambiguous and fail closed.
    if len(set(scopes)) > 1:
        return None, None
    return kind, scopes[0] if scopes else cwd_relative


def _unwrap_validation_tokens(
    tokens: tuple[str, ...],
) -> tuple[str, list[str], str] | None:
    if not tokens:
        return None
    index = 0
    executable = _validator_executable(tokens[index], allow_current_python=True)
    if executable is None:
        return None
    if executable == "uv":
        if len(tokens) < 3 or tokens[1] != "run":
            return None
        index = 2
        # Only the plain `uv run <validator>` form is trusted.  Flags can
        # redirect environments or hide the actual executable.
        if tokens[index].startswith("-"):
            return None
        executable = _validator_executable(tokens[index], allow_current_python=True)
        if executable is None:
            return None
    if executable in _PYTHON_NAMES:
        if len(tokens) <= index + 2 or tokens[index + 1 : index + 2] != ("-m",):
            return None
        module = tokens[index + 2]
        index += 3
        return match_validator_action(module, tokens[index:])
    return match_validator_action(executable, tokens[index + 1 :])


def _validator_executable(token: str, *, allow_current_python: bool) -> str | None:
    """Return a trusted validator executable name without basename matching."""

    if "/" not in token and "\\" not in token:
        return token.casefold()
    if not allow_current_python or not Path(token).is_absolute():
        return None
    try:
        if Path(token).resolve(strict=True) != Path(sys.executable).resolve(strict=True):
            return None
    except OSError:
        return None
    return "python"


def _safe_validation_options(operands: list[str], option_family: str) -> list[str] | None:
    allowed = VALIDATION_FLAGS.get(option_family, frozenset())
    paths: list[str] = []
    after_separator = False
    for token in operands:
        if after_separator:
            if option_family not in VALIDATION_FORWARDED_ARG_FAMILIES:
                paths.append(token)
            continue
        if token == "--":
            after_separator = True
            continue
        if not token.startswith("-"):
            paths.append(token)
            continue
        if token in allowed:
            continue
        if any(
            token.startswith(prefix) for prefix in VALIDATION_OPTION_PREFIXES.get(option_family, ())
        ):
            continue
        return None
    return paths


def _validation_scope(
    operand: str,
    *,
    files: WorkspaceFileService,
    cwd_relative: str,
) -> str | None:
    if not operand or operand.startswith("-"):
        return None
    candidate = Path(operand)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(files.resolver.root).as_posix()
        except ValueError:
            return None
    else:
        if "\\" in operand or any(part in {"", ".", ".."} for part in operand.split("/")):
            if operand != ".":
                return None
        relative = (Path(cwd_relative) / candidate).as_posix()
    try:
        relative = files.resolver.validate_relative_path(relative)
        resolved = files.resolver.resolve_existing(relative)
    except LocalFileError:
        return None
    if resolved.is_symlink:
        return None
    return relative


def _shell_script(tokens: tuple[str, ...]) -> str | None:
    executable = Path(tokens[0]).name.casefold()
    if executable not in _SHELL_INTERPRETERS:
        return None
    for index, token in enumerate(tokens[1:-1], start=1):
        if token == "-c":
            return tokens[index + 1]
    return None


def _tail_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[-max_bytes:].decode("utf-8", errors="ignore")


def _json_size(value: CommandResult) -> int:
    return len(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")))
