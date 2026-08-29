"""Subplan 64: per-AgentRun preparation contracts, probe, and rehydration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.application.agent_runs.preparation import (
    AgentRunPreparationError,
    AgentRunPreparationService,
    PreparedAgentRunRuntime,
    ProviderUnavailableError,
    build_prepared_spec,
    run_policy_digest,
    tool_schema_digest,
)
from morrow.application.turn_lifecycle import (
    PreferenceRunSources,
    TurnSubmissionCoordinator,
    request_digest,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.agent_runs import ModelCapabilities, ProviderCapabilities, exact_model_capabilities
from morrow.core.completion import (
    OutcomeContract,
    ValidationRequirement,
    WorkspaceBaseline,
    WorkspaceBaselineStatus,
)
from morrow.core.domain import (
    AGENT_RUN_SNAPSHOT_MAX_BYTES,
    AgentRunSnapshot,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
    canonical_json_bytes,
)
from morrow.core.models import (
    CredentialRef,
    FinishReason,
    ModelCapabilityOverrides,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    ToolDefinition,
    ToolFunction,
)
from morrow.core.runtime_policy import RuntimePolicyOverrides
from morrow.runtime.policy import load_agent_policy
from morrow.testing import ScriptedModelProvider

AGENT_POLICY = load_agent_policy()


class FakeProvider:
    def __init__(self, config: ProviderConfig, credential: str) -> None:
        self.config = config
        self.credential = credential

    async def complete(self, model, messages) -> str:
        return "prepared"


def _register_fake_adapter(
    app,
    *,
    constructions: list,
    capabilities: ProviderCapabilities | None = None,
):
    def factory(config, credential):
        constructions.append((config, credential))
        return ScriptedModelProvider([["prepared ", "answer"]])

    app.registry.register(
        "fake-adapter",
        factory,
        capabilities=capabilities,
    )
    return factory


def _configure_active(app, *, model_id="m1", base_url=None, capabilities=None):
    config = app.global_store.load()
    credential_ref = CredentialRef(ref="provider:fake-provider:test", version=3)
    app.credentials.set(credential_ref.ref, "topsecret-value")
    app.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "fake-provider": ProviderConfig(
                        adapter="fake-adapter",
                        base_url=base_url or "https://api.example.test/v1",
                        credential_ref=credential_ref,
                        models={
                            model_id: ProviderModelConfig(
                                api_model_id=f"api-{model_id}", capabilities=capabilities
                            )
                        },
                    )
                },
                "active_model": ModelRef(provider_id="fake-provider", model_id=model_id),
            }
        ),
        expected_revision=config.revision,
    )
    return credential_ref


def _preparation(app, *, constructions=None, tool_factory=None, legacy=None):
    if constructions is None:
        constructions = []
        _register_fake_adapter(app, constructions=constructions)
    return AgentRunPreparationService(
        global_store=app.global_store,
        registry=app.registry,
        agent_policy=AGENT_POLICY,
        credential_resolver=app.provider_service.credential_resolver,
        estimate_request_chars=estimate_request_chars,
        tool_factory=tool_factory or (lambda policy: None),
        legacy=legacy,
    )


def _app(tmp_path: Path):
    return build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())


def _open_session_application(app, project: Path):
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    return build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider([["boot ", "runtime"]]),
        model=ModelRef(provider_id="fake-provider", model_id="m1"),
    )


async def _dispatch(orchestrator, text: str):
    result = await orchestrator.dispatch(text)
    return result.events


class _SameMessageId:
    def new_id(self, prefix: str) -> str:
        return "cmsg-fixed"


def test_prepare_new_builds_runtime_from_current_config(tmp_path: Path) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(
        app,
        constructions=constructions,
        capabilities=ProviderCapabilities(
            tool_protocol="openai_function",
            multiple_tool_calls=True,
            structured_output=True,
        ),
    )
    credential_ref = _configure_active(app)
    service = _preparation(app, constructions=constructions)

    runtime = service.prepare_new()

    assert len(constructions) == 1
    provider_config, credential = constructions[0]
    assert credential == "topsecret-value"
    assert runtime.model == ModelRef(provider_id="fake-provider", model_id="m1")
    assert provider_config.credential_ref == credential_ref

    frozen = runtime.spec.provider_runtime
    assert frozen.provider_id == "fake-provider"
    assert frozen.adapter_id == "fake-adapter"
    assert frozen.api_model_id == "api-m1"
    assert frozen.endpoint == "https://api.example.test/v1"
    assert frozen.credential_ref == credential_ref
    assert frozen.capabilities.structured_output is True
    assert frozen.capabilities.tool_protocol == "openai_function"
    assert run_policy_digest(runtime.spec.run_policy) == runtime.spec.run_policy_digest
    assert runtime.spec.run_policy.provider_tool_support.multiple_tool_calls is True

    serialized = runtime.spec.model_dump_json()
    assert "topsecret-value" not in serialized
    assert "sk-" not in serialized


def test_active_model_change_affects_next_new_run_only(tmp_path: Path) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(app)
    service = _preparation(app)

    first = service.prepare_new()
    assert first.model == ModelRef(provider_id="fake-provider", model_id="m1")

    _configure_active(app, model_id="m2")
    second = service.prepare_new()

    assert second.model == ModelRef(provider_id="fake-provider", model_id="m2")
    assert second.spec.provider_runtime.api_model_id == "api-m2"
    # The already-prepared current run stays frozen on its original model.
    assert first.model == ModelRef(provider_id="fake-provider", model_id="m1")
    assert first.provider is not second.provider


def test_model_capability_overrides_only_narrow_adapter_defaults() -> None:
    model = ModelRef(provider_id="provider", model_id="model")
    exact = exact_model_capabilities(
        "adapter",
        ProviderCapabilities(
            streaming_text=False,
            tool_protocol="none",
            multiple_tool_calls=False,
            structured_output=False,
            safe_request_chars=100,
            context_window_tokens=1_000_000,
            max_output_tokens=128_000,
            input_types=("text",),
        ),
        model,
        ModelCapabilities(
            model=model,
            streaming_text=True,
            tool_protocol="openai_function",
            multiple_tool_calls=True,
            structured_output=True,
            safe_request_chars=1000,
            context_window_tokens=800_000,
            max_output_tokens=64_000,
            input_types=("image", "text"),
        ),
    )

    assert exact.streaming_text is False
    assert exact.tool_protocol == "none"
    assert exact.multiple_tool_calls is False
    assert exact.structured_output is False
    assert exact.safe_request_chars == 100
    assert exact.context_window_tokens == 800_000
    assert exact.max_output_tokens == 64_000
    assert exact.input_types == ("text",)


def test_configured_provider_session_defaults_to_long_horizon_with_unknown_window(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()

    session_app = _open_session_application(app, project)

    policy = session_app.context_builder.run_policy
    assert policy.is_long_horizon is True
    assert policy.context_window_tokens is None
    assert policy.effective_request_chars == 262_144
    assert policy.compaction_enabled is True


def test_configured_model_window_and_output_capacity_drive_long_horizon_policy(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(
        app,
        capabilities=ModelCapabilityOverrides(
            context_window_tokens=1_000_000,
            max_output_tokens=384_000,
        ),
    )
    project = tmp_path / "project"
    project.mkdir()

    policy = _open_session_application(app, project).context_builder.run_policy

    assert policy.context_window_tokens == 1_000_000
    assert policy.reserve_tokens == 384_000


def test_automatic_long_horizon_preserves_explicit_legacy_policy_overrides(
    tmp_path: Path,
) -> None:
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(app)
    loaded = app.global_store.load()
    updated = app.global_store.update(
        lambda value: value.model_copy(
            update={
                "runtime_policy": RuntimePolicyOverrides.model_validate(
                    {"agent_run": {"max_tool_rounds": 12}}, strict=True
                )
            }
        ),
        expected_revision=loaded.revision,
    )
    assert updated.status.value == "ok"
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    _register_fake_adapter(app, constructions=[])
    project = tmp_path / "project"
    project.mkdir()

    policy = _open_session_application(app, project).context_builder.run_policy

    assert policy.is_long_horizon is False
    assert policy.max_tool_rounds == 12


def test_prepare_new_without_config_returns_legacy_runtime(tmp_path: Path) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    model = ModelRef(provider_id="fake-provider", model_id="m")
    run_policy = AGENT_POLICY.resolve(model, tool_protocol="none", multiple_tool_calls=False)
    legacy = PreparedAgentRunRuntime(
        spec=build_prepared_spec(
            provider_config=ProviderConfig(
                adapter="fake-adapter",
                base_url="",
                models={"m": ProviderModelConfig(api_model_id="api-m")},
            ),
            model=model,
            exact_capabilities=exact_model_capabilities(
                "fake-adapter", app.registry.capabilities("fake-adapter"), model
            ),
            config_revision=0,
            run_policy=run_policy,
            tools=(),
        ),
        provider=object(),
        model=model,
        context_builder=object(),
        tool_executor=None,
        run_policy=run_policy,
    )
    service = _preparation(app, legacy=legacy)

    assert service.prepare_new() is legacy
    assert constructions == []


async def test_closed_replay_performs_no_provider_construction(tmp_path: Path) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    orchestrator = session_app.orchestrator
    assert orchestrator.preparation is not None
    orchestrator.id_source = _SameMessageId()

    first = await _dispatch(orchestrator, "hi")
    assert first[-1].payload["finish_reason"] == "stop"
    after_first = len(constructions)
    assert after_first == 1  # exactly one prepare_new provider construction

    replay = await _dispatch(orchestrator, "hi")
    assert replay[-1].payload["finish_reason"] == "stop"
    assert replay[0].type == "turn.started"
    # Closed replay performs no provider construction and no new AgentRun.
    assert len(constructions) == after_first
    runs = session_app.persistence.journal.list_session_agent_runs(
        session_app.persistence.workspace_id, session_app.session.session_id
    )
    assert len(runs) == 1


async def test_prepare_failure_is_emitted_as_ordered_error_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)

    def fail_prepare():
        raise ValueError("Provider 凭据不可用")

    monkeypatch.setattr(session_app.orchestrator.preparation, "prepare_new", fail_prepare)
    events = await _dispatch(session_app.orchestrator, "prepare me")

    assert [event.type for event in events] == ["turn.started", "error", "turn.completed"]
    assert events[1].payload["stop_code"] == "internal"
    assert events[-1].payload["finish_reason"] == "error"


async def test_rehydrate_failure_emits_events_and_closes_active_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    accepted = session_app.persistence.submit_user(
        session_app.session,
        "resume me",
        "cmsg-resume-failure",
        turn_id="turn_resume_failure",
        agent_run_id="arun_resume_failure",
        tools=(),
    )
    assert accepted.kind == "accepted"

    def fail_rehydrate(_snapshot):
        raise ProviderUnavailableError("frozen credential is unavailable")

    monkeypatch.setattr(session_app.orchestrator.preparation, "rehydrate", fail_rehydrate)
    events = [event async for event in session_app.orchestrator.resume_recovery()]

    assert [event.type for event in events] == ["turn.started", "error", "turn.completed"]
    assert events[0].turn_id == "turn_resume_failure"
    assert not session_app.session.log.has_active_turn


def test_open_receipt_probe_classifies_recovery(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    session = session_app.session
    persistence = session_app.persistence

    accepted = persistence.submit_user(
        session,
        "hello",
        "cmsg-open",
        turn_id="turn_open",
        agent_run_id="arun_open",
        tools=(),
    )
    assert accepted.kind == "accepted"
    probe = session.durable_runtime.probe(session, "hello", "cmsg-open")
    assert probe.kind == "recovery"
    assert probe.turn_id == "turn_open"


async def test_prepared_submit_freezes_provider_evidence_in_snapshot(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    orchestrator = session_app.orchestrator

    prepared = orchestrator.preparation.prepare_new()
    events = await _dispatch_prepared(orchestrator, "run me", prepared)
    assert events[-1].payload["finish_reason"] == "stop"

    snapshot = session_app.persistence.get_open_run_snapshot()
    assert snapshot is not None
    assert snapshot.provider_runtime == prepared.spec.provider_runtime
    assert snapshot.run_policy == prepared.spec.run_policy
    assert snapshot.run_policy_digest == prepared.spec.run_policy_digest
    assert snapshot.tool_schema_digest == prepared.spec.tool_schema_digest
    payload = canonical_json_bytes(snapshot.model_dump(mode="json"))
    assert len(payload) < AGENT_RUN_SNAPSHOT_MAX_BYTES


async def test_new_submit_omits_and_rehydration_ignores_legacy_completion_evidence(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    prepared = session_app.orchestrator.preparation.prepare_new()
    accepted = session_app.persistence.submit_user(
        session_app.session,
        "fix answer.txt and run pytest tests",
        "cmsg_completion_freeze",
        turn_id="turn_1",
        agent_run_id="arun_1",
        prepared_spec=prepared.spec,
    )
    snapshot = session_app.persistence.get_open_run_snapshot()
    assert accepted.kind == "accepted"
    assert snapshot is not None
    assert snapshot.outcome_contract is None
    assert snapshot.workspace_baseline is None

    contract = OutcomeContract(
        mode="change",
        target_paths=("answer.txt",),
        required_validations=(ValidationRequirement(validator_kind="pytest", scope="tests"),),
    )
    baseline = WorkspaceBaseline(
        status=WorkspaceBaselineStatus.COMPLETE,
        entries=(),
        repository_state="filesystem",
    )
    legacy_snapshot = snapshot.model_copy(
        update={"outcome_contract": contract, "workspace_baseline": baseline}
    )

    hydrated = _preparation(app).rehydrate(legacy_snapshot)
    try:
        assert not hasattr(hydrated.spec, "outcome_contract")
        assert not hasattr(hydrated.spec, "workspace_baseline")
    finally:
        hydrated.close()
        prepared.close()


async def test_rehydrate_rebuilds_exact_provider_from_frozen_evidence(tmp_path: Path) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    orchestrator = session_app.orchestrator
    prepared = orchestrator.preparation.prepare_new()
    await _dispatch_prepared(orchestrator, "freeze", prepared)
    snapshot = session_app.persistence.get_open_run_snapshot()
    assert snapshot is not None

    constructions.clear()
    hydrated = _preparation(app, constructions=constructions).rehydrate(snapshot)

    assert hydrated.model == snapshot.provider_runtime.model
    assert len(constructions) == 1
    provider_config, credential = constructions[0]
    assert provider_config.adapter == "fake-adapter"
    assert provider_config.base_url == "https://api.example.test/v1"
    assert provider_config.models["m1"].api_model_id == "api-m1"
    assert credential == "topsecret-value"
    assert hydrated.run_policy == snapshot.run_policy
    assert hydrated.spec.run_policy_digest == snapshot.run_policy_digest


async def test_rehydrate_does_not_consult_current_active_model(tmp_path: Path) -> None:
    app = _app(tmp_path)
    constructions: list = []
    _register_fake_adapter(app, constructions=constructions)
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    prepared = session_app.orchestrator.preparation.prepare_new()
    await _dispatch_prepared(session_app.orchestrator, "freeze", prepared)
    snapshot = session_app.persistence.get_open_run_snapshot()
    assert snapshot is not None

    # The current configuration moves to model m2 after the run was created.
    _configure_active(app, model_id="m2")
    constructions.clear()
    hydrated = _preparation(app, constructions=constructions).rehydrate(snapshot)

    assert hydrated.model == ModelRef(provider_id="fake-provider", model_id="m1")
    assert hydrated.spec.provider_runtime.api_model_id == "api-m1"


def test_rehydrate_unavailable_frozen_credential_has_no_fallback(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    service = _preparation(app)
    prepared = service.prepare_new()
    model = prepared.model
    run_policy = prepared.spec.run_policy

    missing_ref = CredentialRef(ref="provider:fake-provider:missing", version=1)
    snapshot = AgentRunSnapshot(
        profile=None,
        model=model,
        provider_id=model.provider_id,
        source_revisions=(),
        run_policy_digest=prepared.spec.run_policy_digest,
        tool_schema_digest=prepared.spec.tool_schema_digest,
        permission_profile_digest=prepared.spec.tool_schema_digest,
        runtime_instance_id="inst-1",
        provider_runtime=prepared.spec.provider_runtime.model_copy(
            update={"credential_ref": missing_ref}
        ),
        run_policy=run_policy,
    )
    with_legacy = _preparation(app, legacy=prepared)

    # Even with a legacy runtime present, a frozen CredentialRef that cannot be
    # resolved makes the run unavailable: no silent fallback.
    with pytest.raises(ProviderUnavailableError):
        with_legacy.rehydrate(snapshot)


def test_rehydrate_legacy_snapshot_uses_legacy_runtime(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    legacy = _preparation(app).prepare_new()
    with_legacy = _preparation(app, legacy=legacy)

    old_snapshot = AgentRunSnapshot(
        profile=None,
        model=legacy.model,
        provider_id=legacy.model.provider_id,
        source_revisions=(),
        run_policy_digest=legacy.spec.run_policy_digest,
        tool_schema_digest=legacy.spec.tool_schema_digest,
        permission_profile_digest=legacy.spec.tool_schema_digest,
        runtime_instance_id="inst-1",
    )
    assert old_snapshot.provider_runtime is None
    assert with_legacy.rehydrate(old_snapshot) is legacy

    without_legacy = _preparation(app)
    with pytest.raises(AgentRunPreparationError):
        without_legacy.rehydrate(old_snapshot)


def test_rehydrate_rejects_tool_schema_drift(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    service = _preparation(app)
    prepared = service.prepare_new()

    drifted_tool = ToolDefinition(
        function=ToolFunction(name="echo", description="echo tool", parameters={})
    )
    snapshot = AgentRunSnapshot(
        profile=None,
        model=prepared.model,
        provider_id=prepared.model.provider_id,
        source_revisions=(),
        run_policy_digest=prepared.spec.run_policy_digest,
        tool_schema_digest=tool_schema_digest((drifted_tool,)),
        permission_profile_digest=prepared.spec.tool_schema_digest,
        runtime_instance_id="inst-1",
        provider_runtime=prepared.spec.provider_runtime,
        run_policy=prepared.spec.run_policy,
    )
    drift_service = _preparation(app)
    with pytest.raises(AgentRunPreparationError):
        drift_service.rehydrate(snapshot)


def test_duplicate_concurrent_submit_persists_one_run(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    persistence = session_app.persistence
    session = session_app.session
    coordinator: TurnSubmissionCoordinator = persistence.turn_submission
    workspace_id = persistence.workspace_id
    digest = request_digest("race")

    # One already-admitted foreground run establishes a real turn for the
    # racing receipt (the journal validates receipt/turn/session ownership).
    accepted = coordinator.submit_user(
        session,
        "first",
        "cmsg-first",
        turn_id="turn_first",
        agent_run_id="arun_first",
        tools=(),
        writer=persistence.writer,
    )
    assert accepted.kind == "accepted"
    session.finish_turn(FinishReason.ERROR)  # close the in-memory active turn

    # The racing loser inserts a matching receipt between the probe and the
    # admission transaction; the in-txn recheck wins and creates no AgentRun.
    def racing_preference_loader() -> PreferenceRunSources:
        persistence.journal.put_receipt(
            workspace_id,
            TurnSubmitReceipt(
                session_id=session.session_id,
                client_message_id="cmsg-race",
                request_digest=digest,
                disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
                turn_id="turn_first",
                command_id="cmd_winner",
            ),
        )
        return None

    original_loader = coordinator.preference_loader
    coordinator.preference_loader = racing_preference_loader
    try:
        outcome = coordinator.submit_user(
            session,
            "race",
            "cmsg-race",
            turn_id="turn_loser",
            agent_run_id="arun_loser",
            tools=(),
            writer=persistence.writer,
        )
    finally:
        coordinator.preference_loader = original_loader

    assert outcome.kind == "recovery"
    assert outcome.turn_id == "turn_first"
    runs = persistence.journal.list_session_agent_runs(workspace_id, session.session_id)
    assert len(runs) == 1
    assert runs[0].agent_run_id == "arun_first"


async def test_run_task_closes_unused_runtime_on_concurrent_loser(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    project = tmp_path / "project"
    project.mkdir()
    session_app = _open_session_application(app, project)
    persistence = session_app.persistence
    session = session_app.session
    coordinator: TurnSubmissionCoordinator = persistence.turn_submission
    closed: list = []

    def racing_preference_loader():
        persistence.journal.put_receipt(
            persistence.workspace_id,
            TurnSubmitReceipt(
                session_id=session.session_id,
                client_message_id="cmsg-race2",
                request_digest=request_digest("race"),
                disposition=TurnSubmitDisposition.ACCEPTED_CLOSED,
                turn_id=None,
                command_id="cmd_winner",
            ),
        )
        return None

    prepared = session_app.orchestrator.preparation.prepare_new()
    wrapped = SimpleNamespace(
        spec=prepared.spec,
        provider=prepared.provider,
        model=prepared.model,
        context_builder=prepared.context_builder,
        tool_executor=prepared.tool_executor,
        run_policy=prepared.run_policy,
        close=lambda: closed.append(True),
    )
    original_loader = coordinator.preference_loader
    coordinator.preference_loader = racing_preference_loader
    try:
        events = [
            event
            async for event in session_app.orchestrator.runtime.loop.run_task(
                session, "race", client_message_id="cmsg-race2", prepared=wrapped
            )
        ]
    finally:
        coordinator.preference_loader = original_loader
    assert events[-1].payload["finish_reason"] == "stop"  # closed replay outcome
    assert closed == [True]


def test_old_snapshot_decodes_without_stage6_fields(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _register_fake_adapter(app, constructions=[])
    _configure_active(app)
    prepared = _preparation(app).prepare_new()

    new_json = AgentRunSnapshot(
        profile=None,
        model=prepared.model,
        provider_id=prepared.model.provider_id,
        source_revisions=(),
        run_policy_digest=prepared.spec.run_policy_digest,
        tool_schema_digest=prepared.spec.tool_schema_digest,
        permission_profile_digest=prepared.spec.tool_schema_digest,
        runtime_instance_id="inst-1",
        provider_runtime=prepared.spec.provider_runtime,
        run_policy=prepared.spec.run_policy,
    ).model_dump(mode="json")
    old_json = {
        key: value
        for key, value in new_json.items()
        if key not in ("provider_runtime", "run_policy")
    }

    decoded = AgentRunSnapshot.model_validate(old_json)
    assert decoded.provider_runtime is None
    assert decoded.run_policy is None
    assert decoded.model == prepared.model


async def _dispatch_prepared(orchestrator, text: str, prepared):
    events = [
        event
        async for event in orchestrator.runtime.run_turn(
            orchestrator.session, text, client_message_id="cmsg-prepared", prepared=prepared
        )
    ]
    return events
