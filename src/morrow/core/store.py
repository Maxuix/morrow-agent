"""Operational Store contracts: paths, health, open modes, and sanitized errors.

Core stays free of sqlite3, file locks, and filesystem adapters. Identifiers and
error codes are the public boundary later subplans share.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeVar

STORE_DIRNAME = "store"
DATABASE_NAME = "operational.sqlite"
ARTIFACTS_DIRNAME = "artifacts"
ARTIFACTS_TMP_DIRNAME = "tmp"
BACKUPS_DIRNAME = "backups"
OPERATIONAL_BACKUPS_DIRNAME = "operational"
LOCKS_DIRNAME = "locks"
MAINTENANCE_LOCK_NAME = "operational-store.lock"

APPLICATION_ID = 0x4D4F5257
APPLICATION_NAME = "morrow-operational-store"
SUPPORTED_SCHEMA_VERSION = 9
RESERVED_SCHEMA_VERSIONS = frozenset(range(1, 10))
BUSY_TIMEOUT_MS = 250
WRITE_RETRY_ATTEMPTS = 8
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600

T = TypeVar("T")


class StoreOpenMode(StrEnum):
    CREATE = "create"
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"
    DIAGNOSE = "diagnose"


class StoreHealth(StrEnum):
    OK = "ok"
    NEEDS_REPAIR = "needs_repair"
    READ_ONLY = "read_only"
    FUTURE_SCHEMA = "future_schema"


class StorageErrorCode(StrEnum):
    BUSY = "busy"
    FUTURE_SCHEMA = "future_schema"
    IDENTITY_MISMATCH = "identity_mismatch"
    NEEDS_REPAIR = "needs_repair"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class StorageError(RuntimeError):
    """Public operational-store failure. Message must stay free of SQL and paths."""

    def __init__(self, code: StorageErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OperationalStoreLayout:
    data_root: Path
    store_dir: Path
    database: Path
    wal: Path
    shm: Path
    artifacts_dir: Path
    artifacts_tmp: Path
    backups_dir: Path
    maintenance_lock: Path

    @classmethod
    def from_root(cls, data_root: Path) -> OperationalStoreLayout:
        root = data_root.expanduser()
        database = root / STORE_DIRNAME / DATABASE_NAME
        artifacts = root / ARTIFACTS_DIRNAME
        return cls(
            data_root=root,
            store_dir=database.parent,
            database=database,
            wal=Path(str(database) + "-wal"),
            shm=Path(str(database) + "-shm"),
            artifacts_dir=artifacts,
            artifacts_tmp=artifacts / ARTIFACTS_TMP_DIRNAME,
            backups_dir=root / BACKUPS_DIRNAME / OPERATIONAL_BACKUPS_DIRNAME,
            maintenance_lock=root / LOCKS_DIRNAME / MAINTENANCE_LOCK_NAME,
        )


@dataclass(frozen=True)
class StoreClassification:
    present: bool
    health: StoreHealth | None
    error_code: StorageErrorCode | None
    schema_version: int | None = None
    application_name: str | None = None

    @property
    def ok(self) -> bool:
        return self.present and self.health == StoreHealth.OK and self.error_code is None


@dataclass(frozen=True)
class StoreIdentity:
    application_id: int
    user_version: int
    application_name: str | None
    schema_version: int | None


@dataclass(frozen=True)
class MigrationReport:
    from_version: int
    to_version: int
    applied: tuple[str, ...]
    health: StoreHealth
    backup_name: str | None = None


@dataclass(frozen=True)
class BackupReport:
    health: StoreHealth
    schema_version: int
    destination_name: str
    integrity_ok: bool


class StoreExecutor(Protocol):
    def execute(
        self, sql: str, parameters: Sequence[object] = ()
    ) -> tuple[tuple[object, ...], ...]: ...


class OperationalStoreSessionPort(Protocol):
    health: StoreHealth
    mode: StoreOpenMode
    schema_version: int

    def run_read(self, work: Callable[[StoreExecutor], T]) -> T: ...

    def run_write(self, work: Callable[[StoreExecutor], T]) -> T: ...

    def close(self) -> None: ...


class OperationalStorePort(Protocol):
    def classify(self) -> StoreClassification: ...

    def open(self, mode: StoreOpenMode) -> OperationalStoreSessionPort: ...


class OperationalMaintenancePort(Protocol):
    def initialize(self) -> OperationalStoreSessionPort: ...

    def migrate(self) -> MigrationReport: ...

    def backup(self, destination_name: str | None = None) -> BackupReport: ...


class StoreClock(Protocol):
    def now(self) -> datetime: ...
