"""Stage 7 Subplan 1: durable publication, static restrictions and isolated leaves."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from morrow.adapters.state.definition_yaml import AgentDefinitionYamlStore
from morrow.adapters.state.extension_yaml import ExtensionYamlConflict, ExtensionYamlError
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.agent_definitions.errors import AgentDefinitionAdmissionError
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
    validate_definition,
)
from morrow.application.backup import BackupBundleError, OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.core.agent_definitions import (
    AgentDefinitionDocument,
    AgentDefinitionSource,
    ToolRequirement,
)
from morrow.core.domain import DurableSession
from morrow.core.models import ModelRef
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode
from morrow.testing import FixedClock, FixedIdSource

MODEL = ModelRef(provider_id="fake-provider", model_id="m1")
OTHER = ModelRef(provider_id="fake-provider", model_id="m2")
CATALOG = DefinitionCatalog(
    models=(MODEL, OTHER),
    skill_version_ids=frozenset(),
    tool_access={"read": "read", "write": "write"},
    allowed_tools=frozenset({"read", "write"}),
)


def source(**kwargs):
    return AgentDefinitionSource(
        definition_id="helper",
        name="Helper",
        role_prompt="Inspect password validation and authorization tests.",
        **kwargs,
    )


@pytest.fixture
def state(tmp_path):
    store = OperationalStore(tmp_path / "state", clock=FixedClock())
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle, clock=FixedClock().now)
    journal.create_session(DurableSession(session_id="ses_invoking", workspace_id="ws_one"))
    publication = AgentDefinitionPublicationService(
        journal, workspace_id="ws_one", catalog=CATALOG, id_source=FixedIdSource()
    )
    yield store, handle, journal, publication
    handle.close()


def publish(service, value=None, *, revision=0, head=0, command="cmd_publish", **kwargs):
    return service.publish(
        value or source(),
        source_revision=revision,
        expected_head_revision=head,
        command_id=command,
        **kwargs,
    )


def test_strict_source_and_value_sensitive_safety():
    assert source().role_prompt.startswith("Inspect password")
    assert source(max_agent_generation_requests=1).max_agent_generation_requests == 1
    for field in (
        {"enabled": False},
        {"source_hash": "x"},
        {"max_agent_generation_requests": 0},
        {"model_selection": "fallback"},
        {"max_agent_generation_requests": True},
    ):
        with pytest.raises(ValidationError):
            source(**field)
    for text in ("sk-" + "x" * 24, "password = actual-secret-value"):
        with pytest.raises(ValidationError):
            AgentDefinitionSource(definition_id="unsafe", name="Unsafe", role_prompt=text)
    with pytest.raises(ValidationError):
        source(
            tool_requirements=(
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="read", requirement="forbidden"),
            )
        )
    assert (
        validate_definition(
            source(tool_requirements=(ToolRequirement(name="read", requirement="forbidden"),)),
            CATALOG,
        ).tool_names
        == ()
    )


@pytest.mark.parametrize(
    "name,ceiling,catalog",
    [
        ("absent", "read", CATALOG),
        ("write", "read", CATALOG),
        ("read", "read", replace(CATALOG, allowed_tools=frozenset())),
    ],
)
def test_required_rejected_and_optional_removed(name, ceiling, catalog):
    with pytest.raises(ValueError, match="required tool"):
        validate_definition(
            source(
                access_mode_ceiling=ceiling,
                tool_requirements=(ToolRequirement(name=name, requirement="required"),),
            ),
            catalog,
        )
    result = validate_definition(
        source(
            access_mode_ceiling=ceiling,
            tool_requirements=(ToolRequirement(name=name, requirement="optional"),),
        ),
        catalog,
    )
    assert result.tool_names == ()
    assert result.diagnostics == (f"optional_removed:{name}",)
    assert validate_definition(
        source(
            access_mode_ceiling="write",
            tool_requirements=(ToolRequirement(name="write", requirement="required"),),
        ),
        CATALOG,
    ).tool_names == ("write",)


def test_yaml_occ_hash_and_pure_validation(state, monkeypatch):
    store, handle, journal, service = state
    yaml = AgentDefinitionYamlStore(store.layout.data_root)
    assert yaml.load("ws_one").revision == 0
    assert not yaml.workspace_path("ws_one").exists()
    doc = yaml.write(
        "ws_one", AgentDefinitionDocument(definitions=(source(),)), expected_revision=0
    )
    loaded = yaml.load_definition("ws_one", "helper")
    first = publish(service, loaded.source, revision=loaded.source_revision)
    other = source().model_copy(update={"definition_id": "other"})
    yaml.write(
        "ws_one",
        AgentDefinitionDocument(definitions=(source(), other)),
        expected_revision=doc.revision,
    )
    assert yaml.load_definition("ws_one", "helper").source_hash == first.content_hash
    assert journal.agent_definitions.get_head("ws_one", "helper").source_revision == 1
    with pytest.raises(ExtensionYamlConflict):
        yaml.write("ws_one", doc, expected_revision=0)
    with monkeypatch.context() as guard:
        guard.setattr(journal, "transact", lambda *_: pytest.fail("validation wrote"))
        guard.setattr(
            service.id_source, "new_id", lambda *_: pytest.fail("validation allocated identity")
        )
        assert service.validate(loaded.source).source == loaded.source
    path = yaml.workspace_path("ws_one")
    path.write_text("definitions: [unterminated")
    with pytest.raises(ExtensionYamlError):
        yaml.load("ws_one")
    assert journal.agent_definitions.get_version("ws_one", first.version_id) == first


def test_publication_occ_immutability_gate_and_revocation(state):
    _, handle, journal, service = state
    first = publish(service)
    assert publish(service) == first
    assert publish(service, head=1, command="cmd_noop") == first
    head = service.set_enabled("helper", enabled=False, expected_head_revision=1)
    assert head.row_version == 2
    assert len(journal.agent_definitions.list_versions("ws_one")) == 1
    with pytest.raises(ValueError, match="revision conflict"):
        publish(service, head=1, command="cmd_stale")
    with pytest.raises(AgentDefinitionAdmissionError, match="disabled"):
        service.admit(first.version_id)
    assert service.require_unrevoked(first) == first
    edited = source().model_copy(update={"role_prompt": "Inspect a new task."})
    second = publish(service, edited, head=2, revision=2, command="cmd_next")
    assert not journal.agent_definitions.get_head("ws_one", "helper").enabled
    assert journal.agent_definitions.get_version("ws_one", first.version_id) == first
    service.set_enabled("helper", enabled=True, expected_head_revision=3)
    assert service.admit(first.version_id) == first
    revocation = service.revoke(first.version_id, reason="policy changed", command_id="cmd_revoke")
    assert revocation.created_at == FixedClock().now()
    assert (
        service.revoke(first.version_id, reason="policy changed", command_id="cmd_revoke")
        == revocation
    )
    with pytest.raises(ValueError, match="one-way"):
        service.revoke(first.version_id, reason="undo", command_id="cmd_replace")
    with pytest.raises(AgentDefinitionAdmissionError, match="policy_revoked"):
        service.admit(first.version_id)
    with pytest.raises(AgentDefinitionAdmissionError, match="policy_revoked"):
        publish(service)
    assert service.admit(second.version_id) == second
    with pytest.raises(StorageError):
        handle.run_write(lambda ex: ex.execute("DELETE FROM agent_definition_revocations"))
    with pytest.raises(StorageError):
        handle.run_write(
            lambda ex: ex.execute(
                "UPDATE agent_definition_versions SET content_hash=?", ("a" * 64,)
            )
        )


@pytest.mark.parametrize(
    "draft", [b"definitions: [broken\npassword validation", b"authorization\npassword\n[bad", None]
)
def test_definition_backup_restore_and_doctor_raw_draft(state, tmp_path, draft):
    store, _, journal, service = state
    first = publish(service)
    yaml = AgentDefinitionYamlStore(store.layout.data_root)
    yaml.write("ws_one", AgentDefinitionDocument(definitions=(source(),)), expected_revision=0)
    if draft is not None:
        yaml.workspace_path("ws_one").write_bytes(draft)
    else:
        edited = source().model_copy(update={"description": "desired ahead"})
        yaml.write("ws_one", AgentDefinitionDocument(definitions=(edited,)), expected_revision=1)
    raw = yaml.workspace_path("ws_one").read_bytes()
    report = OperationalDoctor(store).inspect("ws_one")
    assert report.health.value == "ok"
    assert any(i.code == "agent_definition_source_invalid" for i in report.issues) == (
        draft is not None
    )
    backup = OperationalBackupService(store, journal=journal)
    bundle_report = backup.create("definition-roundtrip")
    bundle = store.layout.backups_dir / bundle_report.bundle_name
    assert backup.verify(bundle).ok
    target = tmp_path / "restored"
    assert backup.restore(bundle, target).ok
    assert AgentDefinitionYamlStore(target).workspace_path("ws_one").read_bytes() == raw
    restored_handle = OperationalStore(target).open(StoreOpenMode.READ_ONLY)
    try:
        assert (
            SqliteOperationalJournal(restored_handle).agent_definitions.get_version(
                "ws_one", first.version_id
            )
            == first
        )
    finally:
        restored_handle.close()


def test_raw_backup_refuses_real_token_without_breaking_published_head(state):
    store, _, journal, service = state
    first = publish(service)
    path = AgentDefinitionYamlStore(store.layout.data_root).workspace_path("ws_one")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("broken [" + "sk-" + "x" * 24)
    with pytest.raises(BackupBundleError):
        OperationalBackupService(store, journal=journal).create("unsafe")
    assert service.admit(first.version_id) == first


def prepared_fixture(tmp_path, state, *, exact=False, tools=True, cap=None):
    from pydantic import BaseModel

    from morrow.application.agent_definitions.factory import AgentFactory
    from morrow.application.prompt import DirectCodingPromptAssembler
    from morrow.core.agent_runs import ProviderCapabilities
    from morrow.core.domain import DurableTaskRun
    from morrow.core.models import ProviderModelConfig
    from morrow.runtime.session import Session
    from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
    from test_agent_run_preparation import (
        _app,
        _configure_active,
        _preparation,
        _register_fake_adapter,
    )

    app = _app(tmp_path)
    constructions = []
    _register_fake_adapter(
        app,
        constructions=constructions,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function",
            multiple_tool_calls=True,
        ),
    )
    _configure_active(app)
    app.global_store.update(
        lambda config: config.model_copy(
            update={
                "providers": {
                    "fake-provider": config.providers["fake-provider"].model_copy(
                        update={
                            "models": {
                                "m1": ProviderModelConfig(api_model_id="api-m1"),
                                "m2": ProviderModelConfig(api_model_id="api-m2"),
                            },
                        }
                    )
                },
                "active_model": OTHER,
            }
        )
    )

    class Args(BaseModel):
        path: str

    async def handler(arguments):
        return "safe"

    registry = ToolRegistry()
    if tools:
        registry.register(
            make_tool(name="read", description="Read", arguments_model=Args, handler=handler)
        )
        registry.register(
            make_tool(name="write", description="Write", arguments_model=Args, handler=handler)
        )
    preparation = _preparation(
        app,
        constructions=constructions,
        tool_factory=lambda policy: ToolExecutor(registry.snapshot(), policy),
    )
    preparation.prompt_assembler = DirectCodingPromptAssembler()
    _, _, journal, service = state
    journal.create_session(
        DurableSession(session_id="ses_leaf", workspace_id="ws_one"),
        task=DurableTaskRun(task_run_id="task_leaf", session_id="ses_leaf", workspace_id="ws_one"),
    )
    session = Session(session_id="ses_leaf")
    value = source(
        model_selection=MODEL if exact else "invoking_active",
        max_agent_generation_requests=cap,
        tool_requirements=(
            ToolRequirement(name="read", requirement="required"),
            ToolRequirement(name="write", requirement="forbidden"),
        ),
    )
    version = publish(service, value)
    factory = AgentFactory(
        preparation,
        service,
        version_id=version.version_id,
        session=session,
        task_run_id="task_leaf",
        invoking_session_id="ses_invoking",
    )
    return app, journal, service, version, factory


def freeze(runtime, session):
    from morrow.application.turn_lifecycle import build_agent_run_snapshot

    return build_agent_run_snapshot(
        session,
        model=runtime.model,
        run_policy=runtime.run_policy,
        tools=runtime.tool_executor.definitions,
        runtime_instance_id="runtime_test",
        prepared_spec=runtime.spec,
    )


@pytest.mark.parametrize("exact", [False, True])
def test_factory_exact_model_tools_role_and_frozen_recovery(tmp_path, state, exact):
    app, journal, service, version, factory = prepared_fixture(tmp_path, state, exact=exact)
    runtime = factory.prepare_new(agent_run_id="arun_leaf")
    assert runtime.model == (MODEL if exact else OTHER)
    assert [t.function.name for t in runtime.tool_executor.definitions] == ["read"]
    assert runtime.context_builder.prompt_assembler.role_prompt == version.source.role_prompt
    snapshot = freeze(runtime, factory.session)
    assert snapshot.definition_ref.version_id == version.version_id
    assert snapshot.conversation_session_id == "ses_leaf"
    service.set_enabled("helper", enabled=False, expected_head_revision=1)
    app.global_store.update(lambda config: config.model_copy(update={"active_model": MODEL}))
    # Current desired state is irrelevant to recovery; the old model stays frozen.
    restored = factory.rehydrate(snapshot, agent_run_id="arun_leaf")
    assert restored.model == runtime.model
    assert restored.spec.definition_ref == runtime.spec.definition_ref
    with pytest.raises(AgentDefinitionAdmissionError, match="disabled"):
        factory.prepare_new(agent_run_id="arun_again")
    service.revoke(version.version_id, reason="policy changed", command_id="cmd_revoke")
    with pytest.raises(AgentDefinitionAdmissionError, match="policy_revoked"):
        factory.rehydrate(snapshot, agent_run_id="arun_leaf")
    assert journal.agent_definitions.get_version("ws_one", version.version_id) == version


def test_factory_required_backend_unavailable_is_preparation_local(tmp_path, state):
    _, _, service, version, factory = prepared_fixture(tmp_path, state, tools=False)
    assert service.validate(version.source).tool_names == ("read",)
    with pytest.raises(AgentDefinitionAdmissionError, match="required tool backend"):
        factory.prepare_new(agent_run_id="arun_leaf")
    assert service.admit(version.version_id) == version


def test_factory_requires_distinct_empty_standalone_scope(tmp_path, state):
    _, _, _, _, factory = prepared_fixture(tmp_path, state)
    factory.invoking_session_id = "ses_leaf"
    with pytest.raises(AgentDefinitionAdmissionError, match="distinct"):
        factory.prepare_new(agent_run_id="arun_leaf")
    factory.invoking_session_id = "ses_invoking"
    factory.task_run_id = "task_wrong"
    with pytest.raises(AgentDefinitionAdmissionError, match="pair"):
        factory.prepare_new(agent_run_id="arun_leaf")
    factory.task_run_id = "task_leaf"
    from morrow.core.models import UserMessage

    factory.session.begin_user_turn(UserMessage(content="parent content"))
    with pytest.raises(AgentDefinitionAdmissionError, match="empty"):
        factory.prepare_new(agent_run_id="arun_leaf")


@pytest.mark.parametrize("failed_request", [False, True])
def test_primary_request_cap_is_durable_and_replay_is_free(tmp_path, state, failed_request):
    from morrow.core.domain import DurableAgentRun, DurableTurn

    _, journal, _, _, factory = prepared_fixture(tmp_path, state, cap=1)
    runtime = factory.prepare_new(agent_run_id="arun_leaf")
    journal.create_turn(
        "ws_one",
        DurableTurn(
            turn_id="turn_leaf",
            session_id="ses_leaf",
            task_run_id="task_leaf",
            client_message_id="input",
        ),
    )
    journal.create_agent_run(
        "ws_one",
        DurableAgentRun(
            agent_run_id="arun_leaf",
            turn_id="turn_leaf",
            session_id="ses_leaf",
            snapshot=freeze(runtime, factory.session),
        ),
    )
    args = dict(
        agent_run_id="arun_leaf",
        attempt_ordinal=1,
        estimated_request_chars=10,
        request_char_budget=100,
        model_request_id="mreq_first",
    )
    first = journal.admit_model_request("ws_one", **args)
    assert journal.admit_model_request("ws_one", **args) == first
    if failed_request:
        journal.settle_model_request(
            "ws_one", first.model_request_id, state="failed", error_code="network"
        )
    with pytest.raises(StorageError, match="budget_exhausted") as error:
        journal.admit_model_request(
            "ws_one", **{**args, "attempt_ordinal": 2, "model_request_id": "mreq_next"}
        )
    assert error.value.code is StorageErrorCode.BUDGET_EXHAUSTED


def test_builtin_sources_are_visible_but_never_implicitly_published(state):
    from morrow.application.agent_definitions.builtins import builtin_definitions

    _, _, journal, service = state
    direct, explorer, coder, reviewer, *_ = builtin_definitions(MODEL)
    assert direct.model_selection == MODEL
    assert explorer.model_selection == "invoking_active"
    assert coder.access_mode_ceiling == "write"
    assert reviewer.access_mode_ceiling == "read"
    assert journal.agent_definitions.list_versions("ws_one") == ()
    service.validate(explorer)
    assert journal.agent_definitions.list_versions("ws_one") == ()
    with pytest.raises(ValidationError, match="built-ins"):
        AgentDefinitionDocument(definitions=(explorer,))
    with pytest.raises(ValueError, match="read-only"):
        publish(service, explorer.model_copy(update={"role_prompt": "modified"}), origin="builtin")
    value = publish(service, explorer, origin="builtin")
    assert service.admit(value.version_id) == value


@pytest.mark.asyncio
async def test_factory_leaf_runs_through_ordinary_loop_without_parent_history(tmp_path):
    from morrow.application.agent_definitions.factory import AgentFactory
    from morrow.bootstrap import build_session_application
    from morrow.core.agent_runs import ProviderCapabilities
    from morrow.core.domain import DurableTaskRun
    from test_agent_run_preparation import _app, _configure_active, _register_fake_adapter

    app = _app(tmp_path)
    _register_fake_adapter(
        app,
        constructions=[],
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", multiple_tool_calls=True
        ),
    )
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    parent = build_session_application(app, identity)
    await parent.orchestrator.dispatch("private parent transcript")
    child = build_session_application(app, identity)
    journal = child.persistence.journal
    ws = identity.workspace_id
    journal.create_task_run(
        ws,
        DurableTaskRun(
            task_run_id="task_leaf", session_id=child.session.session_id, workspace_id=ws
        ),
        make_current=True,
    )
    publication = AgentDefinitionPublicationService(
        journal, workspace_id=ws, catalog=CATALOG, id_source=app.id_source
    )
    version = publish(publication)
    child.orchestrator.preparation = AgentFactory(
        child.orchestrator.preparation,
        publication,
        version_id=version.version_id,
        session=child.session,
        task_run_id="task_leaf",
        invoking_session_id=parent.session.session_id,
    )
    result = await child.orchestrator.dispatch("explicit child task")
    assert result.events[-1].payload["finish_reason"] == "stop"
    messages = child.session.log.messages_view()
    assert all("private parent" not in (getattr(m, "content", "") or "") for m in messages)
    assert any(getattr(m, "content", None) == "explicit child task" for m in messages)
    runs = journal.list_session_agent_runs(ws, child.session.session_id)
    assert len(runs) == 1
    assert runs[0].snapshot.definition_ref.version_id == version.version_id
    assert runs[0].snapshot.conversation_session_id == child.session.session_id


def test_publication_rolls_back_version_when_head_write_fails(state, monkeypatch):
    _, _, journal, service = state
    original = journal.agent_definitions.put_head

    def fail(*args, **kwargs):
        raise RuntimeError("injected head failure")

    monkeypatch.setattr(journal.agent_definitions, "put_head", fail)
    with pytest.raises(RuntimeError, match="injected"):
        publish(service)
    assert journal.agent_definitions.list_versions("ws_one") == ()
    assert journal.agent_definitions.publication("ws_one", "cmd_publish") is None
    monkeypatch.setattr(journal.agent_definitions, "put_head", original)
    assert publish(service).version == 1


def test_doctor_and_backup_reject_corrupt_published_head(state):
    store, handle, journal, service = state
    publish(service)
    handle.run_write(
        lambda ex: ex.execute("UPDATE agent_definition_heads SET source_hash=?", ("a" * 64,))
    )
    assert OperationalDoctor(store).inspect("ws_one").health.value == "needs_repair"
    with pytest.raises(BackupBundleError):
        OperationalBackupService(store, journal=journal).create("corrupt")


def test_manifest_definition_inventory_cannot_own_arbitrary_paths():
    from morrow.core.backup import BackupFileEntry, BackupFileKind

    values = dict(kind=BackupFileKind.DEFINITION_SOURCE, sha256="a" * 64, byte_size=10)
    assert BackupFileEntry(path="workspaces/ws_one/agent-definitions.yaml", **values)
    with pytest.raises(ValidationError, match="whitelisted"):
        BackupFileEntry(path="workspaces/ws_one/config.yaml", **values)


def test_exact_skill_versions_reuse_binding_and_effective_tool_checks(tmp_path):
    from morrow.application.skills.selection import SkillSelectionError
    from morrow.core.skills.bindings import SkillSelectionMode
    from test_skill_selection import _services, _source

    _, services = _services(tmp_path)
    package = _source(tmp_path)
    manifest = package / "SKILL.md"
    manifest.write_text(
        manifest.read_text().replace(
            "version: 1.0.0", "version: 1.0.0\nmorrow.required_tools: [read]"
        )
    )
    installed = services.lifecycle.install(package, confirmed=True)
    services.lifecycle.enable("writer-skill", selection_mode=SkillSelectionMode.EXPLICIT)
    args = dict(agent_run_id="arun_exact", exact_version_ids=(installed.version_id,))
    selected = services.selection.select(**args, available_tools=("read",))
    assert [s.version_id for s in selected.selections] == [installed.version_id]
    with pytest.raises(SkillSelectionError, match="unavailable"):
        services.selection.select(**args, available_tools=())
    services.lifecycle.disable("writer-skill")
    with pytest.raises(SkillSelectionError, match="disabled"):
        services.selection.select(**args, available_tools=("read",))
    assert (
        services.selection.select(agent_run_id="arun_empty", exact_version_ids=()).selections == ()
    )


def test_published_skill_reference_is_retained_and_backed_up(tmp_path):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.application.skills.errors import SkillLifecycleError
    from morrow.bootstrap import build_application, build_skill_services
    from morrow.core.skills.bindings import SkillSelectionMode
    from test_skill_selection import _source
    from test_stage6_backup import _store

    store, handle, journal = _store(tmp_path)
    try:
        app = build_application(
            state_root=store.layout.data_root, credentials=MemoryCredentialStore()
        )
        skills = build_skill_services(app, journal=journal, workspace_id="ws_1")
        installed = skills.lifecycle.install(_source(tmp_path), scope_id="ws_1", confirmed=True)
        skills.lifecycle.enable(
            "writer-skill", scope_id="ws_1", selection_mode=SkillSelectionMode.EXPLICIT
        )
        service = AgentDefinitionPublicationService(
            journal,
            workspace_id="ws_1",
            catalog=replace(CATALOG, skill_version_ids=frozenset({installed.version_id})),
            id_source=app.id_source,
        )
        version = publish(service, source(skill_version_ids=(installed.version_id,)))
        assert journal.skill_version_references(installed.version_id)
        backup = OperationalBackupService(store, journal=journal)
        report = backup.create("referenced-skill")
        assert installed.version_id in {v.version_id for v in report.skill_versions}
        assert backup.verify(store.layout.backups_dir / report.bundle_name).ok
        service.revoke(version.version_id, reason="policy changed", command_id="cmd_revoked")
        report = backup.create("revoked-skill")
        target = tmp_path / "restored-revoked"
        assert backup.restore(store.layout.backups_dir / report.bundle_name, target).ok
        with OperationalStore(target).open(StoreOpenMode.READ_ONLY) as restored:
            repo = SqliteOperationalJournal(restored).agent_definitions
            assert repo.get_revocation("ws_1", version.version_id).command_id == "cmd_revoked"
        with pytest.raises(SkillLifecycleError, match="referenced"):
            skills.lifecycle.remove(
                "writer-skill", scope_id="ws_1", version_id=installed.version_id, confirmed=True
            )
    finally:
        handle.close()


def test_builtins_freeze_two_models_and_toolsets_in_distinct_scopes(tmp_path, state):
    from morrow.application.agent_definitions.builtins import builtin_definitions
    from morrow.application.agent_definitions.factory import AgentFactory
    from morrow.core.domain import DurableTaskRun
    from morrow.runtime.session import Session

    _, journal, service, _, base = prepared_fixture(tmp_path, state)
    direct, explorer, _coder, _reviewer, *_ = builtin_definitions(MODEL)
    first = publish(service, direct, command="cmd_direct", origin="builtin")
    second = publish(service, explorer, command="cmd_explorer", origin="builtin")
    journal.create_session(
        DurableSession(session_id="ses_other", workspace_id="ws_one"),
        task=DurableTaskRun(
            task_run_id="task_other", session_id="ses_other", workspace_id="ws_one"
        ),
    )
    runtime1 = AgentFactory(
        base.preparation,
        service,
        version_id=first.version_id,
        session=base.session,
        task_run_id="task_leaf",
        invoking_session_id="ses_invoking",
    ).prepare_new(agent_run_id="arun_direct")
    runtime2 = AgentFactory(
        base.preparation,
        service,
        version_id=second.version_id,
        session=Session(session_id="ses_other"),
        task_run_id="task_other",
        invoking_session_id="ses_invoking",
    ).prepare_new(agent_run_id="arun_explorer")
    assert runtime1.model == MODEL
    assert runtime2.model == OTHER
    assert {t.function.name for t in runtime1.tool_executor.definitions} == {"read", "write"}
    assert {t.function.name for t in runtime2.tool_executor.definitions} == {"read"}
    assert runtime1.spec.conversation_session_id != runtime2.spec.conversation_session_id


def test_value_sensitive_placeholders_and_safe_identity_do_not_weaken_legacy(tmp_path, state):
    from morrow.core.domain import refuse_secret_material

    _, _, _, _, factory = prepared_fixture(tmp_path, state)
    for text in ("password = placeholder", "api_key = $API_KEY", "credential = redacted"):
        assert AgentDefinitionSource(
            definition_id="password_helper", name="Authorization", role_prompt=text
        )
    with pytest.raises(ValueError):
        refuse_secret_material("password validation", label="legacy")
    for text in ("ghp_" + "a" * 24, "password=actual-nonplaceholder"):
        with pytest.raises(ValidationError):
            AgentDefinitionSource(definition_id="helper", name="Helper", role_prompt=text)
    service = factory.publication
    value = source().model_copy(update={"definition_id": "password_helper"})
    version = publish(service, value, command="cmd_identity")
    factory.version_id = version.version_id
    snapshot = freeze(factory.prepare_new(agent_run_id="arun_identity"), factory.session)
    assert snapshot.definition_ref.definition_id == "password_helper"


def test_factory_reuses_current_policy_and_never_admits_a_denied_required_tool(tmp_path, state):
    from morrow.core.capabilities import PermissionProfile, WorkspaceCapability
    from morrow.runtime.capabilities import CapabilityPolicy

    _, _, service, _, factory = prepared_fixture(tmp_path, state)
    value = source(
        access_mode_ceiling="write",
        tool_requirements=(ToolRequirement(name="write", requirement="required"),),
    )
    version = publish(service, value, head=1, command="cmd_writer")
    factory.version_id = version.version_id
    original = factory.preparation.tool_factory

    def restricted(policy):
        executor = original(policy)
        capability = CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="ws_one", root=tmp_path, read_only=True),
        )
        return executor, capability

    # The test's fake tools have no resolver; avoid invoking ToolExecutor's
    # production audit on test-only handlers while retaining its actual policy.
    def factory_with_policy(policy):
        executor, capability = restricted(policy)
        executor.capability_policy = capability
        return executor

    factory.preparation.tool_factory = factory_with_policy
    with pytest.raises(AgentDefinitionAdmissionError, match="denied"):
        factory.prepare_new(agent_run_id="arun_denied")
    factory.preparation.tool_factory = original
    assert [
        t.function.name
        for t in factory.prepare_new(agent_run_id="arun_allowed").tool_executor.definitions
    ] == ["write"]


def test_definition_only_workspace_is_in_backup_inventory(state):
    import json

    store, _, journal, _ = state
    service = AgentDefinitionPublicationService(
        journal, workspace_id="ws_no_session", catalog=CATALOG, id_source=FixedIdSource()
    )
    publish(service)
    assert "ws_no_session" in journal.list_workspace_ids()
    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("definition-only")
    bundle = store.layout.backups_dir / report.bundle_name
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert "ws_no_session" in manifest["workspace_ids"]
    assert backup.verify(bundle).ok


@pytest.mark.parametrize("corruption", ["json", "schema", "workspace", "version"])
def test_corrupt_revocation_uses_storage_repair_error(state, corruption):
    store, handle, journal, service = state
    version = publish(service)
    revoked = service.revoke(version.version_id, reason="policy changed", command_id="cmd_revoke")
    assert journal.agent_definitions.get_revocation("ws_one", version.version_id) == revoked
    body = {"json": "{", "schema": "{}"}.get(corruption)
    if body is None:
        field = "workspace_id" if corruption == "workspace" else "version_id"
        body = revoked.model_copy(
            update={field: "ws_other" if field == "workspace_id" else "adev_other"}
        ).model_dump_json()

    def corrupt(executor):
        executor.execute("DROP TRIGGER agent_definition_revocations_update_immutable")
        executor.execute("UPDATE agent_definition_revocations SET body_json=?", (body,))

    handle.run_write(corrupt)
    with pytest.raises(StorageError, match="^Agent revocation is corrupt$") as error:
        service.admit(version.version_id)
    assert error.value.code is StorageErrorCode.NEEDS_REPAIR
    assert error.value.__cause__ is None
    assert OperationalDoctor(store).inspect("ws_one").health.value == "needs_repair"


def definition_application(tmp_path, *, value=None, with_skill=False):
    from morrow.application.agent_definitions.factory import AgentFactory
    from morrow.bootstrap import build_session_application, build_skill_services
    from morrow.core.agent_runs import ProviderCapabilities
    from morrow.core.domain import DurableTaskRun
    from morrow.core.skills.bindings import SkillSelectionMode
    from morrow.testing import ScriptedModelProvider
    from test_agent_run_preparation import _app, _configure_active
    from test_skill_selection import _source

    app = _app(tmp_path)
    provider = ScriptedModelProvider([["done"]])
    app.registry.register(
        "fake-adapter",
        lambda config, credential: provider,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", multiple_tool_calls=True
        ),
    )
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    (project / "note.txt").write_text("fixture")
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    child = build_session_application(app, identity)
    journal = child.persistence.journal
    ws = identity.workspace_id
    journal.create_task_run(
        ws,
        DurableTaskRun(
            task_run_id="task_leaf", session_id=child.session.session_id, workspace_id=ws
        ),
        make_current=True,
    )
    skills = None
    catalog = CATALOG
    if with_skill:
        skills = build_skill_services(app, journal=journal, workspace_id=ws)
        installed = skills.lifecycle.install(_source(tmp_path), scope_id=ws, confirmed=True)
        skills.lifecycle.enable(
            "writer-skill", scope_id=ws, selection_mode=SkillSelectionMode.EXPLICIT
        )
        value = source(skill_version_ids=(installed.version_id,))
        catalog = replace(CATALOG, skill_version_ids=frozenset({installed.version_id}))
    publication = AgentDefinitionPublicationService(
        journal, workspace_id=ws, catalog=catalog, id_source=app.id_source
    )
    version = publish(publication, value)
    factory = AgentFactory(
        child.orchestrator.preparation,
        publication,
        version_id=version.version_id,
        session=child.session,
        task_run_id="task_leaf",
        invoking_session_id="ses_invoking",
    )
    child.orchestrator.preparation = factory
    return child, factory, provider, skills


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [
        ("disabled", "disabled"),
        ("revoked", "policy_revoked"),
        ("scope", "distinct standalone"),
        ("nonempty", "empty ConversationLog"),
        ("tools", "required tool backend"),
        ("skills", "exact Skill version"),
    ],
)
async def test_definition_preparation_failures_are_known_and_local(tmp_path, failure, diagnostic):
    from morrow.core.models import UserMessage

    child, factory, provider, skills = definition_application(
        tmp_path,
        with_skill=failure == "skills",
        value=source(tool_requirements=(ToolRequirement(name="read", requirement="required"),)),
    )
    publication = factory.publication
    ws = publication.workspace_id
    if failure == "disabled":
        publication.set_enabled("helper", enabled=False, expected_head_revision=1)
    elif failure == "revoked":
        publication.revoke(factory.version_id, reason="policy changed", command_id="cmd_revoke")
    elif failure == "scope":
        factory.invoking_session_id = child.session.session_id
    elif failure == "nonempty":
        child.session.begin_user_turn(UserMessage(content="existing input"))
    elif failure == "tools":
        factory.preparation.tool_factory = lambda policy: None
    else:
        skills.lifecycle.disable("writer-skill", scope_id=ws)
    result = await child.orchestrator.dispatch("explicit leaf task")
    assert result.events[-1].payload["finish_reason"] == "error"
    errors = [event.payload for event in result.events if event.type == "error"]
    assert len(errors) == 1
    assert diagnostic in errors[0]["message"]
    assert errors[0]["stop_code"] == "known_failure"
    assert "凭据" not in errors[0]["message"]
    assert not provider.stream_calls
    assert not publication.journal.list_session_agent_runs(ws, child.session.session_id)
    assert not publication.journal.list_session_turns(ws, child.session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_after_prepare", [False, True])
async def test_exact_skill_preflight_and_atomic_submission(tmp_path, disable_after_prepare):
    child, factory, provider, skills = definition_application(tmp_path, with_skill=True)
    ws = factory.publication.workspace_id
    runtime = factory.prepare_new(agent_run_id="arun_leaf")
    assert not factory.publication.journal.list_session_agent_runs(ws, child.session.session_id)
    if disable_after_prepare:
        skills.lifecycle.disable("writer-skill", scope_id=ws)
    events = [
        event
        async for event in child.orchestrator.runtime.loop.run_task(
            child.session, "explicit leaf task", prepared=runtime, agent_run_id="arun_leaf"
        )
    ]
    runs = factory.publication.journal.list_session_agent_runs(ws, child.session.session_id)
    if disable_after_prepare:
        assert not provider.stream_calls
        assert not runs
        assert not factory.publication.journal.list_session_turns(ws, child.session.session_id)
        error = next(event for event in events if event.type == "error")
        assert error.payload["stop_code"] == "known_failure"
        assert "exact Skill version" in error.payload["message"]
    else:
        assert events[-1].payload["finish_reason"] == "stop"
        assert runs[0].snapshot.skill_selected_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [1, 2])
async def test_generation_cap_through_tool_using_leaf(tmp_path, cap):
    from morrow.core.models import AssistantMessage, FunctionToolCall

    child, factory, provider, _ = definition_application(
        tmp_path,
        value=source(
            max_agent_generation_requests=cap,
            tool_requirements=(ToolRequirement(name="read", requirement="required"),),
        ),
    )
    provider.responses = [
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(id="call_read", name="read", arguments='{"path":"note.txt"}'),
            )
        ),
        AssistantMessage(content="done"),
    ]
    result = await child.orchestrator.dispatch("Read note.txt and report")
    assert len(provider.stream_calls) == cap
    observation = child.persistence.get_agent_run_observation()
    assert len(observation.requests) == cap
    assert observation.terminal_metrics.tool_terminal_counts.succeeded == 1
    if cap == 1:
        error = next(event for event in result.events if event.type == "error")
        assert error.payload["stop_code"] == "known_failure"
        assert "budget_exhausted" in error.payload["message"]
        assert result.events[-1].payload["finish_reason"] == "error"
    else:
        assert result.events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_admitted_skill_leaf_rehydrates_after_binding_and_head_disable(tmp_path):
    child, factory, _, skills = definition_application(tmp_path, with_skill=True)
    runtime = factory.prepare_new(agent_run_id="arun_leaf")
    stream = child.orchestrator.runtime.loop.run_task(
        child.session, "explicit leaf task", prepared=runtime, agent_run_id="arun_leaf"
    )
    try:
        assert (await anext(stream)).type == "turn.started"
        snapshot = child.persistence.get_open_run_snapshot()
        assert snapshot.skill_selected_count == 1
        skills.lifecycle.disable("writer-skill", scope_id=factory.publication.workspace_id)
        factory.publication.set_enabled("helper", enabled=False, expected_head_revision=1)
        restored = factory.rehydrate(snapshot, agent_run_id="arun_leaf")
        assert restored.spec.definition_ref == runtime.spec.definition_ref
        await restored.aclose()
        events = [event async for event in stream]
        assert events[-1].payload["finish_reason"] == "stop"
    finally:
        await stream.aclose()
