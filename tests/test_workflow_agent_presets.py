"""Workflow chat-first Subplan 2: agent presets and precise execution settings.

Covers the preset catalog (D04), idempotent materialization, preference
overlay, quick save-and-available, the D06 selection resolver and generation
freezing through AgentRun evidence. Scripted Providers only; no Live access.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from morrow.adapters.state.definition_yaml import AgentDefinitionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.adapters.state.preset_preference_yaml import AgentPresetPreferenceYamlStore
from morrow.application.agent_definitions.presets import (
    AgentPresetMaterializationError,
    AgentPresetMaterializationService,
)
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.agent_definitions.quick_save import (
    AgentQuickSaveFields,
    build_quick_save_source,
    derive_definition_id,
)
from morrow.application.agent_runs.preparation import AgentRunPreparationError
from morrow.application.workflows.composition import build_workflow_runtime
from morrow.core.agent_definitions import (
    AgentDefinitionDocument,
    AgentDefinitionSource,
    ToolRequirement,
)
from morrow.core.agent_presets import (
    PRESET_DEFINITION_IDS,
    AgentPresetPreference,
    LoadedPresetPreference,
    is_preset_definition_id,
    preset_sources,
)
from morrow.core.execution_selections import (
    ExplicitGenerationSelection,
    ExplicitModelSelection,
    ModelDefaultSelection,
    SelectionInputs,
    resolve_selection,
)
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import GenerationOptions, ModelRef
from morrow.core.store import StoreOpenMode
from morrow.core.workflows.contracts import NodeOutputRef, OutputContract, TaskContract
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowDefinitionSource,
)
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.tools import make_tool
from morrow.testing import FixedClock, FixedIdSource
from test_stage7_multi_agent_pipeline import (
    BUDGET,
    WS,
    PathArgs,
    PipelineFixture,
    _ok_handler,
    _publish_agent,
    _script_submit_then_stop,
    _start_named,
    _stub_intent,
    _task_binding,
)
from test_stage7_multi_agent_pipeline import (
    CATALOG as PIPELINE_CATALOG,
)
from test_stage7_multi_agent_pipeline import (
    MODEL as PIPELINE_MODEL,
)

MODEL = ModelRef(provider_id="fake-provider", model_id="m1")
OTHER = ModelRef(provider_id="fake-provider", model_id="m2")
CATALOG = DefinitionCatalog(
    models=(MODEL, OTHER),
    skill_version_ids=frozenset(),
    tool_access={
        "read": "read",
        "ls": "read",
        "find": "read",
        "grep": "read",
        "write": "write",
        "edit": "write",
        "bash": "write",
        "promote_sandbox_changes": "write",
    },
    allowed_tools=frozenset(
        ("read", "ls", "find", "grep", "write", "edit", "bash", "promote_sandbox_changes")
    ),
)
EFFORTS = ("minimal", "low", "medium", "high")


@pytest.fixture
def state(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    publication = AgentDefinitionPublicationService(
        journal, workspace_id="ws_one", catalog=CATALOG, id_source=FixedIdSource()
    )
    materialization = AgentPresetMaterializationService(
        journal, workspace_id="ws_one", catalog=CATALOG, id_source=FixedIdSource()
    )
    preference_store = AgentPresetPreferenceYamlStore(tmp_path / "data-root")
    yield SimpleNamespace(
        store=store,
        handle=handle,
        journal=journal,
        publication=publication,
        materialization=materialization,
        preference_store=preference_store,
        data_root=tmp_path / "data-root",
    )
    handle.close()


def definition_rows(journal):
    return journal._backend.transaction.require_executor() if False else None


def count_versions(journal):
    handle = journal
    rows = handle._backend.read_all(
        "SELECT COUNT(*) FROM agent_definition_versions WHERE workspace_id=?", ("ws_one",)
    )
    return rows[0][0]


def _tool_for(name):
    return make_tool(
        name=name,
        description=name,
        arguments_model=PathArgs,
        handler=_ok_handler,
        intent_resolver=_stub_intent(name),
    )


# Task 2.1: preset catalog ------------------------------------------------------


def test_preset_catalog_identity_and_fixed_tool_policy():
    sources = preset_sources()
    assert tuple(item.definition_id for item in sources) == PRESET_DEFINITION_IDS
    assert all(is_preset_definition_id(item.definition_id) for item in sources)
    by_id = {item.definition_id: item for item in sources}
    general = by_id["builtin_general"]
    assert general.access_mode_ceiling == "write"
    assert general.model_selection == "invoking_active"
    assert {item.name for item in general.tool_requirements if item.requirement != "forbidden"} >= {
        "read",
        "write",
        "edit",
        "bash",
        "grep",
    }
    for role in ("builtin_explore", "builtin_review"):
        read_only = by_id[role]
        assert read_only.access_mode_ceiling == "read"
        requirements = {item.name: item.requirement for item in read_only.tool_requirements}
        for name in ("bash", "write", "edit", "promote_sandbox_changes"):
            assert requirements[name] == "forbidden", role
        assert requirements["read"] == "required"
        assert not (set(requirements) - set(CATALOG.tool_access))


def test_preset_sources_are_stable_fixtures():
    first = preset_sources()
    second = preset_sources()
    assert [item.content_hash for item in first] == [item.content_hash for item in second]


# Task 2.2: materialization -----------------------------------------------------


def test_materialize_fresh_workspace_creates_enabled_versions(state):
    results = state.materialization.prepare(command_id="cmd_prepare")
    assert [item.status for item in results] == ["materialized"] * 3
    assert all(item.available for item in results)
    versions = state.journal.agent_definitions.list_versions("ws_one")
    assert {item.source.definition_id for item in versions} == set(PRESET_DEFINITION_IDS)
    for version in versions:
        assert version.origin == "builtin"
        head = state.journal.agent_definitions.get_head("ws_one", version.source.definition_id)
        assert head.enabled and head.version_id == version.version_id


def test_materialize_is_idempotent_with_same_identity(state):
    first = state.materialization.prepare(command_id="cmd_prepare")
    again = state.materialization.prepare(command_id="cmd_prepare_again")
    assert [item.version_id for item in first] == [item.version_id for item in again]
    assert [item.status for item in again] == ["current"] * 3
    assert count_versions(state.journal) == 3


def test_materialize_concurrent_first_use_converges_on_identity(state):
    # Two independent services over separate handles prepare the same catalog;
    # SQLite serializes the writers, and content-hash identity must converge
    # regardless of which command lands first. No timing assumptions.
    handle_two = state.store.open(StoreOpenMode.READ_WRITE)
    journal_two = SqliteOperationalJournal(handle_two, clock=FixedClock().now)
    other = AgentPresetMaterializationService(
        journal_two, workspace_id="ws_one", catalog=CATALOG, id_source=FixedIdSource()
    )
    results = []
    results.append(state.materialization.prepare(command_id="cmd_a"))
    results.append(other.prepare(command_id="cmd_b"))
    handle_two.close()
    assert [item.status for item in results[0]] == ["materialized"] * 3
    assert [item.status for item in results[1]] == ["current"] * 3
    assert [item.version_id for item in results[0]] == [item.version_id for item in results[1]]


def test_materialize_does_not_revive_disabled_head(state):
    first = state.materialization.prepare(command_id="cmd_prepare")
    state.publication.set_enabled("builtin_general", enabled=False, expected_head_revision=1)
    again = state.materialization.prepare(command_id="cmd_again")
    by_id = {item.definition_id: item for item in again}
    assert by_id["builtin_general"].status == "disabled"
    assert by_id["builtin_general"].available is False
    assert by_id["builtin_general"].version_id == first[0].version_id
    head = state.journal.agent_definitions.get_head("ws_one", "builtin_general")
    assert head.enabled is False


def test_materialize_does_not_revive_revoked_version(state):
    first = state.materialization.prepare(command_id="cmd_prepare")
    version = next(item for item in first if item.definition_id == "builtin_review")
    state.publication.revoke(version.version_id, reason="policy revoked", command_id="cmd_revoke")
    again = state.materialization.prepare(command_id="cmd_again")
    by_id = {item.definition_id: item for item in again}
    assert by_id["builtin_review"].status == "revoked"
    assert by_id["builtin_review"].available is False
    assert count_versions(state.journal) == 3


def test_preset_upgrade_creates_new_version_without_rewriting_history(state, monkeypatch):
    first = state.materialization.prepare(command_id="cmd_prepare")
    original = first[0]

    from morrow.core import agent_presets as preset_module

    def upgraded(role):
        source = preset_module.preset_source(role)
        if role == "general":
            source = source.model_copy(update={"description": source.description + " Upgraded."})
        return source

    monkeypatch.setattr("morrow.application.agent_definitions.presets.preset_source", upgraded)
    results = state.materialization.prepare(command_id="cmd_upgrade")
    assert results[0].status == "materialized"
    assert results[0].version_id != original.version_id
    assert results[1].status == "current"
    # History stays intact: the original version row still validates.
    old = state.journal.agent_definitions.get_version("ws_one", original.version_id)
    assert old.source.content_hash == original.content_hash
    assert count_versions(state.journal) == 4


def test_materialize_requires_explicit_command_and_queries_stay_read_only(state):
    state.materialization.prepare(command_id="cmd_prepare")
    before = count_versions(state.journal)
    # Query paths never materialize: listing views and loading sources write nothing.
    from morrow.application.workflows.queries import WorkflowQueryService

    queries = WorkflowQueryService(
        state.journal,
        workspace_id="ws_one",
        agent_sources=AgentDefinitionYamlStore(state.data_root),
    )
    queries.list_agent_definitions()
    queries.get_agent_definition("builtin_general")
    assert count_versions(state.journal) == before


def test_materialize_rejects_catalog_without_required_read_tool(tmp_path):
    store = OperationalStore(tmp_path / "state2", clock=FixedClock())
    handle = store.initialize()
    try:
        journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
        narrow = DefinitionCatalog(
            models=(MODEL,),
            skill_version_ids=frozenset(),
            tool_access={"write": "write"},
            allowed_tools=frozenset({"write"}),
        )
        materialization = AgentPresetMaterializationService(
            journal, workspace_id="ws_one", catalog=narrow, id_source=FixedIdSource()
        )
        with pytest.raises(AgentPresetMaterializationError):
            materialization.prepare(command_id="cmd_prepare")
    finally:
        handle.close()


# Task 2.3: preference overlay --------------------------------------------------


def test_preference_roundtrip_occ_and_validation(state):
    from morrow.application.agent_definitions.preferences import AgentPresetPreferenceService

    service = AgentPresetPreferenceService(
        state.preference_store,
        workspace_id="ws_one",
        validator=lambda model: (
            EFFORTS if model == MODEL else (_ for _ in ()).throw(ValueError("unknown model"))
        ),
    )
    document = service.document()
    assert document.revision == 0 and document.presets == ()
    preference = AgentPresetPreference(
        definition_id="builtin_general",
        model=MODEL,
        generation=ExplicitGenerationSelection(mode="explicit", value="high"),
    )
    written = service.set("builtin_general", preference, expected_revision=0)
    assert written.revision == 1
    assert service.preference_for("builtin_general") == preference
    from morrow.core.application import ApplicationError, ApplicationErrorCode

    with pytest.raises(ApplicationError) as conflict:
        service.set("builtin_general", preference, expected_revision=0)
    assert conflict.value.code is ApplicationErrorCode.CONFLICT
    # Unsupported effort is rejected at save time.
    with pytest.raises(Exception, match="思考参数"):
        service.set(
            "builtin_explore",
            AgentPresetPreference(
                definition_id="builtin_explore",
                model=MODEL,
                generation=ExplicitGenerationSelection(mode="explicit", value="max"),
            ),
            expected_revision=1,
        )
    # Unknown model is rejected.
    with pytest.raises(ValueError):
        service.set(
            "builtin_explore",
            AgentPresetPreference(
                definition_id="builtin_explore",
                model=OTHER,
                generation=None,
            ),
            expected_revision=1,
        )
    # Non-preset definitions cannot carry preferences.
    from pydantic import ValidationError as _VE

    with pytest.raises(_VE):
        AgentPresetPreference(definition_id="helper", model=MODEL)
    cleared = service.clear("builtin_general", expected_revision=1)
    assert cleared.revision == 2 and cleared.presets == ()


def test_quick_save_source_build_preserves_advanced_fields():
    catalog = CATALOG
    existing = AgentDefinitionSource(
        definition_id="code-helper",
        name="Code helper",
        description="old purpose",
        role_prompt="old prompt",
        skill_version_ids=("skv_1",),
        max_agent_generation_requests=7,
        model_selection=OTHER,
        derived_from_definition_id="builtin_coder",
        derived_from_source_hash="a" * 64,
    )
    fields = AgentQuickSaveFields(
        name="Code helper",
        purpose="new purpose",
        prompt="new prompt for the helper",
        tools=("read", "grep"),
        model=None,
        generation=ModelDefaultSelection(mode="model_default"),
    )
    source = build_quick_save_source(fields, existing=existing, catalog=catalog)
    assert source.definition_id == "code-helper"
    assert source.description == "new purpose"
    assert source.role_prompt == "new prompt for the helper"
    # Advanced fields survive the simplified edit losslessly.
    assert source.skill_version_ids == ("skv_1",)
    assert source.max_agent_generation_requests == 7
    assert source.derived_from_definition_id == "builtin_coder"
    assert source.derived_from_source_hash == "a" * 64
    # Selection fields follow the simple form.
    assert source.model_selection == "invoking_active"
    assert source.generation_selection == ModelDefaultSelection(mode="model_default")
    assert source.access_mode_ceiling == "read"
    requirements = {item.name: item.requirement for item in source.tool_requirements}
    assert requirements["read"] == "required" and requirements["grep"] == "required"
    assert requirements["bash"] == "forbidden" and requirements["write"] == "forbidden"


def test_quick_save_all_tools_and_auto_id():
    fields = AgentQuickSaveFields(name="Research Sketch", purpose="find things", prompt="find it")
    source = build_quick_save_source(fields, existing=None, catalog=CATALOG)
    assert source.definition_id == derive_definition_id("Research Sketch") == "research-sketch"
    assert source.access_mode_ceiling == "write"
    names = {item.name for item in source.tool_requirements}
    assert {"read", "grep", "write", "bash"} <= names
    assert all(item.requirement == "optional" for item in source.tool_requirements)


def test_quick_save_write_tool_selection_lifts_ceiling():
    fields = AgentQuickSaveFields(name="Fixer", prompt="fix it", tools=("read", "write"))
    source = build_quick_save_source(fields, existing=None, catalog=CATALOG)
    assert source.access_mode_ceiling == "write"


def test_quick_save_cjk_name_derives_stable_id():
    slug = derive_definition_id("代码审查员")
    assert slug == f"agent-{__import__('hashlib').sha256('代码审查员'.encode()).hexdigest()[:8]}"


def test_quick_save_numeric_name_derives_letter_prefixed_id():
    assert derive_definition_id("123_analyst") == "agent-123-analyst"
    assert derive_definition_id("2nd-reviewer") == "agent-2nd-reviewer"
    source = build_quick_save_source(
        AgentQuickSaveFields(name="123_analyst", purpose="analyze", prompt="inspect the report"),
        existing=None,
        catalog=CATALOG,
    )
    assert source.definition_id == "agent-123-analyst"


# Task 2.4: save and available --------------------------------------------------


def _management(state):
    from morrow.application.workflows.management import WorkflowManagementService

    return WorkflowManagementService(
        workspace_id="ws_one",
        agent_sources=AgentDefinitionYamlStore(state.data_root),
        workflow_sources=None,
        agent_publication=state.publication,
        workflow_publication=None,
        runtime=None,
        active_model=MODEL,
        agent_builtins=(),
        workflow_builtins=(),
    )


def _quick_source(state, name="Helper", **overrides):
    base = {
        "purpose": "help with tasks",
        "prompt": "Inspect password validation and authorization tests.",
        "tools": ("read", "grep"),
    }
    base.update(overrides)
    fields = AgentQuickSaveFields(name=name, **base)
    return build_quick_save_source(fields, existing=None, catalog=CATALOG)


def test_save_and_publish_returns_available_version(state):
    management = _management(state)
    source = _quick_source(state)
    result = management.save_and_publish_agent(
        source,
        expected_source_revision=0,
        expected_head_revision=0,
        command_id="cmd_save_1",
    )
    assert result.available_version_id == result.version.version_id
    # Admission proof: the exact version is enabled, unrevoked and resolvable.
    admitted = state.publication.admit(result.version.version_id)
    assert admitted.version_id == result.version.version_id
    # The desired YAML source exists for later edits.
    stored = management._desired_agent_source(source.definition_id)
    assert stored is not None and stored.content_hash == source.content_hash


def test_save_and_publish_replay_after_yaml_write_is_idempotent(state):
    management = _management(state)
    source = _quick_source(state)
    first = management.save_and_publish_agent(
        source,
        expected_source_revision=0,
        expected_head_revision=0,
        command_id="cmd_save_1",
    )
    # Crash before receipt, full command retried with the same baselines.
    second = management.save_and_publish_agent(
        source,
        expected_source_revision=1,
        expected_head_revision=1,
        command_id="cmd_save_1",
    )
    assert second.version.version_id == first.version.version_id


def test_save_and_publish_same_command_conflicting_payload_rejected(state):
    management = _management(state)
    source = _quick_source(state)
    management.save_and_publish_agent(
        source,
        expected_source_revision=0,
        expected_head_revision=0,
        command_id="cmd_save_1",
    )
    other = _quick_source(state, name="Helper", prompt="a different prompt entirely")
    with pytest.raises(ValueError, match="conflicts with prior receipt"):
        management.save_and_publish_agent(
            other,
            expected_source_revision=1,
            expected_head_revision=1,
            command_id="cmd_save_1",
        )


def test_restore_quick_save_rewrites_missing_yaml_without_republish(state):
    management = _management(state)
    source = _quick_source(state)
    result = management.save_and_publish_agent(
        source,
        expected_source_revision=0,
        expected_head_revision=0,
        command_id="cmd_save_1",
    )
    # Simulate the YAML loss after the receipt was durable.
    document = AgentDefinitionDocument()
    AgentDefinitionYamlStore(state.data_root).write("ws_one", document, expected_revision=1)
    restored = management.restore_quick_save_agent(source, version_id=result.available_version_id)
    assert restored.available_version_id == result.available_version_id
    assert restored.enabled is True
    stored = management._desired_agent_source(source.definition_id)
    assert stored is not None and stored.content_hash == source.content_hash


def test_quick_save_cannot_publish_over_builtin_identity(state):
    management = _management(state)
    source = AgentDefinitionSource(
        definition_id="builtin_general",
        name="Impostor",
        role_prompt="Take over the preset catalog.",
    )
    with pytest.raises(ValueError, match="read-only"):
        management.save_and_publish_agent(
            source,
            expected_source_revision=0,
            expected_head_revision=0,
            command_id="cmd_bad",
        )


# Task 2.5: selection resolver --------------------------------------------------


def test_resolver_model_and_generation_chains_are_independent():
    node_model = ExplicitModelSelection(mode="explicit", value=OTHER)
    resolved = resolve_selection(
        SelectionInputs(
            node_model=node_model,
            node_generation=ModelDefaultSelection(mode="model_default"),
            agent_model=MODEL,
            session_model=MODEL,
            session_generation=GenerationOptions(reasoning_effort="high"),
            adapter_default_model=MODEL,
        ),
        exact_reasoning_efforts=EFFORTS,
    )
    assert resolved.model == OTHER and resolved.model_source == "node"
    # Node model_default stops the session-snapshot effort independently.
    assert resolved.generation is None
    assert resolved.generation_source == "node"

    resolved = resolve_selection(
        SelectionInputs(
            agent_model=MODEL,
            session_model=OTHER,
            session_generation=GenerationOptions(reasoning_effort="low"),
        ),
        exact_reasoning_efforts=EFFORTS,
    )
    assert resolved.model == MODEL and resolved.model_source == "agent"
    assert resolved.generation == "low" and resolved.generation_source == "session_snapshot"

    resolved = resolve_selection(
        SelectionInputs(adapter_default_model=MODEL),
    )
    assert resolved.model == MODEL and resolved.model_source == "adapter_default"
    assert resolved.generation is None and resolved.generation_source == "adapter_default"


def test_resolver_agent_model_default_stops_before_adapter_inherit():
    # invoking_active keeps the chain open: the session snapshot applies.
    resolved = resolve_selection(
        SelectionInputs(
            agent_model="invoking_active",
            session_model=OTHER,
            session_generation=GenerationOptions(),
        ),
        exact_reasoning_efforts=EFFORTS,
    )
    assert resolved.model == OTHER and resolved.model_source == "session_snapshot"
    # An explicit session snapshot model_default choice is represented.
    assert resolved.generation is None
    assert resolved.generation_source == "session_snapshot"


def test_resolver_explicit_unsupported_effort_rejected():
    resolved = resolve_selection(
        SelectionInputs(
            agent_model=MODEL,
            agent_generation=ExplicitGenerationSelection(mode="explicit", value="xhigh"),
        ),
        exact_reasoning_efforts=EFFORTS,
    )
    assert resolved.status == "invalid"
    assert "generation_explicit_unsupported" in resolved.diagnostics


def test_resolver_inherit_unsupported_returns_needs_default():
    resolved = resolve_selection(
        SelectionInputs(
            session_model=MODEL,
            session_generation=GenerationOptions(reasoning_effort="xhigh"),
        ),
        exact_reasoning_efforts=EFFORTS,
    )
    assert resolved.status == "needs_default"
    assert resolved.generation is None
    assert resolved.generation_source == "adapter_default"
    assert any(item.startswith("generation_inherit_unsupported") for item in resolved.diagnostics)


def test_resolver_explicit_model_unavailable_is_invalid_not_replaced():
    resolved = resolve_selection(
        SelectionInputs(
            node_model=ExplicitModelSelection(mode="explicit", value=OTHER),
            agent_model=MODEL,
        ),
        exact_reasoning_efforts=EFFORTS,
        model_available=lambda model: model == MODEL,
    )
    assert resolved.status == "invalid"
    assert resolved.model is None
    assert "model_unavailable" in resolved.diagnostics


# Task 2.6: generation freeze through the factory -------------------------------


class RecordingPreparation:
    """Minimal preparation double capturing the frozen composition inputs."""

    prompt_assembler = None

    def __init__(self):
        self.calls = []
        self.runtime = SimpleNamespace(spec=None)

    def prepare_new(self, **kwargs):
        self.calls.append(kwargs)
        from morrow.application.agent_runs.preparation import PreparedAgentRunRuntime

        return PreparedAgentRunRuntime(
            spec=SimpleNamespace(model_copy=lambda update: SimpleNamespace(**{"_update": update})),
            provider=None,
            model=kwargs.get("model"),
            context_builder=None,
            tool_executor=None,
            run_policy=None,
        )


def _factory_for_version(state, version, *, preference=None, resolved_tools=None):
    from morrow.application.agent_definitions.factory import AgentFactory
    from morrow.runtime.session import Session

    preparation = RecordingPreparation()
    factory = AgentFactory(
        preparation,
        state.publication,
        version_id=version.version_id,
        session=Session("ses_leaf"),
        task_run_id="task_leaf",
        invoking_session_id="ses_root",
        preset_preference=preference,
        resolved_tool_requirements=resolved_tools,
    )
    return factory, preparation


def _factory(state, source, *, preference=None, resolved_tools=None):
    from morrow.core.domain import DurableSession, DurableTaskRun, TaskRunStatus

    state.journal.create_session(
        DurableSession(
            session_id="ses_leaf",
            workspace_id="ws_one",
            current_task_run_id="task_leaf",
        ),
        task=DurableTaskRun(
            task_run_id="task_leaf",
            session_id="ses_leaf",
            workspace_id="ws_one",
            status=TaskRunStatus.OPEN,
        ),
    )
    if source.definition_id in PRESET_DEFINITION_IDS:
        state.materialization.prepare(command_id="cmd_materialize")
        version = state.journal.agent_definitions.get_head("ws_one", source.definition_id)
        version = state.journal.agent_definitions.get_version("ws_one", version.version_id)
    else:
        version = state.publication.publish(
            source,
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_publish",
        )
    return (
        *_factory_for_version(state, version, preference=preference, resolved_tools=resolved_tools),
        version,
    )


def test_factory_freezes_definition_generation_into_preparation(state):
    source = AgentDefinitionSource(
        definition_id="effortful",
        name="Effortful",
        role_prompt="Inspect password validation and authorization tests.",
        model_selection=MODEL,
        generation_selection=ExplicitGenerationSelection(mode="explicit", value="high"),
    )
    factory, preparation, version = _factory(state, source)
    factory.prepare_new()
    assert len(preparation.calls) == 1
    call = preparation.calls[0]
    assert call["model"] == MODEL
    assert call["generation"] == GenerationOptions(reasoning_effort="high")
    assert call["settings_sources"]["generation"].scope == "explicit"


def test_factory_freezes_preset_preference_generation_with_workspace_marker(state):
    from morrow.core.agent_presets import preset_source

    source = preset_source("general")
    preference = LoadedPresetPreference(
        preference=AgentPresetPreference(
            definition_id="builtin_general",
            model=None,
            generation=ExplicitGenerationSelection(mode="explicit", value="low"),
        ),
        revision=3,
    )
    factory, preparation, version = _factory(state, source, preference=preference)
    factory.prepare_new()
    call = preparation.calls[0]
    assert call["generation"] == GenerationOptions(reasoning_effort="low")
    assert call["settings_sources"]["generation"].scope == "workspace"
    assert call["settings_sources"]["generation"].revision == 3


def test_factory_without_selection_uses_current_wire(state):
    source = AgentDefinitionSource(
        definition_id="plain",
        name="Plain",
        role_prompt="Inspect password validation and authorization tests.",
        model_selection=MODEL,
    )
    factory, preparation, _version = _factory(state, source)
    factory.prepare_new()
    call = preparation.calls[0]
    assert call["generation"] == GenerationOptions()
    assert call["settings_sources"] is None


def test_factory_preset_preference_model_applies_when_not_frozen(state):
    from morrow.core.agent_presets import preset_source

    source = preset_source("explore")
    preference = LoadedPresetPreference(
        preference=AgentPresetPreference(definition_id="builtin_explore", model=OTHER),
        revision=1,
    )
    factory, preparation, _version = _factory(state, source, preference=preference)
    factory.prepare_new()
    assert preparation.calls[0]["model"] == OTHER


# End-to-end: workflow leaves, presets and frozen request evidence (2.6 / 2.7)


def _tool_for(name):
    return make_tool(
        name=name,
        description=name,
        arguments_model=PathArgs,
        handler=_ok_handler,
        intent_resolver=_stub_intent(name),
    )


def _workflow_source(definition_id, node, *, required_slot="result", **node_changes):
    node = dict(node)
    node.update(node_changes)
    source = WorkflowDefinitionSource(
        workflow_definition_id=definition_id,
        name=definition_id,
        default_budget=BUDGET,
        nodes=(AgentNodeSource(**node),),
        edges=(),
        required_outputs=(NodeOutputRef(node_id=node["node_id"], output_slot=required_slot),),
    )
    return source


async def _run_single_node(fx, source, command_id):
    revision = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id=command_id,
        active_model=PIPELINE_MODEL,
    ).revision
    fx.bank.scripts.append(_script_submit_then_stop("call_1", "result", {"answer": "ok"}))
    started = _start_named(fx, source.workflow_definition_id, revision, "cmd_start")
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    return run, revision


async def test_workflow_leaf_freezes_definition_generation_into_requests_and_recovery(
    tmp_path,
):
    from morrow.core.execution_selections import ExplicitGenerationSelection

    fx = PipelineFixture(tmp_path)
    try:
        source = AgentDefinitionSource(
            definition_id="effortful",
            name="Effortful",
            role_prompt="Inspect password validation and report evidence.",
            access_mode_ceiling="read",
            tool_requirements=(ToolRequirement(name="read", requirement="required"),),
            model_selection=PIPELINE_MODEL,
            generation_selection=ExplicitGenerationSelection(mode="explicit", value="high"),
        )
        ref = _publish_agent(fx, source, "cmd_e1")
        wf = _workflow_source(
            "effortful_wf",
            {
                "node_id": "worker",
                "agent_definition_ref": ref,
                "task_contract": TaskContract(objective="Do the assigned work"),
                "input_bindings": (_task_binding(),),
                "output_contracts": (OutputContract(slot="result"),),
                "access_mode": "read",
            },
        )
        revision = fx.compiler.publish(
            wf,
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_publish_e1",
            active_model=PIPELINE_MODEL,
        ).revision
        fx.bank.scripts.append(_script_submit_then_stop("call_1", "result", {"answer": "ok"}))
        started = _start_named(fx, "effortful_wf", revision, "cmd_start_e1")
        # Crash after the admission request committed its tool-call message; the
        # open leaf is resumed from frozen evidence by the recovery path.
        fx.runtime.scheduler.faults = OnceFaultInjector(
            FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT
        )
        with pytest.raises(InjectedFault):
            await fx.runtime.scheduler.run(started.run.workflow_run_id)
        fx.runtime.scheduler.faults = None
        run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        # Every actual provider request of the leaf carries the frozen effort:
        # the admission request and the recovery-resumed request.
        assert [
            [item.reasoning_effort for item in provider.stream_generations]
            for provider in fx.bank.providers
        ] == [["high"], ["high"]]
        node = next(n for n in fx.journal.workflows.list_nodes(WS, run.workflow_run_id))
        agent_run = fx.journal.get_agent_run(WS, node.agent_run_id)
        snapshot = agent_run.snapshot
        frozen = snapshot.provider_runtime
        assert frozen.generation.reasoning_effort == "high"
        assert frozen.settings_sources["generation"].scope == "explicit"
    finally:
        fx.close()


async def test_preset_preference_overlay_reaches_leaf_requests(tmp_path):
    from morrow.application.agent_definitions.preferences import AgentPresetPreferenceService
    from morrow.application.agent_definitions.presets import (
        AgentPresetMaterializationService,
    )
    from morrow.core.agent_presets import (
        AgentPresetPreference,
        LoadedPresetPreference,
    )
    from morrow.core.agent_runs import AgentDefinitionRef
    from morrow.core.execution_selections import ExplicitGenerationSelection

    fx = PipelineFixture(tmp_path)
    try:
        materialization = AgentPresetMaterializationService(
            fx.journal, workspace_id=WS, catalog=PIPELINE_CATALOG, id_source=fx.ids
        )
        prepared = materialization.prepare(command_id="cmd_materialize")
        general = next(item for item in prepared if item.definition_id == "builtin_general")
        head = fx.journal.agent_definitions.get_head(WS, "builtin_general")
        ref = AgentDefinitionRef(
            definition_id="builtin_general",
            version_id=head.version_id,
            content_hash=head.source_hash,
        )
        wf = _workflow_source(
            "preset_wf",
            {
                "node_id": "worker",
                "agent_definition_ref": ref,
                "task_contract": TaskContract(objective="Do the assigned work"),
                "input_bindings": (_task_binding(),),
                "output_contracts": (OutputContract(slot="result"),),
                "access_mode": "write",
            },
        )
        revision = fx.compiler.publish(
            wf,
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_publish_preset",
            active_model=PIPELINE_MODEL,
        ).revision
        # The fixed preset source has no explicit generation; the overlay owns it.
        assert revision.nodes[0].agent_definition_ref.version_id == general.version_id

        preference_store = AgentPresetPreferenceYamlStore(tmp_path / "data-root")
        preferences = AgentPresetPreferenceService(
            preference_store,
            workspace_id=WS,
            validator=lambda model: ("minimal", "low", "medium", "high"),
        )
        preferences.set(
            "builtin_general",
            AgentPresetPreference(
                definition_id="builtin_general",
                model=None,
                generation=ExplicitGenerationSelection(mode="explicit", value="low"),
            ),
            expected_revision=0,
        )

        def loader(definition_id):
            preference = preferences.preference_for(definition_id)
            if preference is None:
                return None
            return LoadedPresetPreference(preference, preferences.document().revision)

        fx.runtime = build_workflow_runtime(
            fx.journal,
            fx.handle,
            workspace_id=WS,
            artifacts=fx.artifacts,
            agent_publication=fx.agents,
            preparation=fx.preparation,
            id_source=fx.ids,
            runtime_instance_id="inst-test-pref",
            clock=fx.clock.now,
            agent_preference_loader=loader,
        )
        fx.bank.scripts.append(_script_submit_then_stop("call_2", "result", {"answer": "ok"}))
        started = _start_named(fx, "preset_wf", revision, "cmd_start_preset")
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        provider = fx.bank.providers[-1]
        assert provider.stream_generations == [
            GenerationOptions(reasoning_effort="low"),
            GenerationOptions(reasoning_effort="low"),
        ]
    finally:
        fx.close()


async def test_general_preset_node_expands_all_approved_tools_and_nodes_only_narrow(
    tmp_path,
):
    from morrow.core.agent_runs import AgentDefinitionRef

    fx = PipelineFixture(tmp_path)
    try:
        materialization = AgentPresetMaterializationService(
            fx.journal, workspace_id=WS, catalog=PIPELINE_CATALOG, id_source=fx.ids
        )
        materialization.prepare(command_id="cmd_materialize")
        head = fx.journal.agent_definitions.get_head(WS, "builtin_general")
        ref = AgentDefinitionRef(
            definition_id="builtin_general",
            version_id=head.version_id,
            content_hash=head.source_hash,
        )
        # Node overlay narrows the preset superset: bash is forbidden for this node.
        wf = _workflow_source(
            "general_narrow",
            {
                "node_id": "worker",
                "agent_definition_ref": ref,
                "task_contract": TaskContract(objective="Do the assigned work"),
                "input_bindings": (_task_binding(),),
                "output_contracts": (OutputContract(slot="result"),),
                "access_mode": "write",
                "tool_requirements": (ToolRequirement(name="bash", requirement="forbidden"),),
            },
        )
        revision = fx.compiler.publish(
            wf,
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_publish_narrow",
            active_model=PIPELINE_MODEL,
        ).revision
        frozen = {
            item.name: item.requirement
            for item in revision.nodes[0].resolved_tool_requirements
            if item.requirement != "forbidden"
        }
        # "All available" expanded to the task-approved execution set...
        assert {"read", "grep", "write", "edit"} <= set(frozen)
        # ...and the node narrowing is frozen in evidence.
        assert frozen.get("bash") is None
        assert "bash" in {
            item.name
            for item in revision.nodes[0].resolved_tool_requirements
            if item.requirement == "forbidden"
        }
        fx.bank.scripts.append(_script_submit_then_stop("call_3", "result", {"answer": "ok"}))
        run = await fx.runtime.scheduler.run(
            _start_named(fx, "general_narrow", revision, "cmd_start_narrow").run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        offered = {tool.function.name for tool in fx.bank.providers[-1].stream_tools[0]}
        assert "read" in offered and "write" in offered and "bash" not in offered
    finally:
        fx.close()


async def test_unknown_effect_tool_cannot_gain_read_qualification(tmp_path):
    from pydantic import BaseModel as _BaseModel

    from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool

    fx = PipelineFixture(tmp_path)
    try:
        registry = ToolRegistry()
        for name in (
            "read",
            "ls",
            "find",
            "grep",
            "write",
            "edit",
            "bash",
            "promote_sandbox_changes",
        ):
            registry.register(_tool_for(name))

        class _Args(_BaseModel):
            pass

        async def _handler(_arguments):
            return "ok"

        def _unknown_intent(_arguments, _context):
            from morrow.core.capabilities import OperationIntent, OperationKind

            return OperationIntent(kind=OperationKind.WORKSPACE_READ)

        # A dynamic MCP-style tool with NO static contract: its effect cannot
        # be proven read-only, whatever its intent claims.
        registry.register(
            make_tool(
                name="mcp_dynamic_lookup",
                description="dynamic MCP tool",
                arguments_model=_Args,
                handler=_handler,
                intent_resolver=_unknown_intent,
            )
        )
        fx.preparation.tool_factory = lambda policy: ToolExecutor(registry.snapshot(), policy)

        read_ceiling = AgentDefinitionSource(
            definition_id="inspector",
            name="Inspector",
            role_prompt="Inspect the workspace and report evidence only.",
            access_mode_ceiling="read",
            tool_requirements=(
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="mcp_dynamic_lookup", requirement="optional"),
            ),
            model_selection=PIPELINE_MODEL,
        )
        ref = _publish_agent(fx, read_ceiling, "cmd_inspector")
        wf = _workflow_source(
            "inspector_wf",
            {
                "node_id": "worker",
                "agent_definition_ref": ref,
                "task_contract": TaskContract(objective="Inspect"),
                "input_bindings": (_task_binding(),),
                "output_contracts": (OutputContract(slot="result"),),
                "access_mode": "read",
            },
        )
        fx.bank.scripts.append(_script_submit_then_stop("call_4", "result", {"answer": "ok"}))
        run, _revision = await _run_single_node(fx, wf, "cmd_publish_inspector")
        assert run.status is WorkflowStatus.COMPLETED
        offered = {tool.function.name for tool in fx.bank.providers[-1].stream_tools[0]}
        assert "read" in offered
        assert "mcp_dynamic_lookup" not in offered

        # A definition REQUIRING a tool whose effect cannot be proven is not
        # admissible at all: publication rejects it up front (the "提示修正"
        # contract), instead of letting a read-only agent run with an unproven
        # tool or silently dropping a declared dependency.
        strict = AgentDefinitionSource(
            definition_id="strict_inspector",
            name="Strict Inspector",
            role_prompt="Inspect the workspace and report evidence only.",
            access_mode_ceiling="read",
            tool_requirements=(
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="mcp_dynamic_lookup", requirement="required"),
            ),
            model_selection=PIPELINE_MODEL,
        )
        with pytest.raises(ValueError, match="required tool"):
            _publish_agent(fx, strict, "cmd_strict")
    finally:
        fx.close()


async def test_new_tools_do_not_inject_into_admitted_snapshot_and_revocation_blocks(
    tmp_path,
):
    from morrow.runtime.tools import ToolExecutor, ToolRegistry

    fx = PipelineFixture(tmp_path)
    try:
        source = AgentDefinitionSource(
            definition_id="patient",
            name="Patient",
            role_prompt="Inspect password validation and report evidence.",
            access_mode_ceiling="read",
            tool_requirements=(ToolRequirement(name="read", requirement="required"),),
            model_selection=PIPELINE_MODEL,
        )
        ref = _publish_agent(fx, source, "cmd_patient")
        wf = _workflow_source(
            "patient_wf",
            {
                "node_id": "worker",
                "agent_definition_ref": ref,
                "task_contract": TaskContract(objective="Do the assigned work"),
                "input_bindings": (_task_binding(),),
                "output_contracts": (OutputContract(slot="result"),),
                "access_mode": "read",
            },
        )
        revision = fx.compiler.publish(
            wf,
            source_revision=0,
            expected_head_revision=0,
            command_id="cmd_publish_patient",
            active_model=PIPELINE_MODEL,
        ).revision
        fx.bank.scripts.append(_script_submit_then_stop("call_1", "result", {"answer": "ok"}))
        started = _start_named(fx, "patient_wf", revision, "cmd_start_patient")
        fx.runtime.scheduler.faults = OnceFaultInjector(
            FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT
        )
        with pytest.raises(InjectedFault):
            await fx.runtime.scheduler.run(started.run.workflow_run_id)
        fx.runtime.scheduler.faults = None

        # A tool registered after admission is not part of frozen evidence.
        wide_registry = ToolRegistry()
        for name in (
            "read",
            "ls",
            "find",
            "grep",
            "write",
            "edit",
            "bash",
            "promote_sandbox_changes",
        ):
            wide_registry.register(_tool_for(name))
        wide_registry.register(_tool_for("mcp_newly_enabled"))
        fx.preparation.tool_factory = lambda policy: ToolExecutor(wide_registry.snapshot(), policy)
        run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        node = next(n for n in fx.journal.workflows.list_nodes(WS, run.workflow_run_id))
        agent_run = fx.journal.get_agent_run(WS, node.agent_run_id)
        snapshot = agent_run.snapshot
        offered = {tool.function.name for tool in fx.bank.providers[-1].stream_tools[0]}
        assert "mcp_newly_enabled" not in offered
        assert "read" in offered

        # The frozen digest still rejects a drifted executor on recovery paths.
        from morrow.application.prompt import DirectCodingPromptAssembler

        wide = ToolExecutor(wide_registry.snapshot(), snapshot.run_policy)
        with pytest.raises(AgentRunPreparationError, match="tool schema drifted"):
            fx.preparation.rehydrate(
                snapshot,
                prompt_assembler=DirectCodingPromptAssembler(),
                tool_transform=lambda _executor: wide,
            )
        # Revocation is still enforced on recovery paths: a second admitted run
        # crashes, the frozen version is revoked, and recovery refuses to resume
        # it through the published-factory gate before any further request.
        from morrow.core.domain import DurableTaskRunTransition, TaskRunStatus

        root_task = fx.journal.get_task_run(WS, "task_root")
        fx.journal.transition_task_run(
            WS,
            "task_root",
            target=TaskRunStatus.OPEN,
            transition=DurableTaskRunTransition(
                transition_id="ttr_reopen",
                workspace_id=WS,
                session_id="ses_root",
                task_run_id="task_root",
                from_status=root_task.status,
                to_status=TaskRunStatus.OPEN,
                reason="test_reopen",
                created_at=fx.clock.now(),
            ),
            expected_row_version=root_task.row_version,
        )
        fx.bank.scripts.append(_script_submit_then_stop("call_1", "result", {"answer": "ok"}))
        started_two = _start_named(fx, "patient_wf", revision, "cmd_start_patient_two")
        fx.runtime.scheduler.faults = OnceFaultInjector(
            FaultPoint.CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT
        )
        with pytest.raises(InjectedFault):
            await fx.runtime.scheduler.run(started_two.run.workflow_run_id)
        fx.runtime.scheduler.faults = None
        fx.agents.revoke(ref.version_id, reason="policy revoked", command_id="cmd_revoke_patient")
        run_two = await fx.runtime.scheduler.recover(started_two.run.workflow_run_id)
        assert run_two.status is WorkflowStatus.CANCELLED
    finally:
        fx.close()


# Task 2.8: catalog pagination and controlled diagnostics ------------------------


def test_agent_definition_catalog_is_paginated(state):
    from morrow.application.workflows.queries import WorkflowQueryService

    state.materialization.prepare(command_id="cmd_prepare")
    queries = WorkflowQueryService(
        state.journal,
        workspace_id="ws_one",
        agent_sources=AgentDefinitionYamlStore(state.data_root),
    )
    first = queries.list_agent_definitions(limit=2)
    assert len(first) == 2
    assert [item.definition_id for item in first] == sorted(item.definition_id for item in first)
    second = queries.list_agent_definitions(limit=2, after=first[-1].definition_id)
    assert second
    assert not {item.definition_id for item in first} & {item.definition_id for item in second}
    joined = [item.definition_id for item in first] + [item.definition_id for item in second]
    assert joined == sorted(
        item.definition_id for item in queries.list_agent_definitions(limit=100)
    )


def test_resolution_summary_is_bounded_and_carries_diagnostics(tmp_path):
    from morrow.application.agent_definitions.preferences import (
        AgentPresetPreferenceService,
    )
    from morrow.core.agent_presets import preset_source

    service = AgentPresetPreferenceService(
        AgentPresetPreferenceYamlStore(tmp_path / "data-root"),
        workspace_id="ws_one",
        validator=lambda model: ("minimal", "low", "medium", "high"),
    )
    source = preset_source("explore")
    resolved = service.resolve_for_source(
        source,
        session_model=MODEL,
        session_generation=GenerationOptions(reasoning_effort="max"),
        adapter_default_model=MODEL,
    )
    summary = resolved.summary()
    assert summary["model"] == MODEL.model_dump(mode="json")
    assert summary["model_source"] == "session_snapshot"
    # The inherited session effort is not expressible by the exact Model: the
    # diagnostic surfaces the decision instead of silently dropping it.
    assert summary["status"] == "needs_default"
    assert any(item.startswith("generation_inherit_unsupported") for item in summary["diagnostics"])


def test_definition_content_hash_includes_current_optional_fields():
    from morrow.core.domain import canonical_json_bytes, sha256_digest

    source = AgentDefinitionSource(
        definition_id="current-helper",
        name="Current helper",
        role_prompt="Do the assigned work.",
        model_selection=MODEL,
    )
    assert source.content_hash == sha256_digest(
        canonical_json_bytes(source.model_dump(mode="json"))
    )
