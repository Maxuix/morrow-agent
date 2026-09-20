"""Application boundary for the human project-source editor.

The GUI is allowed to edit only workspace-relative UTF-8 text through the
same confined filesystem services used by the local tools.  This module keeps
the HTTP layer free of path-resolution and mutation details, and deliberately
does not capture human edits as Agent artifacts.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import PolicyVerdict, ToolRunContext
from morrow.services.files import (
    MAX_DIRECTORY_ENTRIES,
    LocalFileError,
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
    _encode_newline,
    _normalize_newlines,
)

MAX_EDITABLE_FILE_BYTES = 1024 * 1024
#: 预览与受控下载共用的有界字节上限；超过时明确失败，不做全量内存读取的绕过。
MAX_PREVIEW_FILE_BYTES = 20 * 1024 * 1024
MAX_FILE_TREE_PAGE = 200
MAX_FILE_TREE_FETCH = MAX_DIRECTORY_ENTRIES
MAX_FILE_TREE_RESULT_BYTES = 512 * 1024


def _local_error(error: LocalFileError) -> ApplicationError:
    code = {
        "conflict": ApplicationErrorCode.STALE,
        "not_found": ApplicationErrorCode.NOT_FOUND,
        "workspace_unavailable": ApplicationErrorCode.UNAVAILABLE,
        "path_unavailable": ApplicationErrorCode.UNAVAILABLE,
        "outside_workspace": ApplicationErrorCode.CROSS_WORKSPACE,
        "read_only": ApplicationErrorCode.READ_ONLY,
    }.get(error.code, ApplicationErrorCode.INVALID)
    return ApplicationError(code, error.message)


#: Suffix -> preview family for files that are deliberately not decoded as text.
_IMAGE_SUFFIXES = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "ico"})
_PDF_SUFFIXES = frozenset({"pdf"})


def _binary_preview(relative_path: str) -> str:
    suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _PDF_SUFFIXES:
        return "pdf"
    return "binary"


def media_type_for(relative_path: str) -> str:
    """按路径后缀给出媒体类型；未知一律 application/octet-stream。"""

    guessed, _ = mimetypes.guess_type(relative_path)
    return guessed or "application/octet-stream"


def content_disposition(disposition: str, filename: str) -> str:
    """ASCII 回退 + RFC 5987 文件名；非 ASCII 名称也能安全出现在响应头。

    回退名不是简单丢掉非 ASCII 字符：只剩扩展名或含引号/控制字符时会先清理，
    必要时退回 download.<suffix>，因此头部始终带一个可用的 ASCII 文件名。
    """

    fallback = _ascii_header_name(filename)
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _header_safe(value: str) -> str:
    """去掉不能安全出现在带引号头参数里的字符，并裁掉空白与点。"""

    return "".join(
        char if char.isascii() and char.isprintable() and char not in '"\\' else ""
        for char in value
    ).strip(" .")


def _ascii_header_name(filename: str) -> str:
    if "." in filename:
        stem, _, suffix = filename.rpartition(".")
    else:
        stem, suffix = filename, ""
    stem, suffix = _header_safe(stem), _header_safe(suffix)
    if stem:
        return f"{stem}.{suffix}" if suffix else stem
    return f"download.{suffix}" if suffix else "download"


def _bounded_limit(value: int, *, maximum: int) -> int:
    if value < 1 or value > maximum:
        raise ApplicationError(ApplicationErrorCode.INVALID, "query page size is invalid")
    return value


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except ValueError as exc:
        raise ApplicationError(ApplicationErrorCode.INVALID, "query cursor is invalid") from exc
    if value < 0:
        raise ApplicationError(ApplicationErrorCode.INVALID, "query cursor is invalid")
    return value


class WorkspaceFilesApplicationService:
    """Read and replace source text under one registered workspace root."""

    def __init__(self, root: Path, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        try:
            resolver = WorkspacePathResolver(root)
        except LocalFileError as exc:
            raise _local_error(exc) from None
        self.files = WorkspaceFileService(resolver)

    def tree(self, path: str, *, limit: int, after: str | None) -> dict[str, Any]:
        page_limit = _bounded_limit(limit, maximum=MAX_FILE_TREE_PAGE)
        offset = _offset(after)
        try:
            listing = self.files.list_directory(
                path or ".",
                depth=1,
                max_entries=MAX_FILE_TREE_FETCH,
                result_limit=MAX_FILE_TREE_RESULT_BYTES,
            )
        except LocalFileError as exc:
            raise _local_error(exc) from None

        entries = [item.model_dump(mode="json") for item in listing.entries]
        page = entries[offset : offset + page_limit]
        has_more = offset + len(page) < len(entries) or listing.truncated
        return {
            "schema_version": 1,
            "workspace_id": self.workspace_id,
            "path": listing.path,
            "entries": page,
            "next_cursor": str(offset + len(page)) if has_more and page else None,
            "truncated": listing.truncated,
        }

    def relative_path(self, path: str) -> str:
        """Normalize one caller-supplied path to a workspace-relative one.

        Absolute paths inside the workspace are relativized here, once, and
        anything outside is rejected before the resolver runs. The HTTP layer
        has already decoded the query parameter a single time; no second
        decode happens, so an encoded traversal cannot be smuggled through.
        """

        candidate = path.strip() if isinstance(path, str) else ""
        if candidate == "":
            raise ApplicationError(ApplicationErrorCode.INVALID, "path is required")
        if len(candidate) > 4096:
            raise ApplicationError(ApplicationErrorCode.INVALID, "path is too long")
        if candidate.startswith("/"):
            root = PurePosixPath(self.files.resolver.root.as_posix())
            try:
                candidate = str(PurePosixPath(os.path.normpath(candidate)).relative_to(root))
            except ValueError:
                raise ApplicationError(
                    ApplicationErrorCode.CROSS_WORKSPACE, "路径不在当前工作空间内"
                ) from None
            if candidate in {"", "."}:
                candidate = "."
        return candidate

    def info(self, path: str) -> dict[str, Any]:
        """Narrow, non-leaking identity of one workspace file.

        Resolves through the same confined resolver as every other read, and
        answers with the relative path only: no absolute or internal store
        path ever leaves this boundary. The HTTP layer has already decoded the
        query parameter exactly once, so nothing is decoded again here - a
        second decode would let an encoded traversal cross the workspace root.
        """

        try:
            resolved = self.files.resolver.resolve_file(self.relative_path(path))
        except LocalFileError as exc:
            raise _local_error(exc) from None
        try:
            size = resolved.target.stat().st_size
        except OSError as exc:
            raise _local_error(LocalFileError("path_unavailable", "文件元数据不可用")) from exc
        is_text = False
        if size <= MAX_EDITABLE_FILE_BYTES:
            try:
                self.files.read_source_text(resolved.relative_path)
                is_text = True
            except LocalFileError:
                is_text = False
        return {
            "path": resolved.relative_path,
            "kind": "file",
            "byte_size": size,
            "text": is_text,
            "editable": is_text and size <= MAX_EDITABLE_FILE_BYTES,
            "preview": "text" if is_text else _binary_preview(resolved.relative_path),
        }

    def content(self, path: str) -> dict[str, Any]:
        try:
            source = self.files.read_source_text(self.relative_path(path))
        except LocalFileError as exc:
            raise _local_error(exc) from None
        if source.revision.size > MAX_EDITABLE_FILE_BYTES:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "source file exceeds the editable size limit",
            )
        return self._content_wire(source)

    def download(self, path: str) -> tuple[bytes, str]:
        """有界字节读取，供预览对象 URL 与受控下载使用。

        每次都重新解析真实路径（同一个受限 resolver），因此前端此前拿到的
        metadata 不会变成后续越权读取的通行证；超过上限的文件明确拒绝。
        """

        try:
            resolved = self.files.resolver.resolve_file(self.relative_path(path))
        except LocalFileError as exc:
            raise _local_error(exc) from None
        try:
            size = resolved.target.stat().st_size
        except OSError as exc:
            raise _local_error(LocalFileError("path_unavailable", "文件元数据不可用")) from exc
        if size > MAX_PREVIEW_FILE_BYTES:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "文件超过 20 MiB 预览与下载上限",
            )
        try:
            raw = self.files.filesystem.read_bytes(
                resolved.target, max_bytes=MAX_PREVIEW_FILE_BYTES
            )
        except LocalFileError as exc:
            raise _local_error(exc) from None
        return raw, media_type_for(resolved.relative_path)

    def replace(
        self, *, path: str, content: str, expected_sha256: str, command_id: str
    ) -> dict[str, Any]:
        if len(content.encode("utf-8")) > MAX_EDITABLE_FILE_BYTES:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "source content exceeds the editable size limit",
            )
        try:
            mutation = WorkspaceMutationService(self.files, artifact_capture=False)
            plan = mutation.preflight_write(
                path,
                content=content,
                mode="replace",
                expected_sha256=expected_sha256,
            )
            # Human edits intentionally use the existing mutation service but
            # have no AgentRun/artifact capture context.
            run = ToolRunContext(
                run_id=f"gui_{command_id}",
                session_id=f"gui_{self.workspace_id}",
            )
            result, _fact = mutation.apply(
                plan,
                call_id=command_id,
                tool_name="gui_source_editor",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                run=run,
            )
            source = self.files.read_source_text(path)
        except LocalFileError as exc:
            # 故障窗口：文件已经写好、但回执尚未提交时进程中断，重试会看到
            # "基线已变化"。若磁盘内容正是本次要写的内容，就诚实地报告该写入
            # 已生效，而不是要求用户处理一个并不存在的冲突；这不改动文件，也
            # 不覆盖任何第三方内容。
            if exc.code == "conflict":
                reconciled = self._reconciled_write(path, content)
                if reconciled is not None:
                    return reconciled
            raise _local_error(exc) from None
        return {
            "schema_version": 1,
            "workspace_id": self.workspace_id,
            "file": self._content_wire(source),
            "mutation": result.model_dump(mode="json"),
        }

    def _reconciled_write(self, path: str, content: str) -> dict[str, Any] | None:
        """磁盘内容与本次提交逐字节一致时返回接续结果，否则 None。"""

        try:
            source = self.files.read_source_text(path)
        except LocalFileError:
            return None
        desired = _normalize_newlines(content, source.newline)
        raw = (b"\xef\xbb\xbf" if source.bom else b"") + _encode_newline(desired, source.newline)
        if hashlib.sha256(raw).hexdigest() != source.revision.sha256:
            return None
        return {
            "schema_version": 1,
            "workspace_id": self.workspace_id,
            "file": self._content_wire(source),
            "mutation": None,
            "disposition": "reconciled",
        }

    def _content_wire(self, source) -> dict[str, Any]:
        return {
            "path": source.relative_path,
            "text": source.text,
            "revision": source.revision.model_dump(mode="json"),
            "bom": source.bom,
            "newline": source.newline.value,
        }
