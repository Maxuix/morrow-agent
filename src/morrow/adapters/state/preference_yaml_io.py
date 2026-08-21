"""Atomic filesystem primitives used by the Preference YAML store."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import yaml

from morrow.core.domain import canonical_json_bytes
from morrow.core.preference_documents import GlobalConfigV2, WorkspacePreferenceDocumentV3


class PreferenceYamlIoError(RuntimeError):
    """Bounded filesystem/parse failure without leaking paths or payloads."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fsync_directory(path: Path) -> None:
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


def raw_digest(raw: object) -> str:
    if raw is None:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(canonical_json_bytes(raw)).hexdigest()


def read_raw(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PreferenceYamlIoError("corrupt") from exc
    if not isinstance(raw, dict):
        raise PreferenceYamlIoError("corrupt")
    return raw


def write_bytes(path: Path, data: bytes, failure_injector: Callable[[str], None] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if failure_injector:
        failure_injector("temporary_write")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if failure_injector:
                failure_injector("fsync")
            os.fsync(handle.fileno())
        if failure_injector:
            failure_injector("replace")
        os.replace(temporary, path)
        if failure_injector:
            failure_injector("directory_fsync")
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def yaml_bytes(value: GlobalConfigV2 | WorkspacePreferenceDocumentV3) -> bytes:
    return yaml.safe_dump(
        value.model_dump(mode="json", exclude_none=False),
        allow_unicode=True,
        sort_keys=False,
    ).encode("utf-8")


def with_revision(
    value: GlobalConfigV2 | WorkspacePreferenceDocumentV3, revision: int
) -> GlobalConfigV2 | WorkspacePreferenceDocumentV3:
    return type(value).model_validate(
        value.model_copy(update={"revision": revision, "updated_at": datetime.now(UTC)})
    )


def backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = backup_path.with_name(f".{backup_path.name}.tmp")
    try:
        shutil.copyfile(path, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, backup_path)
        fsync_directory(backup_path.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise PreferenceYamlIoError("backup_failed") from exc


__all__ = [
    "PreferenceYamlIoError",
    "backup",
    "fsync_directory",
    "raw_digest",
    "read_raw",
    "with_revision",
    "write_bytes",
    "yaml_bytes",
]
