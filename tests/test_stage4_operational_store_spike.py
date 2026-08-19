"""Disposable spike for the Stage 4 Operational Store ADR.

This is not a production adapter and must not be imported from ``src/morrow``.
Every test uses a task-private temporary data root and leaves no process state.
"""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

APPLICATION_ID = 0x4D4F5257
APPLICATION_NAME = "morrow-operational-store"
SUPPORTED_SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 250
WRITE_RETRY_ATTEMPTS = 8
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
SQLITE_HEADER = b"SQLite format 3\x00"


class SpikeStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OperationalStorePaths:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.store_dir = data_root / "store"
        self.database = self.store_dir / "operational.sqlite"
        self.artifacts_dir = data_root / "artifacts"
        self.artifacts_tmp = self.artifacts_dir / "tmp"
        self.backups_dir = data_root / "backups" / "operational"
        self.locks_dir = data_root / "locks"
        self.maintenance_lock = self.locks_dir / "operational-store.lock"


def _posix_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _restrict(path: Path, mode: int) -> None:
    path.chmod(mode)
    if _posix_mode(path) != mode:
        raise SpikeStoreError("unavailable", "operational path mode could not be verified")


def _is_busy(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _assert_usable_sqlite_file(database: Path) -> None:
    try:
        with database.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER))
    except OSError as exc:
        raise SpikeStoreError("unavailable", "operational store could not be read") from exc
    if header != SQLITE_HEADER:
        raise SpikeStoreError("needs_repair", "operational store is not a usable SQLite file")


def create_layout(paths: OperationalStorePaths) -> None:
    for directory in (
        paths.data_root,
        paths.store_dir,
        paths.artifacts_dir,
        paths.artifacts_tmp,
        paths.backups_dir,
        paths.locks_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        _restrict(directory, DIRECTORY_MODE)


def apply_session_pragmas(
    connection: sqlite3.Connection, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS
) -> None:
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA trusted_schema = OFF")


def enable_wal(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(journal_mode).lower() != "wal":
        raise SpikeStoreError("unavailable", "operational store did not enter WAL mode")


def require_wal(connection: sqlite3.Connection) -> None:
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    if str(journal_mode).lower() != "wal":
        raise SpikeStoreError("unavailable", "operational store is not in WAL mode")


def connect_spike(
    database: Path,
    *,
    read_only: bool = False,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    missing_ok: bool = False,
) -> sqlite3.Connection:
    existed = database.exists()
    if existed:
        _assert_usable_sqlite_file(database)
    elif read_only or not missing_ok:
        raise SpikeStoreError("not_found", "operational store is missing")
    if read_only:
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=0,
        )
    else:
        connection = sqlite3.connect(
            database,
            isolation_level=None,
            timeout=busy_timeout_ms / 1000,
        )
    connection.row_factory = sqlite3.Row
    apply_session_pragmas(connection, busy_timeout_ms=busy_timeout_ms)
    if existed:
        validate_identity(connection)
    if read_only:
        require_wal(connection)
    else:
        enable_wal(connection)
    return connection


def _pragma_int(connection: sqlite3.Connection, name: str) -> int:
    return int(connection.execute(f"PRAGMA {name}").fetchone()[0])


def read_identity(connection: sqlite3.Connection) -> tuple[int, int, str | None]:
    application_id = _pragma_int(connection, "application_id")
    user_version = _pragma_int(connection, "user_version")
    try:
        row = connection.execute(
            "SELECT application_name, schema_version FROM store_identity WHERE singleton = 1"
        ).fetchone()
    except sqlite3.DatabaseError:
        return application_id, user_version, None
    if row is None:
        return application_id, user_version, None
    return application_id, int(row["schema_version"]), str(row["application_name"])


def validate_identity(
    connection: sqlite3.Connection, *, supported: int = SUPPORTED_SCHEMA_VERSION
) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise SpikeStoreError("needs_repair", "operational store failed integrity check")
    application_id, schema_version, application_name = read_identity(connection)
    if application_id == 0 and application_name is None and schema_version == 0:
        raise SpikeStoreError("needs_repair", "operational store has no identity")
    if application_id != APPLICATION_ID or application_name != APPLICATION_NAME:
        raise SpikeStoreError("identity_mismatch", "file is not a Morrow operational store")
    header_version = _pragma_int(connection, "user_version")
    if header_version != schema_version:
        raise SpikeStoreError("needs_repair", "schema version authorities disagree")
    if schema_version > supported:
        raise SpikeStoreError("future_schema", "operational store is newer than this binary")


def initialize_store(
    paths: OperationalStorePaths, *, schema_version: int = SUPPORTED_SCHEMA_VERSION
) -> None:
    create_layout(paths)
    connection = connect_spike(paths.database, missing_ok=True)
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'store_identity'"
                ).fetchone()
                is not None
            ):
                validate_identity(
                    connection, supported=max(schema_version, SUPPORTED_SCHEMA_VERSION)
                )
                connection.execute("COMMIT")
                return
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {int(schema_version)}")
            connection.execute(
                """
                CREATE TABLE store_identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    application_name TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    created_at_unix INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE spike_records (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE spike_parents (
                    id INTEGER PRIMARY KEY
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE spike_children (
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES spike_parents(id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO store_identity(
                    singleton, application_name, schema_version, created_at_unix
                )
                VALUES (1, ?, ?, 0)
                """,
                (APPLICATION_NAME, int(schema_version)),
            )
            connection.execute("COMMIT")
        except Exception:
            _rollback_quietly(connection)
            raise
    finally:
        connection.close()
    _restrict(paths.database, FILE_MODE)


class MaintenanceLock:
    def __init__(self, paths: OperationalStorePaths, *, timeout: float = 0.1) -> None:
        self.path = paths.maintenance_lock
        self._lock = FileLock(str(self.path), timeout=timeout)

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _restrict(self.path.parent, DIRECTORY_MODE)
        try:
            self._lock.acquire()
        except Timeout as exc:
            raise SpikeStoreError(
                "busy", "operational store maintenance is already running"
            ) from exc

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> MaintenanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def write_with_retry(
    work: Callable[[], None],
    *,
    attempts: int = WRITE_RETRY_ATTEMPTS,
    wait: Callable[[int], None] | None = None,
) -> None:
    last_error: BaseException | None = None
    for index in range(attempts):
        try:
            work()
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if not _is_busy(exc) or index == attempts - 1:
                break
            if wait is not None:
                wait(index)
    raise SpikeStoreError("busy", "operational store write contended") from last_error


def insert_record(
    connection: sqlite3.Connection, key: str, value: str, *, busy_timeout_ms: int | None = None
) -> None:
    if busy_timeout_ms is not None:
        connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT INTO spike_records(key, value) VALUES (?, ?)",
            (key, value),
        )
        connection.execute("COMMIT")
    except Exception:
        _rollback_quietly(connection)
        raise


def backup_store(paths: OperationalStorePaths, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _restrict(destination.parent, DIRECTORY_MODE)
    source = connect_spike(paths.database)
    target = sqlite3.connect(destination, isolation_level=None)
    try:
        source.backup(target)
        apply_session_pragmas(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise SpikeStoreError("needs_repair", "operational backup failed integrity checks")
    finally:
        target.close()
        source.close()
    _restrict(destination, FILE_MODE)


def _commit_then_abort(database: str, key: str, ready, proceed) -> None:
    connection = connect_spike(Path(database))
    insert_record(connection, key, "committed")
    ready.set()
    proceed.wait(timeout=10)
    os._exit(17)


def _insert_then_abort(database: str, key: str, ready) -> None:
    connection = connect_spike(Path(database), busy_timeout_ms=0)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO spike_records(key, value) VALUES (?, ?)",
        (key, "uncommitted"),
    )
    ready.set()
    os._exit(17)


def _hold_write_transaction(database: str, key: str, ready, release) -> None:
    connection = connect_spike(Path(database), busy_timeout_ms=0)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO spike_records(key, value) VALUES (?, ?)",
        (key, "held"),
    )
    ready.set()
    release.wait(timeout=10)
    connection.execute("COMMIT")
    connection.close()


def _hold_maintenance_lock(lock_path: str, ready, release) -> None:
    lock = FileLock(lock_path, timeout=0)
    lock.acquire()
    ready.set()
    release.wait(timeout=10)
    lock.release()


def _hold_maintenance_lock_then_abort(lock_path: str, ready) -> None:
    lock = FileLock(lock_path, timeout=0)
    lock.acquire()
    ready.set()
    os._exit(17)


def _write_extra_records(database: str, prefix: str, count: int, ready, start) -> None:
    ready.set()
    start.wait(timeout=10)
    connection = connect_spike(Path(database))
    for index in range(count):
        insert_record(connection, f"{prefix}-{index}", "live")
    connection.close()


def _prepared_store(tmp_path: Path) -> OperationalStorePaths:
    paths = OperationalStorePaths(tmp_path / "state")
    initialize_store(paths)
    return paths


def test_layout_uses_reserved_names_and_user_only_permissions(tmp_path):
    paths = _prepared_store(tmp_path)
    yaml_marker = paths.data_root / "config.yaml"
    yaml_marker.write_text("keep: true\n", encoding="utf-8")

    assert paths.database.is_file()
    assert _posix_mode(paths.store_dir) == DIRECTORY_MODE
    assert _posix_mode(paths.artifacts_tmp) == DIRECTORY_MODE
    assert _posix_mode(paths.backups_dir) == DIRECTORY_MODE
    assert _posix_mode(paths.database) == FILE_MODE
    assert yaml_marker.read_text(encoding="utf-8") == "keep: true\n"


def test_reopen_keeps_wal_full_sync_and_identity(tmp_path):
    paths = _prepared_store(tmp_path)
    connection = connect_spike(paths.database)
    try:
        assert _pragma_int(connection, "application_id") == APPLICATION_ID
        assert _pragma_int(connection, "user_version") == SUPPORTED_SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
    finally:
        connection.close()


def test_foreign_keys_are_enforced_on_every_connection(tmp_path):
    paths = _prepared_store(tmp_path)
    connection = connect_spike(paths.database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO spike_children(id, parent_id) VALUES (1, 99)")
        connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_commit_survives_subprocess_abort(tmp_path):
    paths = _prepared_store(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    proceed = context.Event()
    process = context.Process(
        target=_commit_then_abort,
        args=(str(paths.database), "kept", ready, proceed),
    )
    process.start()
    assert ready.wait(timeout=10)
    proceed.set()
    process.join(timeout=10)
    assert process.exitcode == 17

    connection = connect_spike(paths.database)
    try:
        row = connection.execute(
            "SELECT value FROM spike_records WHERE key = ?",
            ("kept",),
        ).fetchone()
        assert row["value"] == "committed"
    finally:
        connection.close()


def test_uncommitted_insert_is_discarded_after_subprocess_abort(tmp_path):
    paths = _prepared_store(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_insert_then_abort,
        args=(str(paths.database), "lost", ready),
    )
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 17

    connection = connect_spike(paths.database)
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM spike_records WHERE key = ?",
                ("lost",),
            ).fetchone()["n"]
            == 0
        )
    finally:
        connection.close()


def test_reader_sees_wal_snapshot_while_writer_holds_transaction(tmp_path):
    paths = _prepared_store(tmp_path)
    seed = connect_spike(paths.database)
    insert_record(seed, "visible", "before")
    seed.close()

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_write_transaction,
        args=(str(paths.database), "hidden", ready, release),
    )
    process.start()
    assert ready.wait(timeout=10)
    try:
        reader = connect_spike(paths.database, read_only=True)
        try:
            keys = {
                row["key"] for row in reader.execute("SELECT key FROM spike_records").fetchall()
            }
            assert keys == {"visible"}
        finally:
            reader.close()
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_begin_immediate_contention_is_typed_busy(tmp_path):
    paths = _prepared_store(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_write_transaction,
        args=(str(paths.database), "owner", ready, release),
    )
    process.start()
    assert ready.wait(timeout=10)
    try:
        connection = connect_spike(paths.database, busy_timeout_ms=0)
        try:
            with pytest.raises(sqlite3.OperationalError) as error:
                connection.execute("BEGIN IMMEDIATE")
            assert _is_busy(error.value)
        finally:
            connection.close()
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_write_retry_is_bounded_and_does_not_sleep():
    waited: list[int] = []

    def always_busy() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(SpikeStoreError) as error:
        write_with_retry(always_busy, wait=waited.append)

    assert error.value.code == "busy"
    assert waited == list(range(WRITE_RETRY_ATTEMPTS - 1))


def test_write_retry_succeeds_after_injected_busy(tmp_path):
    paths = _prepared_store(tmp_path)
    connection = connect_spike(paths.database, busy_timeout_ms=0)
    attempts = {"count": 0}

    def flaky() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        insert_record(connection, "retried", "ok", busy_timeout_ms=0)

    try:
        write_with_retry(flaky, wait=lambda _index: None)
        row = connection.execute(
            "SELECT value FROM spike_records WHERE key = ?",
            ("retried",),
        ).fetchone()
        assert row["value"] == "ok"
        assert attempts["count"] == 3
    finally:
        connection.close()


def test_maintenance_lock_excludes_a_second_process(tmp_path):
    paths = _prepared_store(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_maintenance_lock,
        args=(str(paths.maintenance_lock), ready, release),
    )
    process.start()
    assert ready.wait(timeout=10)
    try:
        with pytest.raises(SpikeStoreError) as error:
            with MaintenanceLock(paths, timeout=0):
                pass
        assert error.value.code == "busy"
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_dead_maintenance_lock_owner_releases_the_os_lock(tmp_path):
    paths = _prepared_store(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_hold_maintenance_lock_then_abort,
        args=(str(paths.maintenance_lock), ready),
    )
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 17

    with MaintenanceLock(paths, timeout=0):
        assert paths.maintenance_lock.exists()


def test_future_schema_is_refused_and_left_intact(tmp_path):
    paths = _prepared_store(tmp_path)
    future = connect_spike(paths.database)
    future.execute("BEGIN IMMEDIATE")
    future.execute("PRAGMA user_version = 99")
    future.execute("UPDATE store_identity SET schema_version = 99 WHERE singleton = 1")
    future.execute("COMMIT")
    future.close()
    before = paths.database.read_bytes()

    with pytest.raises(SpikeStoreError) as error:
        connect_spike(paths.database)
    assert error.value.code == "future_schema"
    assert paths.database.read_bytes() == before


def test_foreign_sqlite_file_is_refused_and_left_intact(tmp_path):
    paths = OperationalStorePaths(tmp_path / "state")
    create_layout(paths)
    stranger = sqlite3.connect(paths.database)
    stranger.execute("CREATE TABLE other(id INTEGER PRIMARY KEY)")
    stranger.execute("PRAGMA application_id = 1")
    stranger.commit()
    stranger.close()
    before = paths.database.read_bytes()

    with pytest.raises(SpikeStoreError) as error:
        connect_spike(paths.database)
    assert error.value.code == "identity_mismatch"
    assert paths.database.read_bytes() == before


def test_empty_existing_file_is_repair_not_recreate(tmp_path):
    paths = OperationalStorePaths(tmp_path / "state")
    create_layout(paths)
    paths.database.write_bytes(b"")
    before = paths.database.read_bytes()

    with pytest.raises(SpikeStoreError) as error:
        connect_spike(paths.database)
    assert error.value.code == "needs_repair"
    with pytest.raises(SpikeStoreError) as initialize_error:
        initialize_store(paths)
    assert initialize_error.value.code == "needs_repair"
    assert paths.database.read_bytes() == before


def test_online_backup_during_writes_passes_integrity(tmp_path):
    paths = _prepared_store(tmp_path)
    seed = connect_spike(paths.database)
    for index in range(20):
        insert_record(seed, f"seed-{index}", "base")
    seed.close()

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    start = context.Event()
    process = context.Process(
        target=_write_extra_records,
        args=(str(paths.database), "live", 20, ready, start),
    )
    process.start()
    assert ready.wait(timeout=10)
    start.set()
    destination = paths.backups_dir / "operational.sqlite"
    with MaintenanceLock(paths, timeout=0):
        backup_store(paths, destination)
    process.join(timeout=10)
    assert process.exitcode == 0

    assert _posix_mode(destination) == FILE_MODE
    restored = connect_spike(destination)
    try:
        validate_identity(restored)
        count = restored.execute("SELECT COUNT(*) AS n FROM spike_records").fetchone()["n"]
        assert count >= 20
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        restored.close()
    assert not any(path.name.endswith("-wal") for path in paths.backups_dir.iterdir())
