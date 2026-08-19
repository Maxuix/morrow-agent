"""Subplan 41 Artifact metadata, publication, integrity, and retention tests."""

from __future__ import annotations

import random
import stat
from pathlib import Path

import pytest

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.artifacts import ArtifactService, bounded_utf8_excerpt
from morrow.core.artifacts import (
    ARTIFACT_EXCERPT_MAX_BYTES,
    ARTIFACT_MAX_BYTES,
    TASK_ARTIFACT_MAX_BYTES,
    ArtifactBudgetError,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactIntegrityError,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactProvenanceKind,
    ArtifactProvenanceRef,
    ArtifactSensitivity,
    ArtifactState,
)
from morrow.core.domain import (
    AgentRunSnapshot,
    ArtifactReference,
    DurableAgentRun,
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    TaskOutcome,
    TaskOutcomeTrigger,
    TaskRunStatus,
)
from morrow.core.execution import DurableToolExecution, EffectClass, PreparedIntent
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import ModelRef, Preferences, Profile
from morrow.core.store import DIRECTORY_MODE, FILE_MODE, StorageError, StoreOpenMode
from morrow.testing import FixedClock, FixedIdSource


def _service(tmp_path: Path):
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
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id="ws_1"),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id="ws_1"),
    )
    filesystem = FilesystemArtifactStore(store.layout)
    service = ArtifactService(
        journal=journal,
        filesystem=filesystem,
        workspace_id="ws_1",
        id_source=FixedIdSource(),
        clock=FixedClock().now,
    )
    return handle, journal, filesystem, service


def test_artifact_publication_is_atomic_private_and_restart_readable(tmp_path):
    handle, journal, filesystem, service = _service(tmp_path)
    try:
        content = ("前缀😀" * 20).encode("utf-8")
        metadata = service.publish_bytes(
            content,
            kind=ArtifactKind.TEST_REPORT,
            session_id="ses_1",
            task_run_id="task_1",
        )
        assert metadata.state is ArtifactState.AVAILABLE
        assert metadata.byte_size == len(content)
        assert metadata.excerpt.encode("utf-8")[:ARTIFACT_EXCERPT_MAX_BYTES]
        final = filesystem.final_path(metadata.artifact_id)
        assert final.read_bytes() == content
        assert stat.S_IMODE(final.stat().st_mode) == FILE_MODE
        assert stat.S_IMODE(filesystem.artifacts_dir.stat().st_mode) == DIRECTORY_MODE
        assert not filesystem.temp_path(metadata.artifact_id).exists()
        assert service.read(metadata.artifact_id, max_bytes=len(content)).content == content

        handle.close()
        reopened = OperationalStore(tmp_path / "state", maintenance_timeout=0).open(
            StoreOpenMode.READ_WRITE
        )
        try:
            reopened_journal = SqliteOperationalJournal(reopened)
            assert reopened_journal.get_artifact("ws_1", metadata.artifact_id) == metadata
        finally:
            reopened.close()
    finally:
        if not getattr(handle, "_closed", True):
            handle.close()


def test_excerpt_never_splits_utf8_and_metadata_rejects_overflow(tmp_path):
    content = "😀" * (ARTIFACT_EXCERPT_MAX_BYTES // 4 + 1)
    excerpt = bounded_utf8_excerpt(content.encode("utf-8"))
    assert len(excerpt.encode("utf-8")) <= ARTIFACT_EXCERPT_MAX_BYTES
    assert not excerpt.endswith("\ufffd")

    _handle, _journal, _filesystem, service = _service(tmp_path)
    with pytest.raises(ArtifactBudgetError) as error:
        service.publish_bytes(
            b"x" * (ARTIFACT_MAX_BYTES + 1),
            kind=ArtifactKind.DIAGNOSTIC_REPORT,
            session_id="ses_1",
            task_run_id="task_1",
        )
    assert error.value.code is ArtifactErrorCode.BUDGET


def test_provenance_and_secret_redaction_are_checked_before_reserve(tmp_path):
    handle, journal, _filesystem, service = _service(tmp_path)
    try:
        provenance = ArtifactProvenanceRef(
            kind=ArtifactProvenanceKind.TOOL_EXECUTION,
            reference_id="tex_1",
        )
        with pytest.raises(ArtifactError) as error:
            service.publish_bytes(
                b"password=do-not-store",
                kind=ArtifactKind.COMMAND_OUTPUT,
                session_id="ses_1",
                task_run_id="task_1",
                provenance_refs=(provenance,),
            )
        assert error.value.code is ArtifactErrorCode.INVALID
        assert journal.list_artifacts("ws_1") == ()
    finally:
        handle.close()


def test_fault_after_reserve_leaves_explicit_staging_record(tmp_path):
    handle, journal, filesystem, _service_without_fault = _service(tmp_path)
    try:
        faults = OnceFaultInjector(FaultPoint.ARTIFACT_AFTER_RESERVE)
        service = ArtifactService(
            journal=journal,
            filesystem=filesystem,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
            faults=faults,
        )
        with pytest.raises(InjectedFault):
            service.publish_bytes(
                b"staged",
                kind=ArtifactKind.DIAGNOSTIC_REPORT,
                session_id="ses_1",
                task_run_id="task_1",
            )
        metadata = journal.list_artifacts("ws_1")
        assert len(metadata) == 1
        assert metadata[0].state is ArtifactState.STAGING
        assert not filesystem.final_path(metadata[0].artifact_id).exists()
    finally:
        handle.close()


@pytest.mark.parametrize(
    ("point", "final_exists"),
    (
        (FaultPoint.ARTIFACT_AFTER_TEMP_CREATE, False),
        (FaultPoint.ARTIFACT_FILE_FSYNC, False),
        (FaultPoint.ARTIFACT_BEFORE_RENAME, False),
        (FaultPoint.ARTIFACT_AFTER_RENAME, True),
        (FaultPoint.ARTIFACT_AFTER_PARENT_FSYNC, True),
        (FaultPoint.ARTIFACT_BEFORE_MARK_AVAILABLE, True),
        (FaultPoint.ARTIFACT_AFTER_MARK_AVAILABLE, True),
    ),
)
def test_each_publication_fault_point_leaves_a_recoverable_truthful_state(
    tmp_path, point, final_exists
):
    handle, journal, filesystem, _service_without_fault = _service(tmp_path)
    try:
        service = ArtifactService(
            journal=journal,
            filesystem=filesystem,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
            faults=OnceFaultInjector(point),
        )
        with pytest.raises(InjectedFault):
            service.publish_bytes(
                b"fault boundary",
                kind=ArtifactKind.DIAGNOSTIC_REPORT,
                session_id="ses_1",
                task_run_id="task_1",
            )
        metadata = journal.list_artifacts("ws_1")[0]
        if point is FaultPoint.ARTIFACT_AFTER_MARK_AVAILABLE:
            assert metadata.state is ArtifactState.AVAILABLE
        else:
            assert metadata.state is ArtifactState.STAGING
        assert filesystem.final_path(metadata.artifact_id).exists() is final_exists
    finally:
        handle.close()


def test_disk_write_failure_preserves_staging_without_host_details(tmp_path, monkeypatch):
    handle, journal, filesystem, service = _service(tmp_path)
    try:

        def fail_write(_descriptor, _content):
            raise OSError("simulated disk full at a private path")

        monkeypatch.setattr("morrow.adapters.state.artifacts.os.write", fail_write)
        with pytest.raises(StorageError) as error:
            service.publish_bytes(
                b"disk failure",
                kind=ArtifactKind.DIAGNOSTIC_REPORT,
                session_id="ses_1",
                task_run_id="task_1",
            )
        assert error.value.code.value == "unavailable"
        assert "private path" not in str(error.value)
        assert journal.list_artifacts("ws_1")[0].state is ArtifactState.STAGING
    finally:
        handle.close()


def test_hardlink_collision_is_rejected_without_touching_the_external_file(tmp_path):
    handle, journal, filesystem, service = _service(tmp_path)
    try:
        external = tmp_path / "external-bytes"
        external.write_bytes(b"external")
        collision = filesystem.temp_path("art_collision")
        collision.hardlink_to(external)
        with pytest.raises(ArtifactIntegrityError):
            service.publish_bytes(
                b"replacement",
                kind=ArtifactKind.PATCH,
                session_id="ses_1",
                task_run_id="task_1",
                artifact_id="art_collision",
            )
        assert external.read_bytes() == b"external"
        assert collision.stat().st_nlink == 2
        assert journal.get_artifact("ws_1", "art_collision").state is ArtifactState.CORRUPT
    finally:
        handle.close()


def test_task_artifact_budget_is_reserved_atomically_in_sqlite(tmp_path):
    handle, journal, _filesystem, service = _service(tmp_path)
    try:
        for index in range(4):
            journal.reserve_artifact(
                "ws_1",
                ArtifactMetadata(
                    artifact_id=f"art_budget_{index}",
                    workspace_id="ws_1",
                    session_id="ses_1",
                    task_run_id="task_1",
                    kind=ArtifactKind.DIAGNOSTIC_REPORT,
                    sensitivity=ArtifactSensitivity.REDACTED,
                    sha256="0" * 64,
                    byte_size=ARTIFACT_MAX_BYTES,
                ),
            )
        assert journal.artifact_bytes_for_task("ws_1", "task_1") == TASK_ARTIFACT_MAX_BYTES
        with pytest.raises(ArtifactBudgetError):
            journal.reserve_artifact(
                "ws_1",
                ArtifactMetadata(
                    artifact_id="art_budget_over",
                    workspace_id="ws_1",
                    session_id="ses_1",
                    task_run_id="task_1",
                    kind=ArtifactKind.DIAGNOSTIC_REPORT,
                    sensitivity=ArtifactSensitivity.REDACTED,
                    sha256="1" * 64,
                    byte_size=1,
                ),
            )
        assert len(journal.list_artifacts("ws_1")) == 4
    finally:
        handle.close()


def test_already_redacted_command_output_keeps_safe_fields_but_rejects_raw_values(tmp_path):
    handle, journal, _filesystem, service = _service(tmp_path)
    try:
        metadata = service.publish_command_output(
            '{"password":"<redacted>","token":"Bearer <redacted>","stdout":"ok"}',
            session_id="ses_1",
            task_run_id="task_1",
            tool_execution_id="tex_1",
        )
        assert service.read(metadata.artifact_id, max_bytes=1024).content.startswith(b'{"password"')
        with pytest.raises(ArtifactError) as error:
            service.publish_command_output(
                "password=raw-secret",
                session_id="ses_1",
                task_run_id="task_1",
                tool_execution_id="tex_2",
            )
        assert error.value.code is ArtifactErrorCode.INVALID
        with pytest.raises(ArtifactError):
            service.publish_command_output(
                "password=<redacted>raw-suffix",
                session_id="ses_1",
                task_run_id="task_1",
                tool_execution_id="tex_3",
            )
        assert len(journal.list_artifacts("ws_1")) == 1
    finally:
        handle.close()


def test_fault_after_rename_and_integrity_failure_are_visible(tmp_path):
    handle, journal, filesystem, _service_without_fault = _service(tmp_path)
    try:
        faults = OnceFaultInjector(FaultPoint.ARTIFACT_AFTER_RENAME)
        service = ArtifactService(
            journal=journal,
            filesystem=filesystem,
            workspace_id="ws_1",
            id_source=FixedIdSource(),
            clock=FixedClock().now,
            faults=faults,
        )
        with pytest.raises(InjectedFault):
            service.publish_bytes(
                b"published-but-unmarked",
                kind=ArtifactKind.PATCH,
                session_id="ses_1",
                task_run_id="task_1",
            )
        metadata = journal.list_artifacts("ws_1")[0]
        assert metadata.state is ArtifactState.STAGING
        assert filesystem.final_path(metadata.artifact_id).exists()
        report = service.orphan_report()
        assert any(item.artifact_id == metadata.artifact_id for item in report.candidates)

        filesystem.final_path(metadata.artifact_id).write_bytes(b"tampered")
        journal.save_artifact(
            "ws_1",
            metadata.model_copy(
                update={
                    "state": ArtifactState.AVAILABLE,
                    "row_version": metadata.row_version + 1,
                }
            ),
            expected_row_version=metadata.row_version,
        )
        with pytest.raises(ArtifactIntegrityError):
            service.read(metadata.artifact_id, max_bytes=100)
        assert service.get(metadata.artifact_id).state is ArtifactState.CORRUPT
    finally:
        handle.close()


def test_symlink_collision_and_retention_reports_never_delete(tmp_path):
    handle, journal, filesystem, service = _service(tmp_path)
    try:
        first = service.publish_bytes(
            b"first", kind=ArtifactKind.PATCH, session_id="ses_1", task_run_id="task_1"
        )
        second = service.publish_bytes(
            b"second", kind=ArtifactKind.TEST_REPORT, session_id="ses_1", task_run_id="task_1"
        )
        service.pin(second.artifact_id)
        target = tmp_path / "outside"
        target.write_bytes(b"outside")
        first_path = filesystem.final_path(first.artifact_id)
        first_path.unlink()
        first_path.symlink_to(target)
        with pytest.raises(ArtifactIntegrityError):
            service.read(first.artifact_id, max_bytes=100)
        assert target.read_bytes() == b"outside"
        report = service.retention_report()
        assert first.artifact_id in report.candidates
        assert second.artifact_id in report.pinned
        assert first_path.is_symlink()
        assert journal.get_artifact("ws_1", first.artifact_id) is not None
    finally:
        handle.close()


def test_task_outcome_artifact_reference_round_trips_and_is_retained(tmp_path):
    handle, journal, _filesystem, service = _service(tmp_path)
    try:
        metadata = service.publish_bytes(
            b"outcome evidence",
            kind=ArtifactKind.TASK_SUMMARY,
            session_id="ses_1",
            task_run_id="task_1",
        )
        reference = ArtifactReference(artifact_id=metadata.artifact_id, role="summary")
        outcome = TaskOutcome(
            outcome_id="out_1",
            workspace_id="ws_1",
            session_id="ses_1",
            task_run_id="task_1",
            version=1,
            trigger=TaskOutcomeTrigger.SNAPSHOT,
            task_status=TaskRunStatus.OPEN,
            summary="snapshot",
            artifact_refs=(reference,),
        )
        stored = journal.put_task_outcome("ws_1", outcome)
        assert stored.artifact_refs == (reference,)
        assert journal.get_task_outcome("ws_1", "out_1").artifact_refs == (reference,)
        assert journal.list_artifact_references("ws_1", metadata.artifact_id) == (
            (metadata.artifact_id, "task_outcome", "out_1", "summary"),
        )
        report = service.retention_report()
        assert metadata.artifact_id in report.referenced
        assert metadata.artifact_id not in report.candidates
    finally:
        handle.close()


def test_tool_execution_can_link_a_command_artifact(tmp_path):
    handle, journal, _filesystem, service = _service(tmp_path)
    try:
        journal.create_turn(
            "ws_1",
            DurableTurn(
                turn_id="turn_1",
                session_id="ses_1",
                task_run_id="task_1",
                client_message_id="client-1",
            ),
        )
        digest = "0" * 64
        journal.create_agent_run(
            "ws_1",
            DurableAgentRun(
                agent_run_id="arun_1",
                turn_id="turn_1",
                session_id="ses_1",
                snapshot=AgentRunSnapshot(
                    profile=Profile(name="demo"),
                    preferences=Preferences(),
                    model=ModelRef(provider_id="p", model_id="m"),
                    provider_id="p",
                    run_policy_digest=digest,
                    tool_schema_digest=digest,
                    permission_profile_digest=digest,
                    runtime_instance_id="host-1",
                ),
            ),
        )
        intent = PreparedIntent(
            tool_name="run_command",
            call_id="call1",
            ordinal=1,
            arguments_digest=digest,
            schema_digest=digest,
            permission_context_digest=digest,
            effect_class=EffectClass.PROCESS_EFFECT_NON_DURABLE,
        )
        execution = journal.put_execution(
            "ws_1",
            DurableToolExecution(
                tool_execution_id="tex_1",
                workspace_id="ws_1",
                session_id="ses_1",
                task_run_id="task_1",
                turn_id="turn_1",
                agent_run_id="arun_1",
                call_id="call1",
                ordinal=1,
                tool_name="run_command",
                intent=intent,
            ),
        )
        artifact = service.publish_command_output(
            '{"stdout":"ok"}',
            session_id="ses_1",
            task_run_id="task_1",
            tool_execution_id=execution.tool_execution_id,
        )
        reference = ArtifactReference(artifact_id=artifact.artifact_id, role="tool_output")
        linked = journal.save_execution(
            "ws_1",
            execution.model_copy(
                update={"artifact_refs": (reference,), "row_version": execution.row_version + 1}
            ),
            expected_row_version=execution.row_version,
        )
        assert linked.artifact_refs == (reference,)
        assert journal.get_execution("ws_1", "tex_1").artifact_refs == (reference,)
        assert journal.list_artifact_references("ws_1", artifact.artifact_id) == (
            (artifact.artifact_id, "tool_execution", "tex_1", "tool_output"),
        )
    finally:
        handle.close()
