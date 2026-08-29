"""Bundled developer policy, exact-model resolution, and Adapter metadata."""

from __future__ import annotations

from importlib import resources

import pytest
import yaml
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.registry import AdapterRegistry
from morrow.adapters.state.operational import OperationalStore
from morrow.bootstrap import build_application, build_operational_api, build_operational_services
from morrow.core.models import (
    ModelRef,
    Preferences,
    ProviderToolSupport,
    RunPolicy,
    StateLoadStatus,
)
from morrow.core.runtime_policy import RuntimePolicyOverrides
from morrow.runtime.policy import (
    AgentPolicy,
    PolicyLoadError,
    load_agent_policy,
    load_runtime_policy,
    parse_agent_policy,
)


def _values(**updates):
    values = load_agent_policy().model_dump()
    values.update(updates)
    return values


def test_bundled_runtime_policy_has_approved_defaults_and_empty_exact_model_table():
    runtime = load_runtime_policy()
    policy = runtime.agent_run
    assert runtime.schema_version == 1
    assert policy.model_dump(exclude={"model_safe_request_chars"}) == {
        "tool_timeout_seconds": 120.0,
        "requested_context_chars": 800000,
        "unknown_model_fallback_chars": 262144,
        "max_tool_result_chars": 64000,
        "max_validation_errors": 3,
    }
    assert policy.model_safe_request_chars == {}
    assert runtime.reviews.model_dump() == {
        "preference_timeout_seconds": 300.0,
        "preference_lease_seconds": 360,
        "preference_retry_backoff_seconds": (5, 15),
    }
    with pytest.raises(ValidationError):
        policy.tool_timeout_seconds = 1


def test_unknown_model_uses_fallback_and_v2_result_limit():
    run = load_agent_policy().resolve(
        ModelRef(provider_id="unknown", model_id="model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=None,
    )
    assert run.effective_request_chars == 262144
    assert run.effective_result_limit == 51200
    assert run.provider_tool_support.safe_request_chars is None


def test_unknown_window_keeps_conservative_character_fallback():
    run = load_agent_policy().resolve(
        ModelRef(provider_id="unknown", model_id="model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=None,
    )

    assert run.policy_schema_version == 2
    assert run.context_window_tokens is None
    assert run.effective_request_chars == 262144


def test_policy_reserves_known_maximum_output_capacity():
    run = load_agent_policy().resolve(
        ModelRef(provider_id="vendor", model_id="large-context"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=1_000_000,
        max_output_tokens=384_000,
    )

    assert run.context_window_tokens == 1_000_000
    assert run.reserve_tokens == 384_000


def test_policy_rejects_output_capacity_that_consumes_the_window():
    with pytest.raises(ValueError, match="output reserve"):
        load_agent_policy().resolve(
            ModelRef(provider_id="vendor", model_id="invalid-window"),
            tool_protocol="openai_function",
            multiple_tool_calls=True,
            context_window_tokens=100_000,
            max_output_tokens=100_000,
        )


@pytest.mark.parametrize(
    ("safe", "expected_request"),
    [(100000, 100000), (1000000, 800000)],
)
def test_exact_model_hit_is_used_without_prefix_guessing(safe, expected_request):
    policy = AgentPolicy.model_validate(
        _values(model_safe_request_chars={"vendor/exact-model": safe}), strict=True
    )
    exact = policy.resolve(
        ModelRef(provider_id="vendor", model_id="exact-model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=None,
    )
    prefix_only = policy.resolve(
        ModelRef(provider_id="vendor", model_id="exact-model-v2"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=None,
    )
    assert exact.effective_request_chars == expected_request
    assert exact.effective_result_limit == 51200
    assert exact.provider_tool_support.safe_request_chars == safe
    assert prefix_only.effective_request_chars == 262144


@pytest.mark.parametrize(
    "updates",
    [
        {"tool_timeout_seconds": 301.0},
        {"requested_context_chars": 4_000_001},
        {"max_tool_result_chars": 0},
        {"model_safe_request_chars": {"not-exact": 100}},
    ],
)
def test_policy_rejects_invalid_values_and_combinations(updates):
    with pytest.raises(ValidationError):
        AgentPolicy.model_validate(_values(**updates), strict=True)


def test_user_overlay_changes_only_declared_fields_and_revalidates_combinations():
    overrides = RuntimePolicyOverrides.model_validate(
        {
            "agent_run": {"tool_timeout_seconds": 180.0, "reserve_tokens": 20_000},
            "reviews": {
                "preference_retry_backoff_seconds": [10, 30],
            },
        },
        strict=True,
    )
    effective = load_runtime_policy(overrides=overrides)
    assert effective.agent_run.tool_timeout_seconds == 180.0
    assert effective.long_horizon.reserve_tokens == 20_000
    assert effective.agent_run.model_safe_request_chars == {}
    assert effective.reviews.preference_retry_backoff_seconds == (10, 30)
    assert effective.reviews.preference_timeout_seconds == 300.0

    invalid_combination = RuntimePolicyOverrides.model_validate(
        {
            "agent_run": {
                "retry_base_delay_seconds": 30.0,
                "max_provider_retry_delay_seconds": 10.0,
            }
        },
        strict=True,
    )
    with pytest.raises(PolicyLoadError, match="override"):
        load_runtime_policy(overrides=invalid_combination)


@pytest.mark.parametrize(
    "payload",
    [
        {"agent_run": {"loop_detection_enabled": False}},
        {"agent_run": {"model_safe_request_chars": {"vendor/model": 100}}},
        {"agent_run": {"max_tool_rounds": 30}},
        {"reviews": {"learning_timeout_seconds": 90.0}},
        {"reviews": {"preference_retry_backoff_seconds": [30, 10]}},
        {"unknown": {}},
    ],
)
def test_user_overlay_rejects_safety_owned_unknown_and_over_ceiling_values(payload):
    with pytest.raises(ValidationError):
        RuntimePolicyOverrides.model_validate(payload, strict=True)


def test_config_yaml_override_is_applied_and_preserved_by_unrelated_writes(tmp_path):
    state_root = tmp_path / "state"
    state_root.mkdir()
    config_path = state_root / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "revision": 0,
                "preferences": {"entries": []},
                "providers": {},
                "active_model": None,
                "runtime_policy": {
                    "agent_run": {"tool_timeout_seconds": 180.0},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    application = build_application(
        state_root=state_root,
        credentials=MemoryCredentialStore(),
    )
    assert application.runtime_policy.agent_run.tool_timeout_seconds == 180.0

    handle = OperationalStore(state_root).initialize()
    try:
        services = build_operational_services(
            application,
            "ws_policy",
            handle=handle,
            write=True,
        )
        api = build_operational_api(application, "ws_policy", services)
        assert api.learning_review_runner.timeout_seconds == 300.0
        assert api.learning_review_runner.lease_seconds == 360
        assert api.review_worker.runner.timeout_seconds == 300.0
        assert api.review_worker.lease_seconds == 360
        assert api.review_worker.retry_backoff_seconds == (5, 15)
    finally:
        handle.close()

    written = application.global_store.update(
        lambda value: value.model_copy(update={"preferences": Preferences(language="中文")})
    )
    assert written.status.value == "ok"
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["runtime_policy"]["agent_run"]["tool_timeout_seconds"] == 180.0


def test_v1_run_policy_snapshot_is_rejected():
    with pytest.raises(ValidationError):
        RunPolicy.model_validate(
            {
                "policy_schema_version": 1,
                "task_lifetime_mode": "bounded",
                "max_tool_rounds": 30,
                "provider_tool_support": ProviderToolSupport(
                    tool_protocol="openai_function",
                    multiple_tool_calls=True,
                ),
            },
            strict=True,
        )


def test_invalid_runtime_policy_makes_config_unavailable_instead_of_being_partially_applied(
    tmp_path,
):
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "config.yaml").write_text(
        "schema_version: 2\nrevision: 0\nruntime_policy:\n  reviews:\n"
        "    learning_timeout_seconds: 90\n",
        encoding="utf-8",
    )
    application = build_application(
        state_root=state_root,
        credentials=MemoryCredentialStore(),
    )
    assert application.global_store.load().status is StateLoadStatus.CORRUPT


def test_missing_and_malformed_policy_fail_clearly():
    with pytest.raises(PolicyLoadError, match="missing"):
        load_agent_policy(resource_name="missing-policy.toml")
    with pytest.raises(PolicyLoadError, match="invalid"):
        parse_agent_policy(b"not = [valid")


def test_policy_resource_is_packaged_and_adapter_metadata_is_explicit():
    resource = resources.files("morrow.resources").joinpath("runtime-policy.toml")
    assert resource.is_file()
    assert (
        resource.read_bytes()
        == resources.files("morrow.resources").joinpath("runtime-policy.toml").read_bytes()
    )

    registry = AdapterRegistry()
    registry.register(
        "adapter",
        lambda config, credential: None,
        tool_protocol="openai_function",
        multiple_tool_calls=True,
    )
    support = registry.tool_support("adapter")
    assert support.model_dump() == {
        "tool_protocol": "openai_function",
        "multiple_tool_calls": True,
        "safe_request_chars": None,
    }
