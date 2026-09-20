"""Task-plan Revision admission: provenance, start gates, and runtime projection."""

import json

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.application.workflows.integrity import verify_workflow_rows
from morrow.core.agent_definitions import AgentDefinitionSource, AgentDefinitionVersion
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.domain import DurableSession, DurableTaskRun, canonical_json_bytes, sha256_digest
from morrow.core.models import ModelRef
from morrow.core.store import StoreOpenMode
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowRevision,
    compiled_content_hash,
)
from morrow.core.workflows.drafts import WorkflowDraft
from morrow.core.workflows.planning import (
    FrozenNodeSelection,
    PlanDecision,
    PlanningBinding,
    PlanningContextRef,
    TaskPlanProvenance,
    visible_version_digest,
)
from morrow.testing import FixedClock
from test_stage7_workflow_domain import NOW, candidate, node, revision, source

WS = "ws_one"
SID = "ses_root"
BINDING = "wplan_one"
DRAFT = "wdraft_one"
DEFINITION = "task_" + sha256_digest(canonical_json_bytes(BINDING))[:32]


def agent_source():
    return AgentDefinitionSource(definition_id="helper", name="Helper", role_prompt="Inspect tests")


def frozen_selection(content_hash):
    return FrozenNodeSelection(
        node_id="worker",
        version_id="adev_one",
        content_hash=content_hash,
        resolved_model=ModelRef(provider_id="fake", model_id="m1"),
        model_source="adapter_default",
        generation_source="adapter_default",
    )


def task_revision(content_hash, *, revision_id="wrev_task", number=-1, parent=None):
    compiled = candidate(
        workflow_definition_id=DEFINITION,
        nodes=(
            node(
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="helper", version_id="adev_one", content_hash=content_hash
                )
            ),
        ),
    )
    return WorkflowRevision(
        **compiled.model_dump(),
        workflow_revision_id=revision_id,
        workspace_id=WS,
        revision=number,
        parent_workflow_revision_id=parent,
        content_hash=compiled_content_hash(compiled),
        source_revision=0,
        source_hash=source(workflow_definition_id=DEFINITION).content_hash,
        created_by="cmd_start",
        created_at=NOW,
    )


@pytest.fixture
def state(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    journal.create_session(
        DurableSession(session_id=SID, workspace_id=WS),
        task=DurableTaskRun(task_run_id="task_root", session_id=SID, workspace_id=WS),
    )
    src = agent_source()
    journal.transact(
        lambda _: journal.agent_definitions.put_version(
            AgentDefinitionVersion(
                version_id="adev_one",
                workspace_id=WS,
                version=1,
                source=src,
                content_hash=src.content_hash,
                source_revision=0,
                created_at=NOW,
            )
        )
    )
    draft_source = source(workflow_definition_id=DEFINITION)
    journal.workflows.create_draft(
        WorkflowDraft(
            draft_id=DRAFT,
            workspace_id=WS,
            source=draft_source,
            source_hash=draft_source.content_hash,
            row_version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    journal.workflows.planning.save_binding(
        PlanningBinding(
            planning_binding_id=BINDING,
            workspace_id=WS,
            session_id=SID,
            origin_interaction_id="message1",
            current_draft_id=DRAFT,
            context_ref=PlanningContextRef(
                conversation_position=0,
                model=ModelRef(provider_id="fake", model_id="m1"),
                settings_digest="a" * 64,
            ),
            row_version=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    yield store, handle, journal, src.content_hash
    handle.close()


def _decision(content_hash, *, decision_id="wdec_one", command_id="cmd_start"):
    return PlanDecision(
        plan_decision_id=decision_id,
        workspace_id=WS,
        session_id=SID,
        command_id=command_id,
        decision="start",
        action_source="button",
        interaction_id="message1",
        draft_id=DRAFT,
        draft_version=1,
        visible_version_binding=visible_version_digest(DRAFT, 1),
        execution_digest="b" * 64,
        result_id="wrun_task",
        created_at=NOW,
    )


def _provenance(content_hash, *, revision_id="wrev_task", origin="initial"):
    return TaskPlanProvenance(
        workspace_id=WS,
        workflow_revision_id=revision_id,
        origin=origin,
        planning_binding_id=BINDING,
        draft_id=DRAFT,
        draft_version=1,
        plan_decision_id="wdec_one",
        root_task_run_id="task_root",
        context_digest="c" * 64,
        settings_digest="a" * 64,
        frozen_selections=(frozen_selection(content_hash),),
        created_at=NOW,
        command_id="cmd_start",
    )


def test_parentless_detached_revision_still_requires_provenance(state):
    _, _, journal, digest = state
    with pytest.raises(ValueError, match="lineage mismatch"):
        journal.workflows.store_detached_revision(task_revision(digest))


def test_continuation_detached_revision_keeps_parent_and_negative_namespace(state):
    _, handle, journal, _ = state
    published = revision(
        nodes=(
            node(
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="helper",
                    version_id="adev_one",
                    content_hash=agent_source().content_hash,
                )
            ),
        )
    )
    journal.workflows.store_compiled_revision(
        published,
        WorkflowDefinitionHead(
            workspace_id=WS,
            workflow_definition_id="pipeline",
            workflow_revision_id=published.workflow_revision_id,
            source_revision=0,
            source_hash=source().content_hash,
            row_version=1,
        ),
        expected_row_version=0,
    )
    child = published.model_copy(
        update={
            "workflow_revision_id": "wrev_child",
            "revision": -1,
            "parent_workflow_revision_id": published.workflow_revision_id,
            "created_by": "cmd_patch",
        }
    )
    stored = journal.workflows.store_detached_revision(child)
    assert stored.revision == -1
    assert stored.parent_workflow_revision_id == published.workflow_revision_id
    assert journal.workflows.get_task_plan_provenance(WS, stored.workflow_revision_id) is None
    assert journal.workflows.list_revisions(WS) == (published,)
    assert journal.workflows.get_head(WS, "pipeline") is not None
    assert handle.run_read(verify_workflow_rows) == (True, ())


def test_task_plan_revision_stores_with_verified_provenance(state):
    _, handle, journal, digest = state
    journal.workflows.planning.save_decision(_decision(digest))
    stored = journal.workflows.store_detached_revision(
        task_revision(digest), provenance=_provenance(digest)
    )
    assert stored.revision == -1
    assert stored.parent_workflow_revision_id is None
    assert stored.workflow_definition_id == DEFINITION
    assert journal.workflows.get_head(WS, DEFINITION) is None
    assert journal.workflows.list_revisions(WS) == ()
    origin = journal.workflows.get_task_plan_provenance(WS, stored.workflow_revision_id)
    assert origin.origin == "initial"
    assert origin.planning_binding_id == BINDING
    assert origin.plan_decision_id == "wdec_one"
    assert handle.run_read(verify_workflow_rows) == (True, ())
    replay = journal.workflows.store_detached_revision(
        task_revision(digest), provenance=_provenance(digest)
    )
    assert replay == stored


def test_task_plan_revision_rejects_forged_or_incomplete_origin(state):
    _, _, journal, digest = state
    journal.workflows.planning.save_decision(_decision(digest))
    with pytest.raises(ValueError, match="lineage mismatch"):
        journal.workflows.store_detached_revision(
            task_revision(digest, revision_id="wrev_other"), provenance=_provenance(digest)
        )
    compiled = candidate(
        workflow_definition_id="pipeline",
        nodes=(
            node(
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="helper", version_id="adev_one", content_hash=digest
                )
            ),
        ),
    )
    pipeline = WorkflowRevision(
        **compiled.model_dump(),
        workflow_revision_id="wrev_pipe",
        workspace_id=WS,
        revision=-1,
        content_hash=compiled_content_hash(compiled),
        source_revision=0,
        source_hash=source().content_hash,
        created_by="cmd_start",
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="lineage mismatch"):
        journal.workflows.store_detached_revision(pipeline, provenance=_provenance(digest))
    with pytest.raises(ValueError, match="lineage mismatch"):
        journal.workflows.store_detached_revision(
            task_revision(digest),
            provenance=_provenance(digest).model_copy(
                update={"planning_binding_id": "wplan_other"}
            ),
        )


def test_task_plan_revision_idempotent_conflict_and_continuation_cannot_carry_provenance(state):
    _, handle, journal, digest = state
    journal.workflows.planning.save_decision(_decision(digest))
    journal.workflows.store_detached_revision(task_revision(digest), provenance=_provenance(digest))
    other = _provenance(digest).model_copy(update={"context_digest": "d" * 64})
    with pytest.raises(ValueError, match="identifier conflict"):
        journal.workflows.store_detached_revision(task_revision(digest), provenance=other)
    published = revision(
        nodes=(
            node(
                agent_definition_ref=AgentDefinitionRef(
                    definition_id="helper",
                    version_id="adev_one",
                    content_hash=digest,
                )
            ),
        )
    )
    journal.workflows.store_compiled_revision(
        published,
        WorkflowDefinitionHead(
            workspace_id=WS,
            workflow_definition_id="pipeline",
            workflow_revision_id=published.workflow_revision_id,
            source_revision=0,
            source_hash=source().content_hash,
            row_version=1,
        ),
        expected_row_version=0,
    )
    child = published.model_copy(
        update={
            "workflow_revision_id": "wrev_child",
            "revision": -1,
            "parent_workflow_revision_id": published.workflow_revision_id,
        }
    )
    with pytest.raises(ValueError, match="lineage mismatch"):
        journal.workflows.store_detached_revision(child, provenance=_provenance(digest))
    assert handle.run_read(verify_workflow_rows) == (True, ())


def test_integrity_rejects_parentless_run_local_revision_without_provenance(state):
    _, handle, journal, digest = state
    value = task_revision(digest)
    assigned = value.model_copy(update={"revision": -1})
    handle.run_write(
        lambda ex: ex.execute(
            "INSERT INTO workflow_revisions VALUES(?,?,?,?,?,?)",
            (
                assigned.workflow_revision_id,
                assigned.workspace_id,
                assigned.workflow_definition_id,
                assigned.revision,
                assigned.content_hash,
                assigned.model_dump_json(),
            ),
        )
    )
    for item in assigned.nodes:
        handle.run_write(
            lambda ex, node=item: ex.execute(
                "INSERT INTO workflow_revision_nodes VALUES(?,?,?)",
                (assigned.workflow_revision_id, node.node_id, node.agent_definition_ref.version_id),
            )
        )
    assert handle.run_read(verify_workflow_rows) == (False, ("workflow_integrity",))


def test_backup_restore_and_doctor_recognize_task_plan_provenance(state, tmp_path):
    store, handle, journal, digest = state
    journal.workflows.planning.save_decision(_decision(digest))
    stored = journal.workflows.store_detached_revision(
        task_revision(digest), provenance=_provenance(digest)
    )
    report = OperationalDoctor(store).inspect(WS)
    assert not any(issue.code == "workflow_integrity" for issue in report.issues)
    backup = OperationalBackupService(store, journal=journal)
    created = backup.create("task-plan-provenance")
    bundle = store.layout.backups_dir / created.bundle_name
    assert backup.verify(bundle).ok
    target = tmp_path / "restored"
    assert backup.restore(bundle, target).ok
    with OperationalStore(target).open(StoreOpenMode.READ_ONLY) as restored:
        restored_journal = SqliteOperationalJournal(restored)
        assert restored_journal.workflows.get_revision(WS, stored.workflow_revision_id) == stored
        origin = restored_journal.workflows.get_task_plan_provenance(
            WS, stored.workflow_revision_id
        )
        assert origin is not None and origin.origin == "initial"
        assert restored.run_read(verify_workflow_rows) == (True, ())


def _plan_scripts(*nodes):
    from test_workflow_task_planning import spec

    return [[[json.dumps(spec(*nodes))]], [["node complete"]]]


async def _planned(tmp_path, *nodes):
    from test_stage8_chat_submission import new_session
    from test_stage8_core_api import ServerFixture
    from test_workflow_task_planning import request

    fx = ServerFixture(tmp_path, scripts=_plan_scripts(*nodes))
    sid, root = await new_session(fx)
    reply = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
    assert reply.status == 200 and reply.json()["status"] == "succeeded", reply.body
    view = (await fx.client.get(root + "/task-plan")).json()
    return fx, sid, root, view


@pytest.mark.asyncio
async def test_explicit_start_admits_once_without_publishing(tmp_path):
    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        execution = view["execution"]
        assert execution["allowed"] is True and execution["digest"]
        assert "start" in view["allowed_actions"]
        body = {
            "command_id": "cmd_start",
            "session_id": sid,
            "draft_id": execution["draft_id"],
            "draft_version": execution["draft_version"],
            "execution_digest": execution["digest"],
            "action_source": "button",
            "interaction_id": "message1",
        }
        started = await fx.client.post(root + "/task-plan/start", body)
        assert started.status == 200, started.body
        run_id = started.json()["run"]["workflow_run_id"]
        assert started.json()["replayed"] is False
        replay = await fx.client.post(root + "/task-plan/start", body)
        assert replay.status == 200 and replay.json()["run"]["workflow_run_id"] == run_id
        stale = await fx.client.post(
            root + "/task-plan/start",
            {**body, "command_id": "cmd_stale", "execution_digest": "e" * 64},
        )
        assert stale.status == 409

        def facts():
            journal = fx.host.context.journal
            run = journal.workflows.get_run(fx.workspace_id, run_id)
            revision = journal.workflows.get_revision(fx.workspace_id, run.workflow_revision_id)
            origin = journal.workflows.get_task_plan_provenance(
                fx.workspace_id, run.workflow_revision_id
            )
            return {
                "runs": journal._backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "origin": origin.origin if origin else None,
                "definition": revision.workflow_definition_id,
                "head": journal.workflows.get_head(
                    fx.workspace_id, revision.workflow_definition_id
                ),
                "published": journal.workflows.list_revisions(fx.workspace_id),
            }

        counts = await fx.on_core(facts)
        assert counts["runs"] == 1 and counts["origin"] == "initial"
        assert counts["definition"].startswith("task_")
        assert counts["head"] is None and counts["published"] == ()

        def reject_old_start():
            from morrow.application.workflows.start import StartWorkflowCommand
            from morrow.core.application import ApplicationError
            from morrow.core.workflows.contracts import TaskContract

            run = fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
            session = fx.host.context.api.get_session(sid)
            try:
                fx.host.context.runtime.start.start(
                    StartWorkflowCommand(
                        workflow_definition_id=fx.host.context.journal.workflows.get_revision(
                            fx.workspace_id, run.workflow_revision_id
                        ).workflow_definition_id,
                        workflow_revision_id=run.workflow_revision_id,
                        session_id=sid,
                        root_task_run_id=session.current_task_run_id,
                        expected_root_row_version=1,
                        contract=TaskContract(objective="reuse"),
                        command_id="cmd_old_start",
                    )
                )
            except ApplicationError as exc:
                return exc.message
            return "started"

        assert "start_workflow_plan" in await fx.host.execute_command(reject_old_start)
        projection = (await fx.client.get(root + "/task-plan")).json()
        assert projection["run"]["workflow_run_id"] == run_id
        assert projection["run"]["origin"] == "initial"
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_default_send_stays_single_agent_until_explicit_start(tmp_path):
    from test_stage8_chat_submission import drain, new_session
    from test_stage8_core_api import ServerFixture
    from test_workflow_task_planning import request

    fx = ServerFixture(tmp_path, scripts=_plan_scripts("work") + [[["chat answer"]]])
    try:
        sid, root = await new_session(fx)
        planned = await fx.client.post(root + "/task-plan", request(sid, command_id="cmd_plan"))
        assert planned.status == 200 and planned.json()["status"] == "succeeded"
        sent = await fx.client.post(
            root + "/interactions", {"client_message_id": "client.1", "text": "just chat"}
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)

        def counts():
            backend = fx.host.context.journal._backend
            return {
                "runs": backend.read_one("SELECT count(*) FROM workflow_runs", ())[0],
                "nodes": backend.read_one("SELECT count(*) FROM workflow_node_runs", ())[0],
            }

        assert await fx.on_core(counts) == {"runs": 0, "nodes": 0}
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_single_node_task_plan_run_completes(tmp_path):
    from test_stage8_core_api import wait_for_run

    fx, sid, root, view = await _planned(tmp_path, "work")
    try:
        execution = view["execution"]
        started = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_start",
                "session_id": sid,
                "draft_id": execution["draft_id"],
                "draft_version": execution["draft_version"],
                "execution_digest": execution["digest"],
                "action_source": "button",
                "interaction_id": "message1",
            },
        )
        assert started.status == 200, started.body
        run_id = started.json()["run"]["workflow_run_id"]
        completed = await wait_for_run(fx.client, run_id, "completed", "failed", "blocked")
        assert completed["run"]["status"] == "completed"
        assert completed["run"]["result_status"] == "succeeded"
        assert len(completed["nodes"]) == 1
        from test_stage8_chat_submission import drain

        follow = await fx.client.post(
            root + "/interactions", {"client_message_id": "client.follow", "text": "what next"}
        )
        assert follow.status == 202, follow.body
        await drain(fx, sid)

        def after():
            backend = fx.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM workflow_runs", ())[0]

        assert await fx.on_core(after) == 1
    finally:
        fx.close()


@pytest.mark.asyncio
async def test_missing_review_report_does_not_succeed(tmp_path):
    from test_stage8_core_api import wait_for_run

    fx, sid, root, view = await _planned(tmp_path, "build", "audit")
    try:
        required = {
            (item["node_id"], item["output_slot"])
            for item in view["draft"]["draft"]["source"]["required_outputs"]
        }
        assert ("audit", "review") in required
        execution = view["execution"]
        started = await fx.client.post(
            root + "/task-plan/start",
            {
                "command_id": "cmd_start_review",
                "session_id": sid,
                "draft_id": execution["draft_id"],
                "draft_version": execution["draft_version"],
                "execution_digest": execution["digest"],
                "action_source": "chat_command",
                "interaction_id": "message1",
            },
        )
        assert started.status == 200, started.body
        run_id = started.json()["run"]["workflow_run_id"]
        finished = await wait_for_run(fx.client, run_id, "completed", "failed", "blocked")
        result = finished["run"].get("result_status")
        assert finished["run"]["status"] != "completed" or result != "succeeded"
    finally:
        fx.close()
