"""Read-only, workspace-confined Git status and Diff semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.local.git import GitAdapterError, GitCommandOutput, GitInspectionAdapter
from morrow.core.local_tools import (
    GitDiffResult,
    GitEntryKind,
    GitRepositoryState,
    GitStatusEntry,
    GitStatusResult,
    ProtectedPath,
)
from morrow.services.files import LocalFileError, WorkspaceFileService

MAX_GIT_STATUS_ENTRIES = 512
MAX_GIT_PATH_FILTERS = 32
MAX_GIT_DIFF_BYTES = 16 * 1024
MAX_GIT_RAW_BYTES = 512 * 1024
MAX_GIT_RESULT_BYTES = 16 * 1024


class GitServiceError(RuntimeError):
    """Stable failure from the read-only Git inspection service."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class _GitMetadata:
    root: Path
    git_dir: Path
    common_dir: Path


class GitInspectionService:
    """Inspect only the frozen workspace's own Git metadata."""

    def __init__(
        self,
        files: WorkspaceFileService,
        *,
        adapter: GitInspectionAdapter | None = None,
    ) -> None:
        self.files = files
        self.adapter = adapter or GitInspectionAdapter()

    def status(self, *, result_limit: int = MAX_GIT_RESULT_BYTES) -> GitStatusResult:
        metadata = self._metadata()
        if metadata is None:
            return GitStatusResult(
                repository=False,
                repository_state=GitRepositoryState.NOT_REPOSITORY,
                root=".",
            )
        output = self._run(metadata.root, ("status", "--porcelain=v2", "-z", "--branch"))
        if output.returncode != 0:
            raise GitServiceError("git_command_failed", "Git 状态检查失败")
        result = self._parse_status(metadata, output)
        return self._fit_status(result, result_limit)

    def diff(
        self,
        *,
        staged: bool = False,
        paths: tuple[str, ...] = (),
        result_limit: int = MAX_GIT_RESULT_BYTES,
    ) -> GitDiffResult:
        relative_paths = self._validate_paths(paths)
        metadata = self._metadata()
        if metadata is None:
            return GitDiffResult(
                repository=False,
                repository_state=GitRepositoryState.NOT_REPOSITORY,
                staged=staged,
                paths=relative_paths,
                diff="",
            )
        status = self.status(result_limit=MAX_GIT_RESULT_BYTES)
        command = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--unified=3",
        ]
        if staged:
            command.append("--cached")
        command.extend(("--", *relative_paths))
        output = self._run(metadata.root, tuple(command))
        if output.returncode != 0:
            raise GitServiceError("git_command_failed", "Git Diff 检查失败")
        diff, protected_paths, diff_truncated = self._sanitize_diff(output.stdout)
        result = GitDiffResult(
            repository=True,
            repository_state=status.repository_state,
            staged=staged,
            paths=relative_paths,
            diff=diff,
            protected_paths=tuple(ProtectedPath(path=path) for path in protected_paths),
            truncated=output.truncated or diff_truncated,
        )
        return self._fit_diff(result, result_limit)

    def _metadata(self) -> _GitMetadata | None:
        root = self.files.resolver.root
        output = self._run(
            root,
            (
                "rev-parse",
                "--show-toplevel",
                "--absolute-git-dir",
                "--git-common-dir",
                "--is-inside-work-tree",
            ),
            max_output_bytes=8 * 1024,
        )
        if output.returncode != 0:
            return None
        lines = output.stdout.decode("utf-8", errors="replace").splitlines()
        if len(lines) != 4 or lines[3].strip().lower() != "true":
            raise GitServiceError("git_parse_failed", "Git 元数据响应无效")
        try:
            git_root = Path(lines[0]).expanduser().resolve(strict=True)
            git_dir = self._resolve_metadata_path(root, lines[1])
            common_dir = self._resolve_metadata_path(root, lines[2])
        except (OSError, ValueError) as exc:
            raise GitServiceError("external_git_metadata", "Git 元数据不在冻结工作空间内") from exc
        if git_root != root or not git_dir.is_dir() or not common_dir.is_dir():
            raise GitServiceError("external_git_metadata", "Git 元数据不在冻结工作空间内")
        return _GitMetadata(root=root, git_dir=git_dir, common_dir=common_dir)

    def _resolve_metadata_path(self, root: Path, raw: str) -> Path:
        if not raw or "\x00" in raw:
            raise ValueError("invalid Git metadata path")
        value = Path(raw)
        candidate = value if value.is_absolute() else root / value
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Git metadata is external") from exc
        return resolved

    def _run(
        self,
        root: Path,
        command: tuple[str, ...],
        *,
        max_output_bytes: int = MAX_GIT_RAW_BYTES,
    ) -> GitCommandOutput:
        args = (
            "--no-pager",
            "--no-optional-locks",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.useBuiltinFSMonitor=false",
            "-c",
            "credential.helper=",
            "-c",
            "diff.external=",
            "-C",
            str(root),
            *command,
        )
        original_limit = self.adapter.max_output_bytes
        self.adapter.max_output_bytes = min(original_limit, max_output_bytes)
        try:
            return self.adapter.run(root, args)
        except GitAdapterError as exc:
            raise GitServiceError(exc.code, exc.message) from exc
        finally:
            self.adapter.max_output_bytes = original_limit

    def _parse_status(self, metadata: _GitMetadata, output: GitCommandOutput) -> GitStatusResult:
        records = output.stdout.split(b"\0")
        entries: list[GitStatusEntry] = []
        protected_count = 0
        staged_count = 0
        unstaged_count = 0
        untracked_count = 0
        conflict_count = 0
        branch: str | None = None
        head: str | None = None
        detached = False
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            if record.startswith(b"# branch.head "):
                raw_branch = self._decode(record[len(b"# branch.head ") :])
                if raw_branch == "(detached)":
                    detached = True
                elif raw_branch not in {"(unknown)", "(initial)"}:
                    branch = raw_branch
                continue
            if record.startswith(b"# branch.oid "):
                raw_head = self._decode(record[len(b"# branch.oid ") :])
                if raw_head not in {"(initial)", "(unknown)"}:
                    head = raw_head[:128]
                continue
            kind = chr(record[0])
            original_path: str | None = None
            if kind == "?":
                path = self._decode(record[2:])
                index_status = "?"
                worktree_status = "?"
                entry_kind = GitEntryKind.UNTRACKED
                untracked_count += 1
            elif kind in {"1", "2", "u"}:
                split_limit = {"1": 8, "2": 9, "u": 10}[kind]
                parts = record.split(b" ", split_limit)
                if len(parts) < 3:
                    continue
                xy = self._decode(parts[1])
                path = self._decode(parts[-1])
                index_status = xy[:1] or "."
                worktree_status = xy[1:2] or "."
                if kind == "2":
                    entry_kind = GitEntryKind.RENAMED
                    if index < len(records):
                        original_path = self._decode(records[index])
                        index += 1
                elif kind == "u":
                    entry_kind = GitEntryKind.UNMERGED
                    conflict_count += 1
                else:
                    entry_kind = GitEntryKind.ORDINARY
            else:
                continue
            protected = self.files.sensitive_policy.is_protected_path(path)
            if protected:
                protected_count += 1
            if index_status not in {".", "?"}:
                staged_count += 1
            if worktree_status not in {".", "?"} and entry_kind is not GitEntryKind.UNTRACKED:
                unstaged_count += 1
            if len(entries) < MAX_GIT_STATUS_ENTRIES:
                entries.append(
                    GitStatusEntry(
                        path=path,
                        kind=entry_kind,
                        index_status=index_status,
                        worktree_status=worktree_status,
                        original_path=original_path,
                        protected=protected,
                    )
                )
        state = (
            GitRepositoryState.CONFLICT
            if conflict_count
            else GitRepositoryState.DIRTY
            if entries
            else GitRepositoryState.CLEAN
        )
        return GitStatusResult(
            repository=True,
            repository_state=state,
            root=".",
            branch=branch,
            head=head,
            detached=detached,
            entries=tuple(entries),
            staged_count=staged_count,
            unstaged_count=unstaged_count,
            untracked_count=untracked_count,
            conflict_count=conflict_count,
            protected_count=protected_count,
            truncated=output.truncated or len(records) > MAX_GIT_STATUS_ENTRIES,
        )

    def _sanitize_diff(self, raw: bytes) -> tuple[str, tuple[str, ...], bool]:
        text = raw.decode("utf-8", errors="replace")
        segments = _split_diff_segments(text)
        if not segments:
            if self.files.sensitive_policy.is_protected_content(raw):
                return "[protected diff omitted]\n", (), False
            return text, (), False
        protected_paths: list[str] = []
        sanitized: list[str] = []
        for segment in segments:
            paths = _diff_paths(segment)
            protected = any(self.files.sensitive_policy.is_protected_path(path) for path in paths)
            protected = protected or self.files.sensitive_policy.is_protected_content(
                segment.encode("utf-8", errors="ignore")
            )
            if protected:
                for path in paths:
                    if (
                        self.files.sensitive_policy.is_protected_path(path)
                        and path not in protected_paths
                    ):
                        protected_paths.append(path)
                sanitized.append(
                    "diff --git a/[protected] b/[protected]\n[protected diff omitted]\n"
                )
            else:
                sanitized.append(segment)
        value = "".join(sanitized)
        encoded = value.encode("utf-8")
        truncated = len(encoded) > MAX_GIT_DIFF_BYTES
        if truncated:
            value = _truncate_utf8(encoded, MAX_GIT_DIFF_BYTES)
        return value, tuple(protected_paths), truncated

    def _validate_paths(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        if len(paths) > MAX_GIT_PATH_FILTERS:
            raise GitServiceError("invalid_path", "Git 路径过滤器超过限制")
        normalized: list[str] = []
        for path in paths:
            if not isinstance(path, str):
                raise GitServiceError("invalid_path", "Git 路径过滤器无效")
            try:
                normalized.append(self.files.resolver.validate_relative_path(path))
            except LocalFileError as exc:
                raise GitServiceError(exc.code, exc.message) from exc
        return tuple(normalized)

    @staticmethod
    def _decode(value: bytes) -> str:
        text = value.decode("utf-8", errors="replace")
        return text if text else "<invalid-path>"

    @staticmethod
    def _fit_status(result: GitStatusResult, result_limit: int) -> GitStatusResult:
        entries = list(result.entries)
        truncated = result.truncated
        while _json_size(result.model_copy(update={"entries": tuple(entries)})) > result_limit:
            if not entries:
                raise GitServiceError("output_budget", "Git 状态结果无法放入当前预算")
            entries.pop()
            truncated = True
        fitted = result.model_copy(update={"entries": tuple(entries), "truncated": truncated})
        if _json_size(fitted) > result_limit:
            raise GitServiceError("output_budget", "Git 状态结果无法放入当前预算")
        return fitted

    @staticmethod
    def _fit_diff(result: GitDiffResult, result_limit: int) -> GitDiffResult:
        diff = result.diff
        protected_paths = list(result.protected_paths)
        truncated = result.truncated
        while (
            _json_size(
                result.model_copy(update={"diff": diff, "protected_paths": tuple(protected_paths)})
            )
            > result_limit
        ):
            encoded = diff.encode("utf-8")
            if encoded:
                next_size = max(0, len(encoded) - max(256, len(encoded) // 8))
                diff = _truncate_utf8(encoded, next_size)
                truncated = True
            elif protected_paths:
                protected_paths.pop()
                truncated = True
            else:
                raise GitServiceError("output_budget", "Git Diff 结果无法放入当前预算")
        fitted = result.model_copy(
            update={
                "diff": diff,
                "protected_paths": tuple(protected_paths),
                "truncated": truncated,
            }
        )
        if _json_size(fitted) > result_limit:
            raise GitServiceError("output_budget", "Git Diff 结果无法放入当前预算")
        return fitted


def _split_diff_segments(text: str) -> tuple[str, ...]:
    marker = "diff --git "
    if marker not in text:
        return ()
    parts = text.split(marker)
    return tuple(marker + part for part in parts[1:])


def _diff_paths(segment: str) -> tuple[str, ...]:
    first = segment.splitlines()[0] if segment.splitlines() else ""
    if not first.startswith("diff --git a/"):
        return ()
    remainder = first[len("diff --git a/") :]
    separator = " b/"
    if separator not in remainder:
        return ()
    left, right = remainder.split(separator, 1)
    return tuple(dict.fromkeys((left, right)))


def _truncate_utf8(value: bytes, limit: int) -> str:
    return value[:limit].decode("utf-8", errors="ignore")


def _json_size(model) -> int:
    return len(
        json.dumps(model.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
