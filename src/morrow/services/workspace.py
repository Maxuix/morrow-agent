"""Workspace identity, candidate discovery, and state-root layout."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from morrow.core.models import (
    Profile,
    StateLoadStatus,
    WorkspaceCandidate,
    WorkspaceIdentity,
    WorkspaceIndexEntry,
    WorkspaceResolution,
    utc_now,
)
from morrow.core.ports import IdSource, WorkspaceIndexStore
from morrow.core.store import (
    ARTIFACTS_DIRNAME,
    BACKUPS_DIRNAME,
    DATABASE_NAME,
    MAINTENANCE_LOCK_NAME,
    OPERATIONAL_BACKUPS_DIRNAME,
    STORE_DIRNAME,
)
from morrow.runtime.ids import RandomIdSource


class WorkspaceError(RuntimeError):
    pass


class WorkspaceWriterLock(AbstractContextManager):
    """REPL-lifetime single-writer lock stored outside the project."""

    def __init__(self, data_root: DataRoot, workspace_id: str, timeout: float = 0.1) -> None:
        self.path = data_root.locks_path / f"{workspace_id}.lock"
        self.timeout = timeout
        self._lock = FileLock(str(self.path), timeout=timeout)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise WorkspaceError(
                f"工作空间正在被另一个 Morrow 会话使用（锁：{self.path}）"
            ) from exc
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._lock.release()
        return False


class DataRoot:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path.home() / ".morrow").expanduser()

    @property
    def config_path(self) -> Path:
        return self.root / "config.yaml"

    @property
    def index_path(self) -> Path:
        return self.root / "workspace-index.yaml"

    @property
    def locks_path(self) -> Path:
        return self.root / "locks"

    @property
    def logs_path(self) -> Path:
        return self.root / "logs"

    @property
    def workspaces_path(self) -> Path:
        return self.root / "workspaces"

    @property
    def store_path(self) -> Path:
        return self.root / STORE_DIRNAME / DATABASE_NAME

    @property
    def artifacts_path(self) -> Path:
        return self.root / ARTIFACTS_DIRNAME

    @property
    def backups_path(self) -> Path:
        return self.root / BACKUPS_DIRNAME / OPERATIONAL_BACKUPS_DIRNAME

    @property
    def operational_lock_path(self) -> Path:
        return self.locks_path / MAINTENANCE_LOCK_NAME

    def ensure(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for path in (self.locks_path, self.logs_path, self.workspaces_path):
                path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(
                f"无法创建 Morrow 数据目录 {self.root}: {type(exc).__name__}"
            ) from exc


@dataclass(frozen=True)
class WorkspaceInspection:
    preferences: object
    profile: object

    @property
    def read_only(self) -> bool:
        return self.profile.status != StateLoadStatus.OK

    @property
    def preferences_read_only(self) -> bool:
        return self.preferences.status != StateLoadStatus.OK


class WorkspaceStateService:
    """Owns workspace startup inspection and first-run state publication."""

    def __init__(self, project_store) -> None:
        self.project_store = project_store

    def inspect(self, workspace_id: str) -> WorkspaceInspection:
        return WorkspaceInspection(
            preferences=self.project_store.load_preferences(workspace_id),
            profile=self.project_store.load_profile(workspace_id),
        )

    def onboard(self, workspace_id: str, *, display_name: str, summary: str) -> int | None:
        inspection = self.inspect(workspace_id)
        if inspection.read_only or inspection.profile.value:
            return None
        profile = self.project_store.write_profile(
            workspace_id,
            Profile(name=display_name, summary=summary or None),
            expected_revision=inspection.profile.revision,
        )
        if profile.status.value != "ok":
            raise WorkspaceError("Profile 保存失败，无法安全启动。")
        return profile.revision


def _safe_display_name(path: Path) -> str:
    return path.name or str(path)


def _same_existing_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except (FileNotFoundError, OSError):
        return left == right


class WorkspaceService:
    def __init__(
        self,
        data_root: DataRoot,
        index_store: WorkspaceIndexStore,
        id_source: IdSource | None = None,
    ) -> None:
        self.data_root = data_root
        self.index_store = index_store
        self.id_source = id_source or RandomIdSource()

    @staticmethod
    def normalize_path(path: Path) -> Path:
        path = path.expanduser().absolute()
        try:
            path = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise WorkspaceError(f"工作空间目录不存在: {path}") from exc
        if not path.is_dir():
            raise WorkspaceError(f"工作空间路径不是目录: {path}")
        return path

    @staticmethod
    def git_root(path: Path) -> Path | None:
        current = path
        while True:
            marker = current / ".git"
            if marker.is_dir() or marker.is_file():
                return current
            if current.parent == current:
                return None
            current = current.parent

    def _entries(self):
        result = self.index_store.load()
        if result.status != StateLoadStatus.OK:
            raise WorkspaceError(f"工作空间索引不可用: {result.status.value}")
        return result.value

    def _find_entry(
        self,
        path: Path,
        git_root: Path | None,
        entries,
        *,
        exclude_workspace_id: str | None = None,
        include_removed: bool = False,
    ) -> WorkspaceIndexEntry | None:
        def active(entry) -> bool:
            return include_removed or not entry.removed

        for entry in entries.workspaces.values():
            if entry.workspace_id == exclude_workspace_id or not active(entry):
                continue
            registered = Path(entry.path)
            if registered.exists() and _same_existing_path(path, registered):
                return entry
            if (
                git_root
                and entry.git_root
                and Path(entry.git_root).exists()
                and _same_existing_path(git_root, Path(entry.git_root))
            ):
                return entry
        if git_root:
            for entry in entries.workspaces.values():
                if entry.workspace_id == exclude_workspace_id or not active(entry):
                    continue
                registered = Path(entry.path)
                if registered.exists() and _same_existing_path(git_root, registered):
                    return entry
            # A Git repository is an identity boundary.  Do not let a
            # registered non-Git parent capture an unregistered nested repo.
            return None
        current = path
        while current != current.parent:
            for entry in entries.workspaces.values():
                if entry.workspace_id == exclude_workspace_id or not active(entry):
                    continue
                registered = Path(entry.path)
                if registered.exists() and _same_existing_path(current, registered):
                    return entry
            current = current.parent
        return None

    def get(self, workspace_id: str, *, include_removed: bool = False) -> WorkspaceIdentity | None:
        """Look up one registered workspace identity by exact ID."""

        entries = self._entries()
        entry = entries.workspaces.get(workspace_id)
        if entry is None or (entry.removed and not include_removed):
            return None
        return WorkspaceIdentity(
            workspace_id=entry.workspace_id,
            path=entry.path,
            display_name=entry.display_name,
            git_root=entry.git_root,
        )

    def resolve(self, path: Path, *, include_removed: bool = False) -> WorkspaceResolution:
        normalized = self.normalize_path(path)
        git_root = self.git_root(normalized)
        entries = self._entries()
        entry = self._find_entry(normalized, git_root, entries, include_removed=include_removed)
        if entry:
            return WorkspaceResolution(
                status="existing",
                identity=WorkspaceIdentity(
                    workspace_id=entry.workspace_id,
                    path=entry.path,
                    display_name=entry.display_name,
                    git_root=entry.git_root,
                ),
            )
        stale_similar = [
            item.workspace_id
            for item in entries.workspaces.values()
            if (include_removed or not item.removed)
            and not Path(item.path).exists()
            and item.display_name.casefold() == _safe_display_name(normalized).casefold()
        ]
        return WorkspaceResolution(
            status="candidate",
            candidate=WorkspaceCandidate(
                path=str(git_root or normalized),
                display_name=_safe_display_name(git_root or normalized),
                git_root=str(git_root) if git_root else None,
                similar_workspace_ids=stale_similar,
            ),
        )

    def confirm(
        self,
        resolution: WorkspaceResolution,
        *,
        display_name: str | None = None,
        validate_path=None,
    ) -> WorkspaceIdentity:
        if resolution.status == "existing" and resolution.identity:
            if validate_path is not None:
                validate_path(Path(resolution.identity.path))
            return resolution.identity
        if not resolution.candidate:
            raise WorkspaceError("无可确认的工作空间候选")
        candidate = resolution.candidate

        def claim(current):
            normalized = self.normalize_path(Path(candidate.path))
            git_root = self.git_root(normalized)
            if validate_path is not None:
                validate_path(git_root or normalized)
            existing = self._find_entry(normalized, git_root, current, include_removed=True)
            if existing:
                if not existing.removed:
                    return None, existing
                # Reopening a removed workspace revives it in place instead of
                # registering a duplicate identity for the same path.
                revived = existing.model_copy(update={"removed": False, "last_used_at": utc_now()})
                return (
                    current.model_copy(
                        update={
                            "workspaces": {**current.workspaces, existing.workspace_id: revived}
                        }
                    ),
                    revived,
                )
            canonical_path = git_root or normalized
            workspace_id = self.id_source.new_id("ws")
            entry = WorkspaceIndexEntry(
                workspace_id=workspace_id,
                path=str(canonical_path),
                display_name=display_name or _safe_display_name(canonical_path),
                git_root=str(git_root) if git_root else None,
            )
            updated = current.model_copy(
                update={"workspaces": {**current.workspaces, workspace_id: entry}}
            )
            return updated, entry

        result, entry = self.index_store.transact(claim)
        if result.status.value != "ok":
            raise WorkspaceError(f"无法登记工作空间: {result.error or result.status.value}")
        if entry is None:
            raise WorkspaceError("无法登记工作空间: 未返回权威身份")
        return WorkspaceIdentity(
            workspace_id=entry.workspace_id,
            path=entry.path,
            display_name=entry.display_name,
            git_root=entry.git_root,
        )

    def relink(
        self,
        workspace_id: str,
        path: Path,
        *,
        expected_revision: int | None = None,
        validate_path=None,
    ) -> WorkspaceIdentity:
        def move(current):
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkspaceError("Workspace list changed; refresh and retry")
            normalized = self.normalize_path(path)
            git_root = self.git_root(normalized)
            if validate_path is not None:
                validate_path(git_root or normalized)
            target = current.workspaces.get(workspace_id)
            if not target:
                raise WorkspaceError(f"未知工作空间: {workspace_id}")
            owner = self._find_entry(
                normalized,
                git_root,
                current,
                exclude_workspace_id=workspace_id,
                include_removed=True,
            )
            if owner:
                raise WorkspaceError("目标路径已经属于另一个工作空间")
            canonical_path = git_root or normalized
            updated = target.model_copy(
                update={
                    "path": str(canonical_path),
                    "git_root": str(git_root) if git_root else None,
                }
            )
            index = current.model_copy(
                update={"workspaces": {**current.workspaces, workspace_id: updated}}
            )
            return index, updated

        result, updated = self.index_store.transact(move)
        if result.status.value != "ok":
            raise WorkspaceError(f"重连失败: {result.error or result.status.value}")
        if updated is None:
            raise WorkspaceError("重连失败: 未返回工作空间身份")
        return WorkspaceIdentity(
            workspace_id=workspace_id,
            path=updated.path,
            display_name=updated.display_name,
            git_root=updated.git_root,
        )

    def listing(self) -> dict:
        index = self._entries()
        entries = sorted(
            (entry for entry in index.workspaces.values() if not entry.removed),
            key=lambda entry: entry.last_used_at.isoformat() if entry.last_used_at else "",
            reverse=True,
        )
        return {
            "revision": index.revision,
            "items": [
                {**entry.model_dump(mode="json"), "available": Path(entry.path).is_dir()}
                for entry in entries
            ],
        }

    def update_entry(
        self,
        workspace_id: str,
        *,
        expected_revision: int | None = None,
        display_name: str | None = None,
        removed: bool | None = None,
        touch: bool = False,
    ) -> WorkspaceIdentity:
        if display_name is not None:
            display_name = display_name.strip()
            if (
                not display_name
                or len(display_name) > 120
                or any(ord(c) < 32 for c in display_name)
            ):
                raise WorkspaceError("Workspace name must contain 1–120 printable characters")

        def change(current):
            if expected_revision is not None and current.revision != expected_revision:
                raise WorkspaceError("Workspace list changed; refresh and retry")
            entry = current.workspaces.get(workspace_id)
            if entry is None:
                raise WorkspaceError("Unknown workspace")
            changes = {}
            if display_name is not None:
                changes["display_name"] = display_name
            if removed is not None:
                changes["removed"] = removed
            if touch:
                changes["last_used_at"] = utc_now()
            updated = entry.model_copy(update=changes)
            return current.model_copy(
                update={"workspaces": {**current.workspaces, workspace_id: updated}}
            ), updated

        result, entry = self.index_store.transact(change)
        if result.status.value != "ok" or entry is None:
            raise WorkspaceError("Workspace update failed; refresh and retry")
        return WorkspaceIdentity(
            workspace_id=entry.workspace_id,
            path=entry.path,
            display_name=entry.display_name,
            git_root=entry.git_root,
        )
