"""Provider-facing schemas and thin factories for workspace read/search/mutation tools."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from morrow.application.artifacts import ArtifactService
from morrow.core.artifacts import ARTIFACT_MAX_BYTES, ArtifactError, ArtifactErrorCode
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    ProcessIsolation,
    RiskFlag,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.domain import ArtifactReference
from morrow.core.execution import ToolExecutionDisposition, tool_declaration
from morrow.core.local_tools import (
    WORKSPACE_MUTATION_PATH_PATTERN,
    WORKSPACE_RELATIVE_PATH_MAX_CHARS,
    WORKSPACE_RELATIVE_PATH_PATTERN,
    ChangeSetResult,
    CommandRequest,
    ExactEdit,
    MutationMode,
    SearchCase,
    SearchQuery,
    WorkspaceMutationPath,
)
from morrow.core.models import ToolEffect
from morrow.core.store import StorageError
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tool_arguments import SCHEMA_DIALECT
from morrow.runtime.tools import (
    ApprovalPreviewBudget,
    RegisteredTool,
    ToolErrorCode,
    ToolExecutionError,
    make_tool,
)
from morrow.runtime.truncation import PI_DEFAULT_MAX_BYTES, PI_DEFAULT_MAX_LINES
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    LEGACY_MAX_READ_LINES,
    LocalFileError,
    WorkspaceFileService,
    WorkspaceMutationService,
)
from morrow.services.process import ProcessExecutionService, ProcessServiceError
from morrow.services.sandbox import SandboxServiceError, SandboxSnapshotService
from morrow.services.search import WorkspaceSearchService


def _path(value: str) -> str:
    from morrow.core.local_tools import validate_workspace_relative_path

    return validate_workspace_relative_path(value)


def _path_schema(
    *, mutation: bool = False, max_length: int = WORKSPACE_RELATIVE_PATH_MAX_CHARS
) -> dict[str, object]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": max_length,
        "pattern": (
            WORKSPACE_MUTATION_PATH_PATTERN if mutation else WORKSPACE_RELATIVE_PATH_PATTERN
        ),
    }


def _string_schema(
    *,
    min_length: int | None = None,
    max_length: int | None = None,
    pattern: str | None = None,
    enum: tuple[str, ...] | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {"type": "string"}
    if min_length is not None:
        schema["minLength"] = min_length
    if max_length is not None:
        schema["maxLength"] = max_length
    if pattern is not None:
        schema["pattern"] = pattern
    if enum is not None:
        schema["enum"] = list(enum)
    return schema


def _object_schema(
    properties: dict[str, object],
    *,
    required: tuple[str, ...] = (),
    one_of: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    schema: dict[str, object] = {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    if one_of:
        schema["oneOf"] = list(one_of)
    return schema


_ARTIFACT_ID_PATTERN = r"^art_[A-Za-z0-9_-]{1,124}$"

READ_ARTIFACT_PROVIDER_SCHEMA = _object_schema(
    {
        "artifact_id": _string_schema(pattern=_ARTIFACT_ID_PATTERN),
        "start_byte": {
            "type": "integer",
            "minimum": 0,
            "maximum": ARTIFACT_MAX_BYTES,
        },
        "max_bytes": {
            "type": "integer",
            "minimum": 4,
            "maximum": PI_DEFAULT_MAX_BYTES,
        },
    },
    required=("artifact_id",),
)

PROMOTE_SANDBOX_PROVIDER_SCHEMA = _object_schema(
    {
        "change_set_id": _string_schema(pattern=r"^sbx_[0-9a-f]{24}$"),
        "paths": {
            "type": "array",
            "minItems": 1,
            "maxItems": 16,
            "uniqueItems": True,
            "items": _path_schema(mutation=True),
        },
    },
    required=("change_set_id", "paths"),
)


def _simple_object_schema(
    properties: dict[str, object], *, required: tuple[str, ...] = ()
) -> dict[str, object]:
    """Return the intentionally small Pi-compatible model-facing schema shape."""

    schema: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


READ_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "path": {"type": "string", "description": "Path to the file to read"},
        "offset": {
            "type": "number",
            "description": "Line number to start reading from (1-indexed)",
        },
        "limit": {"type": "number", "description": "Maximum number of lines to read"},
    },
    required=("path",),
)

LS_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "path": {"type": "string", "description": "Directory to list (default: current)"},
        "limit": {
            "type": "number",
            "description": "Maximum number of entries to return (default: 500)",
        },
    }
)

FIND_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "pattern": {"type": "string", "description": "Glob pattern to match files"},
        "path": {"type": "string", "description": "Directory to search (default: current)"},
        "limit": {
            "type": "number",
            "description": "Maximum number of results (default: 1000)",
        },
    },
    required=("pattern",),
)

GREP_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "pattern": {"type": "string", "description": "Search pattern (regex by default)"},
        "path": {
            "type": "string",
            "description": "Directory to search (default: current)",
        },
        "glob": {"type": "string", "description": "Optional file glob filter"},
        "literal": {"type": "boolean", "description": "Treat pattern as literal text"},
        "ignoreCase": {"type": "boolean", "description": "Use case-insensitive matching"},
        "context": {"type": "number", "description": "Context lines before and after matches"},
        "limit": {"type": "number", "description": "Maximum matches (default: 100)"},
    },
    required=("pattern",),
)

EDIT_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "path": {"type": "string", "description": "Path to the file to edit"},
        "edits": {
            "type": "array",
            "description": "One or more exact, non-overlapping replacements",
            "items": {
                "type": "object",
                "properties": {
                    "oldText": {"type": "string", "description": "Exact text to replace"},
                    "newText": {"type": "string", "description": "Replacement text"},
                },
                "required": ["oldText", "newText"],
            },
        },
    },
    required=("path", "edits"),
)

WRITE_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "path": {"type": "string", "description": "Path to the file to write"},
        "content": {"type": "string", "description": "Complete file content"},
    },
    required=("path", "content"),
)

BASH_PROVIDER_SCHEMA = _simple_object_schema(
    {
        "command": {"type": "string", "description": "Bash command to execute"},
        "timeout": {"type": "number", "description": "Timeout in seconds"},
    },
    required=("command",),
)


class ReadArtifactArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_id: str = Field(pattern=_ARTIFACT_ID_PATTERN)
    start_byte: int = Field(default=0, ge=0, le=ARTIFACT_MAX_BYTES)
    max_bytes: int = Field(default=PI_DEFAULT_MAX_BYTES, ge=4, le=PI_DEFAULT_MAX_BYTES)

    @field_validator("artifact_id")
    @classmethod
    def valid_artifact_id(cls, value: str) -> str:
        return ArtifactReference(artifact_id=value).artifact_id


class PromoteSandboxChangesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    change_set_id: str = Field(pattern=r"^sbx_[0-9a-f]{24}$")
    paths: tuple[WorkspaceMutationPath, ...] = Field(min_length=1, max_length=16)

    _valid_paths = field_validator("paths")(lambda values: tuple(_path(value) for value in values))


class _CompatibilityArguments(BaseModel):
    """Pi-compatible input: ignore harmless extras and leave policy to the execution adapter."""

    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True)


class ReadArguments(_CompatibilityArguments):
    path: str
    offset: int = 1
    limit: int = 400


class LsArguments(_CompatibilityArguments):
    path: str = "."
    limit: int = 500


class FindArguments(_CompatibilityArguments):
    pattern: str
    path: str = "."
    limit: int = 1000


class GrepArguments(_CompatibilityArguments):
    pattern: str
    path: str = "."
    glob: str | None = None
    literal: bool = False
    ignore_case: bool = Field(default=False, alias="ignoreCase")
    context: int = 0
    limit: int = 100


class EditItem(_CompatibilityArguments):
    old_text: str = Field(alias="oldText")
    new_text: str = Field(alias="newText")


class EditArguments(_CompatibilityArguments):
    path: str
    edits: tuple[EditItem, ...]


class WriteArguments(_CompatibilityArguments):
    path: str
    content: str


class BashArguments(_CompatibilityArguments):
    command: str
    timeout: float = 90.0


def _tool_error(
    error: LocalFileError | ProcessServiceError | SandboxServiceError,
) -> ToolExecutionError:
    mapping = {
        "invalid_path": ToolErrorCode.INVALID_PATH,
        "outside_workspace": ToolErrorCode.OUTSIDE_WORKSPACE,
        "invalid_target": ToolErrorCode.INVALID_TARGET,
        "invalid_pattern": ToolErrorCode.INVALID_PATTERN,
        "invalid_glob": ToolErrorCode.INVALID_GLOB,
        "invalid_range": ToolErrorCode.INVALID_RANGE,
        "invalid_depth": ToolErrorCode.INVALID_DEPTH,
        "invalid_limit": ToolErrorCode.INVALID_LIMIT,
        "binary_file": ToolErrorCode.BINARY_FILE,
        "invalid_utf8": ToolErrorCode.INVALID_UTF8,
        "file_too_large": ToolErrorCode.FILE_TOO_LARGE,
        "read_failed": ToolErrorCode.READ_FAILED,
        "list_failed": ToolErrorCode.LIST_FAILED,
        "path_unavailable": ToolErrorCode.PATH_UNAVAILABLE,
        "not_found": ToolErrorCode.NOT_FOUND,
        "symlink_not_allowed": ToolErrorCode.SYMLINK_NOT_ALLOWED,
        "conflict": ToolErrorCode.CONFLICT,
        "source_conflict": ToolErrorCode.CONFLICT,
        "destination_exists": ToolErrorCode.CONFLICT,
        "edit_not_found": ToolErrorCode.EDIT_NOT_FOUND,
        "edit_not_unique": ToolErrorCode.EDIT_NOT_UNIQUE,
        "edit_overlap": ToolErrorCode.EDIT_OVERLAP,
        "mutation_limit": ToolErrorCode.MUTATION_LIMIT,
        "protected_resource": ToolErrorCode.PROTECTED_RESOURCE,
        "publish_failed": ToolErrorCode.PUBLISH_FAILED,
        "outcome_unknown": ToolErrorCode.PUBLISH_FAILED,
        "unsupported_capability": ToolErrorCode.UNSUPPORTED_CAPABILITY,
        "cross_device": ToolErrorCode.PUBLISH_FAILED,
        "invalid_command": ToolErrorCode.INVALID_COMMAND,
        "spawn_failed": ToolErrorCode.PROCESS_FAILED,
        "process_failed": ToolErrorCode.PROCESS_FAILED,
        "cleanup_failed": ToolErrorCode.PROCESS_CLEANUP_FAILED,
        "sandbox_unavailable": ToolErrorCode.SANDBOX_UNAVAILABLE,
        "sandbox_violation": ToolErrorCode.SANDBOX_VIOLATION,
        "sandbox_limit": ToolErrorCode.SANDBOX_LIMIT,
        "sandbox_cleanup_failed": ToolErrorCode.PROCESS_CLEANUP_FAILED,
        "sandbox_timeout": ToolErrorCode.TIMEOUT,
        "sandbox_change_set_not_found": ToolErrorCode.NOT_FOUND,
        "sandbox_selection_invalid": ToolErrorCode.INVALID_ARGUMENTS,
        "sandbox_change_not_eligible": ToolErrorCode.PERMISSION_DENIED,
        "invalid_output_limit": ToolErrorCode.OUTPUT_BUDGET,
        "invalid_mode": ToolErrorCode.INVALID_ARGUMENTS,
        "unsupported_newline": ToolErrorCode.INVALID_ARGUMENTS,
        "search_failed": ToolErrorCode.SEARCH_FAILED,
        "rg_timeout": ToolErrorCode.SEARCH_BUDGET,
        "timeout": ToolErrorCode.SEARCH_BUDGET,
        "max_files": ToolErrorCode.SEARCH_BUDGET,
        "max_bytes": ToolErrorCode.SEARCH_BUDGET,
        "output_budget": ToolErrorCode.OUTPUT_BUDGET,
    }
    return ToolExecutionError(
        mapping.get(error.code, ToolErrorCode.EXECUTION_FAILED),
        error.message,
        disposition=(ToolExecutionDisposition.UNKNOWN if error.code == "outcome_unknown" else None),
        facts=tuple(getattr(error, "facts", ())),
    )


def _model_path(value: str, files: WorkspaceFileService) -> str:
    """Normalize familiar path spellings while keeping the frozen workspace boundary."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise LocalFileError("invalid_path", "路径必须是非空文本")
    try:
        supplied = Path(value).expanduser()
        candidate = supplied if supplied.is_absolute() else files.resolver.root / supplied
        # Normalize spelling without resolving symlinks. The service must still inspect the
        # visible alias and every existing component so protected-path and symlink policy applies.
        normalized = Path(os.path.abspath(candidate))
        relative = normalized.relative_to(files.resolver.root)
    except (OSError, RuntimeError):
        raise LocalFileError("invalid_path", "路径无法解析") from None
    except ValueError:
        raise LocalFileError("outside_workspace", "目标不在当前工作空间内") from None
    rendered = relative.as_posix()
    return rendered if rendered else "."


def _bounded(value: int | float, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _artifact_tool_error(error: ArtifactError) -> ToolExecutionError:
    code = {
        ArtifactErrorCode.MISSING: ToolErrorCode.NOT_FOUND,
        ArtifactErrorCode.BUDGET: ToolErrorCode.OUTPUT_BUDGET,
        ArtifactErrorCode.INVALID: ToolErrorCode.INVALID_ARGUMENTS,
        ArtifactErrorCode.INTEGRITY: ToolErrorCode.READ_FAILED,
        ArtifactErrorCode.PATH: ToolErrorCode.READ_FAILED,
        ArtifactErrorCode.UNAVAILABLE: ToolErrorCode.READ_FAILED,
        ArtifactErrorCode.CONFLICT: ToolErrorCode.READ_FAILED,
    }.get(error.code, ToolErrorCode.READ_FAILED)
    message = {
        ToolErrorCode.NOT_FOUND: "Artifact 不存在、不可用或不属于当前会话",
        ToolErrorCode.OUTPUT_BUDGET: "Artifact 读取预算不足",
        ToolErrorCode.INVALID_ARGUMENTS: "Artifact 读取范围无效",
        ToolErrorCode.READ_FAILED: "Artifact 读取失败",
    }[code]
    return ToolExecutionError(code, message)


def _complete_utf8_prefix(content: bytes) -> tuple[bytes, str]:
    """Decode a chunk without exposing an incomplete trailing UTF-8 code point."""

    try:
        return content, content.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.reason != "unexpected end of data" or exc.end != len(content):
            raise
        complete = content[: exc.start]
        if not complete:
            raise
        return complete, complete.decode("utf-8")


def _intent(path: str, service: WorkspaceFileService, *, directory: bool) -> OperationIntent:
    try:
        resolved = service.preflight_directory(path) if directory else service.preflight_file(path)
    except LocalFileError as exc:
        raise _tool_error(exc) from exc
    return OperationIntent(
        kind=OperationKind.WORKSPACE_READ, relative_paths=(resolved.relative_path,)
    )


def make_read_artifact_tool(artifacts: ArtifactService) -> RegisteredTool:
    async def handler(arguments: ReadArtifactArguments, context: ToolCallContext):
        # ArtifactService shares the owner-thread SQLite handle with the foreground Session;
        # keep its journal lookup/read on that thread rather than crossing into asyncio.to_thread.
        metadata = artifacts.get(arguments.artifact_id)
        if metadata is None or metadata.session_id != context.run.session_id:
            raise ToolExecutionError(
                ToolErrorCode.NOT_FOUND,
                "Artifact 不存在、不可用或不属于当前会话",
            )
        read_limit = min(
            arguments.max_bytes,
            context.truncation_max_bytes,
            max(1, context.result_limit - 1024),
        )
        requested_start = arguments.start_byte
        lookbehind = min(3, requested_start)
        read_start = requested_start - lookbehind
        try:
            result = artifacts.read(
                arguments.artifact_id,
                max_bytes=min(ARTIFACT_MAX_BYTES, read_limit + lookbehind),
                start_byte=read_start,
            )
        except ArtifactError as exc:
            raise _artifact_tool_error(exc) from exc
        except StorageError as exc:
            raise ToolExecutionError(ToolErrorCode.READ_FAILED, "Artifact 读取失败") from exc
        try:
            relative_start = requested_start - read_start
            while (
                relative_start > 0
                and relative_start < len(result.content)
                and result.content[relative_start] & 0xC0 == 0x80
            ):
                relative_start -= 1
            actual_start = read_start + relative_start
            chunk, content = _complete_utf8_prefix(
                result.content[relative_start : relative_start + read_limit]
            )
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(
                ToolErrorCode.BINARY_FILE,
                "Artifact 不是可安全读取的 UTF-8 文本",
            ) from exc
        if result.content[relative_start:] and not chunk:
            raise ToolExecutionError(
                ToolErrorCode.INVALID_ARGUMENTS,
                "Artifact 读取预算不足以返回一个完整 UTF-8 字符",
            )
        next_start_byte = actual_start + len(chunk)
        truncated = next_start_byte < result.metadata.byte_size
        return ToolHandlerOutcome(
            payload={
                "artifact_id": result.metadata.artifact_id,
                "kind": result.metadata.kind.value,
                "sha256": result.metadata.sha256,
                "byte_size": result.metadata.byte_size,
                "start_byte": actual_start,
                "next_start_byte": next_start_byte if truncated else None,
                "truncated": truncated,
                "content": content,
            }
        )

    def resolve(_: ReadArtifactArguments, __: ToolCallContext) -> OperationIntent:
        return OperationIntent(kind=OperationKind.INTERNAL_READ)

    return make_tool(
        name="read_artifact",
        description=(
            "按 Artifact 引用分段读取当前会话已安全持久化的文本结果；"
            "截断时返回 next_start_byte，避免把完整结果一次性放入上下文。"
        ),
        arguments_model=ReadArtifactArguments,
        provider_schema=READ_ARTIFACT_PROVIDER_SCHEMA,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        recovery_declaration=tool_declaration("read_artifact"),
    )


def make_mainstream_read_search_tools(
    files: WorkspaceFileService,
    search: WorkspaceSearchService,
    *,
    long_horizon: bool = False,
) -> tuple[RegisteredTool, ...]:
    """Build the Pi-compatible read/ls/find/grep surface over Morrow services."""

    read_max = PI_DEFAULT_MAX_LINES if long_horizon else LEGACY_MAX_READ_LINES

    async def read_handler(arguments: ReadArguments, context: ToolCallContext):
        try:
            path = _model_path(arguments.path, files)
            result = await asyncio.to_thread(
                files.read_file,
                path,
                start_line=max(1, arguments.offset),
                line_count=_bounded(arguments.limit, minimum=1, maximum=read_max),
                result_limit=context.result_limit,
                max_bytes=context.truncation_max_bytes if context.long_horizon else None,
                max_lines=context.truncation_max_lines if context.long_horizon else None,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def read_resolve(arguments: ReadArguments, _: ToolCallContext) -> OperationIntent:
        try:
            return _intent(_model_path(arguments.path, files), files, directory=False)
        except LocalFileError as exc:
            raise _tool_error(exc) from exc

    read = make_tool(
        name="read",
        description="Read a text file. Use offset and limit to continue through large files.",
        arguments_model=ReadArguments,
        provider_schema=READ_PROVIDER_SCHEMA,
        handler=read_handler,
        context_handler=read_handler,
        intent_resolver=read_resolve,
        recovery_declaration=tool_declaration("read"),
    )

    async def ls_handler(arguments: LsArguments, context: ToolCallContext):
        try:
            path = _model_path(arguments.path, files)
            result = await asyncio.to_thread(
                files.list_directory,
                path,
                max_entries=_bounded(arguments.limit, minimum=1, maximum=500),
                result_limit=context.result_limit,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def ls_resolve(arguments: LsArguments, _: ToolCallContext) -> OperationIntent:
        try:
            return _intent(_model_path(arguments.path, files), files, directory=True)
        except LocalFileError as exc:
            raise _tool_error(exc) from exc

    ls = make_tool(
        name="ls",
        description="List entries in a directory.",
        arguments_model=LsArguments,
        provider_schema=LS_PROVIDER_SCHEMA,
        handler=ls_handler,
        context_handler=ls_handler,
        intent_resolver=ls_resolve,
        recovery_declaration=tool_declaration("ls"),
    )

    async def find_handler(arguments: FindArguments, context: ToolCallContext):
        try:
            path = _model_path(arguments.path, files)
            result = await asyncio.to_thread(
                files.find_files,
                path,
                pattern=arguments.pattern,
                max_results=_bounded(arguments.limit, minimum=1, maximum=1000),
                result_limit=context.result_limit,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def find_resolve(arguments: FindArguments, _: ToolCallContext) -> OperationIntent:
        try:
            return _intent(_model_path(arguments.path, files), files, directory=True)
        except LocalFileError as exc:
            raise _tool_error(exc) from exc

    find = make_tool(
        name="find",
        description="Find files by glob pattern.",
        arguments_model=FindArguments,
        provider_schema=FIND_PROVIDER_SCHEMA,
        handler=find_handler,
        context_handler=find_handler,
        intent_resolver=find_resolve,
        recovery_declaration=tool_declaration("find"),
    )

    async def grep_handler(arguments: GrepArguments, context: ToolCallContext):
        query = SearchQuery(
            pattern=arguments.pattern,
            literal=arguments.literal,
            case=SearchCase.INSENSITIVE if arguments.ignore_case else SearchCase.SENSITIVE,
            glob=arguments.glob,
            context_lines=_bounded(arguments.context, minimum=0, maximum=3),
            max_results=_bounded(arguments.limit, minimum=1, maximum=100),
        )
        try:
            path = _model_path(arguments.path, files)
            result = await asyncio.to_thread(
                search.search_text,
                path,
                query=query,
                result_limit=context.result_limit,
                max_line_chars=context.grep_max_line_chars if context.long_horizon else None,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def grep_resolve(arguments: GrepArguments, _: ToolCallContext) -> OperationIntent:
        try:
            return _intent(_model_path(arguments.path, files), files, directory=True)
        except LocalFileError as exc:
            raise _tool_error(exc) from exc

    grep = make_tool(
        name="grep",
        description="Search file contents with a regular expression or literal text.",
        arguments_model=GrepArguments,
        provider_schema=GREP_PROVIDER_SCHEMA,
        handler=grep_handler,
        context_handler=grep_handler,
        intent_resolver=grep_resolve,
        recovery_declaration=tool_declaration("grep"),
    )
    return read, ls, find, grep


COMMAND_PREVIEW_BUDGET = ApprovalPreviewBudget(
    max_lines=8,
    max_line_chars=200,
    max_bytes=1600,
)


def make_bash_tool(process: ProcessExecutionService) -> RegisteredTool:
    """Expose one conventional command string while retaining process preflight and policy."""

    def request(arguments: BashArguments) -> CommandRequest:
        return CommandRequest(
            shell=arguments.command,
            cwd=".",
            timeout_seconds=float(_bounded(arguments.timeout, minimum=1, maximum=90)),
        )

    def resolve(arguments: BashArguments, context: ToolCallContext) -> OperationIntent:
        try:
            plan = process.preflight(request(arguments))
        except (LocalFileError, ProcessServiceError) as exc:
            raise _tool_error(exc) from exc
        except ValueError:
            raise ToolExecutionError(
                ToolErrorCode.INVALID_ARGUMENTS, "命令参数超出执行端边界"
            ) from None
        process.cache_plan(context.run.run_id, context.call_id, plan)
        return process.intent(plan)

    def preview(arguments: BashArguments, context: ToolCallContext) -> tuple[str, ...]:
        del arguments
        plan = process.cached_plan(context.run.run_id, context.call_id)
        if plan is None:
            return ("无法生成宿主命令预览",)
        return (
            f"命令：{process.approval_command(plan)}",
            f"命令类别：{plan.command_class}",
            f"工作目录：{plan.cwd_relative}",
            f"超时上限：{plan.request.timeout_seconds:g} 秒",
            (
                "原生沙箱进程（临时快照）；真实工作空间不会以可写方式暴露"
                if process.requires_sandbox
                else "非沙箱宿主进程；批准后可能访问工作空间外文件或网络"
            ),
        )

    async def handler(arguments: BashArguments, context: ToolCallContext):
        del arguments
        plan = process.cached_plan(context.run.run_id, context.call_id)
        if plan is None:
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "命令预检不存在")
        try:
            result, fact, artifact_content = await process.execute_with_artifact(
                plan,
                result_limit=context.result_limit,
                run=context.run,
                call_id=context.call_id,
                tool_name=context.tool_name,
                ordinal=context.ordinal,
                approval_verdict=context.approval_verdict,
                truncation_max_bytes=(
                    context.truncation_max_bytes if context.long_horizon else None
                ),
                truncation_max_lines=(
                    context.truncation_max_lines if context.long_horizon else None
                ),
            )
        except ProcessServiceError as exc:
            raise _tool_error(exc) from exc
        validation_fact = process.validation_fact(
            plan,
            result,
            call_id=context.call_id,
            tool_name=context.tool_name,
            ordinal=context.ordinal,
            approval_verdict=context.approval_verdict,
        )
        facts = (fact,) if validation_fact is None else (fact, validation_fact)
        return ToolHandlerOutcome(
            payload=result.model_dump(mode="json"),
            facts=facts,
            artifact_content=artifact_content,
        )

    return make_tool(
        name="bash",
        description="Run a shell command in the workspace and return stdout, stderr, and status.",
        arguments_model=BashArguments,
        provider_schema=BASH_PROVIDER_SCHEMA,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        context_cleanup=lambda context: process.discard_plan(context.run.run_id, context.call_id),
        approval_preview_budget=COMMAND_PREVIEW_BUDGET,
        recovery_declaration=tool_declaration(
            "bash",
            process_isolation=(
                ProcessIsolation.NATIVE_SANDBOX
                if process.requires_sandbox
                else ProcessIsolation.HOST
            ),
        ),
    )


PROMOTION_PREVIEW_BUDGET = ApprovalPreviewBudget(
    max_lines=40,
    max_line_chars=240,
    max_bytes=4 * 1024,
    preserve_whitespace=True,
)


def make_promote_sandbox_tool(
    sandbox: SandboxSnapshotService,
    mutation: WorkspaceMutationService,
    changes_service: ChangeSetService,
) -> RegisteredTool:
    def selected(arguments: PromoteSandboxChangesArguments, context: ToolCallContext):
        try:
            return sandbox.selected_changes(context.run, arguments.change_set_id, arguments.paths)
        except SandboxServiceError as exc:
            raise _tool_error(exc) from exc

    def resolve(
        arguments: PromoteSandboxChangesArguments, context: ToolCallContext
    ) -> OperationIntent:
        changes = selected(arguments, context)
        try:
            plans = tuple(_promotion_plan(change, mutation, context.run) for change in changes)
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        mutation.cache_plans(context.run.run_id, context.call_id, plans)
        return OperationIntent(
            kind=OperationKind.WORKSPACE_WRITE,
            effect=ToolEffect.PERSISTENT_WRITE,
            relative_paths=tuple(path for change in changes for path in change.relative_paths),
            risk_flags=(RiskFlag.MUTATION_APPROVAL_REQUIRED,),
            preview_summary=(
                "沙箱变更推广需要审批",
                f"变更集合：{arguments.change_set_id}",
                f"文件数：{len(changes)}",
            ),
        )

    def preview(
        arguments: PromoteSandboxChangesArguments, context: ToolCallContext
    ) -> tuple[str, ...]:
        changes = selected(arguments, context)
        lines = []
        for change in changes:
            lines.extend(change.diff.splitlines())
        return tuple(lines or ("无可显示的文本 Diff",))

    async def handler(
        arguments: PromoteSandboxChangesArguments, context: ToolCallContext
    ) -> ToolHandlerOutcome:
        changes = selected(arguments, context)
        plans = mutation.cached_plans(context.run.run_id, context.call_id)
        if plans is None or len(plans) != len(changes):
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "沙箱变更预检不存在")
        results = []
        facts = []
        for index, (_change, plan) in enumerate(zip(changes, plans, strict=True)):
            try:
                result, fact = await asyncio.to_thread(
                    mutation.apply,
                    plan,
                    call_id=context.call_id,
                    tool_name=context.tool_name,
                    ordinal=context.ordinal,
                    approval_verdict=context.approval_verdict,
                    run=context.run,
                )
            except LocalFileError as exc:
                if exc.change_result is not None:
                    changes_service.record(context.run, exc.change_result)
                current_facts = tuple(facts) + tuple(exc.facts)
                if facts:
                    raise ToolExecutionError(
                        ToolErrorCode.PUBLISH_FAILED,
                        "部分沙箱变更已生效，其余变更未完成",
                        facts=current_facts,
                        disposition=(
                            ToolExecutionDisposition.UNKNOWN
                            if exc.code == "outcome_unknown"
                            else None
                        ),
                        details=(
                            {"key": "applied", "value": str(len(facts))},
                            {"key": "remaining", "value": str(len(changes) - index)},
                        ),
                    ) from exc
                if current_facts:
                    raise ToolExecutionError(
                        ToolErrorCode.PUBLISH_FAILED,
                        exc.message,
                        disposition=(
                            ToolExecutionDisposition.UNKNOWN
                            if exc.code == "outcome_unknown"
                            else None
                        ),
                        facts=current_facts,
                    ) from exc
                raise _tool_error(exc) from exc
            changes_service.record(context.run, result)
            results.append(result)
            facts.append(fact)
        payload = ChangeSetResult(entries=tuple(results)).model_dump(mode="json")
        return ToolHandlerOutcome(payload=payload, facts=tuple(facts))

    return make_tool(
        name="promote_sandbox_changes",
        description="在当前运行中选择沙箱生成的文本变更，并在明确审批后以冲突安全方式推广到真实工作空间。",
        arguments_model=PromoteSandboxChangesArguments,
        provider_schema=PROMOTE_SANDBOX_PROVIDER_SCHEMA,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        context_cleanup=lambda context: mutation.discard_previews(
            context.run.run_id, context.call_id
        ),
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.PERSISTENT_WRITE,
            approval=ToolApproval.REQUIRED,
        ),
        approval_preview_budget=PROMOTION_PREVIEW_BUDGET,
        recovery_declaration=tool_declaration("promote_sandbox_changes"),
    )


def _promotion_plan(change, mutation: WorkspaceMutationService, run):
    operation = change.operation
    if operation == "created":
        return mutation.preflight_write(
            change.relative_path,
            content=change.content or "",
            mode="create",
            expected_sha256=None,
            run=run,
        )
    if operation == "modified":
        return mutation.preflight_write(
            change.relative_path,
            content=change.content or "",
            mode="replace",
            expected_sha256=change.expected_sha256,
            run=run,
        )
    if operation == "deleted":
        return mutation.preflight_delete(
            change.relative_path,
            expected_sha256=change.expected_sha256 or "",
            run=run,
        )
    if operation in {"moved", "renamed"}:
        source_path = change.source_path or change.relative_path
        destination_path = change.destination_path
        if destination_path is None:
            raise LocalFileError("conflict", "沙箱移动目标缺失")
        preflight = mutation.preflight_move if operation == "moved" else mutation.preflight_rename
        return preflight(
            source_path,
            destination_path,
            expected_sha256=change.expected_sha256 or "",
            run=run,
        )
    raise LocalFileError("invalid_target", "沙箱变更类型不受支持")


MUTATION_PREVIEW_BUDGET = ApprovalPreviewBudget(
    max_lines=40,
    max_line_chars=240,
    max_bytes=4 * 1024,
    preserve_whitespace=True,
)


def _mutation_intent(
    plan, mutation: WorkspaceMutationService, context: ToolCallContext
) -> OperationIntent:
    mutation.cache_plan(context.run.run_id, context.call_id, plan)
    flags = (RiskFlag.MUTATION_APPROVAL_REQUIRED,) if plan.threshold_exceeded else ()
    return OperationIntent(
        kind=OperationKind.WORKSPACE_WRITE,
        effect=ToolEffect.PERSISTENT_WRITE,
        relative_paths=plan.relative_paths,
        risk_flags=flags,
        preview_summary=(
            f"文件操作：{plan.operation.value}",
            f"路径：{plan.relative_path}",
            f"变更行数：{plan.changed_lines}，变更字节：{plan.changed_bytes}",
        ),
    )


def _mutation_preview(plan) -> tuple[str, ...]:
    lines = [
        f"路径：{plan.relative_path}",
        f"操作：{plan.operation.value}",
        f"变更行数：{plan.changed_lines}，变更字节：{plan.changed_bytes}",
    ]
    if plan.destination_relative_path is not None:
        lines.append(f"目标路径：{plan.destination_relative_path}")
    if plan.auxiliary_paths:
        lines.append("新增父目录：" + ", ".join(plan.auxiliary_paths))
    diff_lines = plan.diff.splitlines()
    preview_budget = MUTATION_PREVIEW_BUDGET
    preview_bytes = sum(len(line.encode("utf-8")) for line in diff_lines)
    preview_capacity_exceeded = (
        len(lines) + len(diff_lines) > preview_budget.max_lines
        or preview_bytes > preview_budget.max_bytes
    )
    if plan.diff_truncated or preview_capacity_exceeded:
        lines.append("... diff truncated ...")
    lines.extend(diff_lines)
    if not plan.diff:
        lines.append("（无实际内容变化）")
    return tuple(lines)


async def _blocking_mutation(callback):
    task = asyncio.create_task(asyncio.to_thread(callback))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        raise


def _compatibility_mutation_tool(
    *,
    name: str,
    description: str,
    arguments_model: type[BaseModel],
    provider_schema: dict[str, object],
    preflight,
    mutation: WorkspaceMutationService,
    changes: ChangeSetService,
) -> RegisteredTool:
    def resolve(arguments, context: ToolCallContext) -> OperationIntent:
        try:
            plan = preflight(arguments, context)
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return _mutation_intent(plan, mutation, context)

    def preview(arguments, context: ToolCallContext) -> tuple[str, ...]:
        del arguments
        plan = mutation.cached_plan(context.run.run_id, context.call_id)
        return _mutation_preview(plan) if plan is not None else ("无法生成变更预览",)

    async def handler(arguments, context: ToolCallContext):
        del arguments
        plan = mutation.cached_plan(context.run.run_id, context.call_id)
        if plan is None:
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "变更预检不存在")
        try:
            result, fact = await _blocking_mutation(
                lambda: mutation.apply(
                    plan,
                    call_id=context.call_id,
                    tool_name=context.tool_name,
                    ordinal=context.ordinal,
                    approval_verdict=context.approval_verdict,
                    run=context.run,
                )
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        changes.record(context.run, result)
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"), facts=(fact,))

    return make_tool(
        name=name,
        description=description,
        arguments_model=arguments_model,
        provider_schema=provider_schema,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        context_cleanup=lambda context: mutation.discard_previews(
            context.run.run_id, context.call_id
        ),
        approval_preview_budget=MUTATION_PREVIEW_BUDGET,
        recovery_declaration=tool_declaration(name),
    )


def make_edit_tool(mutation: WorkspaceMutationService, changes: ChangeSetService) -> RegisteredTool:
    def preflight(arguments: EditArguments, context: ToolCallContext):
        path = _model_path(arguments.path, mutation.files)
        source = mutation.files.read_source_text(path)
        try:
            edits = tuple(
                ExactEdit(old_text=item.old_text, new_text=item.new_text)
                for item in arguments.edits
            )
        except ValueError:
            raise LocalFileError("invalid_range", "编辑内容超出执行端边界") from None
        if not edits:
            raise LocalFileError("invalid_range", "至少需要一个编辑")
        return mutation.preflight_patch(
            path,
            expected_sha256=source.revision.sha256,
            edits=edits,
            run=context.run,
        )

    return _compatibility_mutation_tool(
        name="edit",
        description="Replace exact text in a file. Read the file first and use unique oldText.",
        arguments_model=EditArguments,
        provider_schema=EDIT_PROVIDER_SCHEMA,
        preflight=preflight,
        mutation=mutation,
        changes=changes,
    )


def make_write_tool(
    mutation: WorkspaceMutationService, changes: ChangeSetService
) -> RegisteredTool:
    def preflight(arguments: WriteArguments, context: ToolCallContext):
        path = _model_path(arguments.path, mutation.files)
        target = mutation.files.resolver.resolve_mutation(path)
        if target.kind == "missing":
            mode = MutationMode.CREATE
            expected_sha256 = None
        else:
            source = mutation.files.read_source_text(path)
            mode = MutationMode.REPLACE
            expected_sha256 = source.revision.sha256
        return mutation.preflight_write(
            path,
            content=arguments.content,
            mode=mode.value,
            expected_sha256=expected_sha256,
            run=context.run,
        )

    return _compatibility_mutation_tool(
        name="write",
        description="Create or overwrite a text file with complete content.",
        arguments_model=WriteArguments,
        provider_schema=WRITE_PROVIDER_SCHEMA,
        preflight=preflight,
        mutation=mutation,
        changes=changes,
    )
