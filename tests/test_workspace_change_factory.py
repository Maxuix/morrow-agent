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
