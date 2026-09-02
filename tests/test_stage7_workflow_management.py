"""Stage 7 management surface: desired state, publication and recovery delegation."""

from __future__ import annotations

import pytest

from morrow.adapters.state.definition_yaml import (
    AgentDefinitionYamlStore,
    WorkflowDefinitionYamlStore,
)
from morrow.application.agent_definitions.builtins import builtin_definitions
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.workflows.builtins import builtin_workflows
from morrow.application.workflows.management import WorkflowManagementService
from morrow.application.workflows.publication import WorkflowCompilationService
from morrow.application.workflows.queries import WorkflowQueryService
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.workflows.contracts import TaskContract
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_isolated_workflow_slice import (
    CONTRACT,
    MODEL,
    WS,
    SliceFixture,
    agent_source,
    workflow_source,
)


@pytest.fixture(name="fx")
def management_fixture(tmp_path):
    fixture = SliceFixture(tmp_path)
    yield fixture
    fixture.close()


def management(fx):
    root = fx.store.layout.data_root
    return WorkflowManagementService(
        workspace_id=WS,
        agent_sources=AgentDefinitionYamlStore(root),
        workflow_sources=WorkflowDefinitionYamlStore(root),
        agent_publication=fx.agents,
        workflow_publication=fx.compiler,
        runtime=fx.runtime,
        active_model=MODEL,
    )


def test_management_create_update_validate_is_write_free_and_publish_is_explicit(fx):
    service = management(fx)
    created = service.create_agent_source(agent_source(), expected_source_revision=0)
    assert created.source_revision == 1
    assert fx.journal.agent_definitions.list_versions(WS) == ()

    first = service.validate_agent("helper")
    second = service.validate_agent("helper")
    assert first == second
    assert fx.journal.agent_definitions.list_versions(WS) == ()

    version = service.publish_agent(
        "helper", expected_head_revision=0, command_id="cmd_manage_agent"
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    workflow = workflow_source(ref)
    workflow_created = service.create_workflow_source(workflow, expected_source_revision=0)
    assert workflow_created.source_revision == 1
    before = fx.journal.workflows.list_revisions(WS)
    assert service.validate_workflow("pipeline").candidate is not None
    assert service.validate_workflow("pipeline").candidate is not None
    assert fx.journal.workflows.list_revisions(WS) == before

    publication = service.publish_workflow(
        "pipeline", expected_head_revision=0, command_id="cmd_manage_workflow"
    )
    assert publication.revision.workflow_definition_id == "pipeline"


def test_management_occ_readonly_builtin_and_exact_revocation_controls(fx):
    service = management(fx)
    service.create_agent_source(agent_source(), expected_source_revision=0)
    version = service.publish_agent(
        "helper", expected_head_revision=0, command_id="cmd_agent_publish"
    )
    with pytest.raises(ValueError, match="already exists"):
        service.create_agent_source(agent_source(), expected_source_revision=1)
    with pytest.raises(ValueError, match="exact AgentDefinitionVersion"):
        service.revoke_agent_version("helper", reason="emergency", command_id="cmd_bad_revoke")

    disabled = service.set_agent_enabled("helper", enabled=False, expected_head_revision=1)
    assert disabled.enabled is False
    revocation = service.revoke_agent_version(
        version.version_id, reason="emergency", command_id="cmd_revoke_agent"
    )
    assert revocation.version_id == version.version_id


@pytest.mark.asyncio
async def test_management_plain_unpublished_run_fails_and_foreground_uses_exact_revision(fx):
    service = management(fx)
    service.create_agent_source(agent_source(), expected_source_revision=0)
    version = service.publish_agent(
        "helper", expected_head_revision=0, command_id="cmd_agent_publish"
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    service.create_workflow_source(workflow_source(ref), expected_source_revision=0)
    root = fx.journal.get_task_run(WS, "task_root")
    unpublished = StartWorkflowCommand(
        workflow_definition_id="pipeline",
        workflow_revision_id="wrev_missing",
        session_id="ses_root",
        root_task_run_id="task_root",
        expected_root_row_version=root.row_version,
        contract=TaskContract(objective=CONTRACT.objective),
        command_id="cmd_unpublished_start",
    )
    with pytest.raises(Exception, match="publish first|ensure-published"):
        await service.run_foreground(unpublished)

    publication = service.publish_workflow(
        "pipeline", expected_head_revision=0, command_id="cmd_workflow_publish"
    )
    command = StartWorkflowCommand(
        **{
            **unpublished.__dict__,
            "workflow_revision_id": publication.revision.workflow_revision_id,
            "command_id": "cmd_foreground_start",
        }
    )
    result = await service.run_foreground(command)
    assert result.workflow_revision_id == publication.revision.workflow_revision_id
    assert result.published is False
    assert result.run.status is WorkflowStatus.COMPLETED

    queries = WorkflowQueryService(
        fx.journal,
        workspace_id=WS,
        agent_sources=service.agent_sources,
        workflow_sources=service.workflow_sources,
    )
    agent_view = queries.list_agent_definitions(limit=1)[0]
    assert agent_view.definition_id == "helper"
    assert agent_view.desired_ahead_of_published is False
    workflow_view = queries.list_workflow_definitions(limit=1)[0]
    assert workflow_view.workflow_definition_id == "pipeline"
    assert workflow_view.published_revision.workflow_revision_id == result.workflow_revision_id
    run_view = queries.get_run_view(result.run.workflow_run_id)
    assert run_view.run == result.run
    assert run_view.terminal_outcome is not None
    assert queries.get_node_view(run_view.nodes[0].node.node_run_id) == run_view.nodes[0]


def test_four_builtin_templates_publish_through_generic_compiler(fx):
    access = {
        "read": "read",
        "grep": "read",
        "ls": "read",
        "find": "read",
        "edit": "write",
        "write": "write",
        "bash": "write",
        "promote_sandbox_changes": "write",
    }
    catalog = DefinitionCatalog(
        models=(MODEL,),
        skill_version_ids=frozenset(),
        tool_access=access,
        allowed_tools=frozenset(access),
    )
    agents = AgentDefinitionPublicationService(
        fx.journal, workspace_id=WS, catalog=catalog, id_source=fx.ids
    )
    compiler = WorkflowCompilationService(
        fx.journal, workspace_id=WS, catalog=catalog, id_source=fx.ids
    )
    refs = {}
    for index, source in enumerate(builtin_definitions(MODEL), start=1):
        version = agents.publish(
            source,
            source_revision=0,
            expected_head_revision=0,
            command_id=f"cmd_builtin_agent_{index}",
            origin="builtin",
        )
        refs[source.definition_id] = AgentDefinitionRef(
            definition_id=source.definition_id,
            version_id=version.version_id,
            content_hash=version.content_hash,
        )

    templates = builtin_workflows(refs, native_sandbox=False)
    assert {item.workflow_definition_id for item in templates} == {
        "builtin_direct_workflow",
        "builtin_explore_implement_verify",
        "builtin_parallel_research",
        "builtin_planned_refactor",
    }
    for index, source in enumerate(templates, start=1):
        assert compiler.validate(source, active_model=MODEL).candidate is not None
        published = compiler.publish(
            source,
            source_revision=0,
            expected_head_revision=0,
            command_id=f"cmd_builtin_workflow_{index}",
            active_model=MODEL,
        )
        assert published.revision.workflow_definition_id == source.workflow_definition_id
    research = next(
        item for item in templates if item.workflow_definition_id == "builtin_parallel_research"
    )
    assert research.required_outputs == (
        next(ref for ref in research.required_outputs if ref.output_slot == "synthesis"),
    )
