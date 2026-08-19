"""Production tests for the Stage 4 v1 Operational Store foundation."""

from __future__ import annotations

import multiprocessing
import os
import random
import shutil
import sqlite3
import stat
import threading
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.migrations import V1, MigrationRegistry, SchemaMigration
from morrow.adapters.state.operational import (
    SQLITE_HEADER,
    BusyRetryPolicy,
    OperationalStore,
    is_busy_or_locked,
    posix_mode,
    run_with_busy_retry,
)
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.bootstrap import build_application
from morrow.core.models import StateLoadStatus
from morrow.core.store import (
    APPLICATION_ID,
    APPLICATION_NAME,
    ARTIFACTS_DIRNAME,
    BACKUPS_DIRNAME,
    DATABASE_NAME,
    DIRECTORY_MODE,
    FILE_MODE,
    MAINTENANCE_LOCK_NAME,
    OPERATIONAL_BACKUPS_DIRNAME,
    STORE_DIRNAME,
    WRITE_RETRY_ATTEMPTS,
    OperationalStoreLayout,
    StorageError,
    StorageErrorCode,
    StoreHealth,
    StoreOpenMode,
)
from morrow.services.workspace import DataRoot
from morrow.testing import FixedClock

STAGE3_FIXTURE = Path(__file__).parent / "fixtures" / "stage3_data_root"
V2_PROBE = SchemaMigration(
    version=2,
    name="test_probe_records",
    statements=(
        """
        CREATE TABLE probe_parents (
            id INTEGER PRIMARY KEY
        )
        """,
        """
        CREATE TABLE probe_children (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES probe_parents(id)
        )
        """,
        """
        CREATE TABLE probe_records (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """,
    ),
)


def _retry(busy_timeout_ms: int = 0) -> BusyRetryPolicy:
    return BusyRetryPolicy(
        busy_timeout_ms=busy_timeout_ms,
        sleep=lambda _delay: None,
        rng=random.Random(0),
    )


def _registry(*extra: SchemaMigration, supported: int | None = None) -> MigrationRegistry:
    registry = MigrationRegistry(supported_version=supported or (extra[-1].version if extra else 1))
    registry.add(V1)
    for migration in extra:
        registry.add(migration)
    return registry


def _process_test_registry() -> MigrationRegistry:
    return _registry(V2_PROBE, supported=2)


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


def _copy_stage3_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "stage3-state"
    shutil.copytree(STAGE3_FIXTURE, destination, ignore=shutil.ignore_patterns("README.md"))
    return destination


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
            release.wait(timeout=10)
            executor.execute(
                "UPDATE store_identity SET application_name = application_name WHERE singleton = 1"
            )

        session.run_write(work)


def _hold_maintenance_lock(root: str, ready, release) -> None:
    store = _store(Path(root))
    with store.maintenance_lock():
        ready.set()
        release.wait(timeout=10)


def _hold_maintenance_then_exit(root: str, ready) -> None:
    store = _store(Path(root))
    store.maintenance_lock().acquire()
    ready.set()
    os._exit(17)


def _migrate_v2(root: str, result) -> None:
    try:
        report = _store(Path(root), registry=_process_test_registry()).migrate()
        result.put(("ok", report.to_version))
    except StorageError as exc:
        result.put((exc.code.value, None))


def _migrate_and_exit(root: str, fault: str) -> None:
    def injector(point: str) -> None:
        if point == fault:
            os._exit(17)

    _store(Path(root), registry=_process_test_registry(), failure_injector=injector).migrate()


def _touch_identity(root: str, count: int, ready, start) -> None:
    ready.set()
    start.wait(timeout=10)
    store = OperationalStore(Path(root), retry_policy=_retry(busy_timeout_ms=250))
    with store.open(StoreOpenMode.READ_WRITE) as session:
        for _ in range(count):
            session.run_write(
                lambda executor: executor.execute(
                    "UPDATE store_identity SET application_name = application_name "
                    "WHERE singleton = 1"
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
        assert session.schema_version == 1
        assert int(_pragma(session, "application_id")) == APPLICATION_ID
        assert int(_pragma(session, "user_version")) == 1
        assert str(_pragma(session, "journal_mode")).lower() == "wal"
        assert int(_pragma(session, "synchronous")) == 2
        assert int(_pragma(session, "foreign_keys")) == 1
        assert int(_pragma(session, "trusted_schema")) == 0
        row = session.run_read(
            lambda executor: executor.execute(
                "SELECT application_name, schema_version FROM store_identity WHERE singleton = 1"
            )
        )
        assert row == ((APPLICATION_NAME, 1),)

    again = store.initialize()
    again.close()
    assert store.classify().ok


def test_create_is_idempotent_and_leaves_yaml_untouched(tmp_path):
    root = _copy_stage3_fixture(tmp_path)
    store = _store(root)
    store.initialize().close()
    store.initialize().close()
    original = [
        path.relative_to(STAGE3_FIXTURE)
        for path in STAGE3_FIXTURE.rglob("*")
        if path.is_file() and path.name != "README.md"
    ]
    for relative in original:
        assert (root / relative).read_bytes() == (STAGE3_FIXTURE / relative).read_bytes()
    assert GlobalConfigYamlStore(root).load().status is StateLoadStatus.OK
    assert WorkspaceIndexYamlStore(root).load().status is StateLoadStatus.OK
    profile = ProjectStateYamlStore(root).load_profile("ws_stage3")
    assert profile.status is StateLoadStatus.OK
    assert profile.value.profile.name == "stage3-fixture"


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


def test_future_schema_is_refused_and_left_intact(tmp_path):
    root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("PRAGMA user_version = 9")
    raw.execute("UPDATE store_identity SET schema_version = 9 WHERE singleton = 1")
    raw.commit()
    raw.close()
    before = store.layout.database.read_bytes()
    classified = store.classify()
    assert classified.health is StoreHealth.FUTURE_SCHEMA
    with pytest.raises(StorageError) as error:
        store.open(StoreOpenMode.READ_WRITE)
    assert error.value.code is StorageErrorCode.FUTURE_SCHEMA
    assert store.layout.database.read_bytes() == before


def test_identity_and_user_version_mismatch_is_repair(tmp_path):
    root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()
    before = store.layout.database.read_bytes()
    classified = store.classify()
    assert classified.error_code is StorageErrorCode.NEEDS_REPAIR
    with pytest.raises(StorageError) as error:
        store.migrate()
    assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    assert store.layout.database.read_bytes() == before


def test_checksum_mismatch_is_repair(tmp_path):
    root, store = _initialized(tmp_path)
    raw = sqlite3.connect(store.layout.database)
    raw.execute("UPDATE schema_migrations SET checksum = 'deadbeef' WHERE version = 1")
    raw.commit()
    raw.close()
    assert store.classify().error_code is StorageErrorCode.NEEDS_REPAIR


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
                "UPDATE store_identity SET schema_version = 1 WHERE singleton = 1"
            )
        )


def test_foreign_keys_are_enforced_on_reopen(tmp_path):
    root, store = _initialized(tmp_path, registry=_registry(V2_PROBE, supported=2))
    with store.open(StoreOpenMode.READ_WRITE) as session:
        assert session.schema_version == 2
        with pytest.raises(StorageError) as error:
            session.run_write(
                lambda executor: executor.execute(
                    "INSERT INTO probe_children(id, parent_id) VALUES (1, 99)"
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


def test_begin_immediate_contention_is_typed_busy(tmp_path):
    root, _store_obj = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_write_transaction, args=(str(root), ready, release))
    process.start()
    assert ready.wait(timeout=10)
    try:
        with (
            _store(root).open(StoreOpenMode.READ_WRITE) as session,
            pytest.raises(StorageError) as error,
        ):
            session.run_write(
                lambda executor: executor.execute(
                    "UPDATE store_identity SET application_name = application_name "
                    "WHERE singleton = 1"
                )
            )
        assert error.value.code is StorageErrorCode.BUSY
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_maintenance_lock_excludes_a_second_process(tmp_path):
    root, store = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_maintenance_lock, args=(str(root), ready, release))
    process.start()
    assert ready.wait(timeout=10)
    try:
        with pytest.raises(StorageError) as error, store.maintenance_lock():
            raise AssertionError("second process acquired the maintenance lock")
        assert error.value.code is StorageErrorCode.BUSY
        _assert_sanitized(error.value, str(root))
        with pytest.raises(StorageError) as migrate_error:
            _store(root, registry=_registry(V2_PROBE, supported=2)).migrate()
        assert migrate_error.value.code is StorageErrorCode.BUSY
        with pytest.raises(StorageError) as backup_error:
            store.backup()
        assert backup_error.value.code is StorageErrorCode.BUSY
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_dead_maintenance_lock_owner_releases_the_os_lock(tmp_path):
    root, store = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_maintenance_then_exit, args=(str(root), ready))
    process.start()
    assert ready.wait(timeout=10)
    process.join(timeout=10)
    assert process.exitcode == 17
    with store.maintenance_lock():
        assert store.layout.maintenance_lock.exists()


def test_ordered_checksummed_migration_rolls_back_a_failed_step(tmp_path):
    root, store = _initialized(tmp_path)
    good = _store(root, registry=_registry(V2_PROBE, supported=2))
    report = good.migrate()
    assert report.from_version == 1
    assert report.to_version == 2
    assert report.applied == ("test_probe_records",)
    assert report.backup_name
    assert (store.layout.backups_dir / report.backup_name).is_file()

    broken = SchemaMigration(
        version=3,
        name="broken_step",
        statements=("THIS IS NOT SQL",),
    )
    with pytest.raises(StorageError) as error:
        _store(root, registry=_registry(V2_PROBE, broken, supported=3)).migrate()
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    reopened = _store(root, registry=_registry(V2_PROBE, supported=2))
    assert reopened.classify().schema_version == 2


def test_interrupted_migration_before_commit_leaves_previous_version(tmp_path):
    root, _store_obj = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_migrate_and_exit, args=(str(root), "before_migration_commit"))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 17
    store = _store(root)
    assert store.classify().schema_version == 1
    assert store.layout.database.exists()


def test_migration_versus_writer_does_not_rewrite_the_file(tmp_path):
    root, _store_obj = _initialized(tmp_path)
    before = Path(root, STORE_DIRNAME, DATABASE_NAME).read_bytes()
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    result = context.Queue()
    holder = context.Process(target=_hold_write_transaction, args=(str(root), ready, release))
    migrator = context.Process(target=_migrate_v2, args=(str(root), result))
    holder.start()
    assert ready.wait(timeout=10)
    migrator.start()
    status, version = result.get(timeout=10)
    try:
        assert status == StorageErrorCode.BUSY.value
        assert version is None
        assert Path(root, STORE_DIRNAME, DATABASE_NAME).read_bytes() == before
    finally:
        release.set()
        holder.join(timeout=10)
        migrator.join(timeout=10)
    assert holder.exitcode == 0
    assert _store(root).classify().schema_version == 1


def test_sidecar_permissions_after_write(tmp_path):
    root, store = _initialized(tmp_path)
    with store.open(StoreOpenMode.READ_WRITE) as session:
        session.run_write(
            lambda executor: executor.execute(
                "UPDATE store_identity SET application_name = application_name WHERE singleton = 1"
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
                "UPDATE store_identity SET application_name = application_name WHERE singleton = 1"
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
            name = session.run_read(
                lambda executor: executor.execute(
                    "SELECT application_name FROM store_identity WHERE singleton = 1"
                )
            )
            assert name[0][0] == APPLICATION_NAME
    finally:
        store.layout.store_dir.chmod(0o700)
        store.layout.database.chmod(0o600)
        for sidecar in (store.layout.wal, store.layout.shm):
            if sidecar.exists():
                sidecar.chmod(0o600)


def test_write_failure_rolls_back(tmp_path):
    root, store = _initialized(tmp_path, registry=_registry(V2_PROBE, supported=2))

    def injector(point: str) -> None:
        if point == "before_commit":
            raise sqlite3.OperationalError("database or disk is full")

    failing = _store(root, registry=_registry(V2_PROBE, supported=2), failure_injector=injector)
    with failing.open(StoreOpenMode.READ_WRITE) as session, pytest.raises(StorageError) as error:
        session.run_write(
            lambda executor: executor.execute(
                "INSERT INTO probe_records(key, value) VALUES (?, ?)",
                ("lost", "value"),
            )
        )
    assert error.value.code is StorageErrorCode.UNAVAILABLE
    with store.open(StoreOpenMode.READ_WRITE) as session:
        count = session.run_read(
            lambda executor: executor.execute("SELECT COUNT(*) FROM probe_records")
        )
        assert count[0][0] == 0


def test_online_backup_during_writes_passes_integrity(tmp_path):
    root, store = _initialized(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    start = context.Event()
    process = context.Process(target=_touch_identity, args=(str(root), 20, ready, start))
    process.start()
    assert ready.wait(timeout=10)
    start.set()
    report = _store(root, retry_policy=_retry(busy_timeout_ms=250)).backup(
        "operational-copy.sqlite"
    )
    process.join(timeout=10)
    assert process.exitcode == 0
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
