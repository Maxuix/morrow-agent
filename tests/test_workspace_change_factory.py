"""Production composition regression: scripted Providers, real tools and durable facts."""

import json

import pytest

from morrow.bootstrap import build_session_application
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.testing import ScriptedModelProvider
from test_stage7_multi_agent_pipeline import (
    MODEL,
    AutoApprovalPort,
    PipelineFixture,
    coder_source,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", [False, True])
async def test_factory_write_records_byte_snapshots_for_chat_and_workflow(tmp_path, workflow):
    from morrow.application.workflows.publication import WorkflowCompilationService
    from morrow.application.workflows.start import StartWorkflowCommand
    from morrow.core.workflows.contracts import NodeOutputRef, OutputContract, TaskContract
    from morrow.core.workflows.definitions import AgentNodeSource, WorkflowDefinitionSource

    fixture = PipelineFixture(tmp_path / "config")
    project = tmp_path / "project"
    project.mkdir()
    identity = fixture.app.workspace_service.confirm(fixture.app.workspace_service.resolve(project))
    script = [
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(
                    id="write-one",
                    name="write",
                    arguments=json.dumps({"path": "a.txt", "content": "agent\n"}),
                ),
            )
        ),
        AssistantMessage(content="done"),
    ]
    products = build_session_application(
        fixture.app,
        identity,
        provider=ScriptedModelProvider(script),
        model=MODEL,
        approval_port=AutoApprovalPort(),
    )
    journal = products.api.journal
    try:
        if workflow:
            runtime = products.workflow_runtime
            publication = runtime.scheduler.agent_publication
            from morrow.core.agent_runs import AgentDefinitionRef

            result = publication.publish(
                coder_source(), source_revision=0, expected_head_revision=0, command_id="cmd_agent"
            )
            version = result
            ref = AgentDefinitionRef(
                definition_id=version.source.definition_id,
                version_id=version.version_id,
                content_hash=version.content_hash,
            )
            source = WorkflowDefinitionSource(
                workflow_definition_id="capture_probe",
                name="Capture probe",
                description="Factory probe",
                required_outputs=(NodeOutputRef(node_id="coder", output_slot="patch"),),
                nodes=(
                    AgentNodeSource(
                        node_id="coder",
                        agent_definition_ref=ref,
                        task_contract=TaskContract(objective="write a.txt"),
                        access_mode="write",
                        output_contracts=(
                            OutputContract(kind="ImplementationPatch", slot="patch"),
                        ),
                    ),
                ),
            )
            compiled = WorkflowCompilationService(
                journal,
                workspace_id=identity.workspace_id,
                catalog=publication.catalog,
                id_source=fixture.app.id_source,
            ).publish(
                source,
                source_revision=0,
                expected_head_revision=0,
                command_id="cmd_compile",
                active_model=MODEL,
            )
            task = products.tasks.new_task(products.session.session_id, command_id="cmd_task").task
            fixture.bank.scripts.append(script)
            started = runtime.start.start(
                StartWorkflowCommand(
                    workflow_definition_id="capture_probe",
                    workflow_revision_id=compiled.revision.workflow_revision_id,
                    session_id=products.session.session_id,
                    root_task_run_id=task.task_run_id,
                    expected_root_row_version=task.row_version,
                    contract=TaskContract(objective="write a.txt"),
                    command_id="cmd_start",
                )
            )
            await runtime.scheduler.run(started.run.workflow_run_id)
            nodes = journal.workflows.list_nodes(identity.workspace_id, started.run.workflow_run_id)
            executions = journal.list_session_executions(
                identity.workspace_id, nodes[0].conversation_session_id
            )
        else:
            fixture.bank.scripts.append(script)
            [e async for e in products.orchestrator.stream("write a.txt")]
            executions = journal.list_session_executions(
                identity.workspace_id, products.session.session_id
            )
        assert (project / "a.txt").read_bytes() == b"agent\n"
        write = next(e for e in executions if e.tool_name == "write")
        assert write.facts.files
        assert not products.mutation.artifact_capture
        records = journal.workspace_changes.list(
            identity.workspace_id, task_ids=(write.task_run_id,)
        )
        assert len(records) == 1
        row = records[0]
        assert row.effect_state == "applied"
        assert row.tool_execution_id == write.tool_execution_id
        assert products.artifacts.read_workspace_snapshot(row.after.snapshot_id) == b"agent\n"
        captures = [
            a
            for a in journal.list_artifacts(identity.workspace_id)
            if any(r.role == "change_capture" for r in a.provenance_refs)
        ]
        assert bool(captures) is workflow

    finally:
        products.persistence.close()
        fixture.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["snapshot", "journal"])
async def test_capture_failure_precedes_file_write(tmp_path, monkeypatch, failure):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application
    from morrow.core.artifacts import ArtifactError, ArtifactErrorCode

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "workspace"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="write",
                        name="write",
                        arguments=json.dumps({"path": "new.txt", "content": "new"}),
                    ),
                )
            ),
            AssistantMessage(content="done"),
        ]
    )
    products = build_session_application(
        app, identity, provider=provider, model=MODEL, approval_port=AutoApprovalPort()
    )
    try:
        if failure == "snapshot":

            def broken(*a, **k):
                raise ArtifactError(ArtifactErrorCode.BUDGET, "injected")

            monkeypatch.setattr(products.artifacts, "publish_workspace_snapshot", broken)
        else:

            def broken(*a, **k):
                raise RuntimeError("injected")

            monkeypatch.setattr(products.api.journal.workspace_changes, "insert", broken)
        [e async for e in products.orchestrator.stream("create new.txt")]
        assert not (project / "new.txt").exists()
    finally:
        products.persistence.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("structural", [False, True])
async def test_factory_sandbox_promotion_records_each_file(tmp_path, monkeypatch, structural):
    from types import SimpleNamespace

    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.adapters.local.sandbox import SandboxCapability
    from morrow.bootstrap import build_application
    from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
    from morrow.services.sandbox import SandboxSnapshotService

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "workspace"
    project.mkdir()
    if structural:
        for name, raw in (
            ("delete.txt", b"delete"),
            ("move.txt", b"move"),
            ("rename.txt", b"rename"),
        ):
            (project / name).write_bytes(raw)
        (project / "sub").mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(project))
    sandbox = SandboxSnapshotService(files, temp_parent=tmp_path)
    isolated = sandbox.prepare(project, run_id="probe", call_id="probe")
    try:
        for name in ("a.txt", "b.txt"):
            (isolated.snapshot_root / name).write_text(name)
        if structural:
            (isolated.snapshot_root / "delete.txt").unlink()
            (isolated.snapshot_root / "move.txt").rename(isolated.snapshot_root / "sub/new.txt")
            (isolated.snapshot_root / "rename.txt").rename(isolated.snapshot_root / "renamed.txt")
        change_set = sandbox.collect(isolated)
    finally:
        sandbox.cleanup(isolated)
    select = SandboxSnapshotService.selected_changes

    def select_frozen(run, cid, paths):
        run.retain_change_set(change_set.change_set_id, change_set)
        return select(run, cid, paths)

    monkeypatch.setattr(SandboxSnapshotService, "selected_changes", staticmethod(select_frozen))
    monkeypatch.setattr(
        "morrow.bootstrap.default_sandbox_backend",
        lambda: SimpleNamespace(
            probe=lambda: SandboxCapability(
                platform="test", backend="test", supported=True, reason="scripted change set"
            )
        ),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="promote",
                        name="promote_sandbox_changes",
                        arguments=json.dumps(
                            {
                                "change_set_id": change_set.change_set_id,
                                "paths": ["a.txt", "b.txt"]
                                + (["delete.txt", "move.txt", "rename.txt"] if structural else []),
                            }
                        ),
                    ),
                )
            ),
            AssistantMessage(content="done"),
        ]
    )
    from morrow.core.capabilities import PermissionPreset, PermissionProfile

    products = build_session_application(
        app,
        identity,
        provider=provider,
        model=MODEL,
        approval_port=AutoApprovalPort(),
        permission_profile=PermissionProfile.from_preset(PermissionPreset.AUTO_SANDBOXED),
    )
    try:
        [e async for e in products.orchestrator.stream("promote the two selected text files")]
        rows = products.api.journal.workspace_changes.list(identity.workspace_id)
        assert [r.path for r in rows] == ["a.txt", "b.txt"] + (
            ["delete.txt", "move.txt", "rename.txt"] if structural else []
        )
        assert len({r.tool_execution_id for r in rows}) == 1
        assert len({r.mutation_key for r in rows}) == len(rows)
        assert all(row.effect_state == "applied" for row in rows)
        for row in rows[:2]:
            assert (
                products.artifacts.read_workspace_snapshot(row.after.snapshot_id)
                == row.path.encode()
            )
        if structural:
            from morrow.application.workspace_restore import WorkspaceRestoreService

            restore = WorkspaceRestoreService(
                products.api.journal, products.artifacts, project, identity.workspace_id
            )
            assert [r.operation for r in rows[2:]] == ["delete", "move", "rename"]
            assert [r.destination_path for r in rows[2:]] == [None, "sub/new.txt", "renamed.txt"]
            for row in rows[2:]:
                assert not (project / row.path).exists()
                assert row.coverage == "complete"
                assert restore.bytes(row.before) == row.operation.encode()
            op = restore.preview(
                rows[0].session_id,
                [r.change_id for r in rows[2:]],
                command_id="cmd_restore_preview",
                selectors={"task_run_id": rows[0].task_run_id},
            )
            assert all(item.state == "pending" for item in op.items)
            result = restore.apply(
                op.session_id,
                op.operation_id,
                preview_digest=op.preview_digest,
                command_id="cmd_restore_apply",
            )
            assert result.state == "completed"
            for row in rows[2:]:
                assert (project / row.path).read_bytes() == row.operation.encode()
    finally:
        products.persistence.close()
