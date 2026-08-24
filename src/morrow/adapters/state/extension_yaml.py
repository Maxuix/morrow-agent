"""Atomic, versioned Extension YAML authority for Skill bindings."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from filelock import FileLock
from pydantic import ValidationError

from morrow.core.domain import canonical_json_bytes, validate_prefixed_id
from morrow.core.models import utc_now
from morrow.core.skills.bindings import (
    ExtensionDocument,
    GlobalExtensionDocument,
    WorkspaceExtensionDocument,
)

EXTENSION_YAML_SCHEMA_VERSION = 1
MAX_EXTENSION_YAML_BYTES = 2 * 1024 * 1024


class ExtensionYamlLoadStatus(StrEnum):
    OK = "ok"
    CORRUPT = "corrupt"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class ExtensionYamlError(RuntimeError):
    """Sanitized Extension YAML failure; payloads and paths never escape."""

    def __init__(self, code: str, message: str = "Extension YAML operation failed") -> None:
        super().__init__(message)
        self.code = code


class ExtensionYamlConflict(ExtensionYamlError):
    def __init__(self, message: str = "Extension YAML revision changed") -> None:
        super().__init__("revision_conflict", message)


@dataclass(frozen=True, slots=True)
class ExtensionYamlLoad:
    status: ExtensionYamlLoadStatus
    value: ExtensionDocument | None
    revision: int
    source_schema_version: int | None
    presence: str = "present"
    error: str | None = None

    @property
    def digest(self) -> str | None:
        if self.value is None:
            return None
        return extension_document_digest(self.value)


def extension_document_digest(value: ExtensionDocument) -> str:
    payload = value.model_dump(mode="json", by_alias=True)
    payload.pop("updated_at", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _read_bytes(path: Path) -> bytes:
    try:
        info = os.lstat(path)
        if not os.path.isfile(path) or os.path.islink(path) or info.st_nlink != 1:
            raise ExtensionYamlError("corrupt", "Extension YAML file is not a regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise
    except ExtensionYamlError:
        raise
    except OSError as exc:
        raise ExtensionYamlError("corrupt", "Extension YAML could not be read") from exc
    try:
        stat_result = os.fstat(fd)
        if stat_result.st_size > MAX_EXTENSION_YAML_BYTES:
            raise ExtensionYamlError("corrupt", "Extension YAML exceeds the byte budget")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_EXTENSION_YAML_BYTES:
                raise ExtensionYamlError("corrupt", "Extension YAML exceeds the byte budget")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise ExtensionYamlError("corrupt", "Extension YAML could not be read") from exc
    finally:
        os.close(fd)


def _read_raw(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(_read_bytes(path).decode("utf-8", errors="strict"))
    except FileNotFoundError:
        return None
    except (UnicodeDecodeError, yaml.YAMLError, ExtensionYamlError) as exc:
        if isinstance(exc, ExtensionYamlError):
            raise
        raise ExtensionYamlError("corrupt", "Extension YAML is not valid") from exc
    if not isinstance(raw, dict):
        raise ExtensionYamlError("corrupt", "Extension YAML must be a mapping")
    return raw


def _schema_version(raw: dict | None) -> int | None:
    if raw is None:
        return None
    value = raw.get("schema_version", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExtensionYamlError("corrupt", "Extension YAML schema is invalid")
    return value


class ExtensionYamlStore:
    """Independent global/workspace YAML documents with OCC and backups."""

    def __init__(
        self,
        root: Path,
        *,
        failure_injector: Callable[[str], None] | None = None,
        create: bool = True,
    ) -> None:
        self.root = root.expanduser()
        self.locks = self.root / "locks"
        self.failure_injector = failure_injector
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            self.locks.mkdir(parents=True, exist_ok=True)

    @property
    def global_path(self) -> Path:
        return self.root / "extensions.yaml"

    def workspace_path(self, workspace_id: str) -> Path:
        return (
            self.root / "workspaces" / validate_prefixed_id(workspace_id, "ws") / "extensions.yaml"
        )

    def _lock(self, scope: str, workspace_id: str | None) -> FileLock:
        name = "extensions-global.lock" if scope == "global" else f"{workspace_id}-extensions.lock"
        return FileLock(str(self.locks / name), timeout=5)

    def load_global(self) -> ExtensionYamlLoad:
        return self._load_path(self.global_path, expected_scope="global", expected_scope_id=None)

    def load_workspace(self, workspace_id: str) -> ExtensionYamlLoad:
        workspace_id = validate_prefixed_id(workspace_id, "ws")
        return self._load_path(
            self.workspace_path(workspace_id),
            expected_scope="workspace",
            expected_scope_id=workspace_id,
        )

    def load_global_backup(self) -> ExtensionYamlLoad:
        return self._load_path(
            self.global_path.with_suffix(".yaml.bak"),
            expected_scope="global",
            expected_scope_id=None,
            missing_error="backup_missing",
        )

    def load_workspace_backup(self, workspace_id: str) -> ExtensionYamlLoad:
        workspace_id = validate_prefixed_id(workspace_id, "ws")
        path = self.workspace_path(workspace_id)
        return self._load_path(
            path.with_suffix(".yaml.bak"),
            expected_scope="workspace",
            expected_scope_id=workspace_id,
            missing_error="backup_missing",
        )

    def _load_path(
        self,
        path: Path,
        *,
        expected_scope: str,
        expected_scope_id: str | None,
        missing_error: str | None = None,
    ) -> ExtensionYamlLoad:
        if not path.exists():
            if missing_error:
                return ExtensionYamlLoad(
                    ExtensionYamlLoadStatus.CORRUPT,
                    None,
                    0,
                    None,
                    error=missing_error,
                )
            value = (
                GlobalExtensionDocument()
                if expected_scope == "global"
                else WorkspaceExtensionDocument(scope_id=expected_scope_id or "")
            )
            return ExtensionYamlLoad(
                ExtensionYamlLoadStatus.OK,
                value,
                0,
                EXTENSION_YAML_SCHEMA_VERSION,
                presence="missing",
            )
        try:
            raw = _read_raw(path)
            schema = _schema_version(raw)
            if schema is not None and schema > EXTENSION_YAML_SCHEMA_VERSION:
                return ExtensionYamlLoad(
                    ExtensionYamlLoadStatus.UNSUPPORTED_SCHEMA,
                    None,
                    int(raw.get("revision", 0) or 0),
                    schema,
                    error="future_extension_schema",
                )
            model_type = (
                GlobalExtensionDocument
                if expected_scope == "global"
                else WorkspaceExtensionDocument
            )
            value = model_type.model_validate(raw or {})
            if value.scope != expected_scope or value.scope_id != expected_scope_id:
                raise ExtensionYamlError("corrupt", "Extension YAML scope does not match its path")
            return ExtensionYamlLoad(
                ExtensionYamlLoadStatus.OK,
                value,
                value.revision,
                schema,
            )
        except ExtensionYamlError as exc:
            return ExtensionYamlLoad(
                ExtensionYamlLoadStatus.CORRUPT,
                None,
                0,
                None,
                error=exc.code,
            )
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            return ExtensionYamlLoad(
                ExtensionYamlLoadStatus.CORRUPT,
                None,
                0,
                None,
                error=type(exc).__name__,
            )

    def write_global(
        self,
        value: GlobalExtensionDocument,
        *,
        expected_revision: int | None = None,
        expected_value_digest: str | None = None,
    ) -> ExtensionYamlLoad:
        return self._write(
            self.global_path,
            value,
            scope="global",
            workspace_id=None,
            expected_revision=expected_revision,
            expected_value_digest=expected_value_digest,
        )

    def write_workspace(
        self,
        workspace_id: str,
        value: WorkspaceExtensionDocument,
        *,
        expected_revision: int | None = None,
        expected_value_digest: str | None = None,
    ) -> ExtensionYamlLoad:
        workspace_id = validate_prefixed_id(workspace_id, "ws")
        return self._write(
            self.workspace_path(workspace_id),
            value,
            scope="workspace",
            workspace_id=workspace_id,
            expected_revision=expected_revision,
            expected_value_digest=expected_value_digest,
        )

    def _write(
        self,
        path: Path,
        value: ExtensionDocument,
        *,
        scope: str,
        workspace_id: str | None,
        expected_revision: int | None,
        expected_value_digest: str | None,
    ) -> ExtensionYamlLoad:
        if value.scope != scope or value.scope_id != workspace_id:
            raise ExtensionYamlError("scope", "Extension YAML value has the wrong scope")
        with self._lock(scope, workspace_id):
            current = self._load_path(
                path,
                expected_scope=scope,
                expected_scope_id=workspace_id,
            )
            if current.status is not ExtensionYamlLoadStatus.OK or current.value is None:
                raise ExtensionYamlError(
                    current.error or "unavailable", "Extension YAML is not writable"
                )
            if expected_revision is not None and current.revision != expected_revision:
                raise ExtensionYamlConflict()
            if expected_value_digest is not None and current.digest != expected_value_digest:
                raise ExtensionYamlConflict("Extension YAML value changed")
            next_value = value.model_copy(
                update={"revision": current.revision + 1, "updated_at": utc_now()}
            )
            data = yaml.safe_dump(
                next_value.model_dump(mode="json", by_alias=True, exclude_none=False),
                allow_unicode=True,
                sort_keys=False,
            ).encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if self.failure_injector:
                    self.failure_injector("backup")
                self._backup(path)
            self._publish(path, data)
            return self._load_path(path, expected_scope=scope, expected_scope_id=workspace_id)

    def _backup(self, path: Path) -> None:
        backup = path.with_suffix(path.suffix + ".bak")
        temporary = backup.with_name(f".{backup.name}.tmp")
        try:
            shutil.copyfile(path, temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, backup)
            self._fsync_directory(backup.parent)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ExtensionYamlError("backup_failed", "Extension YAML backup failed") from exc

    def _publish(self, path: Path, data: bytes) -> None:
        if self.failure_injector:
            self.failure_injector("temporary_write")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                if self.failure_injector:
                    self.failure_injector("fsync")
                os.fsync(handle.fileno())
            if self.failure_injector:
                self.failure_injector("replace")
            os.replace(temporary, path)
            if self.failure_injector:
                self.failure_injector("directory_fsync")
            self._fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            return
        try:
            try:
                os.fsync(fd)
            except OSError:
                return
        finally:
            os.close(fd)


__all__ = [
    "EXTENSION_YAML_SCHEMA_VERSION",
    "ExtensionYamlConflict",
    "ExtensionYamlError",
    "ExtensionYamlLoad",
    "ExtensionYamlLoadStatus",
    "ExtensionYamlStore",
    "extension_document_digest",
]
