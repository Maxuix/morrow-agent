"""Versioned YAML state with locked, atomic, revision-checked writes."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import yaml
from filelock import FileLock, Timeout
from pydantic import BaseModel, ValidationError

from morrow.core.models import (
    CURRENT_SCHEMA_VERSION,
    WORKSPACE_DOCUMENT_SCHEMA_VERSION,
    GlobalConfig,
    Preferences,
    Profile,
    ProfileDocument,
    ProjectPreferencesDocument,
    StateLoadResult,
    StateLoadStatus,
    StatePresence,
    StateWriteResult,
    StateWriteStatus,
    WorkspaceIndex,
    utc_now,
)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


class StateUnavailableError(RuntimeError):
    """The Morrow data root cannot be created or written."""


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                raise
    finally:
        os.close(descriptor)


class YamlDocument:
    """One typed YAML document, independent of the domain owning it."""

    def __init__(
        self,
        path: Path,
        model_type: type[T],
        *,
        default_factory: Callable[[], T] | None = None,
        lock_path: Path | None = None,
        failure_injector: Callable[[str], None] | None = None,
        supported_schema_version: int = CURRENT_SCHEMA_VERSION,
        load_transform: Callable[[dict], dict] | None = None,
        workspace_envelope: bool = False,
    ) -> None:
        self.path = path
        self.model_type = model_type
        self.default_factory = default_factory
        self.lock_path = lock_path or path.with_suffix(path.suffix + ".lock")
        self.failure_injector = failure_injector
        self.supported_schema_version = supported_schema_version
        self.load_transform = load_transform
        self.workspace_envelope = workspace_envelope

    def _fail(self, point: str) -> None:
        if self.failure_injector:
            self.failure_injector(point)

    def _publish(self, data: bytes) -> None:
        """Publish without removing the old source before replacement succeeds."""
        self._fail("temporary_write")
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temp_path = Path(temp_name)
        backup_temp: Path | None = None
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                self._fail("fsync")
                os.fsync(handle.fileno())
            if self.path.exists():
                backup = self.path.with_suffix(self.path.suffix + ".bak")
                backup_fd, backup_name = tempfile.mkstemp(
                    prefix=f".{backup.name}.", dir=self.path.parent
                )
                backup_temp = Path(backup_name)
                with os.fdopen(backup_fd, "wb") as backup_handle:
                    with self.path.open("rb") as source:
                        shutil.copyfileobj(source, backup_handle)
                    backup_handle.flush()
                    os.fsync(backup_handle.fileno())
                os.replace(backup_temp, backup)
                backup_temp = None
                _fsync_directory(self.path.parent)
            self._fail("replace")
            os.replace(temp_path, self.path)
            self._fail("directory_fsync")
            _fsync_directory(self.path.parent)
        finally:
            temp_path.unlink(missing_ok=True)
            if backup_temp:
                backup_temp.unlink(missing_ok=True)

    def load(self) -> StateLoadResult:
        if not self.path.exists():
            return StateLoadResult(
                status=StateLoadStatus.OK,
                value=self.default_factory() if self.default_factory else None,
                revision=0,
                presence=StatePresence.MISSING if self.workspace_envelope else None,
            )
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state document must be a mapping")
            schema_version = int(raw.get("schema_version", 0))
            if schema_version > self.supported_schema_version:
                return StateLoadResult(
                    status=StateLoadStatus.UNSUPPORTED_SCHEMA,
                    revision=raw.get("revision"),
                    error=f"schema_version {schema_version} is newer than supported version",
                )
            if self.load_transform:
                raw = self.load_transform(raw)
            value = self.model_type.model_validate(raw)
            if self.workspace_envelope:
                presence = StatePresence(value.state)
                return StateLoadResult(
                    status=StateLoadStatus.OK,
                    presence=presence,
                    value=value if presence == StatePresence.PRESENT else None,
                    revision=getattr(value, "revision", 0),
                )
            return StateLoadResult(
                status=StateLoadStatus.OK,
                value=value,
                revision=getattr(value, "revision", 0),
            )
        except (OSError, ValueError, TypeError, yaml.YAMLError, ValidationError) as exc:
            return StateLoadResult(status=StateLoadStatus.CORRUPT, error=type(exc).__name__)

    def load_backup(self) -> StateLoadResult:
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        if not backup.exists():
            return StateLoadResult(status=StateLoadStatus.CORRUPT, error="backup_missing")
        original = self.path
        self.path = backup
        try:
            return self.load()
        finally:
            self.path = original

    def write(self, value: T, *, expected_revision: int | None = None) -> StateWriteResult:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.lock_path), timeout=5):
                current = self.load()
                if current.status != StateLoadStatus.OK:
                    return StateWriteResult(
                        status=StateWriteStatus.FAILED, error=current.status.value
                    )
                current_revision = current.revision or 0
                if expected_revision is not None and current_revision != expected_revision:
                    return StateWriteResult(
                        status=StateWriteStatus.REVISION_CONFLICT,
                        revision=current_revision,
                        error="revision changed while the document was being updated",
                    )
                self._fail("validate")
                next_revision = current_revision + 1
                value = value.model_copy(
                    update={"revision": next_revision, "updated_at": utc_now()}
                )
                value = self.model_type.model_validate(value)
                data = yaml.safe_dump(
                    value.model_dump(mode="json", exclude_none=self.workspace_envelope),
                    allow_unicode=True,
                    sort_keys=False,
                ).encode("utf-8")
                self._publish(data)
                return StateWriteResult(
                    status=StateWriteStatus.OK, value=value, revision=next_revision
                )
        except Timeout:
            return StateWriteResult(status=StateWriteStatus.FAILED, error="state is busy")
        except (OSError, ValidationError, ValueError) as exc:
            return StateWriteResult(status=StateWriteStatus.FAILED, error=type(exc).__name__)

    def clear(self, value: T, *, expected_revision: int | None = None) -> StateWriteResult:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.lock_path), timeout=5):
                current = self.load()
                if current.status != StateLoadStatus.OK:
                    return StateWriteResult(
                        status=StateWriteStatus.FAILED, error=current.status.value
                    )
                revision = current.revision or 0
                if expected_revision is not None and revision != expected_revision:
                    return StateWriteResult(
                        status=StateWriteStatus.REVISION_CONFLICT,
                        revision=revision,
                        error="revision changed while the document was being cleared",
                    )
                if current.presence in {StatePresence.MISSING, StatePresence.CLEARED}:
                    return StateWriteResult(status=StateWriteStatus.OK, revision=revision)
                return self._write_locked(value, revision)
        except Timeout:
            return StateWriteResult(status=StateWriteStatus.FAILED, error="state is busy")
        except (OSError, ValidationError, ValueError) as exc:
            return StateWriteResult(status=StateWriteStatus.FAILED, error=type(exc).__name__)


class GlobalConfigYamlStore:
    def __init__(
        self, root: Path, *, failure_injector: Callable[[str], None] | None = None
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        locks = root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        self.document = YamlDocument(
            root / "config.yaml",
            GlobalConfig,
            default_factory=GlobalConfig,
            lock_path=locks / "config.lock",
            failure_injector=failure_injector,
        )

    def load(self) -> StateLoadResult:
        return self.document.load()

    def update(
        self, mutator: Callable[[GlobalConfig], GlobalConfig], expected_revision: int | None = None
    ) -> StateWriteResult:
        try:
            self.document.path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.document.lock_path), timeout=5):
                current = self.document.load()
                if current.status != StateLoadStatus.OK:
                    return StateWriteResult(
                        status=StateWriteStatus.FAILED, error=current.status.value
                    )
                revision = current.revision or 0
                if expected_revision is not None and revision != expected_revision:
                    return StateWriteResult(
                        status=StateWriteStatus.REVISION_CONFLICT, revision=revision
                    )
                updated = mutator(current.value)
                return self.document._write_locked(updated, revision)
        except Timeout:
            return StateWriteResult(status=StateWriteStatus.FAILED, error="state is busy")


class WorkspaceIndexYamlStore:
    def __init__(
        self, root: Path, *, failure_injector: Callable[[str], None] | None = None
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        locks = root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        self.document = YamlDocument(
            root / "workspace-index.yaml",
            WorkspaceIndex,
            default_factory=WorkspaceIndex,
            lock_path=locks / "workspace-index.lock",
            failure_injector=failure_injector,
        )

    def load(self) -> StateLoadResult:
        return self.document.load()

    def update(
        self,
        mutator: Callable[[WorkspaceIndex], WorkspaceIndex],
        expected_revision: int | None = None,
    ) -> StateWriteResult:
        try:
            with FileLock(str(self.document.lock_path), timeout=5):
                current = self.document.load()
                if current.status != StateLoadStatus.OK:
                    return StateWriteResult(
                        status=StateWriteStatus.FAILED, error=current.status.value
                    )
                revision = current.revision or 0
                if expected_revision is not None and revision != expected_revision:
                    return StateWriteResult(
                        status=StateWriteStatus.REVISION_CONFLICT, revision=revision
                    )
                return self.document._write_locked(mutator(current.value), revision)
        except Timeout:
            return StateWriteResult(status=StateWriteStatus.FAILED, error="state is busy")

    def transact(
        self,
        mutator: Callable[[WorkspaceIndex], tuple[WorkspaceIndex | None, R]],
    ) -> tuple[StateWriteResult, R | None]:
        """Decide an index mutation and its domain result under one lock."""
        try:
            with FileLock(str(self.document.lock_path), timeout=5):
                current = self.document.load()
                if current.status != StateLoadStatus.OK:
                    return (
                        StateWriteResult(
                            status=StateWriteStatus.FAILED, error=current.status.value
                        ),
                        None,
                    )
                updated, outcome = mutator(current.value)
                revision = current.revision or 0
                if updated is None:
                    return (
                        StateWriteResult(
                            status=StateWriteStatus.OK,
                            value=current.value,
                            revision=revision,
                        ),
                        outcome,
                    )
                return self.document._write_locked(updated, revision), outcome
        except Timeout:
            return StateWriteResult(status=StateWriteStatus.FAILED, error="state is busy"), None


def _write_locked(self: YamlDocument, value: BaseModel, current_revision: int) -> StateWriteResult:
    """Shared implementation called while the caller holds the document lock."""
    try:
        self._fail("validate")
        value = self.model_type.model_validate(
            value.model_copy(update={"revision": current_revision + 1, "updated_at": utc_now()})
        )
        data = yaml.safe_dump(
            value.model_dump(mode="json", exclude_none=self.workspace_envelope),
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        self._publish(data)
        return StateWriteResult(
            status=StateWriteStatus.OK, value=value, revision=current_revision + 1
        )
    except (OSError, ValidationError, ValueError) as exc:
        return StateWriteResult(status=StateWriteStatus.FAILED, error=type(exc).__name__)


YamlDocument._write_locked = _write_locked  # type: ignore[attr-defined]


def _load_workspace_envelope(raw: dict) -> dict:
    if int(raw.get("schema_version", 0)) == 1 and "state" not in raw:
        return {**raw, "schema_version": WORKSPACE_DOCUMENT_SCHEMA_VERSION, "state": "present"}
    return raw


class ProjectStateYamlStore:
    """Narrow workspace state facade; every operation requires workspace_id."""

    def __init__(
        self, root: Path, *, failure_injector: Callable[[str], None] | None = None
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks = root / "locks"
        self.locks.mkdir(parents=True, exist_ok=True)
        self.failure_injector = failure_injector

    def _document(
        self,
        workspace_id: str,
        name: str,
        model_type: type[T],
    ) -> YamlDocument:
        if not workspace_id or "/" in workspace_id or "\\" in workspace_id:
            raise ValueError("invalid workspace_id")
        directory = self.root / "workspaces" / workspace_id
        return YamlDocument(
            directory / name,
            model_type,
            lock_path=self.locks / f"{workspace_id}-{name}.lock",
            failure_injector=self.failure_injector,
            supported_schema_version=WORKSPACE_DOCUMENT_SCHEMA_VERSION,
            load_transform=_load_workspace_envelope,
            workspace_envelope=True,
        )

    def load_preferences(self, workspace_id: str) -> StateLoadResult:
        return self._document(workspace_id, "preferences.yaml", ProjectPreferencesDocument).load()

    def load_profile(self, workspace_id: str) -> StateLoadResult:
        return self._document(workspace_id, "profile.yaml", ProfileDocument).load()

    def load_preferences_backup(self, workspace_id: str) -> StateLoadResult:
        return self._document(
            workspace_id, "preferences.yaml", ProjectPreferencesDocument
        ).load_backup()

    def load_profile_backup(self, workspace_id: str) -> StateLoadResult:
        return self._document(workspace_id, "profile.yaml", ProfileDocument).load_backup()

    def write_preferences(
        self, workspace_id: str, value: Preferences, expected_revision: int | None = None
    ) -> StateWriteResult:
        existing = self.load_preferences(workspace_id)
        document = self._document(workspace_id, "preferences.yaml", ProjectPreferencesDocument)
        return document.write(
            ProjectPreferencesDocument(preferences=value),
            expected_revision=expected_revision
            if expected_revision is not None
            else existing.revision,
        )

    def write_profile(
        self, workspace_id: str, value: Profile, expected_revision: int | None = None
    ) -> StateWriteResult:
        existing = self.load_profile(workspace_id)
        document = self._document(workspace_id, "profile.yaml", ProfileDocument)
        if expected_revision is None and existing.status == StateLoadStatus.OK:
            expected_revision = existing.revision
        return document.write(ProfileDocument(profile=value), expected_revision=expected_revision)

    def clear_profile(
        self, workspace_id: str, expected_revision: int | None = None
    ) -> StateWriteResult:
        return self._clear_document(
            workspace_id, "profile.yaml", ProfileDocument, expected_revision
        )

    def clear_preferences(
        self, workspace_id: str, expected_revision: int | None = None
    ) -> StateWriteResult:
        return self._clear_document(
            workspace_id, "preferences.yaml", ProjectPreferencesDocument, expected_revision
        )

    def _clear_document(
        self,
        workspace_id: str,
        name: str,
        model_type: type[BaseModel],
        expected_revision: int | None,
    ) -> StateWriteResult:
        document = self._document(workspace_id, name, model_type)
        cleared = model_type.model_validate(
            {
                "schema_version": WORKSPACE_DOCUMENT_SCHEMA_VERSION,
                "state": "cleared",
            }
        )
        return document.clear(cleared, expected_revision=expected_revision)
