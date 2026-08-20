"""Dry-run and exact-target managed orphan cleanup coverage."""

from __future__ import annotations

import os
import random

import pytest

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.artifacts import ArtifactService
from morrow.application.cleanup_fs import TrustedArtifactLayout
from morrow.application.doctor import OperationalDoctor
from morrow.core.artifacts import ArtifactKind, ArtifactRetention
from morrow.core.domain import DurableSession
from morrow.core.store import (
    FILE_MODE,
    OperationalStoreLayout,
    StorageError,
    StorageErrorCode,
    StoreOpenMode,
)
from morrow.testing import FixedClock, FixedIdSource


def test_orphan_cleanup_is_dry_run_by_default_and_only_removes_unmanaged_files(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    files = FilesystemArtifactStore(OperationalStoreLayout.from_root(tmp_path / "state"))
    artifacts = ArtifactService(
        journal=journal, filesystem=files, workspace_id="ws_1", id_source=FixedIdSource()
    )
    metadata = artifacts.publish_bytes(b"kept", kind=ArtifactKind.COMMAND_OUTPUT)
    unmanaged = files.artifacts_dir / "art_unmanaged.artifact"
    unmanaged.write_bytes(b"orphan")
    unmanaged.chmod(0o600)
    api = OperationalApplicationService(
        journal=journal, workspace_id="ws_1", id_source=FixedIdSource(), artifacts=artifacts
    )
    preview = api.cleanup_orphans()
    assert preview.dry_run and preview.eligible == 1 and unmanaged.exists()
    result = api.cleanup_orphans(dry_run=False)
    assert result.removed == 0
    assert result.quarantined == 1
    assert result.reasons == ("managed_metadata_preserved", "quarantine_retained")
    assert not unmanaged.exists()
    assert files.final_path(metadata.artifact_id).exists()
    handle.close()


def test_orphan_cleanup_preserves_artifacts_from_every_workspace(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        journal.create_session(DurableSession(session_id="ses_2", workspace_id="ws_2"))
        files = FilesystemArtifactStore(store.layout)
        first_service = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        second_service = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_2",
            id_source=FixedIdSource(),
        )
        first = first_service.publish_bytes(
            b"workspace one", kind=ArtifactKind.COMMAND_OUTPUT, artifact_id="art_ws1"
        )
        second = second_service.publish_bytes(
            b"workspace two", kind=ArtifactKind.COMMAND_OUTPUT, artifact_id="art_ws2"
        )
        unmanaged = files.artifacts_dir / "art_unmanaged.artifact"
        unmanaged.write_bytes(b"orphan")
        unmanaged.chmod(0o600)
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_2",
            id_source=FixedIdSource(),
            artifacts=second_service,
        )

        preview = api.cleanup_orphans()
        assert preview.inspected == 3
        assert preview.eligible == 1
        assert preview.refused == 2
        assert preview.reasons == ("managed_metadata_preserved",)

        result = api.cleanup_orphans(dry_run=False)
        assert result.removed == 0
        assert result.quarantined == 1
        assert not unmanaged.exists()
        assert files.existing_final_path(first.artifact_id).read_bytes() == b"workspace one"
        assert files.existing_final_path(second.artifact_id).read_bytes() == b"workspace two"
        assert journal.get_artifact("ws_1", first.artifact_id) == first
        assert journal.get_artifact("ws_2", second.artifact_id) == second
    finally:
        handle.close()


def test_managed_artifact_tmp_directory_is_not_an_orphan_candidate(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        metadata = artifacts.publish_bytes(
            b"pinned",
            kind=ArtifactKind.DIAGNOSTIC_REPORT,
            retention=ArtifactRetention.PINNED,
        )
        unmanaged_temp = files.artifacts_tmp / "art_stale.artifact.tmp"
        unmanaged_temp.write_bytes(b"incomplete")
        unmanaged_temp.chmod(0o600)

        orphan = artifacts.orphan_report()
        assert files.artifacts_tmp not in {candidate.path for candidate in orphan.candidates}
        assert {candidate.artifact_id for candidate in orphan.candidates} == {"art_stale"}

        unmanaged_temp.unlink()
        doctor = OperationalDoctor(store).inspect("ws_1")
        assert doctor.counts["artifacts"] == 1
        assert doctor.counts["orphan_candidates"] == 0
        assert all(issue.code != "artifact_orphan" for issue in doctor.issues)
        assert files.existing_final_path(metadata.artifact_id).exists()
    finally:
        handle.close()


def test_cleanup_refuses_artifacts_symlink_without_touching_external_files(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        parked = tmp_path / "parked-artifacts"
        files.artifacts_dir.rename(parked)
        external = tmp_path / "external-artifacts"
        external.mkdir(mode=0o700)
        external_target = external / "art_external.artifact"
        external_target.write_bytes(b"must survive")
        external_target.chmod(FILE_MODE)
        files.artifacts_dir.symlink_to(external, target_is_directory=True)

        with pytest.raises(StorageError) as error:
            api.cleanup_orphans(dry_run=False)

        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert external_target.read_bytes() == b"must survive"
        assert (parked / "tmp").is_dir()
    finally:
        handle.close()


def test_cleanup_refuses_artifacts_directory_replaced_after_scan(tmp_path, monkeypatch):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"trusted-chain orphan")
        target.chmod(FILE_MODE)
        parked = tmp_path / "parked-after-scan"
        external = tmp_path / "external-after-scan"
        external.mkdir(mode=0o700)
        external_target = external / "art_external.artifact"
        external_target.write_bytes(b"external survivor")
        external_target.chmod(FILE_MODE)
        original_authority_check = journal.has_global_artifact_authority
        replaced = False

        def replace_before_authority_result(artifact_id):
            nonlocal replaced
            result = original_authority_check(artifact_id)
            if not replaced:
                replaced = True
                files.artifacts_dir.rename(parked)
                files.artifacts_dir.symlink_to(external, target_is_directory=True)
            return result

        monkeypatch.setattr(
            journal,
            "has_global_artifact_authority",
            replace_before_authority_result,
        )

        with pytest.raises(StorageError) as error:
            api.cleanup_orphans(dry_run=False)

        assert error.value.code is StorageErrorCode.UNAVAILABLE
        assert replaced
        assert (parked / target.name).read_bytes() == b"trusted-chain orphan"
        assert external_target.read_bytes() == b"external survivor"
        assert tuple(parked.glob("*.quarantine")) == ()
    finally:
        handle.close()


@pytest.mark.parametrize("occupy_original", [False, True])
def test_cleanup_quarantines_a_swapped_entry_without_deleting_it(
    tmp_path, monkeypatch, occupy_original
):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"original orphan")
        target.chmod(FILE_MODE)
        saved = files.artifacts_dir / "art_saved.artifact"
        original_rename = os.rename
        swapped = False

        def swap_before_quarantine(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            nonlocal swapped
            if src == target.name and dst == "payload" and not swapped:
                swapped = True
                original_rename(
                    src,
                    saved.name,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=src_dir_fd,
                )
                descriptor = os.open(
                    src,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    FILE_MODE,
                    dir_fd=src_dir_fd,
                )
                try:
                    os.write(descriptor, b"replacement")
                finally:
                    os.close(descriptor)
                result = original_rename(
                    src,
                    dst,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )
                if occupy_original:
                    descriptor = os.open(
                        src,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        FILE_MODE,
                        dir_fd=src_dir_fd,
                    )
                    try:
                        os.write(descriptor, b"new occupant")
                    finally:
                        os.close(descriptor)
                return result
            return original_rename(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(os, "rename", swap_before_quarantine)
        result = api.cleanup_orphans(dry_run=False)

        assert swapped
        assert result.removed == 0
        assert result.refused == 1
        assert saved.read_bytes() == b"original orphan"
        quarantines = tuple(files.artifacts_dir.glob("*.quarantine"))
        if occupy_original:
            assert result.reasons == ("target_changed_quarantined",)
            assert target.read_bytes() == b"new occupant"
            assert len(quarantines) == 1
            assert (quarantines[0] / "payload").read_bytes() == b"replacement"
        else:
            assert result.reasons == ("quarantine_retained", "target_changed")
            assert target.read_bytes() == b"replacement"
            assert len(quarantines) == 1
            assert (quarantines[0] / "payload").samefile(target)
    finally:
        handle.close()


@pytest.mark.parametrize("payload_state", ["missing", "renamed"])
def test_restore_missing_quarantine_payload_does_not_leak_descriptors(tmp_path, payload_state):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        files = FilesystemArtifactStore(store.layout)
        files.ensure_layout()
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"retained payload")
        target.chmod(FILE_MODE)

        with TrustedArtifactLayout.open(files) as layout:
            candidate = layout.scan((), frozenset())[0]
            attempt = layout.quarantine(candidate, on_moved=lambda _target: None)
            assert attempt.status == "quarantined"
            assert attempt.quarantine is not None
            quarantine_path = files.artifacts_dir / attempt.quarantine.directory_name
            payload = quarantine_path / "payload"
            if payload_state == "missing":
                payload.unlink()
            else:
                payload.rename(quarantine_path / "payload-renamed")

            descriptor_directory = next(
                (path for path in ("/dev/fd", "/proc/self/fd") if os.path.isdir(path)),
                None,
            )
            if descriptor_directory is None:
                pytest.skip("platform does not expose a process descriptor directory")
            baseline = len(os.listdir(descriptor_directory))

            for _attempt in range(50):
                assert not layout.restore_quarantine(attempt.quarantine)

            assert len(os.listdir(descriptor_directory)) == baseline
    finally:
        handle.close()


@pytest.mark.parametrize("fault_point", ["before_commit", "after_commit"])
def test_cleanup_commit_fault_is_not_replayed_and_restores_bytes(tmp_path, fault_point):
    state = {"armed": False, "failures": 0}

    def fail_commit(point):
        if state["armed"] and point == fault_point:
            state["armed"] = False
            state["failures"] += 1
            raise StorageError(StorageErrorCode.BUSY, "injected cleanup contention")

    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            attempts=8,
            busy_timeout_ms=0,
            sleep=lambda _delay: None,
            rng=random.Random(0),
        ),
        maintenance_timeout=0,
        failure_injector=fail_commit,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"restored after rollback")
        target.chmod(FILE_MODE)
        state["armed"] = True

        with pytest.raises(StorageError) as error:
            api.cleanup_orphans(dry_run=False)

        assert error.value.code is StorageErrorCode.BUSY
        assert state["failures"] == 1
        assert target.read_bytes() == b"restored after rollback"
        quarantines = tuple(files.artifacts_dir.glob("*.quarantine"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "payload").samefile(target)

        retried = api.cleanup_orphans(dry_run=False)
        assert retried.removed == 0
        assert retried.eligible == 0
        assert target.read_bytes() == b"restored after rollback"
    finally:
        handle.close()


def test_cleanup_quarantined_means_original_managed_path_was_retired(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"retire exact bytes")
        target.chmod(FILE_MODE)

        result = api.cleanup_orphans(dry_run=False)

        assert result.removed == 0
        assert result.quarantined == 1
        assert result.reasons == ("quarantine_retained",)
        assert not target.exists()
        quarantines = tuple(files.artifacts_dir.glob("*.quarantine"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "payload").read_bytes() == b"retire exact bytes"
    finally:
        handle.close()


def test_cleanup_quarantine_never_unlinks_a_replacement(tmp_path, monkeypatch):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"approved inode")
        target.chmod(FILE_MODE)
        original_transact_once = journal.transact_once
        original_unlink = os.unlink
        swapped = False
        payload_unlinks = 0
        payload_truncates = 0

        def swap_after_commit(work):
            nonlocal swapped
            result = original_transact_once(work)
            if getattr(result, "status", None) == "quarantined" and not swapped:
                quarantines = tuple(files.artifacts_dir.glob("*.quarantine"))
                assert len(quarantines) == 1
                quarantine = quarantines[0]
                (quarantine / "payload").rename(quarantine / "saved-original")
                replacement = quarantine / "payload"
                replacement.write_bytes(b"replacement survives")
                replacement.chmod(FILE_MODE)
                swapped = True
            return result

        def refuse_payload_truncate(descriptor, length):
            nonlocal payload_truncates
            payload_truncates += 1
            raise AssertionError("cleanup must not truncate a quarantined inode")

        def refuse_payload_unlink(path, *, dir_fd=None):
            nonlocal payload_unlinks
            if path == "payload":
                payload_unlinks += 1
                raise AssertionError("cleanup must not unlink a post-check name")
            return original_unlink(path, dir_fd=dir_fd)

        monkeypatch.setattr(journal, "transact_once", swap_after_commit)
        monkeypatch.setattr(os, "ftruncate", refuse_payload_truncate)
        monkeypatch.setattr(os, "unlink", refuse_payload_unlink)
        result = api.cleanup_orphans(dry_run=False)

        assert swapped
        assert payload_unlinks == 0
        assert payload_truncates == 0
        assert result.removed == 0
        assert result.quarantined == 1
        assert not target.exists()
        quarantine = tuple(files.artifacts_dir.glob("*.quarantine"))[0]
        assert (quarantine / "saved-original").read_bytes() == b"approved inode"
        assert (quarantine / "payload").read_bytes() == b"replacement survives"
    finally:
        handle.close()


def test_cleanup_quarantine_never_truncates_a_new_outside_hardlink(tmp_path, monkeypatch):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_unmanaged.artifact"
        target.write_bytes(b"outside link survives")
        target.chmod(FILE_MODE)
        outside = tmp_path / "outside-hardlink"
        original_transact_once = journal.transact_once
        linked = False

        def link_after_commit(work):
            nonlocal linked
            result = original_transact_once(work)
            if getattr(result, "status", None) == "quarantined" and not linked:
                quarantine = tuple(files.artifacts_dir.glob("*.quarantine"))[0]
                os.link(quarantine / "payload", outside)
                linked = True
            return result

        def refuse_truncate(descriptor, length):
            raise AssertionError("cleanup must not truncate through a raced hard link")

        monkeypatch.setattr(journal, "transact_once", link_after_commit)
        monkeypatch.setattr(os, "ftruncate", refuse_truncate)
        result = api.cleanup_orphans(dry_run=False)

        assert linked
        assert result.removed == 0
        assert result.quarantined == 1
        assert not target.exists()
        quarantine = tuple(files.artifacts_dir.glob("*.quarantine"))[0]
        assert (quarantine / "payload").read_bytes() == b"outside link survives"
        assert outside.read_bytes() == b"outside link survives"
    finally:
        handle.close()


def test_cleanup_after_commit_authority_never_restores_old_bytes(tmp_path):
    state = {"armed": False, "fired": False}
    root = tmp_path / "state"
    published = {}

    def publish_authority():
        second_store = OperationalStore(
            root,
            clock=FixedClock(),
            retry_policy=BusyRetryPolicy(
                busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(1)
            ),
            maintenance_timeout=0,
        )
        second_handle = second_store.open(StoreOpenMode.READ_WRITE)
        try:
            second_journal = SqliteOperationalJournal(second_handle)
            second_files = FilesystemArtifactStore(second_store.layout)
            second_artifacts = ArtifactService(
                journal=second_journal,
                filesystem=second_files,
                workspace_id="ws_2",
                id_source=FixedIdSource(),
            )
            metadata = second_artifacts.publish_bytes(
                b"authoritative bytes",
                kind=ArtifactKind.COMMAND_OUTPUT,
                artifact_id="art_race",
            )
            second_files.verify(metadata)
            published["metadata"] = metadata
        finally:
            second_handle.close()

    def fail_after_commit(point):
        if state["armed"] and point == "after_commit" and not state["fired"]:
            state["fired"] = True
            state["armed"] = False
            publish_authority()
            raise StorageError(StorageErrorCode.BUSY, "injected post-commit uncertainty")

    store = OperationalStore(
        root,
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            attempts=8,
            busy_timeout_ms=0,
            sleep=lambda _delay: None,
            rng=random.Random(0),
        ),
        maintenance_timeout=0,
        failure_injector=fail_after_commit,
    )
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle)
        journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
        files = FilesystemArtifactStore(store.layout)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=files,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
        )
        api = OperationalApplicationService(
            journal=journal,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            artifacts=artifacts,
        )
        target = files.artifacts_dir / "art_race.artifact"
        target.write_bytes(b"old orphan bytes")
        target.chmod(FILE_MODE)
        state["armed"] = True

        with pytest.raises(StorageError) as error:
            api.cleanup_orphans(dry_run=False)

        assert error.value.code is StorageErrorCode.BUSY
        assert state["fired"]
        metadata = published["metadata"]
        assert journal.get_artifact("ws_2", "art_race") == metadata
        assert target.read_bytes() == b"authoritative bytes"
        files.verify(metadata)
        quarantines = tuple(files.artifacts_dir.glob("*.quarantine"))
        assert len(quarantines) == 1
        assert (quarantines[0] / "payload").read_bytes() == b"old orphan bytes"

        retried = api.cleanup_orphans(dry_run=False)
        assert retried.removed == 0
        assert target.read_bytes() == b"authoritative bytes"
        files.verify(metadata)
    finally:
        handle.close()
