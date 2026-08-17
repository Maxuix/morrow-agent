"""Bundled developer policy, exact-model resolution, and Adapter metadata."""

from __future__ import annotations

from importlib import resources

import pytest
from pydantic import ValidationError

from morrow.adapters.registry import AdapterRegistry
from morrow.core.models import ModelRef
from morrow.runtime.policy import (
    AgentPolicy,
    PolicyLoadError,
    load_agent_policy,
    parse_agent_policy,
)


def _values(**updates):
    values = load_agent_policy().model_dump()
    values.update(updates)
    return values


def test_bundled_policy_has_approved_defaults_and_empty_exact_model_table():
    policy = load_agent_policy()
    assert policy.model_dump(exclude={"model_safe_request_chars"}) == {
        "max_tool_rounds": 30,
        "max_model_attempts": 40,
        "max_tool_calls": 128,
        "max_tool_calls_per_cycle": 32,
        "max_run_seconds": 1800.0,
        "tool_timeout_seconds": 120.0,
        "model_retry_limit": 1,
        "requested_context_chars": 800000,
        "unknown_model_fallback_chars": 160000,
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
    with pytest.raises(ValidationError):
        policy.max_tool_rounds = 1


def test_unknown_model_uses_fallback_and_derived_ratio_limits():
    run = load_agent_policy().resolve(
        ModelRef(provider_id="unknown", model_id="model"),
        tool_protocol="openai_function",
        multiple_tool_calls=True,
    )
    assert run.effective_request_chars == 160000
    assert run.effective_result_limit == 16000
    assert run.effective_cycle_limit == 56000
    assert run.provider_tool_support.safe_request_chars is None


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
    assert prefix_only.effective_request_chars == 160000


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


def test_missing_and_malformed_policy_fail_clearly():
    with pytest.raises(PolicyLoadError, match="missing"):
        load_agent_policy(resource_name="missing-policy.toml")
    with pytest.raises(PolicyLoadError, match="invalid"):
        parse_agent_policy(b"not = [valid")


def test_policy_resource_is_packaged_and_adapter_metadata_is_explicit():
    resource = resources.files("morrow.resources").joinpath("agent-policy.toml")
    assert resource.is_file()
    assert (
        resource.read_bytes()
        == resources.files("morrow.resources").joinpath("agent-policy.toml").read_bytes()
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
