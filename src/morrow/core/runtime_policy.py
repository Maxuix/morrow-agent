"""Strict user overrides and code-owned safety ceilings for runtime policy."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

RUNTIME_POLICY_SCHEMA_VERSION = 1

# These are safety ceilings, not product defaults. They deliberately remain in code so neither a
# packaged resource nor user-owned YAML can widen the bounded runtime beyond reviewed limits.
AGENT_MAX_TOOL_ROUNDS = 100
AGENT_MAX_MODEL_ATTEMPTS = 120
AGENT_MAX_TOOL_CALLS = 512
AGENT_MAX_TOOL_CALLS_PER_CYCLE = 128
AGENT_MAX_RUN_SECONDS = 3_600.0
AGENT_MAX_TOOL_TIMEOUT_SECONDS = 300.0
AGENT_MAX_MODEL_RETRIES = 5
AGENT_MAX_REQUEST_CHARS = 4_000_000
AGENT_MAX_TOOL_RESULT_CHARS = 256_000
AGENT_MAX_TOOL_CYCLE_CHARS = 1_000_000
AGENT_MAX_VALIDATION_ERRORS = 10
AGENT_MAX_LOOP_REPEAT = 10
AGENT_MAX_LOOP_PATTERN_CYCLES = 10
REVIEW_MAX_TIMEOUT_SECONDS = 120.0
REVIEW_MAX_LEASE_SECONDS = 3_600
REVIEW_MAX_RETRY_BACKOFF_SECONDS = 300
REVIEW_RETRY_BACKOFF_COUNT = 2


class _RuntimePolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AgentRunPolicyOverrides(_RuntimePolicyModel):
    """Optional user tuning; safety-owned fields are intentionally absent."""

    max_tool_rounds: int | None = Field(default=None, gt=0, le=AGENT_MAX_TOOL_ROUNDS)
    max_model_attempts: int | None = Field(default=None, gt=0, le=AGENT_MAX_MODEL_ATTEMPTS)
    max_tool_calls: int | None = Field(default=None, gt=0, le=AGENT_MAX_TOOL_CALLS)
    max_tool_calls_per_cycle: int | None = Field(
        default=None, gt=0, le=AGENT_MAX_TOOL_CALLS_PER_CYCLE
    )
    max_run_seconds: float | None = Field(default=None, gt=0, le=AGENT_MAX_RUN_SECONDS)
    tool_timeout_seconds: float | None = Field(
        default=None, gt=0, le=AGENT_MAX_TOOL_TIMEOUT_SECONDS
    )
    model_retry_limit: int | None = Field(default=None, ge=0, le=AGENT_MAX_MODEL_RETRIES)
    requested_context_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_REQUEST_CHARS)
    unknown_model_fallback_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_REQUEST_CHARS)
    max_tool_result_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_TOOL_RESULT_CHARS)
    max_tool_result_request_ratio: float | None = Field(default=None, gt=0, le=1)
    max_tool_cycle_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_TOOL_CYCLE_CHARS)
    max_tool_cycle_request_ratio: float | None = Field(default=None, gt=0, le=1)
    max_validation_errors: int | None = Field(default=None, gt=0, le=AGENT_MAX_VALIDATION_ERRORS)
    loop_repeat_limit: int | None = Field(default=None, ge=2, le=AGENT_MAX_LOOP_REPEAT)
    loop_max_pattern_cycles: int | None = Field(
        default=None, gt=0, le=AGENT_MAX_LOOP_PATTERN_CYCLES
    )


class ReviewPolicyOverrides(_RuntimePolicyModel):
    learning_timeout_seconds: float | None = Field(
        default=None, gt=0, le=REVIEW_MAX_TIMEOUT_SECONDS
    )
    learning_lease_seconds: int | None = Field(default=None, gt=0, le=REVIEW_MAX_LEASE_SECONDS)
    preference_timeout_seconds: float | None = Field(
        default=None, gt=0, le=REVIEW_MAX_TIMEOUT_SECONDS
    )
    preference_lease_seconds: int | None = Field(default=None, gt=0, le=REVIEW_MAX_LEASE_SECONDS)
    preference_retry_backoff_seconds: tuple[int, ...] | None = None

    @field_validator("preference_retry_backoff_seconds", mode="before")
    @classmethod
    def tuple_retry_backoff(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("preference_retry_backoff_seconds")
    @classmethod
    def valid_retry_backoff(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        if value is None:
            return None
        if (
            len(value) != REVIEW_RETRY_BACKOFF_COUNT
            or any(isinstance(item, bool) or item <= 0 for item in value)
            or any(item > REVIEW_MAX_RETRY_BACKOFF_SECONDS for item in value)
            or tuple(sorted(value)) != value
        ):
            raise ValueError("Preference Review retry backoff is outside the safety boundary")
        return value


class RuntimePolicyOverrides(_RuntimePolicyModel):
    """Optional section in the user-owned global ``config.yaml``."""

    agent_run: AgentRunPolicyOverrides | None = None
    reviews: ReviewPolicyOverrides | None = None


def finite_number(value: float, *, label: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


__all__ = [
    "AGENT_MAX_LOOP_PATTERN_CYCLES",
    "AGENT_MAX_LOOP_REPEAT",
    "AGENT_MAX_MODEL_ATTEMPTS",
    "AGENT_MAX_MODEL_RETRIES",
    "AGENT_MAX_REQUEST_CHARS",
    "AGENT_MAX_RUN_SECONDS",
    "AGENT_MAX_TOOL_CALLS",
    "AGENT_MAX_TOOL_CALLS_PER_CYCLE",
    "AGENT_MAX_TOOL_CYCLE_CHARS",
    "AGENT_MAX_TOOL_RESULT_CHARS",
    "AGENT_MAX_TOOL_ROUNDS",
    "AGENT_MAX_TOOL_TIMEOUT_SECONDS",
    "AGENT_MAX_VALIDATION_ERRORS",
    "AgentRunPolicyOverrides",
    "REVIEW_MAX_LEASE_SECONDS",
    "REVIEW_MAX_RETRY_BACKOFF_SECONDS",
    "REVIEW_MAX_TIMEOUT_SECONDS",
    "REVIEW_RETRY_BACKOFF_COUNT",
    "RUNTIME_POLICY_SCHEMA_VERSION",
    "ReviewPolicyOverrides",
    "RuntimePolicyOverrides",
    "finite_number",
]
