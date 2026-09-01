"""Stage 7 Subplan 3: pure deterministic compilation and single publication path."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from morrow.adapters.state.definition_yaml import WorkflowDefinitionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.doctor import OperationalDoctor
from morrow.application.workflows.compiler import (
    COMPILER_VERSION,
    DiagnosticSeverity,
    WorkflowCompilationError,
)
from morrow.application.workflows.publication import WorkflowCompilationService
from morrow.core.agent_definitions import AgentDefinitionSource, ToolRequirement
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.models import ModelRef
from morrow.core.workflows.contracts import (
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    WorkflowInputBinding,
)
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionDocument,
    WorkflowDefinitionSource,
    WorkflowEdge,
)
from morrow.testing import FixedClock, FixedIdSource

MODEL = ModelRef(provider_id="fake-provider", model_id="m1")
OTHER = ModelRef(provider_id="fake-provider", model_id="m2")
CATALOG = DefinitionCatalog(
    models=(MODEL, OTHER),
    skill_version_ids=frozenset(),
    tool_access={"read": "read", "write": "write"},
    allowed_tools=frozenset({"read", "write"}),
)
BUDGET = WorkflowBudget(
    max_agent_generation_requests=10,
    default_node_max_agent_generation_requests=3,
    admission_timeout_seconds=300,
    max_concurrency=1,
)


def definition(**kwargs):
    return AgentDefinitionSource(
        **{
            "definition_id": "helper",
            "name": "Helper",
            "role_prompt": "Inspect password validation and authorization tests.",
            **kwargs,
        }
    )


def node_source(ref, node_id="worker", **changes):
    return AgentNodeSource(
        **{
            "node_id": node_id,
            "agent_definition_ref": ref,
            "task_contract": TaskContract(objective="Inspect authorization tests"),
            "output_contracts": (OutputContract(slot="result"),),
            "access_mode": "read",
            **changes,
        }
    )


def source(ref, **changes):
    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Authorization audit",
            "default_budget": BUDGET,
            "nodes": (node_source(ref),),
            "required_outputs": (NodeOutputRef(node_id="worker", output_slot="result"),),
            **changes,
        }
    )


def errors_of(result):
    return tuple(d for d in result.diagnostics if d.severity is DiagnosticSeverity.ERROR)


def warnings_of(result):
    return tuple(d for d in result.diagnostics if d.severity is DiagnosticSeverity.WARNING)


@pytest.fixture
def state(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    agents = AgentDefinitionPublicationService(
        journal, workspace_id="ws_one", catalog=CATALOG, id_source=FixedIdSource()
    )
    service = WorkflowCompilationService(
        journal, workspace_id="ws_one", catalog=CATALOG, id_source=FixedIdSource()
    )
    yield store, handle, journal, agents, service
    handle.close()


def publish_agent(agents, value=None, *, command="cmd_agent", head=0):
    version = agents.publish(
        value or definition(),
        source_revision=0,
        expected_head_revision=head,
        command_id=command,
    )
    return version, AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )


def publish(service, src, *, head=0, command="cmd_publish", active=MODEL, revision=0, **kwargs):
    return service.publish(
        src,
        source_revision=revision,
        expected_head_revision=head,
        command_id=command,
        active_model=active,
        **kwargs,
    )


def binding_to(node_id, slot="result", input_name="context"):
    return NodeOutputBinding(
        source="node_output",
        input_name=input_name,
        accepts={"kind": "TextResult"},
        node_output=NodeOutputRef(node_id=node_id, output_slot=slot),
    )


def test_one_node_and_multi_node_isolated_positives(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    one = service.validate(source(ref), active_model=MODEL)
    assert one.candidate is not None and one.diagnostics == ()
    compiled = one.candidate
    assert compiled.entry_nodes == ("worker",) and compiled.terminal_nodes == ("worker",)
    node = compiled.nodes[0]
    assert node.conversation_scope == "isolated" and node.access_mode == "read"
    assert node.resolved_model_ref == MODEL
    assert node.declared_node_max_agent_generation_requests == 3
    assert compiled.budget == BUDGET and compiled.compiler_version == COMPILER_VERSION
    multi = source(
        ref,
        nodes=(
            node_source(ref, "explore"),
            node_source(ref, "report", input_bindings=(binding_to("explore"),)),
        ),
        edges=(WorkflowEdge(from_node_id="explore", to_node_id="report"),),
        required_outputs=(NodeOutputRef(node_id="report", output_slot="result"),),
    )
    result = service.validate(multi, active_model=MODEL)
    assert result.candidate is not None and result.diagnostics == ()
    assert result.candidate.entry_nodes == ("explore",)
    assert result.candidate.terminal_nodes == ("report",)


def test_validate_is_write_free_and_matches_publish_diagnostics(state, monkeypatch):
    _, _, journal, agents, service = state
    _, ref = publish_agent(agents)
    bad_ref = AgentDefinitionRef(
        definition_id="helper", version_id="adev_missing", content_hash="a" * 64
    )
    invalid = source(bad_ref)
    with monkeypatch.context() as guard:
        guard.setattr(journal, "transact", lambda *_: pytest.fail("validation wrote"))
        guard.setattr(
            service.id_source, "new_id", lambda *_: pytest.fail("validation allocated identity")
        )
        result = service.validate(invalid, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["agent_version_unresolved"]
    assert journal.workflows.list_revisions("ws_one") == ()
    assert journal.workflows.get_head("ws_one", "pipeline") is None
    with pytest.raises(WorkflowCompilationError) as raised:
        publish(service, invalid)
    assert [d.code for d in raised.value.diagnostics] == ["agent_version_unresolved"]
    assert journal.workflows.list_revisions("ws_one") == ()
    assert journal.workflows.publication("ws_one", "cmd_publish") is None
    # A broken definition never blocks another definition or re-validation.
    assert service.validate(source(ref), active_model=MODEL).candidate is not None


def test_active_model_freeze_noop_and_same_command_replay(state):
    _, _, journal, agents, service = state
    _, ref = publish_agent(agents)
    src = source(ref)
    first = publish(service, src, active=MODEL).revision
    assert first.nodes[0].resolved_model_ref == MODEL
    replayed = publish(service, src, active=OTHER)  # replay survives moved configuration
    assert replayed.revision == first and replayed.diagnostics == ()
    assert (
        publish(service, src, head=1, command="cmd_noop", active=MODEL, revision=1).revision
        == first
    )
    second = publish(service, src, head=1, command="cmd_b", active=OTHER, revision=1).revision
    assert second.revision == 2 and second.parent_workflow_revision_id == first.workflow_revision_id
    assert second.nodes[0].resolved_model_ref == OTHER
    assert second.content_hash != first.content_hash
    assert journal.workflows.get_revision("ws_one", first.workflow_revision_id) == first
    assert (
        publish(service, src, head=2, command="cmd_b2", active=OTHER, revision=1).revision == second
    )
    head = journal.workflows.get_head("ws_one", "pipeline")
    assert head.workflow_revision_id == second.workflow_revision_id and head.enabled
    with pytest.raises(ValueError, match="conflicts with prior receipt"):
        publish(service, source(ref, description="changed"))


def test_moving_agent_head_does_not_drift_an_exact_version_reference(state):
    _, _, journal, agents, service = state
    first_version, ref = publish_agent(agents)
    src = source(ref)
    revision = publish(service, src).revision
    edited = definition().model_copy(update={"role_prompt": "Inspect a new task."})
    moved = agents.publish(
        edited, source_revision=1, expected_head_revision=1, command_id="cmd_agent_v2"
    )
    assert moved.version_id != first_version.version_id
    assert publish(service, src, head=1, command="cmd_noop", revision=1).revision == revision
    moved_ref = AgentDefinitionRef(
        definition_id="helper",
        version_id=moved.version_id,
        content_hash=moved.content_hash,
    )
    recompiled = publish(
        service, source(moved_ref), head=1, command="cmd_explicit", revision=2
    ).revision
    assert recompiled.revision == 2
    assert recompiled.nodes[0].agent_definition_ref == moved_ref
    assert journal.workflows.get_revision("ws_one", revision.workflow_revision_id) == revision


def test_metadata_edit_creates_a_new_revision_then_noop(state):
    _, _, journal, agents, service = state
    _, ref = publish_agent(agents)
    first = publish(service, source(ref)).revision
    edited = source(ref, description="Broader audit", tags=("security",))
    second = publish(service, edited, head=1, command="cmd_meta", revision=1).revision
    assert second.revision == 2 and second.description == "Broader audit"
    assert second.tags == ("security",) and second.content_hash != first.content_hash
    assert publish(service, edited, head=2, command="cmd_meta2", revision=1).revision == second
    head = journal.workflows.get_head("ws_one", "pipeline")
    assert head.source_hash == edited.content_hash


def test_tool_merge_narrows_and_freezes_the_effective_set(state):
    _, _, _, agents, service = state
    value = definition(
        tool_requirements=(
            ToolRequirement(name="read", requirement="required"),
            ToolRequirement(name="write", requirement="optional"),
            ToolRequirement(name="shell", requirement="forbidden"),
        )
    )
    _, ref = publish_agent(agents, value)
    # No overlay: the optional write tool falls to the read ceiling with a diagnostic.
    plain = service.validate(source(ref), active_model=MODEL)
    assert plain.candidate is not None
    assert plain.candidate.nodes[0].tool_requirements is None  # source overlay preserved
    merged = {t.name: t.requirement for t in plain.candidate.nodes[0].resolved_tool_requirements}
    assert merged == {"read": "required", "shell": "forbidden"}
    assert [d.code for d in warnings_of(plain)] == ["optional_removed"]
    # The node may forbid a definition-optional tool; forbidden stays frozen.
    narrowed = source(
        ref,
        nodes=(
            node_source(
                ref,
                tool_requirements=(ToolRequirement(name="write", requirement="forbidden"),),
            ),
        ),
    )
    result = service.validate(narrowed, active_model=MODEL)
    assert result.candidate is not None and result.diagnostics == ()
    assert result.candidate.nodes[0].tool_requirements == (
        ToolRequirement(name="write", requirement="forbidden"),
    )
    merged = {t.name: t.requirement for t in result.candidate.nodes[0].resolved_tool_requirements}
    assert merged == {"read": "required", "shell": "forbidden", "write": "forbidden"}


def test_tool_merge_forbidden_over_required_conflict_is_an_error(state):
    _, _, _, agents, service = state
    value = definition(tool_requirements=(ToolRequirement(name="read", requirement="required"),))
    _, ref = publish_agent(agents, value)
    conflict = source(
        ref,
        nodes=(
            node_source(
                ref, tool_requirements=(ToolRequirement(name="read", requirement="forbidden"),)
            ),
        ),
    )
    result = service.validate(conflict, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["tool_requirement_conflict"]
    with pytest.raises(WorkflowCompilationError, match="tool_requirement_conflict"):
        publish(service, conflict)
    legal = source(
        ref,
        nodes=(
            node_source(
                ref, tool_requirements=(ToolRequirement(name="read", requirement="required"),)
            ),
        ),
    )
    assert service.validate(legal, active_model=MODEL).candidate is not None


def test_required_tool_denied_by_ceiling_beside_optional_removal(state):
    _, _, _, agents, service = state
    writer = definition(
        definition_id="writer",
        access_mode_ceiling="write",
        tool_requirements=(ToolRequirement(name="write", requirement="required"),),
    )
    _, writer_ref = publish_agent(agents, writer, command="cmd_writer")
    # The node keeps access_mode=read, so the required write tool is denied.
    denied = service.validate(source(writer_ref), active_model=MODEL)
    assert denied.candidate is None
    assert [d.code for d in errors_of(denied)] == ["required_tool_denied"]
    legal = source(writer_ref, nodes=(node_source(writer_ref, access_mode="write"),))
    assert service.validate(legal, active_model=MODEL).candidate is not None
    # A catalog-absent optional tool is removed with a diagnostic, never an error.
    optional = definition(
        definition_id="optional",
        tool_requirements=(ToolRequirement(name="read", requirement="optional"),),
    )
    _, optional_ref = publish_agent(agents, optional, command="cmd_optional")
    service.catalog = replace(
        CATALOG, tool_access={"write": "write"}, allowed_tools=frozenset({"write"})
    )
    removed = service.validate(source(optional_ref), active_model=MODEL)
    assert removed.candidate is not None
    assert removed.candidate.nodes[0].resolved_tool_requirements == ()
    assert [d.code for d in warnings_of(removed)] == ["optional_removed"]


def test_required_tool_absent_from_catalogs_is_an_error(state):
    _, _, _, agents, service = state
    reader = definition(
        definition_id="reader",
        tool_requirements=(ToolRequirement(name="read", requirement="required"),),
    )
    _, ref = publish_agent(agents, reader, command="cmd_reader")
    service.catalog = replace(
        CATALOG, tool_access={"write": "write"}, allowed_tools=frozenset({"write"})
    )
    result = service.validate(source(ref), active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["required_tool_absent"]
    with pytest.raises(WorkflowCompilationError, match="required_tool_absent"):
        publish(service, source(ref))


def test_node_overlay_outside_definition_set_is_an_error(state):
    _, _, _, agents, service = state
    value = definition(
        definition_id="narrow",
        tool_requirements=(ToolRequirement(name="read", requirement="optional"),),
    )
    _, ref = publish_agent(agents, value, command="cmd_narrow")
    outside = source(
        ref,
        nodes=(
            node_source(
                ref, tool_requirements=(ToolRequirement(name="write", requirement="required"),)
            ),
        ),
    )
    result = service.validate(outside, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["tool_not_declared"]
    upgraded = source(
        ref,
        nodes=(
            node_source(
                ref, tool_requirements=(ToolRequirement(name="read", requirement="required"),)
            ),
        ),
    )
    compiled = service.validate(upgraded, active_model=MODEL)
    assert compiled.candidate is not None
    merged = {t.name: t.requirement for t in compiled.candidate.nodes[0].resolved_tool_requirements}
    assert merged == {"read": "required"}


def test_write_node_under_read_ceiling_is_capability_escalation(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)  # helper defaults to a read ceiling
    escalation = source(ref, nodes=(node_source(ref, access_mode="write"),))
    result = service.validate(escalation, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["access_mode_escalation"]
    writer = definition(definition_id="writer2", access_mode_ceiling="write")
    _, writer_ref = publish_agent(agents, writer, command="cmd_writer2")
    legal = source(writer_ref, nodes=(node_source(writer_ref, access_mode="write"),))
    compiled = service.validate(legal, active_model=MODEL)
    assert compiled.candidate is not None and compiled.diagnostics == ()
    two_writers = source(
        writer_ref,
        workflow_definition_id="two_writers",
        nodes=(
            node_source(writer_ref, "first", access_mode="write"),
            node_source(writer_ref, "second", access_mode="write"),
        ),
        edges=(WorkflowEdge(from_node_id="first", to_node_id="second"),),
        required_outputs=(
            NodeOutputRef(node_id="first", output_slot="result"),
            NodeOutputRef(node_id="second", output_slot="result"),
        ),
    )
    warned = service.validate(two_writers, active_model=MODEL)
    assert warned.candidate is not None
    assert [d.code for d in warnings_of(warned)] == ["independent_writers"]


def test_disconnected_component_error_and_control_edge_fix(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    nodes = (node_source(ref, "orphan"), node_source(ref))
    result = service.validate(source(ref, nodes=nodes), active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["graph_disconnected"]
    assert "add an explicit control edge or remove them" in errors_of(result)[0].message
    # The fix is an explicit control-only edge; the unconsumed orphan stays a
    # warning and remains execution-required.
    fixed = source(
        ref,
        nodes=nodes,
        edges=(WorkflowEdge(from_node_id="orphan", to_node_id="worker"),),
    )
    resolved = service.validate(fixed, active_model=MODEL)
    assert resolved.candidate is not None
    assert [d.code for d in warnings_of(resolved)] == ["unconsumed_outputs"]
    assert {n.node_id for n in resolved.candidate.nodes} == {"orphan", "worker"}
    assert resolved.candidate.entry_nodes == ("orphan",)


def test_cycle_is_an_error_beside_the_legal_dag(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    nodes = (node_source(ref, "explore"), node_source(ref, input_bindings=(binding_to("explore"),)))
    edge = WorkflowEdge(from_node_id="explore", to_node_id="worker")
    legal = source(ref, nodes=nodes, edges=(edge,))
    assert service.validate(legal, active_model=MODEL).candidate is not None
    cyclic = source(
        ref, nodes=nodes, edges=(edge, WorkflowEdge(from_node_id="worker", to_node_id="explore"))
    )
    result = service.validate(cyclic, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["graph_cycle"]


def test_binding_requires_same_direction_edge(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    nodes = (node_source(ref, "explore"), node_source(ref, input_bindings=(binding_to("explore"),)))
    wrong_way = source(
        ref,
        nodes=nodes,
        edges=(WorkflowEdge(from_node_id="worker", to_node_id="explore"),),
    )
    result = service.validate(wrong_way, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["structure_invalid"]
    assert "same-direction edge" in errors_of(result)[0].message
    edge = WorkflowEdge(from_node_id="explore", to_node_id="worker")
    legal = source(ref, nodes=nodes, edges=(edge,))
    assert service.validate(legal, active_model=MODEL).candidate is not None


def test_binding_and_export_require_a_completion_required_slot(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    observation = node_source(
        ref,
        "explore",
        output_contracts=(OutputContract(slot="result", required_for_node_completion=False),),
    )
    edge = WorkflowEdge(from_node_id="explore", to_node_id="worker")
    bound = source(
        ref,
        nodes=(observation, node_source(ref, input_bindings=(binding_to("explore"),))),
        edges=(edge,),
    )
    result = service.validate(bound, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["structure_invalid"]
    assert "completion-required" in errors_of(result)[0].message
    exported = source(
        ref,
        nodes=(observation, node_source(ref)),
        edges=(edge,),
        required_outputs=(NodeOutputRef(node_id="explore", output_slot="result"),),
    )
    assert service.validate(exported, active_model=MODEL).candidate is None
    # The observation slot is legal when nobody binds or exports it.
    observable = source(ref, nodes=(observation, node_source(ref)), edges=(edge,))
    ok = service.validate(observable, active_model=MODEL)
    assert ok.candidate is not None and ok.diagnostics == ()


def test_input_contract_is_exactly_task_contract_v1(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    assert service.validate(source(ref), active_model=MODEL).candidate is not None
    with pytest.raises(ValidationError):
        source(ref, input_contract={"kind": "TextResult", "version": 1})
    with pytest.raises(ValidationError):
        source(ref, input_contract={"kind": "TaskContract", "version": 2})
    with pytest.raises(ValidationError):
        WorkflowInputBinding(
            source="workflow_input",
            input_name="task",
            accepts={"kind": "TextResult"},
            workflow_input="task",
        )
    bound = node_source(
        ref,
        input_bindings=(
            WorkflowInputBinding(
                source="workflow_input",
                input_name="task",
                accepts={"kind": "TaskContract"},
                workflow_input="task",
            ),
        ),
    )
    assert service.validate(source(ref, nodes=(bound,)), active_model=MODEL).candidate is not None


def test_budget_freeze_uses_node_default_and_definition_ceiling(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    capped = definition(definition_id="capped", max_agent_generation_requests=2)
    _, capped_ref = publish_agent(agents, capped, command="cmd_capped")
    unlimited = service.validate(source(ref), active_model=MODEL).candidate
    assert unlimited.nodes[0].declared_node_max_agent_generation_requests == 3
    override = source(ref, nodes=(node_source(ref, max_agent_generation_requests=5),))
    assert (
        service.validate(override, active_model=MODEL)
        .candidate.nodes[0]
        .declared_node_max_agent_generation_requests
        == 5
    )
    ceiling = service.validate(source(capped_ref), active_model=MODEL).candidate
    assert ceiling.nodes[0].declared_node_max_agent_generation_requests == 2
    # A node maximum larger than the Workflow total is legal: runtime freezes a
    # shrunken effective cap.
    big = source(ref, nodes=(node_source(ref, max_agent_generation_requests=50),))
    assert (
        service.validate(big, active_model=MODEL)
        .candidate.nodes[0]
        .declared_node_max_agent_generation_requests
        == 50
    )
    for field in (
        "max_agent_generation_requests",
        "default_node_max_agent_generation_requests",
    ):
        with pytest.raises(ValidationError):
            WorkflowBudget(**{**BUDGET.model_dump(), field: 0})


def test_missing_or_mismatched_agent_version_is_rejected(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    missing = AgentDefinitionRef(
        definition_id="helper", version_id="adev_missing", content_hash=ref.content_hash
    )
    result = service.validate(source(missing), active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["agent_version_unresolved"]
    tampered = AgentDefinitionRef(
        definition_id="helper", version_id=ref.version_id, content_hash="b" * 64
    )
    assert service.validate(source(tampered), active_model=MODEL).candidate is None
    wrong_definition = AgentDefinitionRef(
        definition_id="other", version_id=ref.version_id, content_hash=ref.content_hash
    )
    assert service.validate(source(wrong_definition), active_model=MODEL).candidate is None
    with pytest.raises(WorkflowCompilationError, match="agent_version_unresolved"):
        publish(service, source(missing))
    # The failure stays scoped: the valid definition still publishes.
    assert publish(service, source(ref), command="cmd_valid").revision is not None


def test_missing_active_model_is_a_scoped_compile_error(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)  # helper uses model_selection=invoking_active
    result = service.validate(source(ref), active_model=None)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["model_unavailable"]
    exact = definition(definition_id="exact", model_selection=MODEL)
    _, exact_ref = publish_agent(agents, exact, command="cmd_exact")
    frozen = service.validate(source(exact_ref), active_model=None).candidate
    assert frozen is not None and frozen.nodes[0].resolved_model_ref == MODEL


def test_occ_stale_and_invalid_metadata_store_nothing(state):
    _, _, journal, agents, service = state
    _, ref = publish_agent(agents)
    src = source(ref)
    first = publish(service, src).revision
    with pytest.raises(ValueError, match="head revision conflict"):
        publish(service, src, head=0, command="cmd_stale")
    assert journal.workflows.list_revisions("ws_one") == (first,)
    assert journal.workflows.publication("ws_one", "cmd_stale") is None
    with pytest.raises(ValueError, match="publication metadata"):
        publish(service, src, head=1, command="cmd_meta", revision=-1)
    with pytest.raises(ValueError, match="publication metadata"):
        publish(service, src, head=-1, command="cmd_meta", revision=1)
    with pytest.raises(ValueError, match="publication metadata"):
        publish(service, src, head=1, command="cmd_meta", revision=1, enabled=1)


def test_publication_rolls_back_revision_when_the_store_write_fails(state, monkeypatch):
    _, _, journal, agents, service = state
    _, ref = publish_agent(agents)
    original = journal.workflows.store_compiled_revision

    def fail(*args, **kwargs):
        raise RuntimeError("injected store failure")

    monkeypatch.setattr(journal.workflows, "store_compiled_revision", fail)
    with pytest.raises(RuntimeError, match="injected"):
        publish(service, source(ref))
    assert journal.workflows.list_revisions("ws_one") == ()
    assert journal.workflows.get_head("ws_one", "pipeline") is None
    assert journal.workflows.publication("ws_one", "cmd_publish") is None
    monkeypatch.setattr(journal.workflows, "store_compiled_revision", original)
    assert publish(service, source(ref)).revision.revision == 1


def test_revoked_references_and_revision_cannot_reenter(state):
    _, _, journal, agents, service = state
    version, ref = publish_agent(agents)
    src = source(ref)
    revision = publish(service, src).revision
    record = service.revoke(
        revision.workflow_revision_id, reason="policy changed", command_id="cmd_revoke_wf"
    )
    assert (
        service.revoke(
            revision.workflow_revision_id, reason="policy changed", command_id="cmd_revoke_wf"
        )
        == record
    )
    with pytest.raises(ValueError, match="one-way"):
        service.revoke(revision.workflow_revision_id, reason="undo", command_id="cmd_replace")
    # Neither a new identical command nor the original receipt can resurrect it.
    with pytest.raises(ValueError, match="policy_revoked"):
        publish(service, src, head=1, command="cmd_reenter", revision=1)
    with pytest.raises(ValueError, match="policy_revoked"):
        publish(service, src)
    edited = source(ref, description="superseding revision")
    successor = publish(service, edited, head=1, command="cmd_supersede", revision=1).revision
    assert successor.revision == 2
    assert journal.workflows.get_revision("ws_one", revision.workflow_revision_id) == revision
    # A revoked referenced Agent version rejects publication of its consumers.
    agents.revoke(version.version_id, reason="policy changed", command_id="cmd_revoke_agent")
    with pytest.raises(ValueError, match="referenced AgentDefinitionVersion is revoked"):
        publish(service, edited, head=2, command="cmd_after_revoke", revision=2)
    assert journal.workflows.list_revisions("ws_one") == (revision, successor)


def test_first_publication_disabled_and_enable_toggle_creates_no_revision(state):
    _, _, journal, agents, service = state
    _, ref = publish_agent(agents)
    first = publish(service, source(ref), enabled=False).revision
    head = journal.workflows.get_head("ws_one", "pipeline")
    assert head.workflow_revision_id == first.workflow_revision_id and not head.enabled
    toggled = service.set_enabled("pipeline", enabled=True, expected_head_revision=1)
    assert toggled.enabled and toggled.row_version == 2
    assert journal.workflows.list_revisions("ws_one") == (first,)
    # Publication preserves the operational enable flag across a new Revision.
    edited = source(ref, description="still enabled")
    publish(service, edited, head=2, command="cmd_next", revision=1, enabled=False)
    assert journal.workflows.get_head("ws_one", "pipeline").enabled
    with pytest.raises(ValueError, match="revision conflict"):
        service.set_enabled("pipeline", enabled=False, expected_head_revision=1)


def test_canonical_hash_is_stable_across_source_ordering(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    edge = WorkflowEdge(from_node_id="explore", to_node_id="worker")
    forward = source(
        ref,
        nodes=(
            node_source(ref, "explore"),
            node_source(ref, input_bindings=(binding_to("explore"),)),
        ),
        edges=(edge,),
    )
    reversed_nodes = source(
        ref,
        nodes=(
            node_source(ref, input_bindings=(binding_to("explore"),)),
            node_source(ref, "explore"),
        ),
        edges=(edge,),
    )
    first = service.validate(forward, active_model=MODEL)
    second = service.validate(reversed_nodes, active_model=MODEL)
    assert first.candidate is not None and second.candidate is not None
    assert first.content_hash == second.content_hash
    assert first.candidate == second.candidate


def test_ghost_endpoint_and_self_loop_are_rejected(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    ghost = source(ref, edges=(WorkflowEdge(from_node_id="ghost", to_node_id="worker"),))
    result = service.validate(ghost, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["edge_endpoint_invalid"]
    loop = source(ref, edges=(WorkflowEdge(from_node_id="worker", to_node_id="worker"),))
    result = service.validate(loop, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["edge_endpoint_invalid"]
    # The adjacent legal control-only edge still compiles.
    legal = source(
        ref,
        nodes=(node_source(ref, "orphan"), node_source(ref)),
        edges=(WorkflowEdge(from_node_id="orphan", to_node_id="worker"),),
    )
    assert service.validate(legal, active_model=MODEL).candidate is not None


def test_binding_contract_mismatch_is_rejected_beside_legal_binding(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    mismatched = NodeOutputBinding(
        source="node_output",
        input_name="context",
        accepts={"kind": "TaskContract"},
        node_output=NodeOutputRef(node_id="explore", output_slot="result"),
    )
    edge = WorkflowEdge(from_node_id="explore", to_node_id="worker")
    bad = source(
        ref,
        nodes=(node_source(ref, "explore"), node_source(ref, input_bindings=(mismatched,))),
        edges=(edge,),
    )
    result = service.validate(bad, active_model=MODEL)
    assert result.candidate is None
    assert [d.code for d in errors_of(result)] == ["structure_invalid"]
    assert "contract mismatch" in errors_of(result)[0].message
    legal = source(
        ref,
        nodes=(
            node_source(ref, "explore"),
            node_source(ref, input_bindings=(binding_to("explore"),)),
        ),
        edges=(edge,),
    )
    assert service.validate(legal, active_model=MODEL).candidate is not None


def test_disconnected_naming_is_deterministic_for_equal_components(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    pair_a = (
        node_source(ref, "alpha_one"),
        node_source(ref, "alpha_two", input_bindings=(binding_to("alpha_one"),)),
    )
    pair_b = (
        node_source(ref, "beta_one"),
        node_source(ref, "beta_two", input_bindings=(binding_to("beta_one"),)),
    )
    disconnected = source(
        ref,
        nodes=pair_a + pair_b,
        edges=(
            WorkflowEdge(from_node_id="alpha_one", to_node_id="alpha_two"),
            WorkflowEdge(from_node_id="beta_one", to_node_id="beta_two"),
        ),
        required_outputs=(NodeOutputRef(node_id="alpha_two", output_slot="result"),),
    )
    first = service.validate(disconnected, active_model=MODEL)
    second = service.validate(disconnected, active_model=MODEL)
    assert first.candidate is None and second.candidate is None
    assert first.diagnostics == second.diagnostics
    message = errors_of(first)[0].message
    assert "beta_one, beta_two" in message and "alpha" not in message.split("nodes")[1]


def test_successful_publish_returns_compile_diagnostics(state):
    _, _, _, agents, service = state
    _, ref = publish_agent(agents)
    fixed = source(
        ref,
        nodes=(node_source(ref, "orphan"), node_source(ref)),
        edges=(WorkflowEdge(from_node_id="orphan", to_node_id="worker"),),
    )
    published = publish(service, fixed)
    assert [d.code for d in warnings_of(published)] == ["unconsumed_outputs"]
    validated = service.validate(fixed, active_model=MODEL)
    assert published.diagnostics == validated.diagnostics
    # A same-command replay does not recompile and returns empty diagnostics.
    assert publish(service, fixed).diagnostics == ()


def test_overlay_restatement_publishes_a_new_revision_and_clears_desired_ahead(state):
    store, _, journal, agents, service = state
    value = definition(tool_requirements=(ToolRequirement(name="read", requirement="required"),))
    _, ref = publish_agent(agents, value)
    plain = source(ref)
    first = publish(service, plain).revision
    # Restating a definition-level requirement in the overlay changes the source
    # body; the frozen merge is identical, so the new Revision carries the same
    # resolved evidence while the head's source evidence advances.
    restated = source(
        ref,
        nodes=(
            node_source(
                ref, tool_requirements=(ToolRequirement(name="read", requirement="required"),)
            ),
        ),
    )
    yaml = WorkflowDefinitionYamlStore(store.layout.data_root)
    yaml.write("ws_one", WorkflowDefinitionDocument(definitions=(restated,)), expected_revision=0)
    second = publish(service, restated, head=1, command="cmd_restated", revision=1).revision
    assert second.workflow_revision_id != first.workflow_revision_id
    assert second.nodes[0].resolved_tool_requirements == first.nodes[0].resolved_tool_requirements
    head = journal.workflows.get_head("ws_one", "pipeline")
    assert head.source_hash == restated.content_hash and head.source_revision == 1
    report = OperationalDoctor(store).inspect("ws_one")
    assert report.health.value == "ok"
    assert not any(i.code == "workflow_desired_ahead" for i in report.issues)
    # Identical current content remains a no-op with no duplicate Revision identity.
    assert publish(service, restated, head=2, command="cmd_again", revision=1).revision == second
    assert len(journal.workflows.list_revisions("ws_one")) == 2
