"""Production tests for the current Operational Store schema."""

from __future__ import annotations

import multiprocessing
import os
import random
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.operational import (
    SQLITE_HEADER,
    BusyRetryPolicy,
    OperationalStore,
    _apply_session_pragmas,
    is_busy_or_locked,
    posix_mode,
    run_with_busy_retry,
)
from morrow.adapters.state.schema_reconcile import reconcile_schema
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.bootstrap import build_application
from morrow.core.models import Profile, StateLoadStatus, StateWriteStatus
from morrow.core.store import (
    APPLICATION_ID,
    ARTIFACTS_DIRNAME,
    BACKUPS_DIRNAME,
    DATABASE_NAME,
    DIRECTORY_MODE,
    FILE_MODE,
    MAINTENANCE_LOCK_NAME,
    OPERATIONAL_BACKUPS_DIRNAME,
    STORE_DIRNAME,
    SUPPORTED_SCHEMA_VERSION,
    WRITE_RETRY_ATTEMPTS,
    OperationalStoreLayout,
    StorageError,
    StorageErrorCode,
    StoreHealth,
    StoreOpenMode,
)
from morrow.services.workspace import DataRoot
from morrow.testing import FixedClock


def _retry(busy_timeout_ms: int = 0) -> BusyRetryPolicy:
    return BusyRetryPolicy(
        busy_timeout_ms=busy_timeout_ms,
        sleep=lambda _delay: None,
        rng=random.Random(0),
    )


def _store(root: Path, **kwargs) -> OperationalStore:
    kwargs.setdefault("retry_policy", _retry())
    kwargs.setdefault("clock", FixedClock())
    kwargs.setdefault("maintenance_timeout", 0)
    return OperationalStore(root, **kwargs)


def _initialized(tmp_path: Path, **kwargs) -> tuple[Path, OperationalStore]:
    root = tmp_path / "secret-home" / "state"
    store = _store(root, **kwargs)
    store.initialize().close()
    return root, store


def _assert_sanitized(error: StorageError, *forbidden: str) -> None:
    text = f"{error!s}{error!r}{error.code}"
    for item in forbidden:
        assert item not in text


def _pragma(session, name: str) -> object:
    return session.run_read(lambda executor: executor.execute(f"PRAGMA {name}"))[0][0]


def _hold_write_transaction(root: str, ready, release) -> None:
    store = _store(Path(root))
    with store.open(StoreOpenMode.READ_WRITE) as session:

        def work(executor) -> None:
            ready.set()
            # The competing interpreter must reach its operation while this lock
            # is held, even if importing application modules on the host is slow.
            assert release.wait(timeout=180)
            executor.execute("UPDATE sessions SET updated_at_unix = updated_at_unix")

        session.run_write(work)


def _hold_maintenance_lock(root: str, ready, release) -> None:
    store = _store(Path(root))
    with store.maintenance_lock():
        ready.set()
        assert release.wait(timeout=180)


def _hold_maintenance_then_exit(root: str, ready) -> None:
    store = _store(Path(root))
    store.maintenance_lock().acquire()
    ready.set()
    os._exit(17)


def _cleanup_processes(*processes) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        process.close()


def _touch_identity(root: str, count: int, ready, start) -> None:
    ready.set()
    assert start.wait(timeout=60)
    store = OperationalStore(Path(root), retry_policy=_retry(busy_timeout_ms=250))
    with store.open(StoreOpenMode.READ_WRITE) as session:
        for _ in range(count):
            session.run_write(
                lambda executor: executor.execute(
                    "UPDATE sessions SET updated_at_unix = updated_at_unix"
                )
            )


def test_data_root_exposes_reserved_operational_paths(tmp_path):
    data_root = DataRoot(tmp_path / "state")
    layout = OperationalStoreLayout.from_root(data_root.root)
    assert data_root.store_path == layout.database
    assert data_root.store_path.name == DATABASE_NAME
    assert data_root.store_path.parent.name == STORE_DIRNAME
    assert data_root.artifacts_path.name == ARTIFACTS_DIRNAME
    assert data_root.backups_path == data_root.root / BACKUPS_DIRNAME / OPERATIONAL_BACKUPS_DIRNAME
    assert data_root.operational_lock_path.name == MAINTENANCE_LOCK_NAME
    assert data_root.operational_lock_path.parent == data_root.locks_path


def test_bootstrap_does_not_create_the_operational_store(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    assert not app.data_root.store_path.exists()
    assert not (app.data_root.root / STORE_DIRNAME).exists()
    assert not app.data_root.artifacts_path.exists()


def test_missing_store_is_not_found_and_does_not_leak_paths(tmp_path):
    root = tmp_path / "secret-home" / "state"
    store = _store(root)
    classified = store.classify()
    assert classified.present is False
    assert classified.error_code is StorageErrorCode.NOT_FOUND
    with pytest.raises(StorageError) as error:
        store.open(StoreOpenMode.READ_WRITE)
    assert error.value.code is StorageErrorCode.NOT_FOUND
    _assert_sanitized(error.value, str(root), "secret-home", "SELECT")


def test_create_reopen_and_layout_permissions(tmp_path):
    root, store = _initialized(tmp_path)
    layout = store.layout
    assert posix_mode(layout.store_dir) == DIRECTORY_MODE
    assert posix_mode(layout.artifacts_tmp) == DIRECTORY_MODE
    assert posix_mode(layout.backups_dir) == DIRECTORY_MODE
    assert posix_mode(layout.database) == FILE_MODE
    for sidecar in (layout.wal, layout.shm):
        if sidecar.exists():
            assert posix_mode(sidecar) == FILE_MODE

    with store.open(StoreOpenMode.READ_WRITE) as session:
        assert session.health is StoreHealth.OK
        assert session.schema_version == SUPPORTED_SCHEMA_VERSION
        assert int(_pragma(session, "application_id")) == APPLICATION_ID
        assert int(_pragma(session, "user_version")) == SUPPORTED_SCHEMA_VERSION
        assert str(_pragma(session, "journal_mode")).lower() == "wal"
        assert int(_pragma(session, "synchronous")) == 2
        assert int(_pragma(session, "foreign_keys")) == 1
        assert int(_pragma(session, "trusted_schema")) == 0
        identity_table = session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='store_identity'"
            )
        )
        assert identity_table == ()

    again = store.initialize()
    again.close()
    assert store.classify().ok


def test_new_store_uses_only_the_current_schema_without_migration_ledger(tmp_path):
    _root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_ONLY) as session:
        objects = session.run_read(
            lambda executor: executor.execute(
                "SELECT type, name FROM sqlite_master WHERE name IN ("
                "'schema_migrations', 'store_identity', 'command_receipts',"
                "'turn_submit_receipts', 'task_command_receipts', 'application_command_receipts',"
                "'chat_control_receipts', 'recovery_receipts', 'workflow_evaluations',"
                "'workflow_feedback', 'workflow_policy_candidates') ORDER BY type, name"
            )
        )
        assert objects == (("table", "command_receipts"),)
        assert session.run_read(
            lambda executor: executor.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ) == ((83,),)
        assert (
            session.run_read(
                lambda executor: executor.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            )
            == ()
        )
        assert session.schema_version == SUPPORTED_SCHEMA_VERSION


def test_additive_reconciliation_restores_missing_objects_without_backup(tmp_path):
    _root, store = _initialized(tmp_path)
    with sqlite3.connect(store.layout.database) as raw:
        raw.execute("DROP TABLE command_receipts")
        preview = reconcile_schema(raw, apply=False)
        assert "table:command_receipts" in preview.pending_changes
        assert not preview.changed

    assert list(store.layout.backups_dir.glob("*.sqlite")) == []
    with store.open(StoreOpenMode.READ_WRITE) as session:
        assert session.schema_version == SUPPORTED_SCHEMA_VERSION
        assert session.run_read(
            lambda executor: executor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='command_receipts'"
            )
        ) == (("command_receipts",),)
    assert list(store.layout.backups_dir.glob("*.sqlite")) == []


def test_unknown_older_schema_is_rejected_without_mutation(tmp_path):
    _root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("PRAGMA user_version = 41")
    raw.commit()
    raw.close()
    before = store.layout.database.read_bytes()

    classified = store.classify()
    assert classified.health is StoreHealth.UNSUPPORTED_SCHEMA
    assert classified.error_code is StorageErrorCode.UNSUPPORTED_SCHEMA
    with pytest.raises(StorageError) as error:
        store.open(StoreOpenMode.READ_WRITE)
    assert error.value.code is StorageErrorCode.UNSUPPORTED_SCHEMA
    assert store.layout.database.read_bytes() == before


def test_legacy_migration_ledger_is_ignored_at_the_current_version(tmp_path):
    _root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT)")
    raw.commit()
    raw.close()

    classified = store.classify()
    assert classified.ok
    with store.open(StoreOpenMode.READ_WRITE) as session:
        assert session.health is StoreHealth.OK
    assert store.layout.database.exists()


def test_session_pragmas_prime_schema_before_disabling_trusted_schema():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        _apply_session_pragmas(connection, busy_timeout_ms=250)
        normalized = [statement.strip().lower() for statement in statements]
        schema_index = normalized.index("select name from sqlite_master")
        synchronous_index = normalized.index("pragma synchronous = full")
        trusted_index = normalized.index("pragma trusted_schema = off")
        assert schema_index < trusted_index
        assert synchronous_index < trusted_index
        assert connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
    finally:
        connection.close()


def test_create_is_idempotent_and_leaves_yaml_untouched(tmp_path):
    root = tmp_path / "state"
    global_store = GlobalConfigYamlStore(root)
    index_store = WorkspaceIndexYamlStore(root)
    project_store = ProjectStateYamlStore(root)
    assert (
        global_store.update(lambda value: value, expected_revision=0).status is StateWriteStatus.OK
    )
    assert (
        index_store.update(lambda value: value, expected_revision=0).status is StateWriteStatus.OK
    )
    assert (
        project_store.write_profile("ws_current", Profile(name="current")).status
        is StateWriteStatus.OK
    )
    yaml_files = {path: path.read_bytes() for path in root.rglob("*.yaml")}
    store = _store(root)
    store.initialize().close()
    store.initialize().close()
    assert {path: path.read_bytes() for path in yaml_files} == yaml_files
    assert global_store.load().status is StateLoadStatus.OK
    assert index_store.load().status is StateLoadStatus.OK
    profile = project_store.load_profile("ws_current")
    assert profile.status is StateLoadStatus.OK
    assert profile.value.profile.name == "current"


def test_empty_file_is_repair_and_is_not_recreated(tmp_path):
    root = tmp_path / "secret-home" / "state"
    store = _store(root)
    store.ensure_layout()
    store.layout.database.write_bytes(b"")
    before = store.layout.database.read_bytes()
    classified = store.classify()
    assert classified.error_code is StorageErrorCode.NEEDS_REPAIR
    with pytest.raises(StorageError) as error:
        store.initialize()
    assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    _assert_sanitized(error.value, str(root), "secret-home")
    assert store.layout.database.read_bytes() == before


def test_foreign_sqlite_file_is_left_intact(tmp_path):
    root = tmp_path / "secret-home" / "state"
    store = _store(root)
    store.ensure_layout()
    stranger = sqlite3.connect(store.layout.database)
    stranger.execute("CREATE TABLE other(id INTEGER PRIMARY KEY)")
    stranger.execute("PRAGMA application_id = 1")
    stranger.commit()
    stranger.close()
    before = store.layout.database.read_bytes()
    with pytest.raises(StorageError) as error:
        store.open(StoreOpenMode.READ_WRITE)
    assert error.value.code is StorageErrorCode.IDENTITY_MISMATCH
    _assert_sanitized(error.value, str(root), "CREATE TABLE")
    assert store.layout.database.read_bytes() == before


def test_future_schema_is_readable_but_write_refused_and_left_intact(tmp_path):
    root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    future_version = SUPPORTED_SCHEMA_VERSION + 1
    raw.execute(f"PRAGMA user_version = {future_version}")
    raw.commit()
    raw.close()
    before = store.layout.database.read_bytes()
    classified = store.classify()
    assert classified.health is StoreHealth.FUTURE_SCHEMA
    with store.open(StoreOpenMode.READ_ONLY) as session:
        assert session.health is StoreHealth.FUTURE_SCHEMA
        assert session.schema_version == future_version
        assert session.run_read(lambda executor: executor.execute("SELECT 1")) == ((1,),)
    with pytest.raises(StorageError) as error:
        store.open(StoreOpenMode.READ_WRITE)
    assert error.value.code is StorageErrorCode.FUTURE_SCHEMA
    assert "backup" in str(error.value).casefold()
    assert store.layout.database.read_bytes() == before


def test_unknown_old_user_version_is_unsupported(tmp_path):
    root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("PRAGMA user_version = 6")
    raw.commit()
    raw.close()
    before = store.layout.database.read_bytes()
    classified = store.classify()
    assert classified.error_code is StorageErrorCode.UNSUPPORTED_SCHEMA
    with pytest.raises(StorageError) as error:
        store.open(StoreOpenMode.READ_WRITE)
    assert error.value.code is StorageErrorCode.UNSUPPORTED_SCHEMA
    assert store.layout.database.read_bytes() == before


def test_valid_header_corruption_is_diagnose_repair_and_left_intact(tmp_path):
    root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw.close()
    store.layout.wal.unlink(missing_ok=True)
    store.layout.shm.unlink(missing_ok=True)
    original = store.layout.database.read_bytes()
    corrupted = bytearray(original)
    corrupted[100 : min(len(corrupted), 800)] = b"\xff" * (min(len(corrupted), 800) - 100)
    store.layout.database.write_bytes(corrupted)
    before = store.layout.database.read_bytes()
    assert before.startswith(SQLITE_HEADER)
    try:
        with store.open(StoreOpenMode.DIAGNOSE) as session:
            assert session.health is StoreHealth.NEEDS_REPAIR
    except StorageError as error:
        assert error.code is StorageErrorCode.NEEDS_REPAIR
    assert store.layout.database.read_bytes() == before


def test_statement_errors_do_not_leak_sql(tmp_path):
    root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_WRITE) as session, pytest.raises(StorageError) as error:
        session.run_write(
            lambda executor: executor.execute("INSERT INTO not_a_table(id) VALUES (1)")
        )
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    _assert_sanitized(error.value, str(root), "not_a_table", "INSERT")


def test_run_read_cannot_write(tmp_path):
    _root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_WRITE) as session, pytest.raises(StorageError):
        session.run_read(
            lambda executor: executor.execute(
                "UPDATE sessions SET updated_at_unix = updated_at_unix"
            )
        )


def test_foreign_keys_are_enforced_on_reopen(tmp_path):
    _root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_WRITE) as session:
        assert session.schema_version == SUPPORTED_SCHEMA_VERSION
        with pytest.raises(StorageError) as error:
            session.run_write(
                lambda executor: executor.execute(
                    "INSERT INTO turns(turn_id, session_id, task_run_id, "
                    "client_message_id, created_at_unix) "
                    "VALUES ('turn_1', 'ses_missing', 'task_missing', 'c1', 0)"
                )
            )
        assert error.value.code is StorageErrorCode.UNAVAILABLE


def test_connection_rejects_other_thread(tmp_path):
    _root, store = _initialized(tmp_path)
    session = store.open(StoreOpenMode.READ_WRITE)
    errors: list[BaseException] = []

    def other() -> None:
        try:
            session.run_read(lambda executor: executor.execute("SELECT 1"))
        except BaseException as exc:
            errors.append(exc)

    try:
        thread = threading.Thread(target=other)
        thread.start()
        thread.join(timeout=5)
        assert thread.is_alive() is False
        assert errors
        assert isinstance(errors[0], StorageError)
        assert errors[0].code is StorageErrorCode.UNAVAILABLE
        _assert_sanitized(errors[0], "ProgrammingError")
    finally:
        session.close()


def test_busy_retry_is_bounded_and_does_not_sleep():
    waited: list[float] = []

    def always_busy() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(StorageError) as error:
        run_with_busy_retry(
            always_busy,
            BusyRetryPolicy(sleep=waited.append, rng=random.Random(0)),
        )
    assert error.value.code is StorageErrorCode.BUSY
    assert len(waited) == WRITE_RETRY_ATTEMPTS - 1


def test_non_busy_errors_are_attempted_once():
    attempts: list[int] = []

    def constraint() -> None:
        attempts.append(1)
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    with pytest.raises(StorageError) as error:
        run_with_busy_retry(constraint, _retry())
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    assert attempts == [1]


def test_disk_full_is_unavailable_and_not_retried():
    attempts: list[int] = []

    def full() -> None:
        attempts.append(1)
        raise sqlite3.OperationalError("database or disk is full")

    with pytest.raises(StorageError) as error:
        run_with_busy_retry(full, _retry())
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    assert attempts == [1]
    assert not is_busy_or_locked(sqlite3.OperationalError("database or disk is full"))


def test_write_retry_succeeds_after_injected_busy(tmp_path):
    _root, store = _initialized(tmp_path)
    attempts = {"count": 0}

    def flaky() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert run_with_busy_retry(flaky, _retry()) == "ok"
    assert attempts["count"] == 3


def test_run_write_once_types_body_busy_without_replaying(tmp_path):
    _root, store = _initialized(tmp_path)
    attempts: list[int] = []

    def busy(_executor) -> None:
        attempts.append(1)
        raise sqlite3.OperationalError("database is locked")

    with store.open(StoreOpenMode.READ_WRITE) as session, pytest.raises(StorageError) as error:
        session.run_write_once(busy)

    assert error.value.code is StorageErrorCode.BUSY
    assert attempts == [1]


def test_run_write_once_does_not_replay_after_commit_fault(tmp_path):
    root, store = _initialized(tmp_path)
    attempts: list[int] = []

    def injector(point: str) -> None:
        if point == "after_commit":
            raise sqlite3.OperationalError("database is locked")

    def insert(executor) -> None:
        attempts.append(1)
        executor.execute(
            """
            INSERT INTO sessions(
                session_id, workspace_id, lifecycle, health, current_task_run_id,
                conversation_position, created_at_unix, updated_at_unix
            ) VALUES ('ses_once', 'ws_1', 'active', 'ok', NULL, 0, 0, 0)
            """
        )

    failing = _store(root, failure_injector=injector)
    with failing.open(StoreOpenMode.READ_WRITE) as session, pytest.raises(StorageError) as error:
        session.run_write_once(insert)

    assert error.value.code is StorageErrorCode.BUSY
    assert attempts == [1]
    with store.open(StoreOpenMode.READ_WRITE) as session:
        rows = session.run_read(
            lambda executor: executor.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = 'ses_once'"
            )
        )
    assert rows == ((1,),)


def test_begin_immediate_contention_is_typed_busy(tmp_path):
    root, _store_obj = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_write_transaction, args=(str(root), ready, release))
    process.start()
    try:
        assert ready.wait(timeout=60)
        with (
            _store(root).open(StoreOpenMode.READ_WRITE) as session,
            pytest.raises(StorageError) as error,
        ):
            session.run_write(
                lambda executor: executor.execute(
                    "UPDATE sessions SET updated_at_unix = updated_at_unix"
                )
            )
        assert error.value.code is StorageErrorCode.BUSY
        release.set()
        process.join(timeout=60)
        assert process.exitcode == 0
    finally:
        release.set()
        _cleanup_processes(process)


def test_maintenance_lock_excludes_a_second_process(tmp_path):
    root, store = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_maintenance_lock, args=(str(root), ready, release))
    process.start()
    try:
        assert ready.wait(timeout=60)
        with pytest.raises(StorageError) as error, store.maintenance_lock():
            raise AssertionError("second process acquired the maintenance lock")
        assert error.value.code is StorageErrorCode.BUSY
        _assert_sanitized(error.value, str(root))
        with pytest.raises(StorageError) as backup_error:
            store.backup()
        assert backup_error.value.code is StorageErrorCode.BUSY
        release.set()
        process.join(timeout=60)
        assert process.exitcode == 0
    finally:
        release.set()
        _cleanup_processes(process)


def test_dead_maintenance_lock_owner_releases_the_os_lock(tmp_path):
    root, store = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_maintenance_then_exit, args=(str(root), ready))
    process.start()
    try:
        assert ready.wait(timeout=60)
        process.join(timeout=60)
        assert process.exitcode == 17
    finally:
        _cleanup_processes(process)
    with store.maintenance_lock():
        assert store.layout.maintenance_lock.exists()


def test_sidecar_permissions_after_write(tmp_path):
    root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_WRITE) as session:
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE sessions SET updated_at_unix = updated_at_unix"
            )
        )
    for sidecar in (store.layout.wal, store.layout.shm):
        if sidecar.exists():
            assert posix_mode(sidecar) == FILE_MODE
            assert stat.S_IMODE(sidecar.stat().st_mode) == FILE_MODE


def test_read_only_filesystem_refuses_writes(tmp_path):
    root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_WRITE) as session:
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE sessions SET updated_at_unix = updated_at_unix"
            )
        )
    store.layout.database.chmod(0o400)
    for sidecar in (store.layout.wal, store.layout.shm):
        if sidecar.exists():
            sidecar.chmod(0o400)
    store.layout.store_dir.chmod(0o500)
    try:
        with pytest.raises(StorageError) as error:
            store.open(StoreOpenMode.READ_WRITE)
        assert error.value.code is StorageErrorCode.UNAVAILABLE
        with store.open(StoreOpenMode.READ_ONLY) as session:
            assert session.mode is StoreOpenMode.READ_ONLY
            assert _pragma(session, "application_id") == APPLICATION_ID
    finally:
        store.layout.store_dir.chmod(0o700)
        store.layout.database.chmod(0o600)
        for sidecar in (store.layout.wal, store.layout.shm):
            if sidecar.exists():
                sidecar.chmod(0o600)


def test_write_failure_rolls_back(tmp_path):
    root, store = _initialized(tmp_path)

    def injector(point: str) -> None:
        if point == "before_commit":
            raise sqlite3.OperationalError("database or disk is full")

    failing = _store(root, failure_injector=injector)
    with failing.open(StoreOpenMode.READ_WRITE) as session, pytest.raises(StorageError) as error:
        session.run_write(
            lambda executor: executor.execute(
                "INSERT INTO sessions(session_id, workspace_id, lifecycle, health, "
                "current_task_run_id, conversation_position, created_at_unix, updated_at_unix) "
                "VALUES ('ses_lost', 'ws_1', 'active', 'ok', NULL, 0, 0, 0)"
            )
        )
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    with store.open(StoreOpenMode.READ_WRITE) as session:
        count = session.run_read(lambda executor: executor.execute("SELECT COUNT(*) FROM sessions"))
        assert count[0][0] == 0


def test_base_exception_during_write_rolls_back_and_releases_transaction(tmp_path):
    root, store = _initialized(tmp_path)
    insert = (
        "INSERT INTO sessions(session_id, workspace_id, lifecycle, health, "
        "current_task_run_id, conversation_position, created_at_unix, updated_at_unix) "
        "VALUES (?, 'ws_1', 'active', 'ok', NULL, 0, 0, 0)"
    )

    with store.open(StoreOpenMode.READ_WRITE) as session:

        def interrupt(executor):
            executor.execute(insert, ("ses_interrupted",))
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            session.run_write(interrupt)
        assert session.run_read(
            lambda executor: executor.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = 'ses_interrupted'"
            )
        ) == ((0,),)
        session.run_write(lambda executor: executor.execute(insert, ("ses_after",)))
        assert session.run_read(
            lambda executor: executor.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_id = 'ses_after'"
            )
        ) == ((1,),)


def test_online_backup_during_writes_passes_integrity(tmp_path):
    root, store = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    start = context.Event()
    process = context.Process(target=_touch_identity, args=(str(root), 20, ready, start))
    process.start()
    try:
        assert ready.wait(timeout=60)
        start.set()
        report = _store(root, retry_policy=_retry(busy_timeout_ms=250)).backup(
            "operational-copy.sqlite"
        )
        process.join(timeout=60)
        assert process.exitcode == 0
    finally:
        _cleanup_processes(process)
    assert report.integrity_ok
    destination = store.layout.backups_dir / report.destination_name
    assert posix_mode(destination) == FILE_MODE
    assert not any(path.name.endswith("-wal") for path in store.layout.backups_dir.iterdir())
    restored = sqlite3.connect(destination)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
        assert restored.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
    finally:
        restored.close()


def test_backup_name_rejects_path_escape(tmp_path):
    _root, store = _initialized(tmp_path)
    with pytest.raises(StorageError) as error:
        store.backup("../escape.sqlite")
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    _assert_sanitized(error.value, "..", "escape.sqlite")
