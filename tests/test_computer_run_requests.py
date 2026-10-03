"""Real durable submission stages local grants before the first model request."""

import pytest

from morrow.application.agent_runs.preparation import AgentRunPreparationError
from morrow.application.computer_requests import ComputerUseSelection
from morrow.bootstrap import build_session_application
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.computer_use import (
    TRUSTED_COMPUTER_USE_AUTHORITY,
    ComputerUseAppIdentity,
    ComputerUseContractError,
    ComputerUseOperation,
    ComputerUseWindowIdentity,
)
from morrow.core.permissions import CapabilityName
from morrow.core.runtime_policy import ComputerUseSettings, RuntimePolicyOverrides
from test_agent_run_preparation import (
    _app,
    _configure_active,
    _dispatch_prepared,
    _register_fake_adapter,
)


@pytest.fixture
def products(tmp_path):
    app = _app(tmp_path)
    _register_fake_adapter(
        app, constructions=[], capabilities=ProviderCapabilities(tool_protocol="openai_function")
    )
    _configure_active(app)
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides(
                    computer_use=ComputerUseSettings(enabled=True)
                )
            }
        ),
        expected_revision=config.revision,
    )
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    result = build_session_application(
        app,
        identity,
        permission_profile=PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL),
        computer_use_lifecycle=object(),
    )
    try:
        yield result
    finally:
        result.persistence.store_session.close()


def request(products, *, operations=(ComputerUseOperation.OBSERVE, ComputerUseOperation.ACTION)):
    app = ComputerUseAppIdentity(bundle_id="com.example.Controlled")
    return products.orchestrator.preparation.computer_factory.select(
        ComputerUseSelection(
            apps=(app,),
            windows=(ComputerUseWindowIdentity(app=app, window_identity="cwin_req"),),
            operations=operations,
        ),
        products.session,
        authority=TRUSTED_COMPUTER_USE_AUTHORITY,
    )


@pytest.mark.parametrize("with_action", [False, True])
async def test_first_run_freezes_selected_tools_then_creates_real_grant(products, with_action):
    factory = products.orchestrator.preparation.computer_factory
    operations = (ComputerUseOperation.OBSERVE,)
    if with_action:
        operations += (ComputerUseOperation.ACTION,)
    selected = request(products, operations=operations)
    prepared = products.orchestrator.preparation.prepare_new(
        agent_run_id="arun_selected", computer_request=selected
    )
    assert factory.journal.get_agent_run(factory.workspace_id, "arun_selected") is None
    assert factory.journal.list_capability_grants(factory.workspace_id) == ()
    with pytest.raises(ComputerUseContractError):
        await prepared.computer_run.observations.discover("tex_before_submit")
    with pytest.raises(ComputerUseContractError):
        prepared.computer_run.observations.action_preview("cobs_before_submit", None, None)
    original_stream = prepared.provider.stream
    seen_permission_at_model_entry = []

    async def checked_stream(model, messages, tools=(), generation=None):
        seen = factory.journal.get_permission_snapshot_for_run(
            factory.workspace_id, "arun_selected"
        )
        assert seen is not None and seen.computer_use_scope is not None
        seen_permission_at_model_entry.append(seen.permission_snapshot_id)
        async for item in original_stream(model, messages, tools=tools, generation=generation):
            yield item

    prepared.provider.stream = checked_stream
    events = await _dispatch_prepared(
        products.orchestrator, "Inspect the controlled window", prepared
    )
    assert len(seen_permission_at_model_entry) == 1
    assert events[-1].payload["finish_reason"] == "stop"
    snapshot = factory.journal.get_permission_snapshot_for_run(
        factory.workspace_id, "arun_selected"
    )
    assert snapshot.granted_capabilities == (CapabilityName.COMPUTER_USE_HOST,)
    assert snapshot.computer_use_scope.agent_run_id == "arun_selected"
    assert snapshot.computer_use_scope.task_run_id == products.persistence.current_task_run_id
    assert snapshot.computer_use_scope.operations == operations
    assert snapshot.tool_schema_digest == prepared.spec.tool_schema_digest
    names = {tool.function.name for tool in prepared.provider.stream_tools[0]}
    assert "computer_observe" in names
    assert ("computer_action" in names) is with_action
    assert len(factory.journal.list_capability_grants(factory.workspace_id)) == 1
    # A captured request cannot authorize a second prepared run.
    with pytest.raises(AgentRunPreparationError):
        products.orchestrator.preparation.prepare_new(
            agent_run_id="arun_reused", computer_request=selected
        )


async def test_restart_requires_new_local_selection_and_new_run_generation(products):
    preparation = products.orchestrator.preparation
    first = preparation.prepare_new(agent_run_id="arun_first", computer_request=request(products))
    await _dispatch_prepared(products.orchestrator, "Inspect", first)
    snapshot = products.persistence.get_open_run_snapshot()
    assert snapshot is not None
    from morrow.application.computer_runs import ComputerUseRunFactory

    old_factory = preparation.computer_factory
    factory = ComputerUseRunFactory(
        lifecycle=object(),
        journal=old_factory.journal,
        visuals=old_factory.visuals,
        settings=old_factory.settings,
        clock=old_factory.clock,
        workspace_id=old_factory.workspace_id,
        workspace_root=products.session.workspace_capability.root,
    )
    factory.grant_creator = products.api.create_grant
    preparation.computer_factory = factory
    original = factory.journal.get_permission_snapshot_for_run(factory.workspace_id, "arun_first")
    with pytest.raises(AgentRunPreparationError) as refused:
        preparation.rehydrate(snapshot, agent_run_id="arun_first")
    assert isinstance(refused.value.__cause__, ComputerUseContractError)
    assert refused.value.__cause__.code == "computer_use_rebind_required"
    # Continue with frozen provider/tools, but a freshly selected local subject.
    second = preparation.rehydrate(
        snapshot, agent_run_id="arun_second", computer_request=request(products)
    )
    events = [
        event
        async for event in products.orchestrator.runtime.run_turn(
            products.session, "Continue", client_message_id="cmsg-second", prepared=second
        )
    ]
    assert events[-1].payload["finish_reason"] == "stop"
    current = factory.journal.get_permission_snapshot_for_run(factory.workspace_id, "arun_second")
    assert current.computer_use_scope.generation > original.computer_use_scope.generation
    assert current.grant_id != original.grant_id
    assert current.tool_schema_digest == original.tool_schema_digest
    assert current.computer_use_scope.agent_run_id != original.computer_use_scope.agent_run_id


def test_selection_refuses_untrusted_or_changed_session_authority(products):
    factory = products.orchestrator.preparation.computer_factory
    selection = ComputerUseSelection(apps=(ComputerUseAppIdentity(bundle_id="com.example.Test"),))
    with pytest.raises(ComputerUseContractError):
        factory.select(selection, products.session, authority="model")
    selected = request(products)
    products.session.read_only = True
    with pytest.raises(AgentRunPreparationError):
        products.orchestrator.preparation.prepare_new(
            agent_run_id="arun_readonly", computer_request=selected
        )
    assert factory.journal.list_capability_grants(factory.workspace_id) == ()


async def test_cancelled_prepared_request_cannot_create_grant(products):
    preparation = products.orchestrator.preparation
    prepared = preparation.prepare_new(
        agent_run_id="arun_cancelled", computer_request=request(products)
    )
    prepared.close()
    events = await _dispatch_prepared(products.orchestrator, "Cancelled selection", prepared)
    assert events[-1].payload["finish_reason"] == "error"
    assert not prepared.provider.stream_calls
    factory = preparation.computer_factory
    assert factory.journal.list_capability_grants(factory.workspace_id) == ()


@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_desktop_settings_yaml_strings_round_trip_without_weakening_budgets(mode):
    from pydantic import ValidationError

    from morrow.core.preference_documents import GlobalConfig

    settings = GlobalConfig.model_validate(
        {"runtime_policy": {"computer_use": {"enabled": True, "mode": mode}}}
    )
    assert settings.runtime_policy.computer_use.mode.value == mode
    assert GlobalConfig.model_validate(settings.model_dump(mode="json")) == settings
    for invalid in [{"mode": "arbitrary"}, {"max_operations": "100"}, {"enabled": "true"}]:
        with pytest.raises(ValidationError):
            GlobalConfig.model_validate({"runtime_policy": {"computer_use": invalid}})


def test_explicit_selection_cannot_be_silently_ignored_without_run_subject(products):
    selected = request(products)
    with pytest.raises(AgentRunPreparationError, match="durable run subject"):
        products.orchestrator.preparation.prepare_new(computer_request=selected)
    factory = products.orchestrator.preparation.computer_factory
    assert factory.journal.list_capability_grants(factory.workspace_id) == ()
