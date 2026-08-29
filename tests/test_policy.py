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
from morrow.core.models import ModelRef, Preferences, StateLoadStatus
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
        "max_tool_rounds": 30,
        "max_model_attempts": 40,
        "max_tool_calls": 128,
        "max_tool_calls_per_cycle": 32,
        "max_run_seconds": 1800.0,
        "tool_timeout_seconds": 120.0,
        "model_retry_limit": 3,
        "requested_context_chars": 800000,
        "unknown_model_fallback_chars": 262144,
        "max_tool_result_chars": 64000,
        "max_tool_result_request_ratio": 0.10,
        "max_tool_cycle_chars": 256000,
        "max_tool_cycle_request_ratio": 0.35,
        "max_validation_errors": 3,
        "loop_detection_enabled": True,
        "loop_repeat_limit": 3,
        "loop_max_pattern_cycles": 4,
    }
    assert policy.model_safe_request_chars == {}
    assert runtime.reviews.model_dump() == {
        "preference_timeout_seconds": 300.0,
        "preference_lease_seconds": 360,
        "preference_retry_backoff_seconds": (5, 15),
    }
    with pytest.raises(ValidationError):
        policy.max_tool_rounds = 1


def test_unknown_model_uses_fallback_and_derived_ratio_limits():
    run = load_agent_policy().resolve(
        ModelRef(provider_id="unknown", model_id="model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
    )
    assert run.effective_request_chars == 262144
    assert run.effective_result_limit == 26214
    assert run.effective_cycle_limit == 91750
    assert run.provider_tool_support.safe_request_chars is None


def test_long_horizon_unknown_window_keeps_conservative_character_fallback():
    run = load_agent_policy().resolve_long_horizon(
        ModelRef(provider_id="unknown", model_id="model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=None,
    )

    assert run.is_long_horizon is True
    assert run.context_window_tokens is None
    assert run.effective_request_chars == 262144


def test_long_horizon_reserves_known_maximum_output_capacity():
    run = load_agent_policy().resolve_long_horizon(
        ModelRef(provider_id="vendor", model_id="large-context"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=1_000_000,
        max_output_tokens=384_000,
    )

    assert run.context_window_tokens == 1_000_000
    assert run.reserve_tokens == 384_000


def test_long_horizon_rejects_output_capacity_that_consumes_the_window():
    with pytest.raises(ValueError, match="output reserve"):
        load_agent_policy().resolve_long_horizon(
            ModelRef(provider_id="vendor", model_id="invalid-window"),
            tool_protocol="openai_function",
            multiple_tool_calls=True,
            context_window_tokens=100_000,
            max_output_tokens=100_000,
        )


@pytest.mark.parametrize(
    ("safe", "expected_request", "expected_result", "expected_cycle"),
    [(100000, 100000, 10000, 35000), (1000000, 800000, 64000, 256000)],
)
def test_exact_model_hit_is_used_without_prefix_guessing(
    safe, expected_request, expected_result, expected_cycle
):
    policy = AgentPolicy.model_validate(
        _values(model_safe_request_chars={"vendor/exact-model": safe}), strict=True
    )
    exact = policy.resolve(
        ModelRef(provider_id="vendor", model_id="exact-model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
    )
    prefix_only = policy.resolve(
        ModelRef(provider_id="vendor", model_id="exact-model-v2"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
    )
    assert exact.effective_request_chars == expected_request
    assert exact.effective_result_limit == expected_result
    assert exact.effective_cycle_limit == expected_cycle
    assert exact.provider_tool_support.safe_request_chars == safe
    assert prefix_only.effective_request_chars == 262144


@pytest.mark.parametrize(
    "updates",
    [
        {"max_tool_rounds": 0},
        {"max_tool_calls_per_cycle": 129},
        {"tool_timeout_seconds": 1801.0},
        {"model_retry_limit": 40},
        {"max_tool_result_request_ratio": 0.0},
        {"max_tool_cycle_request_ratio": 1.1},
        {"loop_repeat_limit": 1},
        {"max_tool_rounds": 11, "loop_repeat_limit": 3, "loop_max_pattern_cycles": 4},
        {"model_safe_request_chars": {"not-exact": 100}},
    ],
)
def test_policy_rejects_invalid_values_and_combinations(updates):
    with pytest.raises(ValidationError):
        AgentPolicy.model_validate(_values(**updates), strict=True)


def test_user_overlay_changes_only_declared_fields_and_revalidates_combinations():
    overrides = RuntimePolicyOverrides.model_validate(
        {
            "agent_run": {"max_run_seconds": 2400.0, "tool_timeout_seconds": 180.0},
            "reviews": {
                "preference_retry_backoff_seconds": [10, 30],
            },
        },
        strict=True,
    )
    effective = load_runtime_policy(overrides=overrides)
    assert effective.agent_run.max_run_seconds == 2400.0
    assert effective.agent_run.tool_timeout_seconds == 180.0
    assert effective.agent_run.loop_detection_enabled is True
    assert effective.agent_run.model_safe_request_chars == {}
    assert effective.reviews.preference_retry_backoff_seconds == (10, 30)
    assert effective.reviews.preference_timeout_seconds == 300.0

    invalid_combination = RuntimePolicyOverrides.model_validate(
        {"agent_run": {"max_tool_calls": 16, "max_tool_calls_per_cycle": 32}}, strict=True
    )
    with pytest.raises(PolicyLoadError, match="override"):
        load_runtime_policy(overrides=invalid_combination)


@pytest.mark.parametrize(
    "payload",
    [
        {"agent_run": {"loop_detection_enabled": False}},
        {"agent_run": {"model_safe_request_chars": {"vendor/model": 100}}},
        {"agent_run": {"max_run_seconds": 3601.0}},
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
                    "agent_run": {"max_run_seconds": 2400.0},
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
    assert application.runtime_policy.agent_run.max_run_seconds == 2400.0

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
    assert persisted["runtime_policy"]["agent_run"]["max_run_seconds"] == 2400.0


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
