"""Packaged runtime defaults, safe user overlays, and exact-model run limits."""

from __future__ import annotations

import tomllib
from enum import StrEnum
from importlib import resources
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from morrow.core.models import ModelRef, ProtocolModel, ProviderToolSupport, RunPolicy, ToolEffect
from morrow.core.runtime_policy import (
    AGENT_MAX_CONTEXT_WINDOW_TOKENS,
    AGENT_MAX_GREP_LINE_CHARS,
    AGENT_MAX_KEEP_RECENT_TOKENS,
    AGENT_MAX_MODEL_RETRIES,
    AGENT_MAX_REQUEST_CHARS,
    AGENT_MAX_RESERVE_TOKENS,
    AGENT_MAX_RETRY_DELAY_SECONDS,
    AGENT_MAX_TOOL_RESULT_CHARS,
    AGENT_MAX_TOOL_TIMEOUT_SECONDS,
    AGENT_MAX_TRUNCATION_BYTES,
    AGENT_MAX_TRUNCATION_LINES,
    AGENT_MAX_VALIDATION_ERRORS,
    PI_DEFAULT_GREP_MAX_LINE_CHARS,
    PI_DEFAULT_KEEP_RECENT_TOKENS,
    PI_DEFAULT_MAX_PROVIDER_RETRY_DELAY_SECONDS,
    PI_DEFAULT_MAX_RETRIES,
    PI_DEFAULT_RESERVE_TOKENS,
    PI_DEFAULT_RETRY_BASE_DELAY_SECONDS,
    PI_DEFAULT_TOOL_MAX_BYTES,
    PI_DEFAULT_TOOL_MAX_LINES,
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
    "LongHorizonPolicySettings",
    "PolicyLoadError",
    "ProviderToolSupport",
    "ReviewPolicy",
    "RunPolicy",
    "RuntimePolicy",
    "ToolApproval",
    "ToolExecutionPolicy",
    "load_runtime_policy",
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


class LongHorizonPolicySettings(ProtocolModel):
    """Bounded v2 tuning that does not impose a task-lifetime ceiling."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    compaction_enabled: bool = True
    reserve_tokens: int = Field(
        default=PI_DEFAULT_RESERVE_TOKENS, gt=0, le=AGENT_MAX_RESERVE_TOKENS
    )
    keep_recent_tokens: int = Field(
        default=PI_DEFAULT_KEEP_RECENT_TOKENS, gt=0, le=AGENT_MAX_KEEP_RECENT_TOKENS
    )
    retry_enabled: bool = True
    max_retries: int = Field(default=PI_DEFAULT_MAX_RETRIES, ge=0, le=AGENT_MAX_MODEL_RETRIES)
    retry_base_delay_seconds: float = Field(
        default=PI_DEFAULT_RETRY_BASE_DELAY_SECONDS,
        gt=0,
        le=AGENT_MAX_RETRY_DELAY_SECONDS,
    )
    max_provider_retry_delay_seconds: float = Field(
        default=PI_DEFAULT_MAX_PROVIDER_RETRY_DELAY_SECONDS,
        gt=0,
        le=AGENT_MAX_RETRY_DELAY_SECONDS,
    )
    truncation_max_bytes: int = Field(
        default=PI_DEFAULT_TOOL_MAX_BYTES, gt=0, le=AGENT_MAX_TRUNCATION_BYTES
    )
    truncation_max_lines: int = Field(
        default=PI_DEFAULT_TOOL_MAX_LINES, gt=0, le=AGENT_MAX_TRUNCATION_LINES
    )
    grep_max_line_chars: int = Field(
        default=PI_DEFAULT_GREP_MAX_LINE_CHARS, gt=0, le=AGENT_MAX_GREP_LINE_CHARS
    )

    @field_validator("retry_base_delay_seconds", "max_provider_retry_delay_seconds", mode="after")
    @classmethod
    def finite_delays(cls, value: float) -> float:
        return finite_number(value, label="retry delay")

    @model_validator(mode="after")
    def valid_retry_window(self) -> LongHorizonPolicySettings:
        if self.max_provider_retry_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError("provider retry cap must cover the base retry delay")
        return self


class AgentPolicy(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tool_timeout_seconds: float = Field(gt=0, le=AGENT_MAX_TOOL_TIMEOUT_SECONDS)
    requested_context_chars: int = Field(gt=0, le=AGENT_MAX_REQUEST_CHARS)
    unknown_model_fallback_chars: int = Field(gt=0, le=AGENT_MAX_REQUEST_CHARS)
    max_tool_result_chars: int = Field(gt=0, le=AGENT_MAX_TOOL_RESULT_CHARS)
    max_validation_errors: int = Field(gt=0, le=AGENT_MAX_VALIDATION_ERRORS)
    model_safe_request_chars: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_combinations(self) -> AgentPolicy:
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
        context_window_tokens: int | None,
        max_output_tokens: int | None = None,
        settings: LongHorizonPolicySettings | None = None,
        host_stop_source: Literal["none", "provided"] = "none",
    ) -> RunPolicy:
        """Resolve the v2 policy with exact token accounting or a character fallback."""
        if context_window_tokens is not None and (
            isinstance(context_window_tokens, bool)
            or context_window_tokens <= 0
            or context_window_tokens > AGENT_MAX_CONTEXT_WINDOW_TOKENS
        ):
            raise ValueError("exact model context window is outside the supported range")
        if max_output_tokens is not None and (
            isinstance(max_output_tokens, bool)
            or max_output_tokens <= 0
            or max_output_tokens > AGENT_MAX_RESERVE_TOKENS
        ):
            raise ValueError("exact model maximum output is outside the supported range")
        selected = settings or LongHorizonPolicySettings()
        reserve_tokens = max(selected.reserve_tokens, max_output_tokens or 0)
        if context_window_tokens is not None and reserve_tokens >= context_window_tokens:
            raise ValueError("model output reserve must be below the context window")
        exact_key = f"{model.provider_id}/{model.model_id}"
        safe = self.model_safe_request_chars.get(exact_key)
        # When the exact token window is unavailable, this remains an explicit conservative
        # request boundary. It is never converted into or reported as a model token window.
        request_limit = min(
            self.requested_context_chars,
            safe if safe is not None else self.unknown_model_fallback_chars,
        )
        result_limit = min(self.max_tool_result_chars, selected.truncation_max_bytes)
        return RunPolicy(
            policy_schema_version=2,
            tool_timeout_seconds=self.tool_timeout_seconds,
            effective_request_chars=request_limit,
            effective_result_limit=max(1, result_limit),
            max_validation_errors=self.max_validation_errors,
            compaction_enabled=selected.compaction_enabled,
            context_window_tokens=context_window_tokens,
            reserve_tokens=reserve_tokens,
            keep_recent_tokens=selected.keep_recent_tokens,
            retry_enabled=selected.retry_enabled,
            max_retries=selected.max_retries,
            retry_base_delay_seconds=selected.retry_base_delay_seconds,
            max_provider_retry_delay_seconds=selected.max_provider_retry_delay_seconds,
            truncation_max_bytes=selected.truncation_max_bytes,
            truncation_max_lines=selected.truncation_max_lines,
            grep_max_line_chars=selected.grep_max_line_chars,
            host_stop_source=host_stop_source,
            provider_tool_support=ProviderToolSupport(
                tool_protocol=tool_protocol,
                multiple_tool_calls=multiple_tool_calls,
                safe_request_chars=safe,
            ),
        )


class ReviewPolicy(ProtocolModel):
    """Process-wide Review tuning below fixed retry and payload invariants."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    preference_timeout_seconds: float = Field(gt=0, le=REVIEW_MAX_TIMEOUT_SECONDS)
    preference_lease_seconds: int = Field(gt=0, le=REVIEW_MAX_LEASE_SECONDS)
    preference_retry_backoff_seconds: tuple[int, ...]

    @field_validator("preference_timeout_seconds")
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
        if self.preference_lease_seconds <= self.preference_timeout_seconds:
            raise ValueError("Preference Review lease must outlive its timeout")
        return self


class RuntimePolicy(ProtocolModel):
    """Versioned packaged defaults after any validated user overlay is applied."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[RUNTIME_POLICY_SCHEMA_VERSION] = RUNTIME_POLICY_SCHEMA_VERSION
    agent_run: AgentPolicy
    long_horizon: LongHorizonPolicySettings = Field(default_factory=LongHorizonPolicySettings)
    reviews: ReviewPolicy


def resolve_runtime_policy(
    defaults: RuntimePolicy, overrides: RuntimePolicyOverrides | None
) -> RuntimePolicy:
    if overrides is None:
        return defaults
    payload = defaults.model_dump(mode="python")
    if overrides.agent_run is not None:
        values = overrides.agent_run.model_dump(mode="python", exclude_none=True)
        v2_names = set(LongHorizonPolicySettings.model_fields)
        payload["agent_run"].update(
            {name: value for name, value in values.items() if name not in v2_names}
        )
        payload["long_horizon"].update(
            {name: value for name, value in values.items() if name in v2_names}
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
