"""Task-private native-sandbox snapshots, bounded Diff, and promotion bundles."""

from __future__ import annotations

import ctypes
import difflib
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from morrow.core.capabilities import SensitiveResourcePolicy, ToolRunContext
from morrow.services.files import WorkspaceFileService

MAX_SNAPSHOT_FILES = 10_000
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
MAX_PROMOTION_BYTES = 64 * 1024
MAX_PROMOTION_FILES = 16
MAX_DIFF_BYTES = 4 * 1024
MAX_DIFF_LINES = 40
_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".morrow",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        "node_modules",
        "dist",
        "build",
    }
)


class SandboxServiceError(RuntimeError):
    """Stable snapshot, Diff, or promotion failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SnapshotEntry:
    relative_path: str
    kind: str
    sha256: str | None
    size: int
    mode: int
    raw: bytes | None
    real_mtime_ns: int | None


@dataclass(frozen=True)
class SandboxChange:
    relative_path: str
    operation: str
    before_sha256: str | None
    after_sha256: str | None
    diff: str
    diff_truncated: bool
    changed_bytes: int
    eligible: bool
    expected_sha256: str | None
    content: str | None


@dataclass(frozen=True)
class SandboxChangeSet:
    change_set_id: str
    changes: tuple[SandboxChange, ...]
    truncated: bool = False

    @property
    def eligible_changes(self) -> tuple[SandboxChange, ...]:
        return tuple(change for change in self.changes if change.eligible)

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(change.relative_path for change in self.changes)

    def summary(self) -> dict[str, object]:
        return {
            "change_set_id": self.change_set_id,
            "paths": self.changed_paths,
            "eligible_paths": tuple(change.relative_path for change in self.eligible_changes),
            "truncated": self.truncated,
        }


@dataclass
class SandboxSession:
    change_set_id: str
    source_root: Path
    temp_root: Path
    snapshot_root: Path
    private_temp: Path
    private_home: Path
    private_cache: Path
    baseline: dict[str, SnapshotEntry]


class SandboxSnapshotService:
    """Create and dispose one isolated snapshot per sandbox command."""

    def __init__(
        self,
        files: WorkspaceFileService,
        *,
        temp_parent: Path | None = None,
        max_files: int = MAX_SNAPSHOT_FILES,
        max_bytes: int = MAX_SNAPSHOT_BYTES,
    ) -> None:
        self.files = files
        self.sensitive_policy: SensitiveResourcePolicy = files.sensitive_policy
        if temp_parent is None and sys.platform == "darwin" and Path("/private/tmp").is_dir():
            temp_parent = Path("/private/tmp")
        self.temp_parent = temp_parent
        self.max_files = max_files
        self.max_bytes = max_bytes

    def reserve_temp_root(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="morrow-sandbox-", dir=self.temp_parent)).resolve(
            strict=True
        )

    def prepare(
        self,
        source_root: Path,
        *,
        run_id: str,
        call_id: str,
        temp_root: Path | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SandboxSession:
        source = self._validate_source_root(source_root)
        temp_root = temp_root or self.reserve_temp_root()
        self._validate_reserved_root(temp_root)
        snapshot_root = temp_root / "workspace"
        private_temp = temp_root / "tmp"
        private_home = temp_root / "home"
        private_cache = temp_root / "cache"
        try:
            self._check_cancelled(cancel_event)
            for directory in (snapshot_root, private_temp, private_home, private_cache):
                directory.mkdir()
            baseline = self._copy_tree(source, snapshot_root, cancel_event=cancel_event)
            self._check_cancelled(cancel_event)
            change_set_id = _opaque_id(run_id, call_id, source)
            return SandboxSession(
                change_set_id=change_set_id,
                source_root=source,
                temp_root=temp_root,
                snapshot_root=snapshot_root,
                private_temp=private_temp,
                private_home=private_home,
                private_cache=private_cache,
                baseline=baseline,
            )
        except SandboxServiceError:
            self.cleanup_reserved(temp_root)
            raise
        except OSError as exc:
            self.cleanup_reserved(temp_root)
            raise SandboxServiceError("sandbox_unavailable", "沙箱快照无法创建") from exc

    def collect(
        self, session: SandboxSession, *, cancel_event: threading.Event | None = None
    ) -> SandboxChangeSet:
        self._validate_session_root(session)
        self._check_cancelled(cancel_event)
        current = self._scan_tree(
            session.snapshot_root, include_raw=True, cancel_event=cancel_event
        )
        paths = sorted(
            set(session.baseline) | set(current), key=lambda value: (value.casefold(), value)
        )
        changes: list[SandboxChange] = []
        for relative in paths:
            self._check_cancelled(cancel_event)
            before = session.baseline.get(relative)
            after = current.get(relative)
            if before is not None and after is not None:
                if (
                    before.sha256 == after.sha256
                    and before.mode == after.mode
                    and before.kind == after.kind
                ):
                    continue
                operation = "modified"
            elif before is None and after is not None:
                operation = "created"
            else:
                operation = "deleted"
            changes.append(self._change(relative, operation, before, after))
            if len(changes) >= MAX_PROMOTION_FILES * 4:
                return SandboxChangeSet(
                    session.change_set_id, tuple(changes[:MAX_PROMOTION_FILES]), True
                )
        return SandboxChangeSet(session.change_set_id, tuple(changes))

    def cleanup(self, session: SandboxSession) -> None:
        self._validate_session_root(session)
        self.cleanup_reserved(session.temp_root)

    def cleanup_reserved(self, temp_root: Path) -> None:
        try:
            metadata = os.lstat(temp_root)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SandboxServiceError("sandbox_cleanup_failed", "沙箱临时目录清理失败") from exc
        self._validate_reserved_root(temp_root)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise SandboxServiceError("sandbox_violation", "拒绝清理未验证的沙箱路径")
        self._cleanup_root(temp_root)

    def retain(self, run: ToolRunContext, change_set: SandboxChangeSet) -> None:
        run.retain_change_set(change_set.change_set_id, change_set)

    @staticmethod
    def selected_changes(
        run: ToolRunContext, change_set_id: str, paths: tuple[str, ...]
    ) -> tuple[SandboxChange, ...]:
        value = run.change_set(change_set_id)
        if not isinstance(value, SandboxChangeSet):
            raise SandboxServiceError("sandbox_change_set_not_found", "沙箱变更集合不存在")
        if not paths or len(paths) > MAX_PROMOTION_FILES or len(set(paths)) != len(paths):
            raise SandboxServiceError("sandbox_selection_invalid", "沙箱变更选择无效")
        selected = []
        by_path = {change.relative_path: change for change in value.eligible_changes}
        for path in paths:
            if not path or path.startswith(("/", "\\")) or ".." in path.split("/"):
                raise SandboxServiceError("sandbox_selection_invalid", "沙箱路径必须是相对路径")
            change = by_path.get(path)
            if change is None:
                raise SandboxServiceError("sandbox_change_not_eligible", "沙箱变更不可推广")
            selected.append(change)
        return tuple(selected)

    def _copy_tree(
        self,
        source: Path,
        destination: Path,
        *,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, SnapshotEntry]:
        baseline: dict[str, SnapshotEntry] = {}
        counters = {"files": 0, "bytes": 0}
        self._copy_directory(source, destination, "", baseline, counters, cancel_event=cancel_event)
        return baseline

    def _copy_directory(
        self,
        source: Path,
        destination: Path,
        prefix: str,
        baseline: dict[str, SnapshotEntry],
        counters: dict[str, int],
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._check_cancelled(cancel_event)
        try:
            entries = sorted(
                os.scandir(source), key=lambda entry: (entry.name.casefold(), entry.name)
            )
        except OSError as exc:
            raise SandboxServiceError("sandbox_unavailable", "沙箱快照无法读取工作空间") from exc
        for entry in entries:
            self._check_cancelled(cancel_event)
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.name in _EXCLUDED_NAMES or self.sensitive_policy.is_protected_path(relative):
                continue
            source_path = Path(entry.path)
            destination_path = destination / entry.name
            try:
                mode = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SandboxServiceError("sandbox_unavailable", "沙箱快照元数据读取失败") from exc
            if stat.S_ISDIR(mode.st_mode):
                destination_path.mkdir()
                self._copy_directory(
                    source_path,
                    destination_path,
                    relative,
                    baseline,
                    counters,
                    cancel_event=cancel_event,
                )
                continue
            if stat.S_ISLNK(mode.st_mode):
                self._copy_symlink(
                    source_path,
                    destination_path,
                    relative,
                    baseline,
                    mode.st_mode,
                    mode.st_mtime_ns,
                )
                continue
            if not stat.S_ISREG(mode.st_mode):
                raise SandboxServiceError("sandbox_limit", "沙箱快照包含不支持的特殊文件")
            raw = self._read_file(source_path, mode.st_size, cancel_event=cancel_event)
            if self.sensitive_policy.is_protected_content(raw):
                continue
            counters["files"] += 1
            counters["bytes"] += len(raw)
            if counters["files"] > self.max_files or counters["bytes"] > self.max_bytes:
                raise SandboxServiceError("sandbox_limit", "沙箱快照超过文件或字节上限")
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            if not _clone_file(source_path, destination_path):
                shutil.copyfile(source_path, destination_path)
            self._check_cancelled(cancel_event)
            os.chmod(destination_path, stat.S_IMODE(mode.st_mode), follow_symlinks=False)
            baseline[relative] = SnapshotEntry(
                relative_path=relative,
                kind="file",
                sha256=_sha256(raw),
                size=len(raw),
                mode=stat.S_IMODE(mode.st_mode),
                raw=raw if len(raw) <= MAX_PROMOTION_BYTES else None,
                real_mtime_ns=mode.st_mtime_ns,
            )

    def _copy_symlink(
        self,
        source: Path,
        destination: Path,
        relative: str,
        baseline: dict[str, SnapshotEntry],
        mode: int,
        mtime_ns: int,
    ) -> None:
        try:
            target = (source.parent / os.readlink(source)).resolve(strict=False)
            target.relative_to(self.files.resolver.root)
            link = os.readlink(source)
            destination.symlink_to(link)
        except (OSError, ValueError):
            return
        baseline[relative] = SnapshotEntry(
            relative_path=relative,
            kind="symlink",
            sha256=None,
            size=0,
            mode=stat.S_IMODE(mode),
            raw=None,
            real_mtime_ns=mtime_ns,
        )

    def _scan_tree(
        self,
        root: Path,
        *,
        include_raw: bool,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, SnapshotEntry]:
        result: dict[str, SnapshotEntry] = {}
        self._scan_directory(root, "", result, include_raw, cancel_event=cancel_event)
        return result

    def _scan_directory(
        self,
        directory: Path,
        prefix: str,
        result: dict[str, SnapshotEntry],
        include_raw: bool,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._check_cancelled(cancel_event)
        try:
            entries = sorted(
                os.scandir(directory), key=lambda entry: (entry.name.casefold(), entry.name)
            )
        except OSError as exc:
            raise SandboxServiceError("sandbox_violation", "沙箱工作目录无法读取") from exc
        for entry in entries:
            self._check_cancelled(cancel_event)
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if entry.name in _EXCLUDED_NAMES or self.sensitive_policy.is_protected_path(relative):
                continue
            path = Path(entry.path)
            try:
                mode = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SandboxServiceError("sandbox_violation", "沙箱变更元数据读取失败") from exc
            if stat.S_ISDIR(mode.st_mode):
                self._scan_directory(
                    path,
                    relative,
                    result,
                    include_raw,
                    cancel_event=cancel_event,
                )
            elif stat.S_ISLNK(mode.st_mode):
                result[relative] = SnapshotEntry(
                    relative, "symlink", None, 0, stat.S_IMODE(mode.st_mode), None, None
                )
            elif stat.S_ISREG(mode.st_mode):
                raw = self._read_file(path, mode.st_size, cancel_event=cancel_event)
                if self.sensitive_policy.is_protected_content(raw):
                    continue
                result[relative] = SnapshotEntry(
                    relative,
                    "file",
                    _sha256(raw),
                    len(raw),
                    stat.S_IMODE(mode.st_mode),
                    raw if include_raw and len(raw) <= MAX_PROMOTION_BYTES else None,
                    mode.st_mtime_ns,
                )
            else:
                raise SandboxServiceError("sandbox_violation", "沙箱生成了不支持的特殊文件")

    def _change(
        self,
        relative: str,
        operation: str,
        before: SnapshotEntry | None,
        after: SnapshotEntry | None,
    ) -> SandboxChange:
        before_raw = before.raw if before is not None else None
        after_raw = after.raw if after is not None else None
        is_text = (before is None or before.kind == "file") and (
            after is None or after.kind == "file"
        )
        diff = ""
        diff_truncated = False
        content: str | None = None
        eligible = False
        if is_text and after_raw is not None and (before is None or before_raw is not None):
            try:
                before_text = (before_raw or b"").decode("utf-8")
                after_text = after_raw.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                diff, diff_truncated = _bounded_diff(relative, before_text, after_text)
                mode_changed = before is not None and before.mode != after.mode
                eligible = (
                    operation in {"created", "modified"}
                    and not mode_changed
                    and len(after_raw) <= MAX_PROMOTION_BYTES
                )
                if eligible:
                    content = after_text
        return SandboxChange(
            relative_path=relative,
            operation=operation,
            before_sha256=before.sha256 if before is not None else None,
            after_sha256=after.sha256 if after is not None else None,
            diff=diff,
            diff_truncated=diff_truncated,
            changed_bytes=(before.size if before is not None else 0)
            + (after.size if after is not None else 0),
            eligible=eligible,
            expected_sha256=before.sha256 if before is not None else None,
            content=content,
        )

    def _validate_source_root(self, root: Path) -> Path:
        try:
            resolved = root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise SandboxServiceError("sandbox_unavailable", "沙箱工作空间不可用") from exc
        if not resolved.is_dir() or not resolved.is_relative_to(self.files.resolver.root):
            raise SandboxServiceError("sandbox_unavailable", "沙箱工作空间不在冻结根目录内")
        return resolved

    def _validate_reserved_root(self, root: Path) -> None:
        try:
            expected_parent = Path(
                self.temp_parent if self.temp_parent is not None else tempfile.gettempdir()
            ).resolve(strict=True)
            actual_parent = root.parent.resolve(strict=True)
        except OSError as exc:
            raise SandboxServiceError("sandbox_violation", "沙箱临时根目录不可用") from exc
        if (
            not root.is_absolute()
            or actual_parent != expected_parent
            or not root.name.startswith("morrow-sandbox-")
        ):
            raise SandboxServiceError("sandbox_violation", "沙箱临时根目录不受当前服务管理")

    @staticmethod
    def _validate_session_root(session: SandboxSession) -> None:
        if session.temp_root.is_symlink() or not session.temp_root.is_dir():
            raise SandboxServiceError("sandbox_violation", "沙箱临时根目录不可用")
        if not session.snapshot_root.is_relative_to(session.temp_root):
            raise SandboxServiceError("sandbox_violation", "沙箱快照路径无效")

    @staticmethod
    def _check_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise SandboxServiceError("sandbox_cancelled", "沙箱阶段已取消")

    @classmethod
    def _read_file(
        cls,
        path: Path,
        size: int,
        *,
        cancel_event: threading.Event | None = None,
    ) -> bytes:
        cls._check_cancelled(cancel_event)
        if size > MAX_SNAPSHOT_FILE_BYTES:
            raise SandboxServiceError("sandbox_limit", "沙箱文件超过单文件上限")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SandboxServiceError("sandbox_unavailable", "沙箱文件读取失败") from exc
        cls._check_cancelled(cancel_event)
        return raw

    @staticmethod
    def _cleanup_root(root: Path) -> None:
        if root.is_symlink() or not root.is_dir() or not root.name.startswith("morrow-sandbox-"):
            raise SandboxServiceError("sandbox_violation", "拒绝清理未验证的沙箱路径")
        try:
            shutil.rmtree(root)
        except OSError as exc:
            raise SandboxServiceError("sandbox_cleanup_failed", "沙箱临时目录清理失败") from exc


def _clone_file(source: Path, destination: Path) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        clonefile = libc.clonefile
        clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        clonefile.restype = ctypes.c_int
        return clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0
    except (AttributeError, OSError):
        return False


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _opaque_id(run_id: str, call_id: str, root: Path) -> str:
    digest = hashlib.sha256(f"{run_id}\0{call_id}\0{root}".encode()).hexdigest()[:24]
    return f"sbx_{digest}"


def _bounded_diff(relative: str, before: str, after: str) -> tuple[str, bool]:
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="\n",
        )
    )
    truncated = len(lines) > MAX_DIFF_LINES
    if truncated:
        lines = lines[:MAX_DIFF_LINES]
    text = "".join(lines)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_DIFF_BYTES:
        text = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    return text, truncated
