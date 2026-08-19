"""SQLite adapter for the Stage 4 v1 Operational Store foundation."""

from __future__ import annotations

import os
import random
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from filelock import FileLock, Timeout

from morrow.adapters.state.migrations import (
    MigrationRegistry,
    SchemaMigration,
    identity_insert_sql,
    identity_version_sql,
    migration_insert_sql,
    production_registry,
)
from morrow.core.store import (
    APPLICATION_ID,
    APPLICATION_NAME,
    BUSY_TIMEOUT_MS,
    DIRECTORY_MODE,
    FILE_MODE,
    WRITE_RETRY_ATTEMPTS,
    BackupReport,
    MigrationReport,
    OperationalStoreLayout,
    StorageError,
    StorageErrorCode,
    StoreClassification,
    StoreClock,
    StoreHealth,
    StoreIdentity,
    StoreOpenMode,
)

SQLITE_HEADER = b"SQLite format 3\x00"
_BUSY_CODES = {
    getattr(sqlite3, "SQLITE_BUSY", 5),
    getattr(sqlite3, "SQLITE_LOCKED", 6),
}


class SystemStoreClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class BusyRetryPolicy:
    attempts: int = WRITE_RETRY_ATTEMPTS
    busy_timeout_ms: int = BUSY_TIMEOUT_MS
    sleep: Callable[[float], None] = field(default=time.sleep)
    rng: random.Random = field(default_factory=random.Random)

    def delay_seconds(self, failed_attempt_index: int) -> float:
        cap = 0.032
        ceiling = min(cap, 0.001 * (2**failed_attempt_index))
        return self.rng.uniform(0.0, ceiling)


def is_busy_or_locked(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return code in _BUSY_CODES
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def translate_sqlite_error(exc: sqlite3.Error) -> StorageError:
    if is_busy_or_locked(exc):
        return StorageError(StorageErrorCode.BUSY, "operational store write contended")
    message = str(exc).lower()
    if "readonly" in message:
        return StorageError(StorageErrorCode.UNAVAILABLE, "operational store is not writable")
    if "full" in message or "disk" in message or "no space" in message:
        return StorageError(StorageErrorCode.UNAVAILABLE, "operational store could not be written")
    if isinstance(exc, sqlite3.IntegrityError):
        return StorageError(
            StorageErrorCode.UNAVAILABLE, "operational store rejected the statement"
        )
    return StorageError(StorageErrorCode.UNAVAILABLE, "operational store statement failed")


def run_with_busy_retry[T](work: Callable[[], T], policy: BusyRetryPolicy) -> T:
    last_error: BaseException | None = None
    for index in range(policy.attempts):
        try:
            return work()
        except StorageError as exc:
            last_error = exc
            if exc.code != StorageErrorCode.BUSY or index == policy.attempts - 1:
                raise
        except sqlite3.Error as exc:
            last_error = exc
            if not is_busy_or_locked(exc) or index == policy.attempts - 1:
                if is_busy_or_locked(exc):
                    raise StorageError(
                        StorageErrorCode.BUSY, "operational store write contended"
                    ) from exc
                raise translate_sqlite_error(exc) from exc
        policy.sleep(policy.delay_seconds(index))
    if isinstance(last_error, StorageError):
        raise last_error
    raise StorageError(StorageErrorCode.BUSY, "operational store write contended") from last_error


def rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def posix_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def restrict_path(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
        if posix_mode(path) != mode:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational path mode could not be verified"
            )
    except OSError as exc:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE, "operational path mode could not be verified"
        ) from exc


def _sqlite_uri(path: Path, *, immutable: bool = False) -> str:
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    return f"file:{quote(path.as_posix(), safe='/')}?{query}"


def _is_readonly_write_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "readonly" in str(exc).lower()


class SqliteExecutor:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self, sql: str, parameters: Sequence[object] = ()
    ) -> tuple[tuple[object, ...], ...]:
        try:
            cursor = self._connection.execute(sql, tuple(parameters))
            rows = cursor.fetchall()
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc) from exc
        return tuple(tuple(row) for row in rows)


class OperationalMaintenanceLock:
    def __init__(self, layout: OperationalStoreLayout, *, timeout: float = 0.1) -> None:
        self.path = layout.maintenance_lock
        self.timeout = timeout
        self._lock = FileLock(str(self.path), timeout=timeout)

    def acquire(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            ) from exc
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise StorageError(
                StorageErrorCode.BUSY, "operational store maintenance is already running"
            ) from exc
        if self.path.exists():
            try:
                restrict_path(self.path, FILE_MODE)
            except StorageError:
                self._lock.release()
                raise

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> OperationalMaintenanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


class OperationalStoreSession:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        mode: StoreOpenMode,
        health: StoreHealth,
        schema_version: int,
        retry_policy: BusyRetryPolicy,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.mode = mode
        self.health = health
        self.schema_version = schema_version
        self._connection = connection
        self._retry_policy = retry_policy
        self._failure_injector = failure_injector
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    def _fail(self, point: str) -> None:
        if self._failure_injector:
            self._failure_injector(point)

    def _assert_open_owner(self) -> None:
        if self._closed:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational store is closed")
        if threading.get_ident() != self._owner_thread_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store connection is bound to its owner thread",
            )

    def run_read[T](self, work: Callable[[SqliteExecutor], T]) -> T:
        self._assert_open_owner()
        try:
            self._connection.execute("PRAGMA query_only = ON")
            try:
                return work(SqliteExecutor(self._connection))
            finally:
                if self.mode in {StoreOpenMode.READ_WRITE, StoreOpenMode.CREATE}:
                    self._connection.execute("PRAGMA query_only = OFF")
        except sqlite3.Error as exc:
            raise translate_sqlite_error(exc) from exc

    def run_write[T](self, work: Callable[[SqliteExecutor], T]) -> T:
        self._assert_open_owner()
        if self.mode not in {StoreOpenMode.READ_WRITE, StoreOpenMode.CREATE}:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational store is not writable")

        def attempt() -> T:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise translate_sqlite_error(exc) from exc
            try:
                self._fail("begin")
                result = work(SqliteExecutor(self._connection))
                self._fail("before_commit")
                self._connection.execute("COMMIT")
                self._fail("after_commit")
                return result
            except Exception:
                rollback_quietly(self._connection)
                raise

        return run_with_busy_retry(attempt, self._retry_policy)

    def close(self) -> None:
        if self._closed:
            return
        if threading.get_ident() != self._owner_thread_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store connection is bound to its owner thread",
            )
        rollback_quietly(self._connection)
        self._connection.close()
        self._closed = True

    def __enter__(self) -> OperationalStoreSession:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class OperationalStore:
    def __init__(
        self,
        data_root: Path,
        *,
        clock: StoreClock | None = None,
        retry_policy: BusyRetryPolicy | None = None,
        maintenance_timeout: float = 0.1,
        failure_injector: Callable[[str], None] | None = None,
        registry: MigrationRegistry | None = None,
    ) -> None:
        self.layout = OperationalStoreLayout.from_root(data_root)
        self.clock = clock or SystemStoreClock()
        self.retry_policy = retry_policy or BusyRetryPolicy()
        self.maintenance_timeout = maintenance_timeout
        self.failure_injector = failure_injector
        self.registry = registry or production_registry()

    def _fail(self, point: str) -> None:
        if self.failure_injector:
            self.failure_injector(point)

    def maintenance_lock(self) -> OperationalMaintenanceLock:
        return OperationalMaintenanceLock(self.layout, timeout=self.maintenance_timeout)

    def classify(self) -> StoreClassification:
        database = self.layout.database
        if not database.exists():
            return StoreClassification(
                present=False, health=None, error_code=StorageErrorCode.NOT_FOUND
            )
        try:
            header = _read_header(database)
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be read"
            ) from exc
        if header != SQLITE_HEADER:
            return StoreClassification(
                present=True,
                health=StoreHealth.NEEDS_REPAIR,
                error_code=StorageErrorCode.NEEDS_REPAIR,
            )
        connection = None
        try:
            connection = self._connect(read_only=True)
            return self._classify_connection(connection, full_integrity=False)
        except StorageError:
            raise
        except sqlite3.Error as exc:
            if is_busy_or_locked(exc):
                raise StorageError(
                    StorageErrorCode.BUSY, "operational store write contended"
                ) from exc
            return StoreClassification(
                present=True,
                health=StoreHealth.NEEDS_REPAIR,
                error_code=StorageErrorCode.NEEDS_REPAIR,
            )
        finally:
            if connection is not None:
                connection.close()

    def open(self, mode: StoreOpenMode) -> OperationalStoreSession:
        if mode is StoreOpenMode.CREATE:
            with self.maintenance_lock():
                return self._initialize_locked()
        if mode is StoreOpenMode.READ_WRITE:
            return self._open_read_write()
        if mode is StoreOpenMode.READ_ONLY:
            return self._open_inspect(mode, full_integrity=False)
        return self._open_inspect(StoreOpenMode.DIAGNOSE, full_integrity=True)

    def initialize(self) -> OperationalStoreSession:
        return self.open(StoreOpenMode.CREATE)

    def migrate(self) -> MigrationReport:
        with self.maintenance_lock():
            return self._migrate_locked()

    def backup(self, destination_name: str | None = None) -> BackupReport:
        with self.maintenance_lock():
            return self._backup_locked(destination_name)

    def ensure_layout(self) -> None:
        directories = (
            self.layout.store_dir,
            self.layout.artifacts_dir,
            self.layout.artifacts_tmp,
            self.layout.backups_dir,
        )
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                restrict_path(directory, DIRECTORY_MODE)
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            ) from exc
        self._fail("layout")

    def _open_read_write(self) -> OperationalStoreSession:
        self._require_present_header()
        if not os.access(self.layout.database, os.W_OK) or not os.access(
            self.layout.store_dir, os.W_OK
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational store is not writable")
        connection = None
        try:
            connection = self._connect(read_only=False)
            _apply_session_pragmas(connection, self.retry_policy.busy_timeout_ms)
            classified = self._classify_connection(connection, full_integrity=False)
            _raise_if_unhealthy(classified, allow_read_only=False)
            _enable_wal(connection)
            _restrict_sidecars(self.layout)
            restrict_path(self.layout.database, FILE_MODE)
            return OperationalStoreSession(
                connection,
                mode=StoreOpenMode.READ_WRITE,
                health=StoreHealth.OK,
                schema_version=classified.schema_version or 0,
                retry_policy=self.retry_policy,
                failure_injector=self.failure_injector,
            )
        except StorageError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise translate_sqlite_error(exc) from exc
        except OSError as exc:
            if connection is not None:
                connection.close()
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            ) from exc

    def _open_inspect(
        self, mode: StoreOpenMode, *, full_integrity: bool
    ) -> OperationalStoreSession:
        self._require_present_header()
        connection = None
        try:
            connection = self._connect(read_only=True)
            _apply_session_pragmas(connection, self.retry_policy.busy_timeout_ms, writable=False)
            classified = self._classify_connection(connection, full_integrity=full_integrity)
            if mode is StoreOpenMode.READ_ONLY:
                _raise_if_unhealthy(classified, allow_read_only=True)
                health = (
                    StoreHealth.READ_ONLY
                    if not os.access(self.layout.database, os.W_OK)
                    else StoreHealth.OK
                )
                if classified.health is StoreHealth.FUTURE_SCHEMA:
                    raise StorageError(
                        StorageErrorCode.FUTURE_SCHEMA, _message_for(StorageErrorCode.FUTURE_SCHEMA)
                    )
                if not _header_is_wal(self.layout.database):
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE, "operational store is not in WAL mode"
                    )
                return OperationalStoreSession(
                    connection,
                    mode=mode,
                    health=health,
                    schema_version=classified.schema_version or 0,
                    retry_policy=self.retry_policy,
                    failure_injector=self.failure_injector,
                )
            health = classified.health or StoreHealth.NEEDS_REPAIR
            return OperationalStoreSession(
                connection,
                mode=mode,
                health=health,
                schema_version=classified.schema_version or 0,
                retry_policy=self.retry_policy,
                failure_injector=self.failure_injector,
            )
        except StorageError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            if is_busy_or_locked(exc):
                raise StorageError(
                    StorageErrorCode.BUSY, "operational store write contended"
                ) from exc
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, _message_for(StorageErrorCode.NEEDS_REPAIR)
            ) from exc

    def _initialize_locked(self) -> OperationalStoreSession:
        database = self.layout.database
        if database.exists():
            classification = self.classify()
            if classification.ok:
                return self._open_read_write()
            code = classification.error_code or StorageErrorCode.NEEDS_REPAIR
            raise StorageError(code, _message_for(code))
        self.ensure_layout()
        connection = None
        try:
            connection = self._connect(read_only=False, missing_ok=True)
            _apply_session_pragmas(connection, self.retry_policy.busy_timeout_ms)
            _enable_wal(connection)
            created_at = int(self.clock.now().timestamp())
            self._fail("begin")
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._apply_v1(connection, created_at)
                self._fail("before_commit")
                connection.execute("COMMIT")
            except Exception:
                rollback_quietly(connection)
                raise
            self._fail("after_commit")
            for migration in self.registry.pending(1):
                self._apply_migration(connection, migration)
            _full_integrity(connection)
            restrict_path(database, FILE_MODE)
            _restrict_sidecars(self.layout)
            return OperationalStoreSession(
                connection,
                mode=StoreOpenMode.CREATE,
                health=StoreHealth.OK,
                schema_version=self.registry.supported_version,
                retry_policy=self.retry_policy,
                failure_injector=self.failure_injector,
            )
        except StorageError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise translate_sqlite_error(exc) from exc
        except OSError as exc:
            if connection is not None:
                connection.close()
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            ) from exc

    def _apply_v1(self, connection: sqlite3.Connection, created_at: int) -> None:
        migration = self.registry.get(1)
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {int(migration.version)}")
        connection.execute(
            identity_insert_sql(),
            (APPLICATION_NAME, migration.version, created_at),
        )
        connection.execute(
            migration_insert_sql(),
            (migration.version, migration.name, migration.checksum, created_at),
        )

    def _migrate_locked(self) -> MigrationReport:
        classification = self._require_present_header()
        if classification.error_code in {
            StorageErrorCode.NEEDS_REPAIR,
            StorageErrorCode.IDENTITY_MISMATCH,
            StorageErrorCode.FUTURE_SCHEMA,
        }:
            raise StorageError(classification.error_code, _message_for(classification.error_code))
        connection = None
        try:
            connection = self._connect(read_only=False)
            _apply_session_pragmas(connection, self.retry_policy.busy_timeout_ms)
            classified = self._classify_connection(connection, full_integrity=True)
            _raise_if_unhealthy(classified, allow_read_only=False)
            current = classified.schema_version or 0
            pending = self.registry.pending(current)
            if not pending:
                _enable_wal(connection)
                connection.close()
                return MigrationReport(
                    from_version=current,
                    to_version=current,
                    applied=(),
                    health=StoreHealth.OK,
                )
            backup = self._backup_locked()
            applied: list[str] = []
            for migration in pending:
                self._apply_migration(connection, migration)
                applied.append(migration.name)
            _enable_wal(connection)
            _full_integrity(connection)
            _restrict_sidecars(self.layout)
            restrict_path(self.layout.database, FILE_MODE)
            connection.close()
            connection = None
            return MigrationReport(
                from_version=current,
                to_version=self.registry.supported_version,
                applied=tuple(applied),
                health=StoreHealth.OK,
                backup_name=backup.destination_name,
            )
        except Exception:
            if connection is not None:
                connection.close()
            raise

    def _apply_migration(self, connection: sqlite3.Connection, migration: SchemaMigration) -> None:
        applied_at = int(self.clock.now().timestamp())
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._fail("begin")
            for statement in migration.statements:
                connection.execute(statement)
            if migration.version == 1:
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(
                    identity_insert_sql(),
                    (APPLICATION_NAME, migration.version, applied_at),
                )
            else:
                connection.execute(identity_version_sql(), (migration.version,))
            connection.execute(f"PRAGMA user_version = {int(migration.version)}")
            connection.execute(
                migration_insert_sql(),
                (migration.version, migration.name, migration.checksum, applied_at),
            )
            self._fail("before_migration_commit")
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            rollback_quietly(connection)
            raise translate_sqlite_error(exc) from exc
        except Exception:
            rollback_quietly(connection)
            raise

    def _backup_locked(self, destination_name: str | None = None) -> BackupReport:
        classification = self._require_present_header()
        if classification.error_code is not None and classification.error_code not in {
            StorageErrorCode.FUTURE_SCHEMA,
        }:
            # Backup of a future schema is allowed as a safe copy; corrupt files are not.
            if classification.error_code is not StorageErrorCode.FUTURE_SCHEMA:
                raise StorageError(
                    classification.error_code, _message_for(classification.error_code)
                )
        self.ensure_layout()
        name = _backup_name(destination_name, self.clock)
        destination = self.layout.backups_dir / name
        if destination.exists():
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational backup already exists")
        source = None
        target = None
        try:
            source = self._connect(read_only=True)
            _apply_session_pragmas(source, self.retry_policy.busy_timeout_ms, writable=False)
            classified = self._classify_connection(source, full_integrity=False)
            if classified.error_code in {
                StorageErrorCode.NEEDS_REPAIR,
                StorageErrorCode.IDENTITY_MISMATCH,
            }:
                raise StorageError(classified.error_code, _message_for(classified.error_code))
            target = sqlite3.connect(
                destination,
                isolation_level=None,
                timeout=0,
                check_same_thread=True,
            )
            source.backup(target)
            self._fail("after_backup_copy")
            _apply_session_pragmas(target, self.retry_policy.busy_timeout_ms)
            _full_integrity(target)
            identity = _read_identity(target)
            target.close()
            target = None
            restrict_path(destination, FILE_MODE)
            return BackupReport(
                health=StoreHealth.OK,
                schema_version=identity.user_version,
                destination_name=name,
                integrity_ok=True,
            )
        except StorageError:
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise
        except sqlite3.Error as exc:
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise translate_sqlite_error(exc) from exc
        except OSError as exc:
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be written"
            ) from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

    def _require_present_header(self) -> StoreClassification:
        database = self.layout.database
        if not database.exists():
            raise StorageError(StorageErrorCode.NOT_FOUND, _message_for(StorageErrorCode.NOT_FOUND))
        try:
            header = _read_header(database)
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not be read"
            ) from exc
        if header != SQLITE_HEADER:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, _message_for(StorageErrorCode.NEEDS_REPAIR)
            )
        return self.classify()

    def _connect(self, *, read_only: bool, missing_ok: bool = False) -> sqlite3.Connection:
        database = self.layout.database
        existed = database.exists()
        if not existed and (read_only or not missing_ok):
            raise StorageError(StorageErrorCode.NOT_FOUND, _message_for(StorageErrorCode.NOT_FOUND))
        if existed:
            try:
                header = _read_header(database)
            except OSError as exc:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational store could not be read"
                ) from exc
            if header != SQLITE_HEADER:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR, _message_for(StorageErrorCode.NEEDS_REPAIR)
                )
        self._fail("connect")
        try:
            if read_only:
                immutable = not os.access(database.parent, os.W_OK) and not (
                    self.layout.wal.exists() and os.access(self.layout.wal, os.R_OK)
                )
                connection = sqlite3.connect(
                    _sqlite_uri(database, immutable=immutable),
                    uri=True,
                    isolation_level=None,
                    timeout=0,
                    check_same_thread=True,
                )
            else:
                connection = sqlite3.connect(
                    database,
                    isolation_level=None,
                    timeout=0,
                    check_same_thread=True,
                )
        except sqlite3.Error as exc:
            if is_busy_or_locked(exc):
                raise StorageError(
                    StorageErrorCode.BUSY, "operational store write contended"
                ) from exc
            if _is_readonly_write_error(exc):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational store is not writable"
                ) from exc
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, _message_for(StorageErrorCode.NEEDS_REPAIR)
            ) from exc
        connection.row_factory = None
        return connection

    def _classify_connection(
        self, connection: sqlite3.Connection, *, full_integrity: bool
    ) -> StoreClassification:
        if full_integrity:
            try:
                _full_integrity(connection)
            except StorageError as exc:
                if exc.code is StorageErrorCode.NEEDS_REPAIR:
                    return StoreClassification(
                        present=True,
                        health=StoreHealth.NEEDS_REPAIR,
                        error_code=StorageErrorCode.NEEDS_REPAIR,
                    )
                raise
        try:
            identity = _read_identity(connection)
        except sqlite3.Error as exc:
            if is_busy_or_locked(exc):
                raise StorageError(
                    StorageErrorCode.BUSY, "operational store write contended"
                ) from exc
            return StoreClassification(
                present=True,
                health=StoreHealth.NEEDS_REPAIR,
                error_code=StorageErrorCode.NEEDS_REPAIR,
            )
        return _classify_identity_with_checksums(connection, identity, self.registry)


def _read_header(database: Path) -> bytes:
    with database.open("rb") as handle:
        return handle.read(len(SQLITE_HEADER))


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def _read_identity(connection: sqlite3.Connection) -> StoreIdentity:
    application_id = _pragma_int(connection, "application_id")
    user_version = _pragma_int(connection, "user_version")
    try:
        row = connection.execute(
            "SELECT application_name, schema_version FROM store_identity WHERE singleton = 1"
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        if is_busy_or_locked(exc):
            raise
        return StoreIdentity(
            application_id=application_id,
            user_version=user_version,
            application_name=None,
            schema_version=None,
        )
    if row is None:
        return StoreIdentity(
            application_id=application_id,
            user_version=user_version,
            application_name=None,
            schema_version=None,
        )
    return StoreIdentity(
        application_id=application_id,
        user_version=user_version,
        application_name=str(row[0]),
        schema_version=int(row[1]),
    )


def _classify_identity(identity: StoreIdentity, registry: MigrationRegistry) -> StoreClassification:
    if (
        identity.application_id == 0
        and identity.application_name is None
        and identity.user_version == 0
        and identity.schema_version is None
    ):
        return StoreClassification(
            present=True,
            health=StoreHealth.NEEDS_REPAIR,
            error_code=StorageErrorCode.NEEDS_REPAIR,
        )
    if identity.application_id != APPLICATION_ID or identity.application_name != APPLICATION_NAME:
        return StoreClassification(
            present=True,
            health=StoreHealth.NEEDS_REPAIR,
            error_code=StorageErrorCode.IDENTITY_MISMATCH,
            schema_version=identity.schema_version,
            application_name=identity.application_name,
        )
    if identity.schema_version is None or identity.user_version != identity.schema_version:
        return StoreClassification(
            present=True,
            health=StoreHealth.NEEDS_REPAIR,
            error_code=StorageErrorCode.NEEDS_REPAIR,
            schema_version=identity.schema_version,
            application_name=identity.application_name,
        )
    if identity.user_version > registry.supported_version:
        return StoreClassification(
            present=True,
            health=StoreHealth.FUTURE_SCHEMA,
            error_code=StorageErrorCode.FUTURE_SCHEMA,
            schema_version=identity.user_version,
            application_name=identity.application_name,
        )
    return StoreClassification(
        present=True,
        health=StoreHealth.OK,
        error_code=None,
        schema_version=identity.user_version,
        application_name=identity.application_name,
    )


def _checksum_matches(connection: sqlite3.Connection, registry: MigrationRegistry) -> bool:
    try:
        rows = connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        if is_busy_or_locked(exc):
            raise
        return False
    for version, checksum in rows:
        expected = registry.checksum_for(int(version))
        if expected is not None and expected != str(checksum):
            return False
    return True


def _classify_identity_with_checksums(
    connection: sqlite3.Connection, identity: StoreIdentity, registry: MigrationRegistry
) -> StoreClassification:
    classified = _classify_identity(identity, registry)
    if classified.error_code is not None:
        return classified
    if not _checksum_matches(connection, registry):
        return StoreClassification(
            present=True,
            health=StoreHealth.NEEDS_REPAIR,
            error_code=StorageErrorCode.NEEDS_REPAIR,
            schema_version=classified.schema_version,
            application_name=classified.application_name,
        )
    return classified


def _apply_session_pragmas(
    connection: sqlite3.Connection, busy_timeout_ms: int, *, writable: bool = True
) -> None:
    try:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        if writable:
            connection.execute("PRAGMA synchronous = FULL")
    except sqlite3.Error as exc:
        raise translate_sqlite_error(exc) from exc


def _enable_wal(connection: sqlite3.Connection) -> None:
    try:
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    except sqlite3.Error as exc:
        raise translate_sqlite_error(exc) from exc
    if str(journal_mode).lower() != "wal":
        raise StorageError(StorageErrorCode.UNAVAILABLE, "operational store did not enter WAL mode")


def _header_is_wal(database: Path) -> bool:
    try:
        with database.open("rb") as handle:
            handle.seek(18)
            write_version = handle.read(1)
    except OSError:
        return False
    return write_version == b"\x02"


def _full_integrity(connection: sqlite3.Connection) -> None:
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, _message_for(StorageErrorCode.NEEDS_REPAIR)
        ) from exc
    if integrity != "ok" or foreign_keys:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, _message_for(StorageErrorCode.NEEDS_REPAIR)
        )


def _restrict_sidecars(layout: OperationalStoreLayout) -> None:
    for path in (layout.wal, layout.shm):
        if path.exists():
            restrict_path(path, FILE_MODE)


def _raise_if_unhealthy(classification: StoreClassification, *, allow_read_only: bool) -> None:
    if classification.error_code is None:
        return
    if allow_read_only and classification.error_code is StorageErrorCode.FUTURE_SCHEMA:
        return
    raise StorageError(classification.error_code, _message_for(classification.error_code))


def _message_for(code: StorageErrorCode) -> str:
    return {
        StorageErrorCode.BUSY: "operational store write contended",
        StorageErrorCode.FUTURE_SCHEMA: "operational store is newer than this binary",
        StorageErrorCode.IDENTITY_MISMATCH: "file is not a Morrow operational store",
        StorageErrorCode.NEEDS_REPAIR: "operational store is not a usable SQLite file",
        StorageErrorCode.NOT_FOUND: "operational store is missing",
        StorageErrorCode.UNAVAILABLE: "operational store is unavailable",
    }[code]


def _backup_name(destination_name: str | None, clock: StoreClock) -> str:
    if destination_name is None:
        return f"operational-{int(clock.now().timestamp())}.sqlite"
    candidate = Path(destination_name)
    if candidate.name != destination_name or candidate.suffix != ".sqlite":
        raise StorageError(StorageErrorCode.UNAVAILABLE, "operational backup name is invalid")
    if destination_name in {".", ".."} or "/" in destination_name or "\\" in destination_name:
        raise StorageError(StorageErrorCode.UNAVAILABLE, "operational backup name is invalid")
    return destination_name
