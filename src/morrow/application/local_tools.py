"""Provider-facing schemas and thin factories for workspace read/search/mutation tools."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    RiskFlag,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.local_tools import (
    ChangeSetResult,
    CommandRequest,
    ExactEdit,
    MutationMode,
    SearchCase,
    SearchQuery,
)
from morrow.core.models import ToolEffect
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import (
    ApprovalPreviewBudget,
    RegisteredTool,
    ToolErrorCode,
    ToolExecutionError,
    make_tool,
)
from morrow.services.changes import ChangeSetService
from morrow.services.files import LocalFileError, WorkspaceFileService, WorkspaceMutationService
from morrow.services.git import GitInspectionService, GitServiceError
from morrow.services.process import ProcessExecutionService, ProcessServiceError
from morrow.services.sandbox import SandboxServiceError, SandboxSnapshotService
from morrow.services.search import WorkspaceSearchService


def _path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty workspace-relative string")
    if "\x00" in value or "\\" in value or value.startswith("/") or value.startswith("~"):
        raise ValueError("path must be workspace-relative")
    return value


class ListDirectoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = "."
    depth: int = Field(default=1, ge=1, le=4)
    max_entries: int = Field(default=500, ge=1, le=500)

    _valid_path = field_validator("path")(_path)


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    start_line: int = Field(default=1, ge=1)
    line_count: int = Field(default=400, ge=1, le=400)

    _valid_path = field_validator("path")(_path)


class FindFilesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = "."
    pattern: str = Field(min_length=1, max_length=128)
    max_results: int = Field(default=1000, ge=1, le=1000)

    _valid_path = field_validator("path")(_path)


class SearchTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = "."
    pattern: str = Field(min_length=1, max_length=256)
    literal: bool = True
    case: SearchCase = SearchCase.SMART
    glob: str | None = Field(default=None, max_length=128)
    context_lines: int = Field(default=0, ge=0, le=3)
    max_results: int = Field(default=100, ge=1, le=100)

    _valid_path = field_validator("path")(_path)


class ApplyPatchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Keep 9-16 edits schema-valid so policy can require approval instead of rejecting them.
    edits: tuple[ExactEdit, ...] = Field(min_length=1, max_length=16)

    _valid_path = field_validator("path")(_path)


class WriteFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    content: str = Field(max_length=1024 * 1024)
    mode: MutationMode
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    _valid_path = field_validator("path")(_path)

    @model_validator(mode="after")
    def expected_revision_matches_mode(self) -> WriteFileArguments:
        if self.mode is MutationMode.REPLACE and self.expected_sha256 is None:
            raise ValueError("replace requires expected_sha256")
        if self.mode is MutationMode.CREATE and self.expected_sha256 is not None:
            raise ValueError("create does not accept expected_sha256")
        return self


class ShowChangesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunCommandArguments(CommandRequest):
    """Strict Provider-facing command schema; environment/stdin/TTY are absent by design."""


class PromoteSandboxChangesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    change_set_id: str = Field(pattern=r"^sbx_[0-9a-f]{24}$")
    paths: tuple[str, ...] = Field(min_length=1, max_length=16)

    _valid_paths = field_validator("paths")(lambda values: tuple(_path(value) for value in values))


class GitStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GitDiffArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    staged: bool = False
    paths: tuple[str, ...] = Field(default=(), max_length=32)

    _valid_paths = field_validator("paths")(lambda values: tuple(_path(value) for value in values))


def _tool_error(
    error: LocalFileError | ProcessServiceError | GitServiceError,
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
        "edit_not_found": ToolErrorCode.EDIT_NOT_FOUND,
        "edit_not_unique": ToolErrorCode.EDIT_NOT_UNIQUE,
        "edit_overlap": ToolErrorCode.EDIT_OVERLAP,
        "mutation_limit": ToolErrorCode.MUTATION_LIMIT,
        "protected_resource": ToolErrorCode.PROTECTED_RESOURCE,
        "publish_failed": ToolErrorCode.PUBLISH_FAILED,
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
        "external_git_metadata": ToolErrorCode.EXTERNAL_GIT_METADATA,
        "git_unavailable": ToolErrorCode.GIT_UNAVAILABLE,
        "git_timeout": ToolErrorCode.GIT_TIMEOUT,
        "git_command_failed": ToolErrorCode.GIT_FAILED,
        "git_parse_failed": ToolErrorCode.GIT_FAILED,
        "git_failed": ToolErrorCode.GIT_FAILED,
    }
    return ToolExecutionError(
        mapping.get(error.code, ToolErrorCode.EXECUTION_FAILED), error.message
    )


def _intent(path: str, service: WorkspaceFileService, *, directory: bool) -> OperationIntent:
    try:
        resolved = service.preflight_directory(path) if directory else service.preflight_file(path)
    except LocalFileError as exc:
        raise _tool_error(exc) from exc
    return OperationIntent(
        kind=OperationKind.WORKSPACE_READ, relative_paths=(resolved.relative_path,)
    )


def make_list_directory_tool(files: WorkspaceFileService) -> RegisteredTool:
    async def handler(arguments: ListDirectoryArguments, context: ToolCallContext):
        try:
            result = await asyncio.to_thread(
                files.list_directory,
                arguments.path,
                depth=arguments.depth,
                max_entries=arguments.max_entries,
                result_limit=context.result_limit,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def resolve(arguments: ListDirectoryArguments, _: ToolCallContext) -> OperationIntent:
        return _intent(arguments.path, files, directory=True)

    return make_tool(
        name="list_directory",
        description="列出工作空间内目录的有界条目；不会穿越目录符号链接或读取文件内容。",
        arguments_model=ListDirectoryArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )


def make_read_file_tool(files: WorkspaceFileService) -> RegisteredTool:
    async def handler(arguments: ReadFileArguments, context: ToolCallContext):
        try:
            result = await asyncio.to_thread(
                files.read_file,
                arguments.path,
                start_line=arguments.start_line,
                line_count=arguments.line_count,
                result_limit=context.result_limit,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def resolve(arguments: ReadFileArguments, _: ToolCallContext) -> OperationIntent:
        return _intent(arguments.path, files, directory=False)

    return make_tool(
        name="read_file",
        description="读取工作空间内 UTF-8 文本文件的有界行窗口；结果包含 revision 与继续读取位置。",
        arguments_model=ReadFileArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )


def make_find_files_tool(files: WorkspaceFileService) -> RegisteredTool:
    async def handler(arguments: FindFilesArguments, context: ToolCallContext):
        try:
            result = await asyncio.to_thread(
                files.find_files,
                arguments.path,
                pattern=arguments.pattern,
                max_results=arguments.max_results,
                result_limit=context.result_limit,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def resolve(arguments: FindFilesArguments, _: ToolCallContext) -> OperationIntent:
        return _intent(arguments.path, files, directory=True)

    return make_tool(
        name="find_files",
        description="按文件名或 glob 在工作空间内发现文件；结果稳定排序且有界。",
        arguments_model=FindFilesArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )


def make_search_text_tool(search: WorkspaceSearchService) -> RegisteredTool:
    async def handler(arguments: SearchTextArguments, context: ToolCallContext):
        query = SearchQuery(
            pattern=arguments.pattern,
            literal=arguments.literal,
            case=arguments.case,
            glob=arguments.glob,
            context_lines=arguments.context_lines,
            max_results=arguments.max_results,
        )
        try:
            result = await asyncio.to_thread(
                search.search_text,
                arguments.path,
                query=query,
                result_limit=context.result_limit,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def resolve(arguments: SearchTextArguments, _: ToolCallContext) -> OperationIntent:
        return _intent(arguments.path, search.files, directory=True)

    return make_tool(
        name="search_text",
        description="在工作空间内按字面量或正则搜索 UTF-8 文本；结果含匹配行、引擎和有界上下文。",
        arguments_model=SearchTextArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )


def make_read_search_tools(
    files: WorkspaceFileService, search: WorkspaceSearchService
) -> tuple[RegisteredTool, ...]:
    return (
        make_list_directory_tool(files),
        make_read_file_tool(files),
        make_find_files_tool(files),
        make_search_text_tool(search),
    )


COMMAND_PREVIEW_BUDGET = ApprovalPreviewBudget(
    max_lines=8,
    max_line_chars=200,
    max_bytes=1600,
)


def make_run_command_tool(process: ProcessExecutionService) -> RegisteredTool:
    def resolve(arguments: RunCommandArguments, context: ToolCallContext) -> OperationIntent:
        try:
            plan = process.preflight(arguments)
        except (LocalFileError, ProcessServiceError) as exc:
            raise _tool_error(exc) from exc
        process.cache_plan(context.run.run_id, context.call_id, plan)
        return process.intent(plan)

    def preview(arguments: RunCommandArguments, context: ToolCallContext) -> tuple[str, ...]:
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
                else "非沙箱宿主进程；批准后项目代码可能以当前用户权限访问工作空间外文件或网络"
            ),
        )

    async def handler(arguments: RunCommandArguments, context: ToolCallContext):
        del arguments
        plan = process.cached_plan(context.run.run_id, context.call_id)
        if plan is None:
            raise ToolExecutionError(ToolErrorCode.PREFLIGHT_FAILED, "命令预检不存在")
        try:
            result, fact = await process.execute(
                plan,
                result_limit=context.result_limit,
                run=context.run,
                call_id=context.call_id,
                tool_name=context.tool_name,
                ordinal=context.ordinal,
                approval_verdict=context.approval_verdict,
            )
        except ProcessServiceError as exc:
            raise _tool_error(exc) from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"), facts=(fact,))

    return make_tool(
        name="run_command",
        description=(
            "在工作空间相对 cwd 执行一个非交互命令。"
            "必须且只能提供 argv 或 shell 二选一：优先 argv 字符串数组，"
            '例如 {"argv":["python3","run_acceptance.py"]}；不要同时传两者，也不要省略两者。'
            "禁止安装依赖、访问网络、下载或修改 Git；不要调用 pip、uv、npm、curl 或 wget。"
            "项目校验应使用仓库内已有解释器、脚本或测试。"
        ),
        arguments_model=RunCommandArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        approval_preview_budget=COMMAND_PREVIEW_BUDGET,
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
        return OperationIntent(
            kind=OperationKind.WORKSPACE_WRITE,
            effect=ToolEffect.PERSISTENT_WRITE,
            relative_paths=tuple(change.relative_path for change in changes),
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
        results = []
        facts = []
        for change in changes:
            mode = "create" if change.operation == "created" else "replace"
            try:
                plan = await asyncio.to_thread(
                    mutation.preflight_write,
                    change.relative_path,
                    content=change.content or "",
                    mode=mode,
                    expected_sha256=change.expected_sha256,
                    run=context.run,
                )
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
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.PERSISTENT_WRITE,
            approval=ToolApproval.REQUIRED,
        ),
        approval_preview_budget=PROMOTION_PREVIEW_BUDGET,
    )


def make_git_status_tool(git: GitInspectionService) -> RegisteredTool:
    def resolve(arguments: GitStatusArguments, context: ToolCallContext) -> OperationIntent:
        del arguments, context
        return OperationIntent(kind=OperationKind.GIT_READ, relative_paths=(".",))

    async def handler(
        arguments: GitStatusArguments, context: ToolCallContext
    ) -> ToolHandlerOutcome:
        del arguments
        try:
            result = await asyncio.to_thread(git.status, result_limit=context.result_limit)
        except GitServiceError as exc:
            raise _tool_error(exc) from exc
        fact = _git_fact(
            context,
            result.repository_state.value,
            result.truncated,
            (".",),
        )
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"), facts=(fact,))

    return make_tool(
        name="git_status",
        description="读取当前工作空间仓库的分支、HEAD、暂存/未暂存/未跟踪与冲突状态；只读且有界。",
        arguments_model=GitStatusArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )


def make_git_diff_tool(git: GitInspectionService) -> RegisteredTool:
    def resolve(arguments: GitDiffArguments, context: ToolCallContext) -> OperationIntent:
        del context
        return OperationIntent(
            kind=OperationKind.GIT_READ,
            relative_paths=arguments.paths or (".",),
        )

    async def handler(arguments: GitDiffArguments, context: ToolCallContext) -> ToolHandlerOutcome:
        try:
            result = await asyncio.to_thread(
                git.diff,
                staged=arguments.staged,
                paths=arguments.paths,
                result_limit=context.result_limit,
            )
        except GitServiceError as exc:
            raise _tool_error(exc) from exc
        fact = _git_fact(
            context,
            result.repository_state.value,
            result.truncated,
            arguments.paths or (".",),
        )
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"), facts=(fact,))

    return make_tool(
        name="git_diff",
        description="读取当前工作空间仓库的有界暂存或未暂存 unified Diff；禁用外部 diff/textconv 且只读。",
        arguments_model=GitDiffArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )


def _git_fact(
    context: ToolCallContext,
    repository_state: str,
    diff_truncated: bool,
    relative_paths: tuple[str, ...],
):
    from morrow.core.capabilities import GitToolFact

    return GitToolFact(
        call_id=context.call_id,
        tool_name=context.tool_name,
        ordinal=context.ordinal,
        relative_paths=relative_paths,
        approval_verdict=context.approval_verdict,
        repository_state=repository_state,
        diff_truncated=diff_truncated,
    )


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
        relative_paths=(plan.relative_path,),
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


def make_apply_patch_tool(
    mutation: WorkspaceMutationService, changes: ChangeSetService
) -> RegisteredTool:
    def resolve(arguments: ApplyPatchArguments, context: ToolCallContext) -> OperationIntent:
        try:
            plan = mutation.preflight_patch(
                arguments.path,
                expected_sha256=arguments.expected_sha256,
                edits=arguments.edits,
                run=context.run,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return _mutation_intent(plan, mutation, context)

    def preview(arguments: ApplyPatchArguments, context: ToolCallContext) -> tuple[str, ...]:
        del arguments
        plan = mutation.cached_plan(context.run.run_id, context.call_id)
        return _mutation_preview(plan) if plan is not None else ("无法生成变更预览",)

    async def handler(arguments: ApplyPatchArguments, context: ToolCallContext):
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
        name="apply_patch",
        description="根据已读取文件的 SHA-256 和唯一精确文本编辑修改一个工作空间文件，并返回实际 Diff。",
        arguments_model=ApplyPatchArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        approval_preview_budget=MUTATION_PREVIEW_BUDGET,
    )


def make_write_file_tool(
    mutation: WorkspaceMutationService, changes: ChangeSetService
) -> RegisteredTool:
    def resolve(arguments: WriteFileArguments, context: ToolCallContext) -> OperationIntent:
        try:
            plan = mutation.preflight_write(
                arguments.path,
                content=arguments.content,
                mode=arguments.mode.value,
                expected_sha256=arguments.expected_sha256,
                run=context.run,
            )
        except LocalFileError as exc:
            raise _tool_error(exc) from exc
        return _mutation_intent(plan, mutation, context)

    def preview(arguments: WriteFileArguments, context: ToolCallContext) -> tuple[str, ...]:
        del arguments
        plan = mutation.cached_plan(context.run.run_id, context.call_id)
        return _mutation_preview(plan) if plan is not None else ("无法生成变更预览",)

    async def handler(arguments: WriteFileArguments, context: ToolCallContext):
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
        name="write_file",
        description="创建或按 SHA-256 版本检查替换一个工作空间 UTF-8 文件，并返回实际 Diff。",
        arguments_model=WriteFileArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
        context_approval_preview=preview,
        approval_preview_budget=MUTATION_PREVIEW_BUDGET,
    )


def make_show_changes_tool(changes: ChangeSetService) -> RegisteredTool:
    async def handler(arguments: ShowChangesArguments, context: ToolCallContext):
        del arguments
        try:
            result = changes.show(context.run, result_limit=context.result_limit)
        except ValueError as exc:
            raise ToolExecutionError(ToolErrorCode.OUTPUT_BUDGET, "变更结果预算不足") from exc
        return ToolHandlerOutcome(payload=result.model_dump(mode="json"))

    def resolve(_: ShowChangesArguments, __: ToolCallContext) -> OperationIntent:
        return OperationIntent(kind=OperationKind.INTERNAL_READ)

    return make_tool(
        name="show_changes",
        description="显示当前运行中已实际发布的有界 ChangeSet 和 Diff；不读取助手文字。",
        arguments_model=ShowChangesArguments,
        handler=handler,
        context_handler=handler,
        intent_resolver=resolve,
    )
