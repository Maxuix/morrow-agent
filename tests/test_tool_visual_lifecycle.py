"""Image evidence survives checkpoint/cleanup/backup; doctor remains read-only."""

import test_computer_visual_service as visual_tests
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.backup import OperationalBackupService
from morrow.application.checkpoints import ContextCheckpointService
from morrow.application.cleanup import ArtifactCleanupService
from morrow.application.computer_visuals import ComputerVisualService
from morrow.application.doctor import OperationalDoctor
from morrow.core.domain import sha256_digest
from morrow.core.models import AssistantMessage, FinishReason, FunctionToolCall, UserMessage
from morrow.core.store import StoreOpenMode
from morrow.runtime.conversation import ConversationLog
from morrow.runtime.durable_log import DurableConversationWriter
from morrow.testing import FixedClock, FixedIdSource
from test_computer_visual_service import complete, publish

environment = visual_tests.environment


def closed_image_history(environment):
    service, journal, _, _, _, _ = environment
    reference = publish(environment)
    complete(environment, reference, write_history=False)
    log = ConversationLog()
    writer = DurableConversationWriter(
        log, journal, workspace_id="ws_a", session_id="ses_1", id_source=FixedIdSource()
    )
    writer.commit(log.plan_begin_turn(UserMessage(content="inspect fixture")))
    writer.commit(
        log.plan_append_assistant(
            AssistantMessage(
                tool_calls=(FunctionToolCall(id="call1", name="computer_observe", arguments="{}"),)
            )
        )
    )
    writer.commit(log.plan_append_tool_result("call1", "{}", visual_refs=(reference,)))
    writer.commit(log.plan_append_assistant(AssistantMessage(content="observed")))
    writer.commit(log.plan_finish_turn(FinishReason.STOP))
    return reference


def test_refs_are_retained_by_execution_conversation_and_checkpoint_and_cleanup_keeps_bytes(
    environment,
):
    service, journal, _, _, _, _ = environment
    reference = closed_image_history(environment)
    refs = journal.list_artifact_references("ws_a", reference.artifact_id)
    assert {item[1] for item in refs} >= {"tool_execution", "conversation_record"}
    retention = service.artifacts.retention_report()
    assert reference.artifact_id in retention.referenced
    assert reference.artifact_id not in retention.candidates
    checkpoint = ContextCheckpointService(
        journal, workspace_id="ws_a", id_source=FixedIdSource(), clock=FixedClock()
    ).create("ses_1", task_run_id="task_1", checkpoint_id="chk_visual")
    assert reference.artifact_id in {item.artifact_id for item in checkpoint.artifact_refs}
    refs = journal.list_artifact_references("ws_a", reference.artifact_id)
    assert "context_checkpoint" in {item[1] for item in refs}
    data = service.artifacts.filesystem.final_path(reference.artifact_id).read_bytes()
    ArtifactCleanupService(service.artifacts).run(dry_run=False)
    assert service.artifacts.filesystem.final_path(reference.artifact_id).read_bytes() == data
    assert service.read(reference, session_id="ses_1").content == data


def test_backup_restore_preserves_actual_image_and_source_authority(environment, tmp_path):
    service, _, _, _, _, _ = environment
    reference = closed_image_history(environment)
    store = OperationalStore(service.artifacts.filesystem.layout.data_root)
    backup = OperationalBackupService(store)
    report = backup.create("visual-evidence")
    bundle = store.layout.backups_dir / report.bundle_name
    assert backup.verify(bundle).ok
    restored_root = tmp_path / "restore"
    assert backup.restore(bundle, restored_root).restored
    restored = OperationalStore(restored_root)
    handle = restored.open(StoreOpenMode.READ_ONLY)
    try:
        journal = SqliteOperationalJournal(handle)
        artifacts = ArtifactService(
            journal=journal,
            filesystem=FilesystemArtifactStore(restored.layout),
            workspace_id="ws_a",
            id_source=FixedIdSource(),
        )
        resolver = ComputerVisualService(artifacts, journal, clock=FixedClock().now)
        capture = resolver.read(reference, session_id="ses_1")
        assert sha256_digest(capture.content) == reference.sha256
        assert journal.get_execution("ws_a", "tex_1").result_envelope.visual_refs == (reference,)
        assert "conversation_record" in {
            item[1] for item in journal.list_artifact_references("ws_a")
        }
    finally:
        handle.close()


def test_doctor_audits_visual_source_read_only_without_constructing_driver(environment):
    import morrow.adapters.computer_use as adapter

    service, journal, _, _, _, _ = environment
    reference = closed_image_history(environment)
    store = OperationalStore(service.artifacts.filesystem.layout.data_root)
    before = adapter.DRIVER_CONSTRUCTION_COUNT
    execution = journal.get_execution("ws_a", "tex_1")
    metadata = service.artifacts.get(reference.artifact_id)
    report = OperationalDoctor(store).inspect("ws_a")
    assert "tool_visual_source" not in {issue.code for issue in report.issues}
    assert report.counts["tool_visual_references"] == 1
    # Damage the source metadata without touching the image bytes; the doctor
    # must catch attribution failure that the generic file-hash audit cannot.
    # Simulate out-of-band database corruption; ordinary journal writes reject
    # provenance changes because Artifact identity is immutable.
    journal.transact(
        lambda _: journal._backend.executor().execute(
            "UPDATE artifacts SET provenance_json='[]' WHERE artifact_id=?",
            (metadata.artifact_id,),
        )
    )
    damaged = service.artifacts.get(reference.artifact_id)
    report = OperationalDoctor(store).inspect("ws_a")
    assert "tool_visual_source" in {issue.code for issue in report.issues}
    assert service.artifacts.get(reference.artifact_id) == damaged
    assert journal.get_execution("ws_a", "tex_1") == execution
    assert adapter.DRIVER_CONSTRUCTION_COUNT == before


def test_conversation_reference_keeps_image_reachable_if_auxiliary_reference_index_is_lost(
    environment,
):
    service, journal, _, _, _, _ = environment
    reference = closed_image_history(environment)
    journal.transact(
        lambda _: journal._backend.executor().execute(
            "UPDATE artifacts SET references_json='[]' WHERE artifact_id=?",
            (reference.artifact_id,),
        )
    )
    references = journal.list_artifact_references("ws_a", reference.artifact_id)
    assert "conversation_record" in {item[1] for item in references}
    assert reference.artifact_id in service.artifacts.retention_report().referenced
    assert reference.artifact_id not in service.artifacts.retention_report().candidates
    assert sha256_digest(service.read(reference, session_id="ses_1").content) == reference.sha256
