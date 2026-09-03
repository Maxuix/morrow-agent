"""Workflow domain persistence without a compiler or execution path."""

from datetime import timedelta

import pytest

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.definition_yaml import WorkflowDefinitionYamlStore
from morrow.adapters.state.extension_yaml import ExtensionYamlConflict, ExtensionYamlError
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.application.tasks import TaskService
from morrow.application.workflows.evidence import workflow_task_outcome
from morrow.application.workflows.integrity import verify_workflow_rows
from morrow.core.agent_definitions import AgentDefinitionSource, AgentDefinitionVersion
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.domain import (
    DurableSession,
    DurableTaskRun,
    DurableTurn,
    TaskRunPurpose,
    TaskRunStatus,
)
from morrow.core.store import StorageError, StoreOpenMode
from morrow.core.workflows.contracts import ArtifactBinding, TaskContract
from morrow.core.workflows.definitions import (
    WorkflowDefinitionDocument,
    WorkflowDefinitionHead,
    WorkflowRevisionRevocation,
)
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus
from morrow.testing import FixedClock, FixedIdSource
from test_stage7_workflow_domain import BUDGET, NOW, node, outcome_fields, revision, source


@pytest.fixture
def state(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    journal.create_session(
        DurableSession(session_id="ses_root", workspace_id="ws_one"),
        task=DurableTaskRun(task_run_id="task_root", session_id="ses_root", workspace_id="ws_one"),
    )
    src = AgentDefinitionSource(definition_id="helper", name="Helper", role_prompt="Inspect tests")
    version = AgentDefinitionVersion(
        version_id="adev_one",
        workspace_id="ws_one",
        version=1,
        source=src,
        content_hash=src.content_hash,
        source_revision=0,
        created_at=NOW,
    )
    journal.transact(lambda _: journal.agent_definitions.put_version(version))
    rev = revision(
        nodes=(
            node(
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="helper", version_id="adev_one", content_hash=src.content_hash
                )
            ),
        )
    )
    head = WorkflowDefinitionHead(
        workspace_id="ws_one",
        workflow_definition_id="pipeline",
        workflow_revision_id=rev.workflow_revision_id,
        source_revision=0,
        source_hash=source().content_hash,
        row_version=1,
    )
    journal.workflows.store_compiled_revision(rev, head, expected_row_version=0)
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id="ws_one",
        id_source=FixedIdSource(),
        clock=FixedClock().now,
    )
    artifact = artifacts.publish_workflow_payload(
        TaskContract(objective="Inspect password_validation.py"),
        session_id="ses_root",
        task_run_id="task_root",
    )
    run = WorkflowRun(
        workflow_run_id="wrun_one",
        workspace_id="ws_one",
        workflow_revision_id=rev.workflow_revision_id,
        root_task_run_id="task_root",
        budget_snapshot=BUDGET,
        started_at=NOW,
        admission_deadline_at=NOW + timedelta(seconds=BUDGET.admission_timeout_seconds),
        input_artifacts=(
            ArtifactBinding(
                name="task", artifact_id=artifact.artifact_id, contract={"kind": "TaskContract"}
            ),
        ),
    )
    leaf = NodeRun(
        node_run_id="nrun_one", workspace_id="ws_one", workflow_run_id="wrun_one", node_id="worker"
    )
    journal.workflows.create_run(run, (leaf,))
    yield store, handle, journal, artifacts, rev, run, leaf
    handle.close()


def test_revision_head_revocation_and_workspace_roundtrip(state):
    _, handle, journal, _, rev, run, leaf = state
    repo = journal.workflows
    assert repo.get_revision("ws_one", rev.workflow_revision_id) == rev
    assert repo.get_revision("ws_other", rev.workflow_revision_id) is None
    assert repo.get_run("ws_one", run.workflow_run_id) == run
    assert repo.list_nodes("ws_one", run.workflow_run_id) == (leaf,)
    assert handle.run_read(verify_workflow_rows) == (True, ())
    disabled = repo.set_enabled("ws_one", "pipeline", enabled=False, expected_row_version=1)
    assert not disabled.enabled and repo.get_run("ws_one", run.workflow_run_id) == run
    with pytest.raises(ValueError, match="conflict"):
        repo.set_enabled("ws_one", "pipeline", enabled=True, expected_row_version=1)
    revoked = WorkflowRevisionRevocation(
        workspace_id="ws_one",
        workflow_revision_id=rev.workflow_revision_id,
        reason="policy changed",
        command_id="cmd_revoke",
        created_at=NOW,
    )
    assert repo.put_revocation(revoked) == repo.put_revocation(revoked)
    with pytest.raises(ValueError, match="immutable"):
        repo.put_revocation(revoked.model_copy(update={"reason": "replace"}))
    assert repo.get_revision("ws_one", rev.workflow_revision_id) == rev


def test_root_uniqueness_ordinary_guards_and_post_terminal_behavior(state):
    _, _, journal, _, _, run, leaf = state
    tasks = TaskService(journal=journal, workspace_id="ws_one", id_source=FixedIdSource())
    for action in (
        tasks.accept,
        tasks.snapshot,
        tasks.resume,
        tasks.cancel,
        tasks.fail,
        tasks.abandon,
    ):
        with pytest.raises(StorageError, match="Workflow"):
            action("task_root")
    with pytest.raises(StorageError, match="Workflow"):
        tasks.new_task("ses_root")
    with pytest.raises(StorageError, match="Workflow"):
        journal.create_turn(
            "ws_one",
            DurableTurn(
                turn_id="turn_root",
                session_id="ses_root",
                task_run_id="task_root",
                client_message_id="input",
            ),
        )
    with pytest.raises((StorageError, ValueError)):
        journal.workflows.create_run(
            run.model_copy(update={"workflow_run_id": "wrun_other"}),
            (
                leaf.model_copy(
                    update={"workflow_run_id": "wrun_other", "node_run_id": "nrun_other"}
                ),
            ),
        )
    closed = WorkflowRun.model_validate(
        {**run.model_dump(), "status": "cancelled", "completed_at": NOW, "row_version": 2}
    )
    assert journal.workflows.save_run(closed, expected_row_version=1) == closed
    assert journal.workflows.save_run(closed, expected_row_version=1) == closed
    assert tasks.snapshot("task_root").outcome is not None
    assert journal.create_turn(
        "ws_one",
        DurableTurn(
            turn_id="turn_root",
            session_id="ses_root",
            task_run_id="task_root",
            client_message_id="input",
        ),
    )


def test_internal_leaf_lifecycle_and_learning_exclusion(state):
    _, _, journal, _, _, _, _ = state
    leaf = DurableTaskRun(
        task_run_id="task_leaf",
        session_id="ses_leaf",
        workspace_id="ws_one",
        purpose=TaskRunPurpose.WORKFLOW_NODE,
        created_at=NOW,
        updated_at=NOW,
    )
    session = DurableSession(session_id="ses_leaf", workspace_id="ws_one")
    with pytest.raises(StorageError, match="Workflow"):
        journal.create_task_run("ws_one", leaf)
    journal.create_workflow_leaf("ws_one", "nrun_one", session, leaf)
    tasks = TaskService(journal=journal, workspace_id="ws_one", id_source=FixedIdSource())
    assert tasks.list("ses_leaf") == ()
    assert journal.get_task_run("ws_one", "task_leaf") == leaf
    for action in (
        tasks.accept,
        tasks.snapshot,
        tasks.resume,
        tasks.cancel,
        tasks.fail,
        tasks.abandon,
    ):
        with pytest.raises(StorageError, match="Workflow"):
            action("task_leaf")
    with pytest.raises(StorageError, match="Workflow"):
        tasks.new_task("ses_leaf")
    turn = DurableTurn(
        turn_id="turn_leaf",
        session_id="ses_leaf",
        task_run_id="task_leaf",
        client_message_id="input",
        created_at=NOW,
    )
    with pytest.raises(StorageError, match="Workflow"):
        journal.create_turn("ws_one", turn)
    assert journal.create_workflow_turn("ws_one", "nrun_one", turn) == turn
    with pytest.raises(StorageError, match="TaskOutcome"):
        journal.put_task_outcome(
            "ws_one",
            workflow_task_outcome(
                **{
                    **outcome_fields(),
                    "session_id": "ses_leaf",
                    "task_run_id": "task_leaf",
                    "task_status": TaskRunStatus.OPEN,
                }
            ),
        )


def test_terminal_workflow_rejects_turns_and_can_drain_pre_admission_leaf(state):
    from morrow.application.workflows.tasks import WorkflowTaskLifecycle
    from morrow.core.domain import DurableTaskRunTransition

    _, _, journal, _, _, run, _ = state
    leaf = DurableTaskRun(
        task_run_id="task_leaf",
        session_id="ses_leaf",
        workspace_id="ws_one",
        purpose=TaskRunPurpose.WORKFLOW_NODE,
        created_at=NOW,
        updated_at=NOW,
    )
    journal.create_workflow_leaf(
        "ws_one",
        "nrun_one",
        DurableSession(session_id="ses_leaf", workspace_id="ws_one"),
        leaf,
    )
    closed = run.model_copy(
        update={"status": WorkflowStatus.CANCELLED, "completed_at": NOW, "row_version": 2}
    )
    journal.workflows.save_run(closed, expected_row_version=1)

    turn = DurableTurn(
        turn_id="turn_leaf",
        session_id="ses_leaf",
        task_run_id="task_leaf",
        client_message_id="input",
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="active Workflow"):
        journal.create_workflow_turn("ws_one", "nrun_one", turn)

    cancelled = WorkflowTaskLifecycle(journal, workspace_id="ws_one").transition(
        "wrun_one",
        "task_leaf",
        target=TaskRunStatus.CANCELLED,
        transition=DurableTaskRunTransition(
            transition_id="ttr_cancel_leaf",
            workspace_id="ws_one",
            session_id="ses_leaf",
            task_run_id="task_leaf",
            from_status=TaskRunStatus.OPEN,
            to_status=TaskRunStatus.CANCELLED,
            reason="workflow_cancelled",
            created_at=NOW,
        ),
        expected_row_version=1,
    )
    assert cancelled.status is TaskRunStatus.CANCELLED


def test_profile_roundtrip_backup_malformed_source_and_doctor(state, tmp_path):
    store, handle, journal, artifacts, rev, run, _ = state
    metadata = artifacts.read(run.input_artifacts[0].artifact_id, max_bytes=16384).metadata
    assert metadata.text_safety_profile.value == "workflow_value_sensitive"
    assert "password_validation.py" in metadata.excerpt
    with pytest.raises(TypeError):
        artifacts.publish_bytes(
            b"safe", kind=metadata.kind, text_safety_profile="workflow_value_sensitive"
        )
    yaml = WorkflowDefinitionYamlStore(store.layout.data_root)
    path = yaml.workspace_path("ws_one")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"definitions: [ malformed password validation\n")
    report = OperationalDoctor(store).inspect("ws_one")
    assert any(i.code == "workflow_definition_source_invalid" for i in report.issues)
    assert handle.run_read(verify_workflow_rows) == (True, ())
    backup = OperationalBackupService(store, journal=journal)
    created = backup.create("workflow-domain")
    bundle = store.layout.backups_dir / created.bundle_name
    assert backup.verify(bundle).ok
    target = tmp_path / "restored"
    assert backup.restore(bundle, target).ok
    assert (
        WorkflowDefinitionYamlStore(target).workspace_path("ws_one").read_bytes()
        == path.read_bytes()
    )
    with OperationalStore(target).open(StoreOpenMode.READ_ONLY) as restored:
        assert (
            SqliteOperationalJournal(restored).workflows.get_revision(
                "ws_one", rev.workflow_revision_id
            )
            == rev
        )


def test_yaml_occ_and_unrelated_body_hash(state):
    store, _, _, _, _, _, _ = state
    yaml = WorkflowDefinitionYamlStore(store.layout.data_root)
    first = yaml.write(
        "ws_one", WorkflowDefinitionDocument(definitions=(source(),)), expected_revision=0
    )
    digest = yaml.load_definition("ws_one", "pipeline").source_hash
    yaml.write(
        "ws_one",
        WorkflowDefinitionDocument(definitions=(source(), source(workflow_definition_id="other"))),
        expected_revision=first.revision,
    )
    assert yaml.load_definition("ws_one", "pipeline").source_hash == digest
    with pytest.raises(ExtensionYamlConflict):
        yaml.write("ws_one", first, expected_revision=0)
    with pytest.raises(ExtensionYamlError, match="Workflow definition is missing"):
        yaml.load_definition("ws_one", "missing")


def admitted_leaf(state):
    from morrow.core.domain import AgentRunSnapshot, DurableAgentRun, sha256_digest
    from test_stage4_journal import _snapshot

    _, _, journal, _, rev, run, leaf = state
    journal.create_workflow_leaf(
        "ws_one",
        leaf.node_run_id,
        DurableSession(session_id="ses_leaf", workspace_id="ws_one"),
        DurableTaskRun(
            task_run_id="task_leaf",
            session_id="ses_leaf",
            workspace_id="ws_one",
            purpose=TaskRunPurpose.WORKFLOW_NODE,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    journal.create_workflow_turn(
        "ws_one",
        leaf.node_run_id,
        DurableTurn(
            turn_id="turn_leaf",
            session_id="ses_leaf",
            task_run_id="task_leaf",
            client_message_id="input",
            created_at=NOW,
        ),
    )
    snapshot = AgentRunSnapshot.model_validate(
        {
            **_snapshot().model_dump(),
            "definition_ref": rev.nodes[0].agent_definition_ref,
            "conversation_session_id": "ses_leaf",
            "model": rev.nodes[0].resolved_model_ref,
            "prompt_profile_id": "default",
            "prompt_profile_version": "1",
            "prompt_profile_digest": "a" * 64,
            "role_prompt_digest": sha256_digest("Inspect tests"),
        }
    )
    journal.create_agent_run(
        "ws_one",
        DurableAgentRun(
            agent_run_id="arun_leaf",
            session_id="ses_leaf",
            turn_id="turn_leaf",
            snapshot=snapshot,
            created_at=NOW,
        ),
    )
    running = leaf.model_copy(
        update={
            "status": WorkflowStatus.RUNNING,
            "started_at": NOW,
            "row_version": 2,
            "conversation_session_id": "ses_leaf",
            "leaf_task_run_id": "task_leaf",
            "agent_run_id": "arun_leaf",
            "effective_node_generation_request_cap": 2,
        }
    )
    journal.workflows.save_node(running, expected_row_version=1)
    journal.workflows.save_run(
        run.model_copy(update={"status": WorkflowStatus.RUNNING, "row_version": 2}),
        expected_row_version=1,
    )
    return running


def test_node_admission_provenance_output_completion_and_recovery_state(state):
    from morrow.application.workflows.evidence import text_result_from_assistant

    _, handle, journal, artifacts, _, _, _ = state
    running = admitted_leaf(state)
    assert (
        journal.get_agent_run("ws_one", "arun_leaf").workflow_ref.node_run_id == running.node_run_id
    )
    with pytest.raises(ValueError, match="frozen"):
        journal.workflows.save_node(
            running.model_copy(
                update={
                    "status": WorkflowStatus.BLOCKED,
                    "row_version": 3,
                    "effective_node_generation_request_cap": 3,
                }
            ),
            expected_row_version=2,
        )
    completed = running.model_copy(
        update={"status": WorkflowStatus.COMPLETED, "row_version": 3, "completed_at": NOW}
    )
    with pytest.raises(ValueError, match="required outputs"):
        journal.workflows.save_node(completed, expected_row_version=2)
    result = artifacts.publish_workflow_payload(
        text_result_from_assistant("rec_final", "authorization test passed"),
        session_id="ses_leaf",
        task_run_id="task_leaf",
        producer_node_run_id=running.node_run_id,
        output_slot="result",
    )
    assert (
        artifacts.publish_workflow_payload(
            text_result_from_assistant("rec_final", "authorization test passed"),
            session_id="ses_leaf",
            task_run_id="task_leaf",
            producer_node_run_id=running.node_run_id,
            output_slot="result",
        )
        == result
    )
    binding = ArtifactBinding(
        name="result", artifact_id=result.artifact_id, contract={"kind": "TextResult"}
    )
    assert (
        journal.workflows.bind_artifact(
            "ws_one", "wrun_one", binding, node_run_id=running.node_run_id, direction="output"
        )
        == binding
    )
    with pytest.raises(ValueError, match="slot"):
        journal.workflows.bind_artifact(
            "ws_one",
            "wrun_one",
            binding.model_copy(update={"name": "missing"}),
            node_run_id=running.node_run_id,
            direction="output",
        )
    assert journal.workflows.save_node(completed, expected_row_version=2) == completed
    assert journal.workflows.save_node(completed, expected_row_version=2) == completed
    assert handle.run_read(verify_workflow_rows) == (True, ())
    with pytest.raises(StorageError):
        handle.run_write(
            lambda ex: ex.execute("DELETE FROM workflow_node_runs WHERE node_run_id='nrun_one'")
        )


def test_workflow_outcome_terminal_redaction_and_learning_roundtrip(state):
    from morrow.application.learning.requests import LearningReviewRequestService
    from morrow.application.workflows.evidence import text_result_from_assistant
    from morrow.application.workflows.tasks import WorkflowTaskLifecycle
    from morrow.core.domain import (
        ArtifactReference,
        DurableTaskRunTransition,
        TaskOutcomeEvidenceRef,
        TaskOutcomeTrigger,
    )

    store, handle, journal, artifacts, _, _, _ = state
    running = admitted_leaf(state)
    result = artifacts.publish_workflow_payload(
        text_result_from_assistant("rec_final", "password_validation.py passed"),
        session_id="ses_leaf",
        task_run_id="task_leaf",
        producer_node_run_id=running.node_run_id,
        output_slot="result",
    )
    journal.workflows.bind_artifact(
        "ws_one",
        "wrun_one",
        ArtifactBinding(
            name="result", artifact_id=result.artifact_id, contract={"kind": "TextResult"}
        ),
        node_run_id=running.node_run_id,
        direction="output",
    )
    journal.workflows.save_node(
        running.model_copy(
            update={"status": WorkflowStatus.COMPLETED, "completed_at": NOW, "row_version": 3}
        ),
        expected_row_version=2,
    )
    lifecycle = WorkflowTaskLifecycle(journal, workspace_id="ws_one")
    ready = lifecycle.transition(
        "wrun_one",
        "task_root",
        target=TaskRunStatus.READY_FOR_ACCEPTANCE,
        transition=DurableTaskRunTransition(
            transition_id="ttr_ready",
            workspace_id="ws_one",
            session_id="ses_root",
            task_run_id="task_root",
            from_status=TaskRunStatus.OPEN,
            to_status=TaskRunStatus.READY_FOR_ACCEPTANCE,
            reason="workflow_complete",
            created_at=NOW,
        ),
        expected_row_version=1,
    )
    run = journal.workflows.get_run("ws_one", "wrun_one")
    journal.workflows.save_run(
        run.model_copy(
            update={
                "status": WorkflowStatus.COMPLETED,
                "result_status": "succeeded",
                "completed_at": NOW,
                "row_version": 3,
            }
        ),
        expected_row_version=2,
    )
    evidence = (
        TaskOutcomeEvidenceRef(
            kind="workflow_run", reference_id="wrun_one", role="workflow_result_snapshot"
        ),
        TaskOutcomeEvidenceRef(
            kind="task_transition", reference_id="ttr_ready", role="workflow_ready_transition"
        ),
    )
    outcome = workflow_task_outcome(
        **{
            **outcome_fields(),
            "summary": "authorization test passed",
            "changed_paths": ("password_validation.py",),
            "validation_facts": ("password = test-only-value",),
            "evidence_refs": evidence,
            "goal_reference": TaskOutcomeEvidenceRef(
                kind="artifact", reference_id=run.input_artifacts[0].artifact_id
            ),
            "artifact_refs": (ArtifactReference(artifact_id=result.artifact_id),),
        }
    )
    assert journal.put_task_outcome("ws_one", outcome) == outcome
    assert (
        "workflow_evidence_redacted=true"
        in journal.get_task_outcome("ws_one", "out_one").completion_basis
    )
    accepted = journal.transition_task_run(
        "ws_one",
        "task_root",
        target=TaskRunStatus.ACCEPTED,
        transition=DurableTaskRunTransition(
            transition_id="ttr_accept",
            workspace_id="ws_one",
            session_id="ses_root",
            task_run_id="task_root",
            from_status=ready.status,
            to_status=TaskRunStatus.ACCEPTED,
            reason="user_accept",
            created_at=NOW,
        ),
        expected_row_version=ready.row_version,
    )
    accepted_outcome = outcome.model_copy(
        update={
            "outcome_id": "out_accepted",
            "version": 2,
            "trigger": TaskOutcomeTrigger.ACCEPTANCE,
            "task_status": accepted.status,
        }
    )
    journal.put_task_outcome("ws_one", accepted_outcome)
    review_service = LearningReviewRequestService(
        journal=journal, workspace_id="ws_one", id_source=FixedIdSource(), clock=FixedClock().now
    )
    decision = journal.transact(
        lambda txn: review_service.ensure_for_accepted_outcome(txn, accepted_outcome)
    )
    assert decision.review is not None
    assert handle.run_read(verify_workflow_rows) == (True, ())
    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("workflow-outcome")
    assert backup.verify(store.layout.backups_dir / report.bundle_name).ok


def test_workflow_revision_tamper_is_error_but_desired_ahead_is_warning(state):
    store, handle, journal, _, _, _, _ = state
    yaml = WorkflowDefinitionYamlStore(store.layout.data_root)
    yaml.write(
        "ws_one",
        WorkflowDefinitionDocument(definitions=(source(description="desired ahead"),)),
        expected_revision=0,
    )
    report = OperationalDoctor(store).inspect("ws_one")
    assert report.health.value == "ok" and any(
        i.code == "workflow_desired_ahead" for i in report.issues
    )
    with pytest.raises(StorageError):
        handle.run_write(
            lambda ex: ex.execute("UPDATE workflow_revisions SET content_hash=?", ("f" * 64,))
        )
    handle.run_write(
        lambda ex: ex.execute(
            "UPDATE workflow_definition_heads SET body_json=json_set(body_json, '$.workflow_revision_id', 'wrev_missing')"
        )
    )
    assert OperationalDoctor(store).inspect("ws_one").health.value == "needs_repair"


def test_previous_current_migration_defaults_and_future_refusal(tmp_path):
    from morrow.adapters.state.migrations import MigrationRegistry, production_registry
    from morrow.core.domain import TaskOutcome, TextSafetyProfile, canonical_json_bytes
    from morrow.core.store import StoreHealth

    old = MigrationRegistry(supported_version=23)
    production = production_registry()
    for migration in production.pending(0):
        if migration.version <= 23:
            old.add(migration)
    root = tmp_path / "v23"
    with OperationalStore(root, registry=old).initialize() as handle:
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO sessions(session_id, workspace_id, lifecycle, health, conversation_position, created_at_unix, updated_at_unix) VALUES('ses_old','ws_one','active','ok',0,1,1)"
            )
        )
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO task_runs(task_run_id, session_id, workspace_id, status, row_version, attempt, created_at_unix, updated_at_unix) VALUES('task_old','ses_old','ws_one','open',1,1,1,1)"
            )
        )
        old_outcome = TaskOutcome(
            **{
                **outcome_fields(),
                "summary": "Older safe outcome",
                "session_id": "ses_old",
                "task_run_id": "task_old",
                "task_status": TaskRunStatus.OPEN,
            }
        )
        raw = canonical_json_bytes(
            old_outcome.model_dump(mode="json", exclude={"text_safety_profile"})
        )
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO task_outcomes(outcome_id, workspace_id, session_id, task_run_id, version, trigger, task_status, payload_json, payload_bytes, created_at_unix, artifact_refs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "out_one",
                    "ws_one",
                    "ses_old",
                    "task_old",
                    1,
                    "snapshot",
                    "open",
                    raw.decode(),
                    len(raw),
                    int(NOW.timestamp()),
                    "[]",
                ),
            )
        )
        handle.run_write(
            lambda ex: ex.execute(
                "INSERT INTO artifacts(artifact_id, workspace_id, session_id, task_run_id, kind, sensitivity, state, retention, sha256, byte_size, excerpt, provenance_json, row_version, created_at_unix, updated_at_unix) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "art_old",
                    "ws_one",
                    "ses_old",
                    "task_old",
                    "task_summary",
                    "redacted",
                    "missing",
                    "standard",
                    "a" * 64,
                    0,
                    "Older safe excerpt",
                    "[]",
                    1,
                    1,
                    1,
                ),
            )
        )
    store = OperationalStore(root)
    assert store.migrate().applied == (
        "workflow_revision_artifact_contracts",
        "workflow_node_request_cap",
        "workflow_pause_drain_lineage",
    )
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        assert (
            SqliteOperationalJournal(handle).get_task_run("ws_one", "task_old").purpose
            == TaskRunPurpose.USER
        )
        journal = SqliteOperationalJournal(handle)
        assert (
            journal.get_task_outcome("ws_one", "out_one").text_safety_profile
            == TextSafetyProfile.LEGACY_STRICT
        )
        assert (
            journal.get_artifact("ws_one", "art_old").text_safety_profile
            == TextSafetyProfile.LEGACY_STRICT
        )
        handle.run_write(lambda ex: ex.execute("PRAGMA user_version=27"))
        handle.run_write(lambda ex: ex.execute("UPDATE store_identity SET schema_version=27"))
    assert store.classify().health is StoreHealth.FUTURE_SCHEMA


def test_cancel_intent_and_blocked_recovery_are_monotonic(state):
    _, _, journal, _, _, _, _ = state
    admitted_leaf(state)
    run = journal.workflows.get_run("ws_one", "wrun_one")
    pending = run.model_copy(update={"pending_terminal_intent": "user_cancel", "row_version": 3})
    journal.workflows.save_run(pending, expected_row_version=2)
    blocked = pending.model_copy(update={"status": WorkflowStatus.BLOCKED, "row_version": 4})
    journal.workflows.save_run(blocked, expected_row_version=3)
    with pytest.raises(ValueError, match="cleared"):
        journal.workflows.save_run(
            blocked.model_copy(
                update={
                    "status": WorkflowStatus.RUNNING,
                    "pending_terminal_intent": None,
                    "row_version": 5,
                }
            ),
            expected_row_version=4,
        )
    cancelled = blocked.model_copy(
        update={"status": WorkflowStatus.CANCELLED, "completed_at": NOW, "row_version": 5}
    )
    journal.workflows.save_run(cancelled, expected_row_version=4)
    with pytest.raises(ValueError, match="illegal"):
        journal.workflows.save_run(
            cancelled.model_copy(
                update={"status": WorkflowStatus.RUNNING, "completed_at": None, "row_version": 6}
            ),
            expected_row_version=5,
        )


def test_wrong_outcome_workflow_reference_and_missing_ready_epoch_rejected(state):
    from morrow.core.domain import TaskOutcomeEvidenceRef

    _, _, journal, _, _, _, _ = state
    fields = {**outcome_fields(), "task_status": TaskRunStatus.OPEN}
    wrong = TaskOutcomeEvidenceRef(kind="workflow_run", reference_id="wrun_missing")
    with pytest.raises(StorageError, match="reference scope"):
        journal.put_task_outcome("ws_one", workflow_task_outcome(**fields, evidence_refs=(wrong,)))
    marker = TaskOutcomeEvidenceRef(
        kind="workflow_run", reference_id="wrun_one", role="workflow_result_snapshot"
    )
    with pytest.raises(StorageError, match="ready transition"):
        journal.put_task_outcome("ws_one", workflow_task_outcome(**fields, evidence_refs=(marker,)))
    with pytest.raises(ValueError, match="reference"):
        journal.workflows.bind_artifact(
            "ws_other",
            "wrun_one",
            journal.workflows.get_run("ws_one", "wrun_one").input_artifacts[0],
        )


def test_pause_resume_ladder_occ_and_terminal_rejection(state):
    from morrow.application.workflows.transitions import WorkflowTransitionService
    from morrow.core.application import ApplicationError

    _, _, journal, _, _, _, _ = state
    transitions = WorkflowTransitionService(journal, workspace_id="ws_one", clock=journal.now)

    # Pause on a never-admitted run pauses it directly; the fact is durable.
    paused = transitions.request_pause("wrun_one")
    assert paused.status is WorkflowStatus.PAUSED and paused.pause_requested
    assert paused.row_version == 2
    # Idempotent replay returns the same row.
    assert transitions.request_pause("wrun_one") == paused
    # Stale OCC writes are rejected.
    stale = paused.model_copy(
        update={
            "status": WorkflowStatus.RUNNING,
            "pause_requested": False,
            "row_version": paused.row_version + 1,
        }
    )
    with pytest.raises(ValueError, match="revision conflict"):
        journal.workflows.save_run(stale, expected_row_version=1)
    # Resume atomically clears the fact and returns to running.
    resumed = transitions.resume_run("wrun_one")
    assert resumed.status is WorkflowStatus.RUNNING and not resumed.pause_requested
    assert transitions.resume_run("wrun_one") == resumed
    # An idle RUNNING run completes its drain in the pause transaction.
    settled = transitions.request_pause("wrun_one")
    assert settled.status is WorkflowStatus.PAUSED and settled.pause_requested
    assert transitions.complete_drain("wrun_one") == settled
    running = transitions.resume_run("wrun_one")
    assert running.status is WorkflowStatus.RUNNING and not running.pause_requested
    # A terminal run rejects Pause.
    cancelled = transitions.cancel_run("wrun_one")
    assert cancelled.status is WorkflowStatus.CANCELLED
    with pytest.raises(ApplicationError, match="terminal"):
        transitions.request_pause("wrun_one")


def test_pause_on_blocked_run_keeps_status_and_cancel_intent_rejects(state):
    from morrow.application.workflows.transitions import WorkflowTransitionService
    from morrow.core.application import ApplicationError

    _, _, journal, _, _, _, _ = state
    transitions = WorkflowTransitionService(journal, workspace_id="ws_one", clock=journal.now)

    transitions.mark_run_running("wrun_one")
    blocked = transitions.block_run("wrun_one")
    assert blocked.status is WorkflowStatus.BLOCKED
    # Pause on a blocked run records only the fact; the status stays blocked.
    paused = transitions.request_pause("wrun_one")
    assert paused.status is WorkflowStatus.BLOCKED and paused.pause_requested
    # Resume clears the fact but leaves the blocked run recovery-owned.
    resumed = transitions.resume_run("wrun_one")
    assert resumed.status is WorkflowStatus.BLOCKED and not resumed.pause_requested
    # A pending user-cancel intent rejects Pause outright.
    with_intent = transitions.set_pending_user_cancel("wrun_one")
    assert with_intent.pending_terminal_intent == "user_cancel"
    with pytest.raises(ApplicationError, match="cancellation"):
        transitions.request_pause("wrun_one")
