"""Workspace-confined path resolution and bounded text/file services."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from morrow.adapters.local.filesystem import (
    CAPTURE_NAME_PREFIX,
    ConfinedFileState,
    FileSystemAdapter,
    FileSystemMutationError,
)
from morrow.core.local_tools import (
    WORKSPACE_RELATIVE_PATH_MAX_CHARS,
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
    ReadFileResult,
    validate_workspace_relative_path,
)

MAX_RELATIVE_PATH_CHARS = WORKSPACE_RELATIVE_PATH_MAX_CHARS
LEGACY_MAX_READ_LINES = 400
MAX_READ_LINES = 2_000
MAX_READ_TEXT_BYTES = 8 * 1024
MAX_DIRECTORY_ENTRIES = 500
MAX_DIRECTORY_DEPTH = 4
MAX_FIND_RESULTS = 1_000
MAX_SOURCE_FILE_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024


class LocalFileError(RuntimeError):
    """Safe, stable error for a local filesystem operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        facts: tuple[object, ...] = (),
        change_result: MutationResult | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.facts = tuple(facts)
        self.change_result = change_result


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
    is_text: bool = True


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
    source_target: Path | None = None
    destination_target: Path | None = None
    destination_relative_path: str | None = None
    staging_target: Path | None = None
    staging_relative_path: str | None = None

    @property
    def relative_paths(self) -> tuple[str, ...]:
        if self.destination_relative_path is not None:
            return (self.relative_path, self.destination_relative_path)
        return (self.relative_path,)

    @property
    def affected_paths(self) -> tuple[Path, ...]:
        values = [self.target]
        if self.source_target is not None:
            values.append(self.source_target)
        if self.destination_target is not None:
            values.append(self.destination_target)
        if self.staging_target is not None:
            values.append(self.staging_target)
        return tuple(values)


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
        try:
            return validate_workspace_relative_path(value, allow_root=allow_root)
        except ValueError as exc:
            raise LocalFileError("invalid_path", str(exc)) from None

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
    ) -> None:
        self.resolver = resolver
        self.filesystem = filesystem or FileSystemAdapter()

    def preflight_file(self, path: str) -> ResolvedWorkspacePath:
        return self.resolver.resolve_file(path)

    def preflight_directory(self, path: str) -> ResolvedWorkspacePath:
        return self.resolver.resolve_directory(path)

    def read_source_text(self, path: str) -> SourceText:
        resolved = self.resolver.resolve_file(path)
        relative = resolved.relative_path
        raw = self.filesystem.read_bytes(resolved.target, max_bytes=MAX_SOURCE_FILE_BYTES)
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

    def read_mutation_source(self, path: str) -> SourceText:
        """Read bounded regular-file metadata for destructive mutations.

        Delete and move do not need to decode a file, so a regular binary file remains an
        admissible source.  ``SourceText`` keeps the bounded bytes only in process memory for
        hashing and a possible safe preview; prepared durable evidence stores hashes and sizes.
        """

        resolved = self.resolver.resolve_mutation(path)
        if resolved.kind == "missing":
            raise LocalFileError("not_found", "源文件不存在")
        if resolved.kind != "file":
            raise LocalFileError("invalid_target", "源文件不是普通文件")
        relative = resolved.relative_path
        try:
            state = self.filesystem.read_confined_file(
                resolved.target,
                workspace_root=self.resolver.root,
                max_bytes=MAX_SOURCE_FILE_BYTES,
            )
        except FileSystemMutationError as exc:
            code = {
                "source_conflict": "not_found",
                "not_regular": "invalid_target",
                "file_too_large": "file_too_large",
                "unsupported_capability": "unsupported_capability",
            }.get(exc.code, "path_unavailable")
            raise LocalFileError(code, exc.message) from exc
        raw = state.raw
        bom = raw.startswith(b"\xef\xbb\xbf")
        content = raw[3:] if bom else raw
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return SourceText(
                relative_path=relative,
                target=resolved.target,
                raw=raw,
                text="",
                revision=FileRevision(
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size=len(raw),
                    mtime_ns=state.mtime_ns,
                ),
                bom=False,
                newline=NewlineStyle.NONE,
                mode=state.mode,
                is_text=False,
            )
        if b"\x00" in content:
            return SourceText(
                relative_path=relative,
                target=resolved.target,
                raw=raw,
                text="",
                revision=FileRevision(
                    sha256=hashlib.sha256(raw).hexdigest(),
                    size=len(raw),
                    mtime_ns=state.mtime_ns,
                ),
                bom=bom,
                newline=NewlineStyle.NONE,
                mode=state.mode,
                is_text=False,
            )
        return SourceText(
            relative_path=relative,
            target=resolved.target,
            raw=raw,
            text=text,
            revision=FileRevision(
                sha256=hashlib.sha256(raw).hexdigest(),
                size=len(raw),
                mtime_ns=state.mtime_ns,
            ),
            bom=bom,
            newline=_newline_style(text),
            mode=state.mode,
            is_text=True,
        )

    def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        line_count: int = LEGACY_MAX_READ_LINES,
        result_limit: int = MAX_RESULT_BYTES,
        max_bytes: int | None = None,
        max_lines: int | None = None,
        start_byte: int | None = None,
    ) -> ReadFileResult:
        if start_line < 1 or line_count < 1 or line_count > MAX_READ_LINES:
            raise LocalFileError("invalid_range", "读取行范围超出限制")
        if start_byte is not None and start_byte < 0:
            raise LocalFileError("invalid_range", "读取字节位置超出限制")
        selected_max_bytes = MAX_READ_TEXT_BYTES if max_bytes is None else max_bytes
        selected_max_lines = MAX_READ_LINES if max_lines is None else max_lines
        if (
            selected_max_bytes < 1
            or selected_max_bytes > 50 * 1024
            or selected_max_lines < 1
            or selected_max_lines > 2_000
        ):
            raise LocalFileError("invalid_limit", "读取输出限制超出边界")
        resolved = self.resolver.resolve_file(path)
        relative = resolved.relative_path
        source = self.read_source_text(path)
        raw = source.raw
        text = source.text
        text_bytes = text.encode("utf-8")
        all_lines = text.splitlines(keepends=True)
        if start_byte is not None:
            if start_byte > len(text_bytes):
                raise LocalFileError("invalid_range", "读取字节位置超出文件范围")
            try:
                window_text = text_bytes[start_byte:].decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise LocalFileError("invalid_range", "读取字节位置不是 UTF-8 字符边界") from exc
            lines = window_text.splitlines(keepends=True)
            window_start_byte = start_byte
        else:
            lines = all_lines
            window_start_byte = len("".join(lines[: start_line - 1]).encode("utf-8"))
        total_lines = len(all_lines)
        if start_byte is None:
            selected = lines[start_line - 1 : start_line - 1 + min(line_count, selected_max_lines)]
        else:
            selected = lines[: min(line_count, selected_max_lines)]
        selected, truncated, partial_line = _fit_lines(selected, max_bytes=selected_max_bytes)
        if partial_line and not selected:
            raise LocalFileError("invalid_limit", "UTF-8 读取预算不足以返回一个完整字符")
        end_line = start_line + len(selected) - 1 if selected else start_line - 1
        if start_byte is None and end_line < total_lines:
            truncated = True
        elif start_byte is not None and len(selected) < len(lines):
            truncated = True
        output = "".join(selected)
        next_start_byte = None
        if start_byte is not None:
            if truncated:
                next_start_byte = window_start_byte + len(output.encode("utf-8"))
            next_start = None
        else:
            if partial_line:
                partial_start = window_start_byte + len("".join(selected[:-1]).encode("utf-8"))
                next_start_byte = partial_start + len(selected[-1].encode("utf-8"))
            next_start = (
                end_line + 1 if truncated and not partial_line and end_line >= start_line else None
            )
        payload = ReadFileResult(
            path=relative,
            text=output,
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
            start_byte=(window_start_byte if start_byte is not None or partial_line else None),
            next_start_byte=next_start_byte,
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
        queue: list[tuple[Path, str, int]] = [(resolved.target, root_relative, 0)]
        truncated = False
        while queue:
            directory, directory_relative, level = queue.pop(0)
            for item in self.filesystem.iter_directory(directory):
                relative = _join_relative(directory_relative, item.name)
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
        result = DirectoryListingResult(
            path=root_relative,
            entries=tuple(entries),
            depth=depth,
            truncated=truncated,
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
                if fnmatchcase(item.name, pattern) or fnmatchcase(relative, pattern):
                    found.append(relative)
                    if len(found) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        found.sort(key=lambda item: (item.casefold(), item))
        result = FindFilesResult(
            path=resolved.relative_path,
            pattern=pattern,
            paths=tuple(found),
            truncated=truncated,
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

    @staticmethod
    def _fit_read_result(result: ReadFileResult, result_limit: int) -> ReadFileResult:
        if _json_size(result.model_dump(mode="json")) <= result_limit:
            return result
        if result.start_byte is not None or result.next_start_byte is not None:
            start_byte = result.start_byte or 0
            text = result.text
            while text:
                next_byte = start_byte + len(text.encode("utf-8"))
                candidate = result.model_copy(
                    update={
                        "text": text,
                        "end_line": (
                            result.start_line + len(text.splitlines(keepends=True)) - 1
                            if text
                            else result.start_line - 1
                        ),
                        "truncated": True,
                        "next_start_line": None,
                        "next_start_byte": next_byte,
                    }
                )
                if _json_size(candidate.model_dump(mode="json")) <= result_limit:
                    return candidate
                text = text[:-1]
            raise LocalFileError("output_budget", "读取结果无法放入当前预算")
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
        self._previews: dict[tuple[str, str], tuple[MutationPlan, ...]] = {}

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

    def preflight_delete(
        self,
        path: str,
        *,
        expected_sha256: str,
        run=None,
    ) -> MutationPlan:
        source = self._preflight_destructive_source(path, expected_sha256)
        return self._destructive_plan(
            source=source,
            operation=MutationOperation.DELETE,
            run=run,
        )

    def preflight_move(
        self,
        source_path: str,
        destination_path: str,
        *,
        expected_sha256: str,
        run=None,
    ) -> MutationPlan:
        source = self._preflight_destructive_source(source_path, expected_sha256)
        destination, destination_relative = self._resolve_destination(destination_path)
        if source.target == destination:
            raise LocalFileError("invalid_path", "源路径和目标路径必须不同")
        return self._destructive_plan(
            source=source,
            operation=MutationOperation.MOVE,
            destination=destination,
            destination_relative=destination_relative,
            run=run,
        )

    def preflight_rename(
        self,
        source_path: str,
        destination_path: str,
        *,
        expected_sha256: str,
        run=None,
    ) -> MutationPlan:
        source = self._preflight_destructive_source(source_path, expected_sha256)
        destination_relative = self.files.resolver.validate_relative_path(
            destination_path, allow_root=False
        )
        if Path(source.relative_path).parent != Path(destination_relative).parent:
            raise LocalFileError("invalid_path", "rename 只能在同一父目录内进行")
        destination, destination_relative = self._resolve_destination(destination_path)
        if source.target == destination:
            raise LocalFileError("invalid_path", "源路径和目标路径必须不同")
        return self._destructive_plan(
            source=source,
            operation=MutationOperation.RENAME,
            destination=destination,
            destination_relative=destination_relative,
            run=run,
        )

    def _preflight_destructive_source(self, path: str, expected_sha256: str) -> SourceText:
        source = self.files.read_mutation_source(path)
        self._check_expected(source.revision, expected_sha256)
        return source

    def _resolve_destination(self, path: str) -> tuple[Path, str]:
        relative = self.files.resolver.validate_relative_path(path, allow_root=False)
        target = self.files.resolver.root.joinpath(*relative.split("/"))
        self._revalidate_parent_chain(
            target, allow_missing=False, symlink_error_code="symlink_not_allowed"
        )
        try:
            current = os.lstat(target)
        except FileNotFoundError:
            current = None
        except OSError as exc:
            raise LocalFileError("path_unavailable", "目标路径不可用") from exc
        if current is not None:
            if stat.S_ISLNK(current.st_mode):
                raise LocalFileError("symlink_not_allowed", "目标路径不能是符号链接")
            raise LocalFileError("conflict", "目标路径已经存在")
        return target, relative

    def _new_staging_target(self, source: SourceText) -> tuple[Path, str]:
        """Reserve a bounded, workspace-relative private capture name for one plan."""

        parent_relative = source.relative_path.rpartition("/")[0]
        for _attempt in range(4):
            name = f"{CAPTURE_NAME_PREFIX}{uuid.uuid4().hex}"
            relative = f"{parent_relative}/{name}" if parent_relative else name
            if len(relative) > MAX_RELATIVE_PATH_CHARS:
                raise LocalFileError("mutation_limit", "内部捕获路径超过长度限制")
            target = source.target.parent / name
            try:
                os.lstat(target)
            except FileNotFoundError:
                return target, relative
            except OSError as exc:
                raise LocalFileError("path_unavailable", "内部捕获路径不可用") from exc
        raise LocalFileError("conflict", "无法保留私有内部捕获路径")

    def _destructive_plan(
        self,
        *,
        source: SourceText,
        operation: MutationOperation,
        destination: Path | None = None,
        destination_relative: str | None = None,
        run=None,
    ) -> MutationPlan:
        if operation is MutationOperation.DELETE:
            changed_lines, changed_bytes = (
                _change_stats(source.text, "") if source.is_text else (0, len(source.raw))
            )
            diff, diff_truncated = _structural_diff(
                f"a/{source.relative_path}", "/dev/null", source.revision.size
            )
            status = MutationStatus.DELETED
        else:
            if destination is None or destination_relative is None:
                raise ValueError("move plan requires a destination")
            changed_lines, changed_bytes = 0, 0
            diff, diff_truncated = _structural_diff(
                f"a/{source.relative_path}", f"b/{destination_relative}", source.revision.size
            )
            status = (
                MutationStatus.MOVED
                if operation is MutationOperation.MOVE
                else MutationStatus.RENAMED
            )
        staging_target, staging_relative = self._new_staging_target(source)
        return MutationPlan(
            relative_path=source.relative_path,
            target=source.target,
            operation=operation,
            status=status,
            before=source,
            desired_text="",
            desired_raw=b"",
            after_revision=None,
            changed_lines=changed_lines,
            changed_bytes=changed_bytes,
            diff=diff,
            diff_truncated=diff_truncated,
            edit_count=0,
            threshold_exceeded=True,
            source_target=source.target,
            destination_target=destination,
            destination_relative_path=destination_relative,
            staging_target=staging_target,
            staging_relative_path=staging_relative,
        )

    def cache_plan(self, run_id: str, call_id: str, plan: MutationPlan) -> None:
        self.cache_plans(run_id, call_id, (plan,))

    def cache_plans(self, run_id: str, call_id: str, plans: tuple[MutationPlan, ...]) -> None:
        if not plans:
            raise ValueError("at least one mutation plan is required")
        self._previews[(run_id, call_id)] = tuple(plans)

    def cached_plan(self, run_id: str, call_id: str) -> MutationPlan | None:
        plans = self._previews.get((run_id, call_id))
        return plans[0] if plans else None

    def cached_plans(self, run_id: str, call_id: str) -> tuple[MutationPlan, ...] | None:
        return self._previews.get((run_id, call_id))

    def discard_previews(self, run_id: str, call_id: str) -> None:
        self._previews.pop((run_id, call_id), None)

    def clear_previews(self, run_id: str | None = None) -> None:
        if run_id is None:
            self._previews.clear()
        else:
            to_delete = [key for key in self._previews if key[0] == run_id]
            for key in to_delete:
                self._previews.pop(key, None)

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
        with self.files.filesystem.paths_lock(plan.affected_paths):
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
                    if plan.operation is MutationOperation.DELETE:
                        before = plan.before
                        if before is None:
                            raise LocalFileError("conflict", "源文件证据不存在")
                        try:
                            self.files.filesystem.unlink_confined(
                                plan.target,
                                workspace_root=self.files.resolver.root,
                                expected_sha256=before.revision.sha256,
                                max_bytes=MAX_SOURCE_FILE_BYTES,
                                staging_name=(
                                    plan.staging_target.name
                                    if plan.staging_target is not None
                                    else None
                                ),
                            )
                        except FileSystemMutationError as exc:
                            if exc.effect_applied:
                                raise self._outcome_unknown(
                                    plan,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    ordinal=ordinal,
                                    approval_verdict=approval_verdict,
                                    run=run,
                                    staging_present=exc.staging_present,
                                ) from exc
                            raise _filesystem_error(exc) from exc
                        after_revision = None
                    elif plan.operation in {MutationOperation.MOVE, MutationOperation.RENAME}:
                        source = plan.source_target
                        destination = plan.destination_target
                        before = plan.before
                        if source is None or destination is None or before is None:
                            raise LocalFileError("conflict", "移动证据不完整")
                        try:
                            published = self.files.filesystem.move_no_replace(
                                source,
                                destination,
                                workspace_root=self.files.resolver.root,
                                expected_sha256=before.revision.sha256,
                                max_bytes=MAX_SOURCE_FILE_BYTES,
                                staging_name=(
                                    plan.staging_target.name
                                    if plan.staging_target is not None
                                    else None
                                ),
                            )
                        except FileSystemMutationError as exc:
                            if exc.effect_applied:
                                raise self._outcome_unknown(
                                    plan,
                                    call_id=call_id,
                                    tool_name=tool_name,
                                    ordinal=ordinal,
                                    approval_verdict=approval_verdict,
                                    run=run,
                                    staging_present=exc.staging_present,
                                ) from exc
                            raise _filesystem_error(exc) from exc
                        if not isinstance(published, ConfinedFileState):
                            published = self.files.filesystem.read_confined_file(
                                destination,
                                workspace_root=self.files.resolver.root,
                                max_bytes=MAX_SOURCE_FILE_BYTES,
                            )
                        after_raw = published.raw
                        if hashlib.sha256(after_raw).hexdigest() != before.revision.sha256:
                            raise self._outcome_unknown(
                                plan,
                                call_id=call_id,
                                tool_name=tool_name,
                                ordinal=ordinal,
                                approval_verdict=approval_verdict,
                                run=run,
                            )
                        if published.size != before.revision.size or published.mode != stat.S_IMODE(
                            before.mode
                        ):
                            raise self._outcome_unknown(
                                plan,
                                call_id=call_id,
                                tool_name=tool_name,
                                ordinal=ordinal,
                                approval_verdict=approval_verdict,
                                run=run,
                            )
                        after_revision = FileRevision(
                            sha256=hashlib.sha256(after_raw).hexdigest(),
                            size=published.size,
                            mtime_ns=published.mtime_ns,
                        )
                    else:
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
                except FileSystemMutationError as exc:
                    self._cleanup_parents(created_paths)
                    if exc.effect_applied:
                        raise self._outcome_unknown(
                            plan,
                            call_id=call_id,
                            tool_name=tool_name,
                            ordinal=ordinal,
                            approval_verdict=approval_verdict,
                            run=run,
                            staging_present=exc.staging_present,
                        ) from exc
                    raise _filesystem_error(exc) from exc
                except Exception as exc:
                    self._cleanup_parents(created_paths)
                    raise LocalFileError("publish_failed", "文件发布失败") from exc
            fact = self._fact(
                plan,
                result,
                call_id=call_id,
                tool_name=tool_name,
                ordinal=ordinal,
                approval_verdict=approval_verdict,
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
        if plan.operation is MutationOperation.DELETE:
            self._revalidate_staging(plan)
            self._revalidate_destructive_source(plan)
            return plan
        if plan.operation in {MutationOperation.MOVE, MutationOperation.RENAME}:
            self._revalidate_staging(plan)
            self._revalidate_move(plan)
            return plan
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

    def _revalidate_destructive_source(self, plan: MutationPlan) -> SourceText:
        self._revalidate_parent_chain(plan.target, allow_missing=False)
        try:
            metadata = os.lstat(plan.target)
        except FileNotFoundError as exc:
            raise LocalFileError("conflict", "源文件已发生变化") from exc
        except OSError as exc:
            raise LocalFileError("conflict", "源文件已发生变化") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise LocalFileError("conflict", "源文件已发生变化")
        try:
            source = self.files.read_mutation_source(plan.relative_path)
        except LocalFileError as exc:
            if exc.code in {"not_found", "invalid_target", "symlink_not_allowed"}:
                raise LocalFileError("conflict", "源文件已发生变化") from exc
            raise
        if plan.before is None or source.revision.sha256 != plan.before.revision.sha256:
            raise LocalFileError("conflict", "源文件已发生变化")
        return source

    def _revalidate_staging(self, plan: MutationPlan) -> None:
        target = plan.staging_target
        if target is None or plan.staging_relative_path is None:
            raise LocalFileError("conflict", "内部捕获证据不完整")
        self._revalidate_parent_chain(target, allow_missing=False)
        try:
            os.lstat(target)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise LocalFileError("conflict", "内部捕获路径无法确认") from exc
        raise LocalFileError("conflict", "内部捕获路径已经存在")

    def _revalidate_move(self, plan: MutationPlan) -> None:
        source = plan.source_target
        destination = plan.destination_target
        if source is None or destination is None:
            raise LocalFileError("conflict", "移动证据不完整")
        self._revalidate_destructive_source(plan)
        self._revalidate_parent_chain(destination, allow_missing=False)
        try:
            current = os.lstat(destination)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise LocalFileError("conflict", "目标路径已发生变化") from exc
        if stat.S_ISLNK(current.st_mode) or stat.S_ISDIR(current.st_mode):
            raise LocalFileError("conflict", "目标路径已发生变化")
        raise LocalFileError("conflict", "目标路径已经存在")

    def _revalidate_parent_chain(
        self,
        target: Path,
        *,
        allow_missing: bool = False,
        symlink_error_code: str = "conflict",
    ) -> None:
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
            if stat.S_ISLNK(metadata.st_mode):
                raise LocalFileError(symlink_error_code, "父目录不能包含符号链接")
            if not stat.S_ISDIR(metadata.st_mode):
                raise LocalFileError(
                    "invalid_path" if symlink_error_code == "symlink_not_allowed" else "conflict",
                    "父目录不是目录",
                )

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
    def _fact(
        plan: MutationPlan,
        result: MutationResult,
        *,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
    ):
        from morrow.core.capabilities import ChangeToolFact

        relative_paths = list(plan.relative_paths)
        if plan.destination_relative_path is None:
            relative_paths = [plan.relative_path, *plan.auxiliary_paths]
        for path in result.auxiliary_paths:
            if path not in relative_paths:
                relative_paths.append(path)
        return ChangeToolFact(
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            relative_paths=tuple(relative_paths),
            approval_verdict=approval_verdict,
            operation=plan.operation.value,
            status=result.status.value,
            source_path=result.source_path,
            destination_path=result.destination_path,
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

    def _outcome_unknown(
        self,
        plan: MutationPlan,
        *,
        call_id: str,
        tool_name: str,
        ordinal: int,
        approval_verdict,
        run,
        staging_present: bool = False,
    ) -> LocalFileError:
        result = self._result(
            plan,
            plan,
            status=MutationStatus.OUTCOME_UNKNOWN,
            change_set_id=_change_set_id(run, call_id, plan),
            staging_present=staging_present,
        )
        fact = self._fact(
            plan,
            result,
            call_id=call_id,
            tool_name=tool_name,
            ordinal=ordinal,
            approval_verdict=approval_verdict,
        )
        return LocalFileError(
            "outcome_unknown",
            "文件变更已执行但持久化或结果无法确认",
            facts=(fact,),
            change_result=result,
        )

    @staticmethod
    def _result(
        plan: MutationPlan,
        current: MutationPlan,
        *,
        change_set_id: str,
        after_revision: FileRevision | None = None,
        status: MutationStatus | None = None,
        staging_present: bool = False,
    ) -> MutationResult:
        auxiliary_paths = list(plan.auxiliary_paths)
        if staging_present and plan.staging_relative_path is not None:
            if plan.staging_relative_path not in auxiliary_paths:
                auxiliary_paths.append(plan.staging_relative_path)
        return MutationResult(
            path=plan.relative_path,
            operation=plan.operation,
            status=status or current.status,
            before_revision=plan.before.revision if plan.before is not None else None,
            after_revision=after_revision or current.after_revision or plan.after_revision,
            changed_lines=plan.changed_lines,
            changed_bytes=plan.changed_bytes,
            diff=plan.diff,
            diff_truncated=plan.diff_truncated,
            change_set_id=change_set_id,
            auxiliary_paths=tuple(auxiliary_paths),
            source_path=(
                plan.relative_path
                if plan.operation
                in {
                    MutationOperation.DELETE,
                    MutationOperation.MOVE,
                    MutationOperation.RENAME,
                }
                else None
            ),
            destination_path=plan.destination_relative_path,
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


def _structural_diff(source: str, destination: str, size: int) -> tuple[str, bool]:
    """Describe a path-level mutation without retaining file contents."""

    return f"--- {source}\n+++ {destination}\n@@ move/delete {size} bytes @@\n", False


def _filesystem_error(error: FileSystemMutationError) -> LocalFileError:
    mapping = {
        "destination_exists": "conflict",
        "source_conflict": "conflict",
        "not_regular": "invalid_target",
        "file_too_large": "file_too_large",
        "unsupported_capability": "unsupported_capability",
        "cross_device": "publish_failed",
        "outcome_unknown": "outcome_unknown",
    }
    return LocalFileError(mapping.get(error.code, "publish_failed"), error.message)


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
    destination = plan.destination_relative_path or ""
    seed = (
        f"{getattr(run, 'run_id', 'run')}:{call_id}:{plan.operation.value}:"
        f"{plan.relative_path}:{destination}"
    ).encode()
    return "cs_" + hashlib.sha256(seed).hexdigest()[:24]


def _join_relative(directory: str, name: str) -> str:
    return name if directory == "." else f"{directory}/{name}"


def _fit_lines(lines: list[str], *, max_bytes: int) -> tuple[list[str], bool, bool]:
    selected: list[str] = []
    total = 0
    truncated = False
    partial_line = False
    for line in lines:
        size = len(line.encode("utf-8"))
        if total + size > max_bytes:
            truncated = True
            remaining = max_bytes - total
            if remaining > 0:
                prefix = _take_utf8_prefix(line, remaining)
                if prefix:
                    selected.append(prefix)
                    partial_line = True
            break
        selected.append(line)
        total += size
    return selected, truncated, partial_line


def _take_utf8_prefix(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    used = 0
    end = 0
    for index, character in enumerate(value):
        size = len(character.encode("utf-8"))
        if used + size > max_bytes:
            break
        used += size
        end = index + 1
    return value[:end]


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
