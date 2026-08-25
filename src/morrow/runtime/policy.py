"""Packaged runtime defaults, safe user overlays, and exact-model run limits."""

from __future__ import annotations

import tomllib
from enum import StrEnum
from importlib import resources
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from morrow.core.models import ModelRef, ProtocolModel, ProviderToolSupport, RunPolicy, ToolEffect
from morrow.core.runtime_policy import (
    AGENT_MAX_LOOP_PATTERN_CYCLES,
    AGENT_MAX_LOOP_REPEAT,
    AGENT_MAX_MODEL_ATTEMPTS,
    AGENT_MAX_MODEL_RETRIES,
    AGENT_MAX_REQUEST_CHARS,
    AGENT_MAX_RUN_SECONDS,
    AGENT_MAX_TOOL_CALLS,
    AGENT_MAX_TOOL_CALLS_PER_CYCLE,
    AGENT_MAX_TOOL_CYCLE_CHARS,
    AGENT_MAX_TOOL_RESULT_CHARS,
    AGENT_MAX_TOOL_ROUNDS,
    AGENT_MAX_TOOL_TIMEOUT_SECONDS,
    AGENT_MAX_VALIDATION_ERRORS,
    REVIEW_MAX_LEASE_SECONDS,
    REVIEW_MAX_RETRY_BACKOFF_SECONDS,
    REVIEW_MAX_TIMEOUT_SECONDS,
    REVIEW_RETRY_BACKOFF_COUNT,
    RUNTIME_POLICY_SCHEMA_VERSION,
    RuntimePolicyOverrides,
    finite_number,
)

__all__ = [
    "AgentPolicy",
    "PolicyLoadError",
    "ProviderToolSupport",
    "ReviewPolicy",
    "RunPolicy",
    "RuntimePolicy",
    "ToolApproval",
    "ToolExecutionPolicy",
    "load_agent_policy",
    "load_runtime_policy",
    "parse_agent_policy",
    "parse_runtime_policy",
    "resolve_runtime_policy",
]


class PolicyLoadError(RuntimeError):
    """The packaged runtime policy is missing or invalid."""


class ToolApproval(StrEnum):
    """Local approval requirement; it is not part of the Provider protocol."""

    NEVER = "never"
    REQUIRED = "required"


class ToolExecutionPolicy(ProtocolModel):
    """Immutable local effect and approval metadata for one registered tool."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect: ToolEffect = ToolEffect.NONE
    approval: ToolApproval = ToolApproval.NEVER


class AgentPolicy(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_tool_rounds: int = Field(gt=0, le=AGENT_MAX_TOOL_ROUNDS)
    max_model_attempts: int = Field(gt=0, le=AGENT_MAX_MODEL_ATTEMPTS)
    max_tool_calls: int = Field(gt=0, le=AGENT_MAX_TOOL_CALLS)
    max_tool_calls_per_cycle: int = Field(gt=0, le=AGENT_MAX_TOOL_CALLS_PER_CYCLE)
    max_run_seconds: float = Field(gt=0, le=AGENT_MAX_RUN_SECONDS)
    tool_timeout_seconds: float = Field(gt=0, le=AGENT_MAX_TOOL_TIMEOUT_SECONDS)
    model_retry_limit: int = Field(ge=0, le=AGENT_MAX_MODEL_RETRIES)
    requested_context_chars: int = Field(gt=0, le=AGENT_MAX_REQUEST_CHARS)
    unknown_model_fallback_chars: int = Field(gt=0, le=AGENT_MAX_REQUEST_CHARS)
    max_tool_result_chars: int = Field(gt=0, le=AGENT_MAX_TOOL_RESULT_CHARS)
    max_tool_result_request_ratio: float = Field(gt=0, le=1)
    max_tool_cycle_chars: int = Field(gt=0, le=AGENT_MAX_TOOL_CYCLE_CHARS)
    max_tool_cycle_request_ratio: float = Field(gt=0, le=1)
    max_validation_errors: int = Field(gt=0, le=AGENT_MAX_VALIDATION_ERRORS)
    loop_detection_enabled: bool
    loop_repeat_limit: int = Field(ge=2, le=AGENT_MAX_LOOP_REPEAT)
    loop_max_pattern_cycles: int = Field(gt=0, le=AGENT_MAX_LOOP_PATTERN_CYCLES)
    model_safe_request_chars: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_combinations(self) -> AgentPolicy:
        if self.max_tool_calls_per_cycle > self.max_tool_calls:
            raise ValueError("max_tool_calls_per_cycle cannot exceed max_tool_calls")
        if self.tool_timeout_seconds > self.max_run_seconds:
            raise ValueError("tool_timeout_seconds cannot exceed max_run_seconds")
        if self.model_retry_limit >= self.max_model_attempts:
            raise ValueError("model_retry_limit must be below max_model_attempts")
        if self.loop_repeat_limit * self.loop_max_pattern_cycles > self.max_tool_rounds:
            raise ValueError("longest repeated pattern must fit within max_tool_rounds")
        if any(not key.strip() or "/" not in key for key in self.model_safe_request_chars):
            raise ValueError("model safe-size keys must be exact provider_id/model_id values")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
            or value > AGENT_MAX_REQUEST_CHARS
            for value in self.model_safe_request_chars.values()
        ):
            raise ValueError("model safe sizes are outside the safety boundary")
        return self

    def resolve(
        self,
        model: ModelRef,
        *,
        tool_protocol: Literal["none", "openai_function"],
        multiple_tool_calls: bool,
    ) -> RunPolicy:
        exact_key = f"{model.provider_id}/{model.model_id}"
        safe = self.model_safe_request_chars.get(exact_key)
        request_limit = min(
            self.requested_context_chars,
            safe if safe is not None else self.unknown_model_fallback_chars,
        )
        result_limit = min(
            self.max_tool_result_chars,
            int(request_limit * self.max_tool_result_request_ratio),
        )
        cycle_limit = min(
            self.max_tool_cycle_chars,
            int(request_limit * self.max_tool_cycle_request_ratio),
        )
        return RunPolicy(
            max_tool_rounds=self.max_tool_rounds,
            max_model_attempts=self.max_model_attempts,
            max_tool_calls=self.max_tool_calls,
            max_tool_calls_per_cycle=self.max_tool_calls_per_cycle,
            max_run_seconds=self.max_run_seconds,
            tool_timeout_seconds=self.tool_timeout_seconds,
            model_retry_limit=self.model_retry_limit,
            effective_request_chars=request_limit,
            effective_result_limit=result_limit,
            effective_cycle_limit=cycle_limit,
            max_validation_errors=self.max_validation_errors,
            loop_detection_enabled=self.loop_detection_enabled,
            loop_repeat_limit=self.loop_repeat_limit,
            loop_max_pattern_cycles=self.loop_max_pattern_cycles,
            provider_tool_support=ProviderToolSupport(
                tool_protocol=tool_protocol,
                multiple_tool_calls=multiple_tool_calls,
                safe_request_chars=safe,
            ),
        )


class ReviewPolicy(ProtocolModel):
    """Process-wide Review tuning below fixed retry and payload invariants."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    learning_timeout_seconds: float = Field(gt=0, le=REVIEW_MAX_TIMEOUT_SECONDS)
    learning_lease_seconds: int = Field(gt=0, le=REVIEW_MAX_LEASE_SECONDS)
    preference_timeout_seconds: float = Field(gt=0, le=REVIEW_MAX_TIMEOUT_SECONDS)
    preference_lease_seconds: int = Field(gt=0, le=REVIEW_MAX_LEASE_SECONDS)
    preference_retry_backoff_seconds: tuple[int, ...]

    @field_validator("learning_timeout_seconds", "preference_timeout_seconds")
    @classmethod
    def finite_timeouts(cls, value: float) -> float:
        return finite_number(value, label="Review timeout")

    @field_validator("preference_retry_backoff_seconds", mode="before")
    @classmethod
    def tuple_retry_backoff(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("preference_retry_backoff_seconds")
    @classmethod
    def valid_retry_backoff(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if (
            len(value) != REVIEW_RETRY_BACKOFF_COUNT
            or any(isinstance(item, bool) or item <= 0 for item in value)
            or any(item > REVIEW_MAX_RETRY_BACKOFF_SECONDS for item in value)
            or tuple(sorted(value)) != value
        ):
            raise ValueError("Preference Review retry backoff is outside the safety boundary")
        return value

    @model_validator(mode="after")
    def leases_outlive_attempts(self) -> ReviewPolicy:
        if self.learning_lease_seconds <= self.learning_timeout_seconds:
            raise ValueError("Learning Review lease must outlive its timeout")
        if self.preference_lease_seconds <= self.preference_timeout_seconds:
            raise ValueError("Preference Review lease must outlive its timeout")
        return self


class RuntimePolicy(ProtocolModel):
    """Versioned packaged defaults after any validated user overlay is applied."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[RUNTIME_POLICY_SCHEMA_VERSION] = RUNTIME_POLICY_SCHEMA_VERSION
    agent_run: AgentPolicy
    reviews: ReviewPolicy


def resolve_runtime_policy(
    defaults: RuntimePolicy, overrides: RuntimePolicyOverrides | None
) -> RuntimePolicy:
    if overrides is None:
        return defaults
    payload = defaults.model_dump(mode="python")
    if overrides.agent_run is not None:
        payload["agent_run"].update(
            overrides.agent_run.model_dump(mode="python", exclude_none=True)
        )
    if overrides.reviews is not None:
        payload["reviews"].update(overrides.reviews.model_dump(mode="python", exclude_none=True))
    try:
        return RuntimePolicy.model_validate(payload, strict=True)
    except Exception as exc:
        raise PolicyLoadError("user runtime policy override is invalid") from exc


def parse_runtime_policy(
    data: bytes, *, overrides: RuntimePolicyOverrides | None = None
) -> RuntimePolicy:
    try:
        payload = tomllib.loads(data.decode("utf-8"))
        defaults = RuntimePolicy.model_validate(payload, strict=True)
    except Exception as exc:
        raise PolicyLoadError("packaged runtime policy is invalid") from exc
    return resolve_runtime_policy(defaults, overrides)


def parse_agent_policy(data: bytes) -> AgentPolicy:
    """Compatibility entrypoint returning the AgentRun section of runtime policy."""

    return parse_runtime_policy(data).agent_run


def load_runtime_policy(
    *,
    overrides: RuntimePolicyOverrides | None = None,
    package: str = "morrow.resources",
    resource_name: str = "runtime-policy.toml",
) -> RuntimePolicy:
    try:
        data = resources.files(package).joinpath(resource_name).read_bytes()
    except Exception as exc:
        raise PolicyLoadError("packaged runtime policy resource is missing") from exc
    return parse_runtime_policy(data, overrides=overrides)


def load_agent_policy(
    *, package: str = "morrow.resources", resource_name: str = "runtime-policy.toml"
) -> AgentPolicy:
    """Compatibility entrypoint returning packaged AgentRun defaults."""

    return load_runtime_policy(package=package, resource_name=resource_name).agent_run
