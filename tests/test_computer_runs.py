"""Frozen authority composition and ordinary per-run resource cleanup."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from morrow.application.agent_runs.preparation import tool_schema_digest
from morrow.application.computer_runs import ComputerUseRunFactory
from morrow.application.computer_tools import ComputerObserveArguments
from morrow.core.agent_runs import ProviderCapabilities
from morrow.core.computer_use import ComputerUseContractError
from morrow.core.runtime_policy import ComputerUseSettings
from morrow.runtime.tools import ToolExecutionError, ToolExecutor, ToolRegistry
from morrow.testing import FixedClock, make_run_policy
from test_agent_run_preparation import _app, _configure_active, _preparation, _register_fake_adapter
from test_computer_use_observer import _application
from test_computer_use_observer import environment as _observer_environment
from test_computer_use_permissions import NOW
from test_computer_use_tools import context


@pytest.fixture
def environment(tmp_path):
    yield from _observer_environment.__wrapped__(tmp_path)


def factory(environment, tmp_path, **changes):
    _, lifecycle = _application(environment)
    values = dict(
        lifecycle=lifecycle,
        journal=environment[1],
        visuals=environment[0],
        settings=ComputerUseSettings(enabled=True),
        clock=FixedClock(NOW),
        workspace_id="ws_a",
        workspace_root=tmp_path,
    )
    values.update(changes)
    return ComputerUseRunFactory(**values), lifecycle


def test_disabled_or_unbound_run_adds_no_tools_and_never_opens_sdk(environment, tmp_path):
    class NoJournal:
        def __getattr__(self, name):
            pytest.fail(f"disabled desktop queried {name}")

    disabled, lifecycle = factory(
        environment, tmp_path, settings=ComputerUseSettings(), journal=NoJournal()
    )
    assert disabled("arun_1", make_run_policy()) is None
    enabled, _ = factory(environment, tmp_path)
    assert enabled("arun_other", make_run_policy()) is None
    assert lifecycle.calls == []


def test_registration_is_lazy_and_preserves_execution_policy(environment, tmp_path):
    compose, lifecycle = factory(environment, tmp_path)
    binding = compose("arun_1", make_run_policy())
    approval = object()
    base = ToolExecutor(ToolRegistry().snapshot(), make_run_policy(), approval_port=approval)
    extended = binding.extend(base)
    assert {tool.function.name for tool in extended.definitions} == {
        "computer_observe",
        "computer_action",
    }
    assert not base.definitions
    assert extended.run_policy is base.run_policy
    assert extended.approval_port is approval
    assert extended.capability_policy is base.capability_policy
    assert lifecycle.calls == []
    assert repr(binding) == "PreparedComputerUseRun()"
    with pytest.raises(ComputerUseContractError, match="function_tools_required"):
        binding.extend(None)
    with pytest.raises(ValueError):
        binding.extend(extended)


def test_wrong_workspace_root_refuses_registration(environment, tmp_path):
    compose, lifecycle = factory(environment, tmp_path / "other")
    with pytest.raises(ComputerUseContractError, match="execution_not_authorized"):
        compose("arun_1", make_run_policy())
    assert lifecycle.calls == []


async def test_frozen_tools_recheck_revocation_without_native_entry(environment, tmp_path):
    compose, lifecycle = factory(environment, tmp_path)
    binding = compose("arun_1", make_run_policy())
    journal = environment[1]
    grant = journal.get_capability_grant("ws_a", "grt_1")
    journal.save_capability_grant(
        "ws_a",
        grant.model_copy(update={"revoked_at": NOW, "revocation_reason": "stop", "row_version": 2}),
        expected_row_version=1,
    )
    with pytest.raises(ComputerUseContractError, match="grant_inactive"):
        compose("arun_1", make_run_policy())
    with pytest.raises(ToolExecutionError):
        await binding.tools[0].context_handler(
            ComputerObserveArguments(operation="discover"), context()
        )
    assert lifecycle.calls == []


async def test_preparation_freezes_schema_and_cleans_desktop_even_if_mcp_close_fails(
    environment, tmp_path
):
    app = _app(tmp_path / "config")
    _register_fake_adapter(
        app,
        constructions=[],
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", input_types=("text", "image")
        ),
    )
    service = _preparation(
        app,
        constructions=[],
        tool_factory=lambda policy: ToolExecutor(ToolRegistry().snapshot(), policy),
    )
    _configure_active(app)
    service.computer_factory, lifecycle = factory(environment, tmp_path)
    runtime = service.prepare_new(agent_run_id="arun_1")
    assert runtime.spec.tool_count == 2
    assert runtime.spec.tool_schema_digest == tool_schema_digest(runtime.tool_executor.definitions)
    assert lifecycle.calls == []
    await runtime.computer_run.observations.discover("tex_observe")
    assert lifecycle.calls == ["open", "session"]

    async def broken_close():
        raise RuntimeError("MCP close failed")

    runtime = replace(runtime, mcp_run=SimpleNamespace(pool=SimpleNamespace(close=broken_close)))
    runtime.close()
    with pytest.raises(ComputerUseContractError):
        await runtime.computer_run.observations.discover("tex_observe")
    assert lifecycle.calls == ["open", "session"]
    with pytest.raises(RuntimeError, match="MCP close failed"):
        await runtime.aclose()
    assert lifecycle.calls == ["open", "session", "close"]
    await runtime.computer_run.aclose()
    assert lifecycle.calls == ["open", "session", "close"]


async def test_close_failure_keeps_session_for_retry_and_admission_closed(environment, tmp_path):
    compose, lifecycle = factory(environment, tmp_path)
    binding = compose("arun_1", make_run_policy())
    await binding.observations.discover("tex_observe")
    original_close = lifecycle.close_run_session

    async def failed_close(request):
        raise RuntimeError("native drain failed")

    lifecycle.close_run_session = failed_close
    with pytest.raises(RuntimeError, match="native drain failed"):
        await binding.aclose()
    with pytest.raises(ComputerUseContractError):
        await binding.observations.discover("tex_observe")
    lifecycle.close_run_session = original_close
    await binding.aclose()
    assert lifecycle.calls == ["open", "session", "close"]


def test_injected_runtime_keeps_provider_and_base_executor_when_binding_tools(
    environment, tmp_path
):
    app = _app(tmp_path / "config")
    _register_fake_adapter(
        app,
        constructions=[],
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function", input_types=("text", "image")
        ),
    )
    service = _preparation(
        app,
        constructions=[],
        tool_factory=lambda policy: ToolExecutor(ToolRegistry().snapshot(), policy),
    )
    _configure_active(app)
    base = service.prepare_new()
    # The injected path is the ordinary bootstrap path without a configured active model.
    config = app.global_store.load()
    app.global_store.update(
        lambda value: value.model_copy(update={"active_model": None}),
        expected_revision=config.revision,
    )
    service.injected = base
    service.computer_factory, lifecycle = factory(environment, tmp_path)
    prepared = service.prepare_new(agent_run_id="arun_1")
    assert prepared is not base
    assert prepared.provider is base.provider
    assert prepared.context_builder is not base.context_builder
    assert base.context_builder.tool_visual_hydrator is None
    assert prepared.context_builder.tool_visual_hydrator.agent_run_id == "arun_1"
    assert prepared.run_policy is base.run_policy
    assert prepared.agent_run_id == "arun_1"
    assert prepared.spec.tool_count == 2
    assert not base.tool_executor.definitions
    assert lifecycle.calls == []
    assert service.prepare_new() is base


def test_context_binding_refuses_unsupported_model_before_driver_entry(environment, tmp_path):
    from morrow.core.runtime_policy import ComputerUseMode
    from morrow.testing import make_context_builder

    compose, lifecycle = factory(
        environment,
        tmp_path,
        settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
    )
    binding = compose("arun_1", make_run_policy())
    context_builder = make_context_builder()
    for capabilities, reason in [
        (
            SimpleNamespace(tool_protocol="none", input_types=("text", "image")),
            "function_tools_required",
        ),
        (
            SimpleNamespace(tool_protocol="openai_function", input_types=("text",)),
            "model_image_tools_required",
        ),
    ]:
        with pytest.raises(ComputerUseContractError, match=reason):
            binding.bind_context(context_builder, capabilities)
    copied = binding.bind_context(
        context_builder,
        SimpleNamespace(tool_protocol="openai_function", input_types=("text", "image")),
    )
    assert copied.tool_visual_hydrator.session_id == "ses_1"
    assert copied.tool_visual_hydrator.agent_run_id == "arun_1"
    assert context_builder.tool_visual_hydrator is None
    assert lifecycle.calls == []


def test_bootstrap_uses_supplied_shared_lifecycle_and_default_registry_is_unchanged(tmp_path):
    from morrow.bootstrap import build_session_application
    from morrow.core.models import ModelRef
    from morrow.testing import ScriptedModelProvider

    app = _app(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    lifecycle = object()
    products = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider([["done"]]),
        model=ModelRef(provider_id="fake-provider", model_id="m1"),
        computer_use_lifecycle=lifecycle,
    )
    try:
        assert products.computer_use is lifecycle
        preparation = products.orchestrator.preparation
        assert preparation.computer_factory.lifecycle is lifecycle
        runtime = preparation.prepare_new(agent_run_id="arun_unbound")
        assert runtime.computer_run is None
        assert "computer_observe" not in {
            tool.function.name for tool in runtime.tool_executor.definitions
        }
    finally:
        products.persistence.store_session.close()


def test_management_context_and_lazy_chat_share_one_lifecycle(tmp_path):
    from morrow.server.composition import build_server_context

    app = _app(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    context = build_server_context(app, identity)
    try:
        _register_fake_adapter(app, constructions=[])
        _configure_active(app)
        # The management surface starts without a Provider. Its lazy execution
        # product must reuse the lifecycle already owned by CoreHost shutdown.
        products = context.products._load()
        assert products.computer_use is context.computer_use
        assert products.orchestrator.preparation.computer_factory.lifecycle is context.computer_use
    finally:
        context.close()
