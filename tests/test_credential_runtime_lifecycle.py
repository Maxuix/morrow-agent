"""Credentials retain their source and process ownership across runtime replacement."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.local.process import HostProcessAdapter, _CursorBuffer
from morrow.adapters.models.openai_compatible import (
    OpenAICompatibleProvider,
    estimate_request_chars,
)
from morrow.application.agent_runs.preparation import (
    AgentRunPreparationService,
    ProviderUnavailableError,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.agent_runs import ProviderCapabilities, ProviderRuntimeSnapshot
from morrow.core.domain import AgentRunSnapshot
from morrow.core.local_tools import CommandRequest, TrackedLifecycle
from morrow.runtime.policy import load_runtime_policy
from morrow.services.bash_execution import _read, _start, _stop
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import PreparedBash, ProcessExecutionService
from morrow.services.tracked_process import TrackedProcessError, TrackedProcessRegistry
from morrow.testing import ScriptedModelProvider


def _application(tmp_path, monkeypatch):
    monkeypatch.delenv("MORROW_DEMO_API_KEY", raising=False)
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    constructions = []

    def create(config, credential):
        constructions.append((config, credential))
        return ScriptedModelProvider([["prepared answer"]])

    app.registry.register("fake-adapter", create, capabilities=ProviderCapabilities())
    service = app.provider_service
    service.add_provider(
        "demo", adapter_id="fake-adapter", base_url="https://example.test", secret="keyring-value"
    )
    service.add_model("demo", "model")
    service.use_model("demo", "model")
    return app, constructions


def _preparation(app):
    service = app.provider_service
    return AgentRunPreparationService(
        global_store=app.global_store,
        registry=app.registry,
        agent_policy=load_runtime_policy().agent_run,
        credential_resolver=service.credential_resolver,
        credential_source_resolver=service.resolve_run_credential,
        frozen_credential_resolver=service.resolve_frozen_credential,
        estimate_request_chars=estimate_request_chars,
        tool_factory=lambda policy: None,
    )


def _snapshot(prepared):
    return AgentRunSnapshot(
        profile=None,
        model=prepared.model,
        provider_id=prepared.model.provider_id,
        source_revisions=(),
        run_policy_digest=prepared.spec.run_policy_digest,
        tool_schema_digest=prepared.spec.tool_schema_digest,
        permission_profile_digest=prepared.spec.tool_schema_digest,
        runtime_instance_id="instance-fixture",
        provider_runtime=prepared.spec.provider_runtime,
        run_policy=prepared.run_policy,
    )


@pytest.mark.parametrize("env_only", [False, True])
def test_environment_source_survives_snapshot_roundtrip_and_fails_closed(
    tmp_path, monkeypatch, env_only
):
    app, constructions = _application(tmp_path, monkeypatch)
    service = app.provider_service
    if env_only:
        service._commit(
            service.list(),
            lambda config: config.model_copy(
                update={
                    "providers": {
                        "demo": config.providers["demo"].model_copy(update={"credential_ref": None})
                    }
                }
            ),
        )
    monkeypatch.setenv("MORROW_DEMO_API_KEY", "environment-value")
    preparation = _preparation(app)
    snapshot = _snapshot(preparation.prepare_new())
    assert constructions[-1][1] == "environment-value"
    assert snapshot.provider_runtime.credential_source == "environment"

    serialized = snapshot.model_dump_json()
    for value in ("keyring-value", "environment-value"):
        assert value not in serialized
        assert hashlib.sha256(value.encode()).hexdigest() not in serialized
    restored = AgentRunSnapshot.model_validate_json(serialized)
    assert preparation.rehydrate(restored).owns_provider is True
    assert constructions[-1][1] == "environment-value"

    # The named source may rotate its value, but never silently changes to Keyring.
    monkeypatch.setenv("MORROW_DEMO_API_KEY", "rotated-environment-value")
    preparation.rehydrate(restored)
    assert constructions[-1][1] == "rotated-environment-value"
    monkeypatch.delenv("MORROW_DEMO_API_KEY")
    with pytest.raises(ProviderUnavailableError):
        preparation.rehydrate(restored)


def test_keyring_and_legacy_snapshots_ignore_later_environment_override(tmp_path, monkeypatch):
    app, constructions = _application(tmp_path, monkeypatch)
    preparation = _preparation(app)
    prepared = preparation.prepare_new()
    assert prepared.owns_provider is True
    snapshot = _snapshot(prepared)
    assert snapshot.provider_runtime.credential_source == "keyring"
    monkeypatch.setenv("MORROW_DEMO_API_KEY", "later-environment-value")
    preparation.rehydrate(snapshot)
    assert constructions[-1][1] == "keyring-value"

    legacy = snapshot.model_dump(mode="json")
    legacy["provider_runtime"].pop("credential_source")
    restored = AgentRunSnapshot.model_validate(legacy)
    assert restored.provider_runtime.credential_source is None
    assert restored.model_dump(mode="json") == legacy
    preparation.rehydrate(restored)
    assert constructions[-1][1] == "keyring-value"

    # A historical snapshot without a reference cannot gain authority from today's env.
    legacy["provider_runtime"]["credential_ref"] = None
    with pytest.raises(ProviderUnavailableError):
        preparation.rehydrate(AgentRunSnapshot.model_validate(legacy))


def test_frozen_credential_source_requires_valid_non_sensitive_identity(tmp_path, monkeypatch):
    app, _ = _application(tmp_path, monkeypatch)
    frozen = _preparation(app).prepare_new().spec.provider_runtime.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ProviderRuntimeSnapshot.model_validate({**frozen, "credential_source": "secret-value"})
    with pytest.raises(ValidationError):
        ProviderRuntimeSnapshot.model_validate({**frozen, "credential_ref": None})


def test_explicit_keyring_resolver_does_not_freeze_unrelated_environment(tmp_path, monkeypatch):
    app, constructions = _application(tmp_path, monkeypatch)
    monkeypatch.setenv("MORROW_DEMO_API_KEY", "unrelated-environment-value")
    app.provider_service.credential_resolver = app.provider_service.resolve_frozen_credential
    preparation = _preparation(app)
    snapshot = _snapshot(preparation.prepare_new())
    assert snapshot.provider_runtime.credential_source == "keyring"
    assert constructions[-1][1] == "keyring-value"
    preparation.rehydrate(snapshot)
    assert constructions[-1][1] == "keyring-value"


async def test_provider_removal_preserves_durable_snapshot_credential(tmp_path, monkeypatch):
    app, constructions = _application(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(app, identity)
    try:
        await session_app.orchestrator.dispatch("freeze this run")
        snapshot = session_app.persistence.get_open_run_snapshot()
        assert snapshot is not None
        assert snapshot.provider_runtime.credential_source == "keyring"
        frozen_ref = snapshot.provider_runtime.credential_ref
        app.provider_service.add_provider(
            "next", adapter_id="fake-adapter", base_url="https://example.test", secret="next-value"
        )
        app.provider_service.add_model("next", "model")
        app.provider_service.use_model("next", "model")
        app.provider_service.remove_provider("demo")
        assert "demo" not in app.provider_service.list().providers
        assert app.credentials.get(frozen_ref.ref) == "keyring-value"
        _preparation(app).rehydrate(snapshot)
        assert constructions[-1][1] == "keyring-value"
    finally:
        session_app.persistence.store_session.close()


class _CapturedAdapter(HostProcessAdapter):
    """No OS process: exercise startup binding, ownership and both output channels."""

    async def spawn(self, **kwargs):
        stdout = _CursorBuffer(kwargs["output_limit"])
        stderr = _CursorBuffer(kwargs["output_limit"])
        stdout.add(b"old-environment-secret new-keyring-secret\n")
        stderr.add(b"old-environment-secret new-keyring-secret\n")
        return SimpleNamespace(
            stdout=stdout,
            stderr=stderr,
            process=SimpleNamespace(pid=12345, returncode=0),
        )


@pytest.mark.parametrize(
    ("lifecycle", "poll_task"),
    [(TrackedLifecycle.ACCEPTANCE, "next-task"), (TrackedLifecycle.TASK, "original-task")],
)
@pytest.mark.parametrize("operation", ["poll", "stop"])
async def test_tracked_output_retains_startup_secret_when_runtime_redactor_changes(
    tmp_path, monkeypatch, lifecycle, poll_task, operation
):
    monkeypatch.setattr("morrow.services.tracked_process._group_alive", lambda pid: False)
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    registry = TrackedProcessRegistry()
    original = ProcessExecutionService(
        files,
        adapter=_CapturedAdapter(),
        tracked=registry,
        secrets=("old-environment-secret",),
    )
    prepared = PreparedBash(
        action="start",
        plan=original.preflight(CommandRequest(shell="printf fixture")),
        lifecycle=lifecycle,
    )
    execution_id = await _start(
        original, prepared, session_id="session", task_id="original-task", run_id="original-run"
    )
    replacement = ProcessExecutionService(files, tracked=registry, secrets=("new-keyring-secret",))
    arguments = dict(session_id="session", task_id=poll_task, offset=0, stderr_offset=0, limit=4096)
    if operation == "poll":
        view = _read(replacement, execution_id, **arguments)
    else:
        view = await _stop(replacement, execution_id, **arguments)
    for output in (view.stdout, view.stderr):
        assert output == "<redacted> <redacted>\n"
    assert view.output_offset == len(b"old-environment-secret new-keyring-secret\n")
    assert view.stderr_offset == view.output_offset
    execution = registry._items[execution_id]
    assert "old-environment-secret" not in repr(execution)
    # A raw cursor inside the original secret remains safe after replacement,
    # even when the requested page is too short to contain the whole value.
    tiny = _read(
        replacement,
        execution_id,
        session_id="session",
        task_id=poll_task,
        offset=7,
        stderr_offset=7,
        limit=1,
    )
    assert tiny.stdout == tiny.stderr == "<redacted>"
    assert tiny.output_offset == tiny.stderr_offset == len(b"old-environment-secret")
    with pytest.raises(TrackedProcessError, match="其他任务或会话"):
        registry.read(
            execution_id,
            session_id="other-session",
            task_id=poll_task,
            offset=0,
            stderr_offset=0,
            limit=4096,
            redactor=replacement.redactor,
        )
    if lifecycle is TrackedLifecycle.TASK:
        with pytest.raises(TrackedProcessError, match="其他任务或会话"):
            registry.read(
                execution_id,
                session_id="session",
                task_id="other-task",
                offset=0,
                stderr_offset=0,
                limit=4096,
                redactor=replacement.redactor,
            )


async def test_owned_provider_closes_client_while_injected_runtime_keeps_it(tmp_path, monkeypatch):
    app, _ = _application(tmp_path, monkeypatch)
    preparation = _preparation(app)

    class Client:
        closed = 0

        async def close(self):
            self.closed += 1

    client = Client()
    provider = OpenAICompatibleProvider("https://example.test", "fixture-value")
    provider._client = client
    owned = replace(preparation.prepare_new(), provider=provider)
    shared = replace(owned, owns_provider=False)
    preparation.injected = shared
    monkeypatch.setattr(preparation.global_store, "load", lambda: SimpleNamespace(value=None))
    borrowed = preparation.prepare_new()
    assert borrowed is shared
    await borrowed.aclose()
    assert client.closed == 0
    await owned.aclose()
    await owned.aclose()
    assert client.closed == 1
    assert provider._client is None


@pytest.mark.parametrize("failure", ["mcp", "computer"])
async def test_owned_provider_closes_even_when_other_run_resource_cleanup_fails(
    tmp_path, monkeypatch, failure
):
    app, _ = _application(tmp_path, monkeypatch)
    cleaned = []

    async def close_mcp():
        cleaned.append("mcp")
        if failure == "mcp":
            raise RuntimeError("mcp cleanup failed")

    async def close_computer():
        cleaned.append("computer")
        if failure == "computer":
            raise RuntimeError("computer cleanup failed")

    async def close_provider():
        cleaned.append("provider")

    owned = replace(
        _preparation(app).prepare_new(),
        provider=SimpleNamespace(aclose=close_provider),
        mcp_run=SimpleNamespace(pool=SimpleNamespace(close=close_mcp)),
        computer_run=SimpleNamespace(aclose=close_computer),
    )
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await owned.aclose()
    assert cleaned == ["mcp", "computer", "provider"]
