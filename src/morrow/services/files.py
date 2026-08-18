"""Workspace-confined path resolution and bounded text/file services."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from morrow.adapters.local.filesystem import FileSystemAdapter
from morrow.core.capabilities import (
    DefaultSensitiveResourcePolicy,
    SensitiveResourcePolicy,
)
from morrow.core.local_tools import (
    DirectoryEntry,
    DirectoryListingResult,
    ExactEdit,
    FileRevision,
    FindFilesResult,
    LocalFileKind,
    MutationOperation,
    MutationResult,
    MutationStatus,
    NewlineStyle,
    ProtectedPath,
    ReadFileResult,
)

MAX_RELATIVE_PATH_CHARS = 512
MAX_READ_LINES = 400
MAX_READ_TEXT_BYTES = 8 * 1024
MAX_DIRECTORY_ENTRIES = 500
MAX_DIRECTORY_DEPTH = 4
MAX_FIND_RESULTS = 1_000
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024


class LocalFileError(RuntimeError):
    """Safe, stable error for a local filesystem operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResolvedWorkspacePath:
    lexical: Path
    target: Path
    relative_path: str
    kind: Literal["file", "directory", "symlink_file", "symlink_directory", "special", "missing"]
    is_symlink: bool


@dataclass(frozen=True)
class SourceText:
    relative_path: str
    target: Path
    raw: bytes
    text: str
    revision: FileRevision
    bom: bool
    newline: NewlineStyle
    mode: int


@dataclass(frozen=True)
class MutationPlan:
    relative_path: str
    target: Path
    operation: MutationOperation
    status: MutationStatus
    before: SourceText | None
    desired_text: str
    desired_raw: bytes
    after_revision: FileRevision | None
    changed_lines: int
    changed_bytes: int
    diff: str
    diff_truncated: bool
    edit_count: int = 0
    auxiliary_paths: tuple[str, ...] = ()
    threshold_exceeded: bool = False


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class WorkspacePathResolver:
    """Resolve only existing objects whose inspected path remains inside root."""

    def __init__(self, root: Path) -> None:
        try:
            canonical = root.expanduser().absolute().resolve(strict=True)
        except OSError as exc:
            raise LocalFileError("workspace_unavailable", "工作空间不可用") from exc
        if not canonical.is_dir():
            raise LocalFileError("workspace_unavailable", "工作空间不是目录")
        self.root = canonical

    @staticmethod
    def validate_relative_path(value: str, *, allow_root: bool = True) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_RELATIVE_PATH_CHARS:
            raise LocalFileError("invalid_path", "路径必须是有界的非空相对路径")
        if "\x00" in value or "\\" in value or value.startswith("/"):
            raise LocalFileError("invalid_path", "路径格式不受支持")
        if value.startswith("~") or (len(value) >= 2 and value[1] == ":"):
            raise LocalFileError("invalid_path", "路径格式不受支持")
        if value == ".":
            if allow_root:
                return "."
            raise LocalFileError("invalid_path", "路径不能指向工作空间根目录")
        parts = value.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise LocalFileError("invalid_path", "路径不能包含空段或目录回退")
        return "/".join(parts)

    def _lexical(self, value: str, *, allow_root: bool = True) -> tuple[Path, str, tuple[str, ...]]:
        relative = self.validate_relative_path(value, allow_root=allow_root)
        if relative == ".":
            return self.root, relative, ()
        parts = tuple(relative.split("/"))
        return self.root.joinpath(*parts), relative, parts

    @staticmethod
    def _kind_for(mode: int) -> str:
        if stat.S_ISREG(mode):
            return "file"
        if stat.S_ISDIR(mode):
            return "directory"
        return "special"

    def _inside(self, target: Path) -> bool:
        try:
            target.relative_to(self.root)
        except ValueError:
            return False
        return True

    def _walk_components(self, lexical: Path, parts: tuple[str, ...]) -> os.stat_result:
        current = self.root
        for part in parts[:-1]:
            current = current / part
            try:
                entry = os.lstat(current)
            except OSError as exc:
                raise LocalFileError("not_found", "路径不存在") from exc
            if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                raise LocalFileError("invalid_path", "路径包含不可穿越的目录组件")
        try:
            return os.lstat(lexical)
        except OSError as exc:
            raise LocalFileError("not_found", "路径不存在") from exc

    def resolve_existing(self, value: str) -> ResolvedWorkspacePath:
        lexical, relative, parts = self._lexical(value)
        if not parts:
            return ResolvedWorkspacePath(
                lexical=self.root,
                target=self.root,
                relative_path=relative,
                kind="directory",
                is_symlink=False,
            )
        entry = self._walk_components(lexical, parts)
        is_symlink = stat.S_ISLNK(entry.st_mode)
        if is_symlink:
            try:
                target = lexical.resolve(strict=True)
                target_stat = os.stat(target)
            except OSError as exc:
                raise LocalFileError("invalid_path", "符号链接目标不可用") from exc
            if not self._inside(target):
                raise LocalFileError("outside_workspace", "目标不在当前工作空间内")
            target_kind = self._kind_for(target_stat.st_mode)
            if target_kind == "directory":
                kind = "symlink_directory"
            elif target_kind == "file":
                kind = "symlink_file"
            else:
                kind = "special"
            return ResolvedWorkspacePath(lexical, target, relative, kind, True)
        kind = self._kind_for(entry.st_mode)
        return ResolvedWorkspacePath(lexical, lexical, relative, kind, False)

    def resolve_file(self, value: str) -> ResolvedWorkspacePath:
        resolved = self.resolve_existing(value)
        if resolved.kind not in {"file", "symlink_file"}:
            raise LocalFileError("invalid_target", "目标不是普通文件")
        return resolved

    def resolve_directory(self, value: str) -> ResolvedWorkspacePath:
        resolved = self.resolve_existing(value)
        if resolved.kind != "directory":
            raise LocalFileError("invalid_target", "目标不是普通目录")
        return resolved

    def resolve_mutation(self, value: str) -> ResolvedWorkspacePath:
        lexical, relative, parts = self._lexical(value, allow_root=False)
        current = self.root
        for index, part in enumerate(parts):
            current = current / part
            try:
                entry = os.lstat(current)
            except FileNotFoundError:
                return ResolvedWorkspacePath(
                    lexical=lexical,
                    target=lexical,
                    relative_path=relative,
                    kind="missing",
                    is_symlink=False,
                )
            except OSError as exc:
                raise LocalFileError("path_unavailable", "路径不可用") from exc
            if stat.S_ISLNK(entry.st_mode):
                raise LocalFileError("symlink_not_allowed", "写入路径不能包含符号链接")
            if index < len(parts) - 1 and not stat.S_ISDIR(entry.st_mode):
                raise LocalFileError("invalid_path", "路径包含不可穿越的目录组件")
        return ResolvedWorkspacePath(
            lexical=lexical,
            target=lexical,
            relative_path=relative,
            kind=self._kind_for(entry.st_mode),
            is_symlink=False,
        )


class WorkspaceFileService:
    """Read/list/find operations over a frozen WorkspacePathResolver."""

    def __init__(
        self,
        resolver: WorkspacePathResolver,
        *,
        filesystem: FileSystemAdapter | None = None,
        sensitive_policy: SensitiveResourcePolicy | None = None,
    ) -> None:
        self.resolver = resolver
        self.filesystem = filesystem or FileSystemAdapter()
        self.sensitive_policy = sensitive_policy or DefaultSensitiveResourcePolicy()

    def preflight_file(self, path: str) -> ResolvedWorkspacePath:
        return self.resolver.resolve_file(path)

    def preflight_directory(self, path: str) -> ResolvedWorkspacePath:
        return self.resolver.resolve_directory(path)

    def is_protected_resolved(self, relative: str, target: Path | None = None) -> bool:
        """Apply path policy to both the visible alias and its confined resolved target."""

        if self.sensitive_policy.is_protected_path(relative):
            return True
        if target is None:
            return False
        try:
            target_relative = target.relative_to(self.resolver.root).as_posix()
        except ValueError:
            return True
        return self.sensitive_policy.is_protected_path(target_relative)

    def read_source_text(self, path: str) -> SourceText:
        resolved = self.resolver.resolve_file(path)
        relative = resolved.relative_path
        if self.is_protected_resolved(relative, resolved.target):
            raise LocalFileError("protected_resource", "资源受到本地内容策略保护")
        raw = self.filesystem.read_bytes(resolved.target, max_bytes=MAX_SOURCE_FILE_BYTES)
        if self.sensitive_policy.is_protected_content(raw):
            raise LocalFileError("protected_resource", "资源受到本地内容策略保护")
        revision = self._revision(resolved.target, raw)
        bom = raw.startswith(b"\xef\xbb\xbf")
        content = raw[3:] if bom else raw
        if b"\x00" in content:
            raise LocalFileError("binary_file", "文件不是可读取的 UTF-8 文本")
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise LocalFileError("invalid_utf8", "文件不是可读取的 UTF-8 文本") from exc
        try:
            mode = resolved.target.stat().st_mode
        except OSError as exc:
            raise LocalFileError("path_unavailable", "文件元数据不可用") from exc
        return SourceText(
            relative_path=relative,
            target=resolved.target,
            raw=raw,
            text=text,
            revision=revision,
            bom=bom,
            newline=_newline_style(text),
            mode=mode,
        )

    def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        line_count: int = MAX_READ_LINES,
        result_limit: int = MAX_RESULT_BYTES,
    ) -> ReadFileResult:
        if start_line < 1 or line_count < 1 or line_count > MAX_READ_LINES:
            raise LocalFileError("invalid_range", "读取行范围超出限制")
        resolved = self.resolver.resolve_file(path)
        relative = resolved.relative_path
        if self.is_protected_resolved(relative, resolved.target):
            return self._protected_read_result(relative, start_line, result_limit)
        try:
            source = self.read_source_text(path)
        except LocalFileError as exc:
            if exc.code == "protected_resource":
                return self._protected_read_result(relative, start_line, result_limit)
            raise
        raw = source.raw
        text = source.text
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        selected = lines[start_line - 1 : start_line - 1 + line_count]
        selected, truncated = _fit_lines(selected, max_bytes=MAX_READ_TEXT_BYTES)
        end_line = start_line + len(selected) - 1 if selected else start_line - 1
        if end_line < total_lines:
            truncated = True
        next_start = end_line + 1 if truncated and end_line >= start_line else None
        payload = ReadFileResult(
            path=relative,
            text="".join(selected),
            start_line=start_line,
            end_line=end_line,
            total_lines=total_lines,
            original_bytes=len(raw),
            original_lines=total_lines,
            revision=source.revision,
            bom=source.bom,
            newline=source.newline,
            truncated=truncated,
            next_start_line=next_start,
        )
        return self._fit_read_result(payload, result_limit)

    def list_directory(
        self,
        path: str = ".",
        *,
        depth: int = 1,
        max_entries: int = MAX_DIRECTORY_ENTRIES,
        result_limit: int = MAX_RESULT_BYTES,
    ) -> DirectoryListingResult:
        if depth < 1 or depth > MAX_DIRECTORY_DEPTH:
            raise LocalFileError("invalid_depth", "目录深度超出限制")
        if max_entries < 1 or max_entries > MAX_DIRECTORY_ENTRIES:
            raise LocalFileError("invalid_limit", "目录条目数超出限制")
        resolved = self.resolver.resolve_directory(path)
        root_relative = resolved.relative_path
        entries: list[DirectoryEntry] = []
        protected: list[ProtectedPath] = []
        queue: list[tuple[Path, str, int]] = [(resolved.target, root_relative, 0)]
        truncated = False
        while queue:
            directory, directory_relative, level = queue.pop(0)
            for item in self.filesystem.iter_directory(directory):
                relative = _join_relative(directory_relative, item.name)
                resolved_target = None
                if item.kind is LocalFileKind.SYMLINK:
                    try:
                        resolved_target = item.path.resolve(strict=True)
                    except OSError:
                        pass
                if self.is_protected_resolved(relative, resolved_target):
                    protected.append(ProtectedPath(path=relative))
                    entry = DirectoryEntry(
                        path=relative,
                        kind=item.kind,
                        size=item.size,
                        protected=True,
                    )
                else:
                    entry = DirectoryEntry(path=relative, kind=item.kind, size=item.size)
                entries.append(entry)
                if len(entries) >= max_entries:
                    truncated = True
                    break
                if (
                    level + 1 < depth
                    and item.kind is LocalFileKind.DIRECTORY
                    and item.name != ".git"
                ):
                    queue.append((item.path, relative, level + 1))
            if truncated:
                break
        entries.sort(key=lambda entry: (entry.path.casefold(), entry.path))
        protected.sort(key=lambda item: (item.path.casefold(), item.path))
        result = DirectoryListingResult(
            path=root_relative,
            entries=tuple(entries),
            depth=depth,
            truncated=truncated,
            protected_paths=tuple(protected),
        )
        return self._fit_listing_result(result, result_limit)

    def find_files(
        self,
        path: str,
        *,
        pattern: str,
        max_results: int = MAX_FIND_RESULTS,
        result_limit: int = MAX_RESULT_BYTES,
    ) -> FindFilesResult:
        from fnmatch import fnmatchcase

        if (
            not pattern
            or len(pattern) > 128
            or "\x00" in pattern
            or "\\" in pattern
            or pattern.startswith("/")
            or pattern.startswith("~")
            or any(part == ".." for part in pattern.split("/"))
        ):
            raise LocalFileError("invalid_pattern", "文件匹配模式不受支持")
        if max_results < 1 or max_results > MAX_FIND_RESULTS:
            raise LocalFileError("invalid_limit", "搜索结果数超出限制")
        resolved = self.resolver.resolve_directory(path)
        found: list[str] = []
        protected: list[ProtectedPath] = []
        stack = [(resolved.target, resolved.relative_path)]
        truncated = False
        while stack:
            directory, directory_relative = stack.pop()
            for item in self.filesystem.iter_directory(directory):
                relative = _join_relative(directory_relative, item.name)
                if item.kind is LocalFileKind.DIRECTORY:
                    if item.name != ".git":
                        stack.append((item.path, relative))
                    continue
                if item.kind is not LocalFileKind.FILE and item.kind is not LocalFileKind.SYMLINK:
                    continue
                if item.kind is LocalFileKind.SYMLINK:
                    try:
                        target = item.path.resolve(strict=True)
                        if not target.is_relative_to(self.resolver.root) or not target.is_file():
                            continue
                    except OSError:
                        continue
                else:
                    target = item.path
                if self.is_protected_resolved(relative, target):
                    if fnmatchcase(item.name, pattern) or fnmatchcase(relative, pattern):
                        protected.append(ProtectedPath(path=relative))
                    continue
                if fnmatchcase(item.name, pattern) or fnmatchcase(relative, pattern):
                    found.append(relative)
                    if len(found) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        found.sort(key=lambda item: (item.casefold(), item))
        protected.sort(key=lambda item: (item.path.casefold(), item.path))
        result = FindFilesResult(
            path=resolved.relative_path,
            pattern=pattern,
            paths=tuple(found),
            truncated=truncated,
            protected_paths=tuple(protected),
        )
        return self._fit_find_result(result, result_limit)

    def _revision(self, path: Path, raw: bytes) -> FileRevision:
        try:
            metadata = path.stat()
        except OSError as exc:
            raise LocalFileError("path_unavailable", "文件元数据不可用") from exc
        if metadata.st_size > MAX_SOURCE_FILE_BYTES:
            raise LocalFileError("file_too_large", "文件超过读取上限")
        return FileRevision(
            sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), mtime_ns=metadata.st_mtime_ns
        )

    def _protected_read_result(self, relative: str, start_line: int, result_limit: int):
        marker = ReadFileResult(
            path=relative,
            text="",
            start_line=start_line,
            end_line=start_line - 1,
            total_lines=0,
            original_bytes=0,
            original_lines=0,
            revision=None,
            truncated=False,
            protected=True,
        )
        if _json_size(marker.model_dump(mode="json")) > result_limit:
            raise LocalFileError("output_budget", "受保护资源标记无法放入当前结果预算")
        return marker

    @staticmethod
    def _fit_read_result(result: ReadFileResult, result_limit: int) -> ReadFileResult:
        if _json_size(result.model_dump(mode="json")) <= result_limit:
            return result
        selected = result.text.splitlines(keepends=True)
        while (
            selected
            and _json_size(
                result.model_copy(update={"text": "".join(selected)}).model_dump(mode="json")
            )
            > result_limit
        ):
            selected.pop()
        end_line = result.start_line + len(selected) - 1 if selected else result.start_line - 1
        updated = result.model_copy(
            update={
                "text": "".join(selected),
                "end_line": end_line,
                "truncated": True,
                "next_start_line": end_line + 1
                if end_line >= result.start_line
                else result.start_line,
            }
        )
        if _json_size(updated.model_dump(mode="json")) > result_limit:
            raise LocalFileError("output_budget", "读取结果无法放入当前预算")
        return updated

    @staticmethod
    def _fit_listing_result(
        result: DirectoryListingResult, result_limit: int
    ) -> DirectoryListingResult:
        entries = list(result.entries)
        protected = list(result.protected_paths)
        while (
            _json_size(
                result.model_copy(
                    update={"entries": tuple(entries), "protected_paths": tuple(protected)}
                ).model_dump(mode="json")
            )
            > result_limit
        ):
            if entries:
                entries.pop()
            elif protected:
                protected.pop()
            else:
                break
        updated = result.model_copy(
            update={
                "entries": tuple(entries),
                "protected_paths": tuple(protected),
                "truncated": result.truncated
                or len(entries) < len(result.entries)
                or len(protected) < len(result.protected_paths),
            }
        )
        if _json_size(updated.model_dump(mode="json")) > result_limit:
            raise LocalFileError("output_budget", "目录结果无法放入当前预算")
        return updated

    @staticmethod
    def _fit_find_result(result: FindFilesResult, result_limit: int) -> FindFilesResult:
        paths = list(result.paths)
        protected = list(result.protected_paths)
        while (
            _json_size(
                result.model_copy(
                    update={"paths": tuple(paths), "protected_paths": tuple(protected)}
                ).model_dump(mode="json")
            )
            > result_limit
        ):
            if paths:
                paths.pop()
            elif protected:
                protected.pop()
            else:
                break
        updated = result.model_copy(
            update={
                "paths": tuple(paths),
                "protected_paths": tuple(protected),
                "truncated": result.truncated
                or len(paths) < len(result.paths)
                or len(protected) < len(result.protected_paths),
            }
        )
        if _json_size(updated.model_dump(mode="json")) > result_limit:
            raise LocalFileError("output_budget", "文件发现结果无法放入当前预算")
        return updated


class WorkspaceMutationService:
    """Exact, revision-checked mutations over a WorkspaceFileService."""

    def __init__(self, files: WorkspaceFileService) -> None:
        self.files = files
        self._previews: dict[tuple[str, str], MutationPlan] = {}

    def preflight_patch(
        self,
        path: str,
        *,
        expected_sha256: str,
        edits: tuple[ExactEdit, ...],
        run=None,
    ) -> MutationPlan:
        target, auxiliary = self._resolve_target(path, allow_missing=False)
        source = self.files.read_source_text(path)
        if source.newline is NewlineStyle.MIXED:
            raise LocalFileError("unsupported_newline", "混合换行文件暂不支持安全修改")
        self._check_expected(source.revision, expected_sha256)
        desired = _apply_exact_edits(source.text, edits)
        return self._plan(
            source=source,
            target=target,
            desired=desired,
            operation=MutationOperation.PATCH,
            edit_count=len(edits),
            auxiliary=auxiliary,
            run=run,
        )

    def preflight_write(
        self,
        path: str,
        *,
        content: str,
        mode: str,
        expected_sha256: str | None = None,
        run=None,
    ) -> MutationPlan:
        if "\x00" in content:
            raise LocalFileError("binary_file", "写入内容不能包含 NUL")
        target, auxiliary = self._resolve_target(path, allow_missing=mode == "create")
        if mode == "create":
            if target.exists() or target.is_symlink():
                raise LocalFileError("conflict", "目标文件已经存在")
            if self.files.sensitive_policy.is_protected_path(
                target.relative_to(self.files.resolver.root).as_posix()
            ):
                raise LocalFileError("protected_resource", "资源受到本地内容策略保护")
            if self.files.sensitive_policy.is_protected_content(content.encode("utf-8")):
                raise LocalFileError("protected_resource", "写入内容受到本地内容策略保护")
            return self._plan(
                source=None,
                target=target,
                desired=content,
                operation=MutationOperation.CREATE,
                edit_count=0,
                auxiliary=auxiliary,
                run=run,
            )
        if mode != "replace":
            raise LocalFileError("invalid_mode", "写入模式不受支持")
        source = self.files.read_source_text(path)
        if source.newline is NewlineStyle.MIXED:
            raise LocalFileError("unsupported_newline", "混合换行文件暂不支持安全修改")
        if expected_sha256 is None:
            raise LocalFileError("conflict", "replace 必须提供 expected_sha256")
        self._check_expected(source.revision, expected_sha256)
        desired = _normalize_newlines(content, source.newline)
        return self._plan(
            source=source,
            target=target,
            desired=desired,
            operation=MutationOperation.REPLACE,
            edit_count=1,
            auxiliary=auxiliary,
            run=run,
        )

    def cache_plan(self, run_id: str, call_id: str, plan: MutationPlan) -> None:
        self._previews[(run_id, call_id)] = plan

    def cached_plan(self, run_id: str, call_id: str) -> MutationPlan | None:
        return self._previews.get((run_id, call_id))

    def apply(
        self,
        plan: MutationPlan,
        *,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
        run,
    ) -> tuple[MutationResult, object]:
        from morrow.core.capabilities import ChangeToolFact

        with self.files.filesystem.target_lock(plan.target):
            current = self._revalidate(plan)
            if current.status is MutationStatus.UNCHANGED:
                result = self._result(
                    plan, current, change_set_id=_change_set_id(run, call_id, plan)
                )
            else:
                created_paths: list[Path] = []
                try:
                    if plan.operation is MutationOperation.CREATE:
                        created_paths = self._create_parents(plan.auxiliary_paths)
                    self._revalidate(plan)
                    mode = stat.S_IMODE(plan.before.mode) if plan.before is not None else 0o644
                    self.files.filesystem.atomic_write(
                        plan.target,
                        plan.desired_raw,
                        mode=mode,
                        workspace_root=self.files.resolver.root,
                    )
                    after_raw = self.files.filesystem.read_bytes(
                        plan.target, max_bytes=MAX_SOURCE_FILE_BYTES
                    )
                    if self.files.sensitive_policy.is_protected_content(after_raw):
                        raise LocalFileError(
                            "protected_resource", "发布后的内容受到本地内容策略保护"
                        )
                    after_revision = self.files._revision(plan.target, after_raw)
                    result = self._result(
                        plan,
                        plan,
                        change_set_id=_change_set_id(run, call_id, plan),
                        after_revision=after_revision,
                    )
                except LocalFileError:
                    self._cleanup_parents(created_paths)
                    raise
                except Exception as exc:
                    self._cleanup_parents(created_paths)
                    raise LocalFileError("publish_failed", "文件发布失败") from exc
            fact = ChangeToolFact(
                call_id=call_id,
                tool_name=tool_name,
                ordinal=ordinal,
                relative_paths=(plan.relative_path, *plan.auxiliary_paths),
                approval_verdict=approval_verdict,
                operation=plan.operation.value,
                before_revision=(plan.before.revision.sha256 if plan.before is not None else None),
                after_revision=(
                    result.after_revision.sha256 if result.after_revision is not None else None
                ),
                edit_count=plan.edit_count,
                changed_lines=result.changed_lines,
                changed_bytes=result.changed_bytes,
                diff_truncated=result.diff_truncated,
                change_set_id=result.change_set_id,
            )
            return result, fact

    def _resolve_target(self, path: str, *, allow_missing: bool) -> tuple[Path, tuple[str, ...]]:
        relative = self.files.resolver.validate_relative_path(path, allow_root=False)
        parts = tuple(relative.split("/"))
        target = self.files.resolver.root.joinpath(*parts)
        current = self.files.resolver.root
        missing_parent_indices: list[int] = []
        for index, part in enumerate(parts[:-1]):
            current = current / part
            try:
                entry = os.lstat(current)
            except FileNotFoundError:
                missing_parent_indices.append(index)
                continue
            except OSError as exc:
                raise LocalFileError("path_unavailable", "路径不可用") from exc
            if stat.S_ISLNK(entry.st_mode):
                raise LocalFileError("symlink_not_allowed", "写入路径不能包含符号链接")
            if not stat.S_ISDIR(entry.st_mode):
                raise LocalFileError("invalid_path", "路径包含不可穿越的目录组件")
        try:
            final = os.lstat(target)
        except FileNotFoundError:
            final = None
        except OSError as exc:
            raise LocalFileError("path_unavailable", "目标路径不可用") from exc
        if final is None:
            if not allow_missing:
                raise LocalFileError("not_found", "目标文件不存在")
            if len(missing_parent_indices) > 4:
                raise LocalFileError("mutation_limit", "创建父目录层级超过限制")
            auxiliary = tuple("/".join(parts[: index + 1]) for index in missing_parent_indices)
            return target, auxiliary
        if stat.S_ISLNK(final.st_mode):
            raise LocalFileError("symlink_not_allowed", "写入目标不能是符号链接")
        if stat.S_ISDIR(final.st_mode) or not stat.S_ISREG(final.st_mode):
            raise LocalFileError("invalid_target", "写入目标不是普通文件")
        return target, ()

    def _create_parents(self, paths: tuple[str, ...]) -> list[Path]:
        created: list[Path] = []
        for relative in paths:
            path = self.files.resolver.root / Path(*relative.split("/"))
            try:
                entry = os.lstat(path)
            except FileNotFoundError:
                path.mkdir()
                created.append(path)
                continue
            except OSError as exc:
                raise LocalFileError("path_unavailable", "父目录不可用") from exc
            if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                raise LocalFileError("symlink_not_allowed", "父目录不能包含符号链接")
        return created

    @staticmethod
    def _cleanup_parents(paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                path.rmdir()
            except OSError:
                pass

    def _revalidate(self, plan: MutationPlan) -> MutationPlan:
        self._revalidate_parent_chain(
            plan.target, allow_missing=plan.operation is MutationOperation.CREATE
        )
        try:
            current = os.lstat(plan.target)
        except FileNotFoundError:
            current = None
        except OSError as exc:
            raise LocalFileError("path_unavailable", "目标路径不可用") from exc
        if plan.operation is MutationOperation.CREATE:
            if current is not None:
                raise LocalFileError("conflict", "目标文件已经存在")
            return plan
        if current is None or stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise LocalFileError("conflict", "目标文件已发生变化")
        source = self.files.read_source_text(plan.relative_path)
        if plan.before is None or source.revision.sha256 != plan.before.revision.sha256:
            raise LocalFileError("conflict", "目标文件已发生变化")
        if source.text == plan.desired_text:
            return MutationPlan(**{**plan.__dict__, "status": MutationStatus.UNCHANGED})
        return plan

    def _revalidate_parent_chain(self, target: Path, *, allow_missing: bool = False) -> None:
        current = target.parent
        parents: list[Path] = []
        while current != self.files.resolver.root:
            parents.append(current)
            if current.parent == current:
                raise LocalFileError("outside_workspace", "父目录不在当前工作空间内")
            current = current.parent
        for parent in reversed(parents):
            try:
                metadata = os.lstat(parent)
            except FileNotFoundError:
                if allow_missing:
                    continue
                raise LocalFileError("conflict", "父目录已发生变化") from None
            except OSError as exc:
                raise LocalFileError("conflict", "父目录已发生变化") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise LocalFileError("conflict", "父目录已发生变化")

    def _plan(
        self,
        *,
        source: SourceText | None,
        target: Path,
        desired: str,
        operation: MutationOperation,
        edit_count: int,
        auxiliary: tuple[str, ...],
        run,
    ) -> MutationPlan:
        if self.files.sensitive_policy.is_protected_content(desired.encode("utf-8")):
            raise LocalFileError("protected_resource", "写入内容受到本地内容策略保护")
        before_text = source.text if source is not None else ""
        newline = source.newline if source is not None else NewlineStyle.LF
        bom = source.bom if source is not None else False
        desired = _normalize_newlines(desired, newline)
        raw = (b"\xef\xbb\xbf" if bom else b"") + _encode_newline(desired, newline)
        changed_lines, changed_bytes = _change_stats(before_text, desired)
        status = (
            MutationStatus.UNCHANGED
            if before_text == desired and source is not None
            else (MutationStatus.CREATED if source is None else MutationStatus.MODIFIED)
        )
        diff, diff_truncated = _bounded_diff(
            before_text,
            desired,
            self.files.resolver.validate_relative_path(
                target.relative_to(self.files.resolver.root).as_posix(), allow_root=False
            ),
        )
        threshold = _threshold_exceeded(
            operation=operation,
            source=source,
            desired=desired,
            edit_count=edit_count,
            changed_lines=changed_lines,
            changed_bytes=changed_bytes,
            run=run,
            relative_path=target.relative_to(self.files.resolver.root).as_posix(),
        )
        return MutationPlan(
            relative_path=target.relative_to(self.files.resolver.root).as_posix(),
            target=target,
            operation=operation,
            status=status,
            before=source,
            desired_text=desired,
            desired_raw=raw,
            after_revision=source.revision
            if status is MutationStatus.UNCHANGED and source
            else None,
            changed_lines=changed_lines,
            changed_bytes=changed_bytes,
            diff=diff,
            diff_truncated=diff_truncated,
            edit_count=edit_count,
            auxiliary_paths=auxiliary,
            threshold_exceeded=threshold,
        )

    @staticmethod
    def _check_expected(revision: FileRevision, expected: str) -> None:
        if revision.sha256 != expected:
            raise LocalFileError("conflict", "目标文件已发生变化")

    @staticmethod
    def _result(
        plan: MutationPlan,
        current: MutationPlan,
        *,
        change_set_id: str,
        after_revision: FileRevision | None = None,
    ) -> MutationResult:
        return MutationResult(
            path=plan.relative_path,
            operation=plan.operation,
            status=current.status,
            before_revision=plan.before.revision if plan.before is not None else None,
            after_revision=after_revision or current.after_revision or plan.after_revision,
            changed_lines=plan.changed_lines,
            changed_bytes=plan.changed_bytes,
            diff=plan.diff,
            diff_truncated=plan.diff_truncated,
            change_set_id=change_set_id,
            auxiliary_paths=plan.auxiliary_paths,
        )


def _parent_paths(existing_parent: Path, missing: list[str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    current = existing_parent
    for part in reversed(missing[:-1]):
        current = current / part
        paths.append(current)
    return tuple(reversed(paths))


def _relative_path(root: Path, path: Path) -> str:
    value = path.relative_to(root).as_posix()
    return value or "."


def _normalize_newlines(value: str, style: NewlineStyle) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized


def _encode_newline(value: str, style: NewlineStyle) -> bytes:
    if style is NewlineStyle.CRLF:
        value = value.replace("\n", "\r\n")
    elif style is NewlineStyle.CR:
        value = value.replace("\n", "\r")
    return value.encode("utf-8")


def _apply_exact_edits(text: str, edits: tuple[ExactEdit, ...]) -> str:
    replacements: list[tuple[int, int, str]] = []
    normalized = _normalize_newlines(text, NewlineStyle.LF)
    for edit in edits:
        old = _normalize_newlines(edit.old_text, NewlineStyle.LF)
        new = _normalize_newlines(edit.new_text, NewlineStyle.LF)
        positions: list[int] = []
        start = 0
        while True:
            found = normalized.find(old, start)
            if found < 0:
                break
            positions.append(found)
            start = found + len(old)
        if not positions:
            raise LocalFileError("edit_not_found", "精确编辑目标不存在")
        if len(positions) != 1:
            raise LocalFileError("edit_not_unique", "精确编辑目标不是唯一匹配")
        replacements.append((positions[0], positions[0] + len(old), new))
    replacements.sort()
    for previous, current in zip(replacements, replacements[1:], strict=False):
        if current[0] < previous[1]:
            raise LocalFileError("edit_overlap", "精确编辑目标发生重叠")
    for start, end, new in reversed(replacements):
        normalized = normalized[:start] + new + normalized[end:]
    return normalized


def _change_stats(before: str, after: str) -> tuple[int, int]:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    changed_lines = 0
    changed_bytes = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, before_lines, after_lines
    ).get_opcodes():
        if tag != "equal":
            changed_lines += (i2 - i1) + (j2 - j1)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes():
        if tag != "equal":
            changed_bytes += len(before[i1:i2].encode("utf-8"))
            changed_bytes += len(after[j1:j2].encode("utf-8"))
    return changed_lines, changed_bytes


def _bounded_diff(before: str, after: str, relative: str) -> tuple[str, bool]:
    full = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="\n",
        )
    )
    encoded = full.encode("utf-8")
    if len(encoded) <= 4 * 1024:
        return full, False
    marker = "\n... diff truncated ..."
    prefix_limit = max(0, 4 * 1024 - len(marker.encode("utf-8")))
    bounded = encoded[:prefix_limit].decode("utf-8", errors="ignore")
    return bounded + marker, True


def _threshold_exceeded(
    *,
    operation: MutationOperation,
    source: SourceText | None,
    desired: str,
    edit_count: int,
    changed_lines: int,
    changed_bytes: int,
    run,
    relative_path: str,
) -> bool:
    if source is None:
        per_call = len(desired.splitlines()) > 64 or len(desired.encode("utf-8")) > 4 * 1024
    else:
        before_lines = source.text.splitlines(keepends=True)
        after_lines = desired.splitlines(keepends=True)
        changed_non_empty = 0
        for tag, i1, i2, _, _ in difflib.SequenceMatcher(
            None, before_lines, after_lines
        ).get_opcodes():
            if tag != "equal":
                changed_non_empty += sum(1 for line in before_lines[i1:i2] if line.strip())
        non_empty = [line for line in before_lines if line.strip()]
        changed_ratio_exceeded = bool(non_empty) and changed_non_empty > len(non_empty) / 4
        per_call = (
            operation is MutationOperation.REPLACE
            or edit_count > 8
            or changed_lines > 64
            or changed_bytes > 4 * 1024
            or changed_ratio_exceeded
        )
    prior_paths: set[str] = set()
    prior_edits = 0
    prior_lines = 0
    prior_bytes = 0
    if run is not None:
        from morrow.core.capabilities import ChangeToolFact

        for fact in run.facts:
            if isinstance(fact, ChangeToolFact):
                if fact.relative_paths:
                    prior_paths.add(fact.relative_paths[0])
                prior_edits += fact.edit_count
                prior_lines += fact.changed_lines
                prior_bytes += fact.changed_bytes
    return (
        per_call
        or len(prior_paths | {relative_path}) > 4
        or prior_edits + edit_count > 16
        or prior_lines + changed_lines > 128
        or prior_bytes + changed_bytes > 8 * 1024
    )


def _change_set_id(run, call_id: str, plan: MutationPlan) -> str:
    seed = f"{getattr(run, 'run_id', 'run')}:{call_id}:{plan.relative_path}".encode()
    return "cs_" + hashlib.sha256(seed).hexdigest()[:24]


def _join_relative(directory: str, name: str) -> str:
    return name if directory == "." else f"{directory}/{name}"


def _fit_lines(lines: list[str], *, max_bytes: int) -> tuple[list[str], bool]:
    selected: list[str] = []
    total = 0
    truncated = False
    for line in lines:
        size = len(line.encode("utf-8"))
        if total + size > max_bytes:
            truncated = True
            break
        selected.append(line)
        total += size
    return selected, truncated


def _newline_style(text: str) -> NewlineStyle:
    styles: set[str] = set()
    index = 0
    while index < len(text):
        if text[index] == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                styles.add("crlf")
                index += 2
            else:
                styles.add("cr")
                index += 1
        elif text[index] == "\n":
            styles.add("lf")
            index += 1
        else:
            index += 1
    if not styles:
        return NewlineStyle.NONE
    if len(styles) > 1:
        return NewlineStyle.MIXED
    return NewlineStyle(next(iter(styles)))
