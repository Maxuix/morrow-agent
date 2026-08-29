from __future__ import annotations

import pytest
from typer.testing import CliRunner

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.adapters.registry import AdapterRegistry
from morrow.application.agent_runs.preparation import AgentRunPreparationService
from morrow.application.providers.control import ProviderControlConflict
from morrow.bootstrap import build_application
from morrow.core.agent_runs import ProviderCapabilities, exact_model_capabilities
from morrow.core.models import (
    ModelCapabilityOverrides,
    ModelErrorCode,
    ModelProviderError,
    ModelRef,
    StateWriteResult,
    StateWriteStatus,
)
from morrow.core.providers import DiscoveredModel, validate_base_url
from morrow.interfaces import cli as cli_module
from morrow.interfaces.cli import app as cli_app
from morrow.runtime.policy import load_runtime_policy


class FakeProvider:
    async def complete(self, model, messages):
        del model, messages
        return "ok"

    async def stream(self, model, messages, tools=()):
        del model, messages, tools
        if False:
            yield


def _app(tmp_path, *, discovery=None):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    app.registry.register(
        "fake-adapter",
        lambda config, credential: FakeProvider(),
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function",
            multiple_tool_calls=True,
            structured_output=True,
            safe_request_chars=1000,
            safe_context_chars=8000,
            input_types=("text", "image"),
        ),
        discovery=discovery,
    )
    return app


def test_provider_model_control_keeps_credentials_out_of_yaml_and_has_no_default_provider(
    tmp_path,
):
    app = _app(tmp_path)
    config = app.provider_service.add_provider(
        "demo",
        adapter_id="fake-adapter",
        base_url="https://example.test/v1/",
        secret="credential-secret",
    )
    assert config.base_url == "https://example.test/v1"
    assert app.provider_service.current_model() is None

    first = app.provider_service.add_model("demo", "text-model", api_model_id="vendor/text")
    second = app.provider_service.add_model("demo", "vision-model")
    assert first == ModelRef(provider_id="demo", model_id="text-model")
    assert second == ModelRef(provider_id="demo", model_id="vision-model")
    raw = app.data_root.config_path.read_text(encoding="utf-8")
    assert "credential-secret" not in raw
    assert "provider:demo:" in raw

    active = app.provider_service.use_model("demo", "text-model")
    assert active == first
    with pytest.raises(ValueError, match="active_model"):
        app.provider_service.remove_model("demo", "text-model")

    app.provider_service.use_model("demo", "vision-model")
    app.provider_service.remove_model("demo", "text-model")
    with pytest.raises(ValueError, match="active_model"):
        app.provider_service.remove_provider("demo")


def test_model_capability_override_is_persisted_and_narrows_the_next_run_snapshot(tmp_path):
    app = _app(tmp_path)
    app.provider_service.add_provider(
        "demo", adapter_id="fake-adapter", base_url="https://example.test", secret="secret"
    )
    app.provider_service.add_model(
        "demo",
        "model",
        capabilities=ModelCapabilityOverrides(
            streaming_text=False,
            tool_protocol="none",
            multiple_tool_calls=True,
            structured_output=True,
            safe_request_chars=200,
            safe_context_chars=4000,
            input_types=("image",),
        ),
    )
    model = app.provider_service.use_model("demo", "model")
    stored = app.provider_service.provider("demo").models[model.model_id]
    exact = exact_model_capabilities(
        "fake-adapter", app.registry.capabilities("fake-adapter"), model, stored.capabilities
    )
    assert exact.streaming_text is False
    assert exact.tool_protocol == "none"
    assert exact.multiple_tool_calls is True
    assert exact.structured_output is True
    assert exact.safe_request_chars == 200
    assert exact.safe_context_chars == 4000
    assert exact.input_types == ("image",)

    preparation = AgentRunPreparationService(
        global_store=app.global_store,
        registry=app.registry,
        agent_policy=load_runtime_policy().agent_run,
        credential_resolver=app.provider_service.credential_resolver,
        estimate_request_chars=estimate_request_chars,
        tool_factory=lambda policy: None,
    )
    runtime = preparation.prepare_new()
    assert runtime.spec.provider_runtime.capabilities == exact
    assert runtime.spec.provider_runtime.config_revision == app.global_store.load().revision


def test_model_sync_is_explicit_projection_and_preserves_active_model(tmp_path):
    async def discover(config, credential):
        assert config.adapter == "fake-adapter"
        assert credential == "secret"
        return (DiscoveredModel(model_id="new-model", api_model_id="vendor/new-model"),)

    app = _app(tmp_path, discovery=discover)
    app.provider_service.add_provider(
        "demo", adapter_id="fake-adapter", base_url="https://example.test", secret="secret"
    )
    app.provider_service.add_model("demo", "old-model")
    app.provider_service.use_model("demo", "old-model")

    models = app.provider_service.sync_models("demo")

    assert models == (ModelRef(provider_id="demo", model_id="new-model"),)
    stored = app.provider_service.provider("demo")
    assert set(stored.models) == {"old-model", "new-model"}
    assert app.provider_service.current_model() == ModelRef(
        provider_id="demo", model_id="old-model"
    )


def test_model_sync_failure_is_typed_and_sanitized(tmp_path):
    async def discover(config, credential):
        del config, credential
        raise ModelProviderError(ModelErrorCode.AUTH, "raw-key-and-response")

    app = _app(tmp_path, discovery=discover)
    app.provider_service.add_provider(
        "demo", adapter_id="fake-adapter", base_url="https://example.test", secret="secret"
    )

    with pytest.raises(ValueError, match="认证失败") as caught:
        app.provider_service.sync_models("demo")
    assert "raw-key" not in str(caught.value)
    assert "raw-key" not in app.data_root.config_path.read_text(encoding="utf-8")


def test_provider_control_uses_occ_and_rejects_unsafe_urls(tmp_path, monkeypatch):
    app = _app(tmp_path)
    with pytest.raises(ValueError):
        validate_base_url("https://user:password@example.test/v1")
    with pytest.raises(ValueError):
        app.provider_service.add_provider(
            "demo", adapter_id="fake-adapter", base_url="https://example.test?token=secret"
        )

    app.provider_service.add_provider(
        "demo", adapter_id="fake-adapter", base_url="https://example.test", secret="secret"
    )
    original_update = app.global_store.update

    def conflict(*args, **kwargs):
        del args, kwargs
        return StateWriteResult(status=StateWriteStatus.REVISION_CONFLICT, revision=99)

    monkeypatch.setattr(app.global_store, "update", conflict)
    with pytest.raises(ProviderControlConflict, match="配置已变化"):
        app.provider_service.add_model("demo", "m")
    monkeypatch.setattr(app.global_store, "update", original_update)
    assert "m" not in app.provider_service.provider("demo").models


def test_second_adapter_can_be_registered_without_registry_branches():
    registry = AdapterRegistry()
    registry.register(
        "second",
        lambda config, credential: FakeProvider(),
        capabilities=ProviderCapabilities(input_types=("audio",), safe_context_chars=1234),
    )
    exact = exact_model_capabilities(
        "second",
        registry.capabilities("second"),
        ModelRef(provider_id="p", model_id="m"),
    )
    assert exact.input_types == ("audio",)
    assert exact.safe_context_chars == 1234


def test_cli_provider_and_model_control_commands_are_local_and_explicit(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    monkeypatch.setattr(cli_module, "_secret", lambda provider_id="": "cli-secret")
    real_build_application = cli_module.build_application
    credentials = MemoryCredentialStore()
    monkeypatch.setattr(
        cli_module,
        "build_application",
        lambda **kwargs: real_build_application(credentials=credentials, **kwargs),
    )
    runner = CliRunner()

    added = runner.invoke(
        cli_app,
        [
            "provider",
            "add",
            "--name",
            "demo",
            "--adapter",
            "openai-compatible",
            "--base-url",
            "https://example.test/v1",
            "--state-root",
            str(state_root),
        ],
    )
    assert added.exit_code == 0, added.output
    model = runner.invoke(
        cli_app,
        [
            "model",
            "add",
            "--provider",
            "demo",
            "--model-id",
            "m",
            "--state-root",
            str(state_root),
        ],
    )
    assert model.exit_code == 0, model.output
    selected = runner.invoke(
        cli_app,
        ["model", "use", "demo/m", "--state-root", str(state_root)],
    )
    assert selected.exit_code == 0, selected.output
    shown = runner.invoke(
        cli_app,
        ["model", "show", "demo/m", "--state-root", str(state_root)],
    )
    assert shown.exit_code == 0, shown.output
    assert "api_model_id: m" in shown.output
