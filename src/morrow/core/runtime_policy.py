"""Strict user overrides and code-owned safety ceilings for runtime policy."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, field_validator

RUNTIME_POLICY_SCHEMA_VERSION = 1
# Agent-run policy has its own version. The outer runtime-policy document remains v1 while prepared
# runs carry an explicit version in their immutable RunPolicy snapshot.
AGENT_RUN_POLICY_SCHEMA_VERSION = 2

# These are safety ceilings, not product defaults. They deliberately remain in code so neither a
# packaged resource nor user-owned YAML can widen the bounded runtime beyond reviewed limits.
AGENT_MAX_TOOL_TIMEOUT_SECONDS = 300.0
AGENT_MAX_MODEL_RETRIES = 5
AGENT_MAX_REQUEST_CHARS = 4_000_000
AGENT_MAX_TOOL_RESULT_CHARS = 256_000
AGENT_MAX_VALIDATION_ERRORS = 10
AGENT_MAX_CONTEXT_WINDOW_TOKENS = 10_000_000
AGENT_MAX_RESERVE_TOKENS = 1_000_000
AGENT_MAX_KEEP_RECENT_TOKENS = 1_000_000
AGENT_MAX_RETRY_DELAY_SECONDS = 60.0
PI_DEFAULT_RESERVE_TOKENS = 16_384
PI_DEFAULT_KEEP_RECENT_TOKENS = 20_000
PI_DEFAULT_MAX_RETRIES = 3
PI_DEFAULT_RETRY_BASE_DELAY_SECONDS = 2.0
PI_DEFAULT_MAX_PROVIDER_RETRY_DELAY_SECONDS = 60.0
PI_DEFAULT_TOOL_MAX_BYTES = 50 * 1024
PI_DEFAULT_TOOL_MAX_LINES = 2_000
PI_DEFAULT_GREP_MAX_LINE_CHARS = 500
# These bounds protect the model-context projection.  They are not task-lifetime limits and
# cannot be widened by a user-owned policy document.
AGENT_MAX_TRUNCATION_BYTES = PI_DEFAULT_TOOL_MAX_BYTES
AGENT_MAX_TRUNCATION_LINES = PI_DEFAULT_TOOL_MAX_LINES
AGENT_MAX_GREP_LINE_CHARS = PI_DEFAULT_GREP_MAX_LINE_CHARS
REVIEW_MAX_TIMEOUT_SECONDS = 300.0
REVIEW_MAX_LEASE_SECONDS = 3_600
REVIEW_MAX_RETRY_BACKOFF_SECONDS = 300
REVIEW_RETRY_BACKOFF_COUNT = 2


class _RuntimePolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AgentRunPolicyOverrides(_RuntimePolicyModel):
    """Optional user tuning; safety-owned fields are intentionally absent."""

    tool_timeout_seconds: float | None = Field(
        default=None, gt=0, le=AGENT_MAX_TOOL_TIMEOUT_SECONDS
    )
    requested_context_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_REQUEST_CHARS)
    unknown_model_fallback_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_REQUEST_CHARS)
    max_tool_result_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_TOOL_RESULT_CHARS)
    max_validation_errors: int | None = Field(default=None, gt=0, le=AGENT_MAX_VALIDATION_ERRORS)
    compaction_enabled: bool | None = None
    reserve_tokens: int | None = Field(default=None, gt=0, le=AGENT_MAX_RESERVE_TOKENS)
    keep_recent_tokens: int | None = Field(default=None, gt=0, le=AGENT_MAX_KEEP_RECENT_TOKENS)
    retry_enabled: bool | None = None
    max_retries: int | None = Field(default=None, ge=0, le=AGENT_MAX_MODEL_RETRIES)
    retry_base_delay_seconds: float | None = Field(
        default=None, gt=0, le=AGENT_MAX_RETRY_DELAY_SECONDS
    )
    max_provider_retry_delay_seconds: float | None = Field(
        default=None, gt=0, le=AGENT_MAX_RETRY_DELAY_SECONDS
    )
    truncation_max_bytes: int | None = Field(default=None, gt=0, le=AGENT_MAX_TRUNCATION_BYTES)
    truncation_max_lines: int | None = Field(default=None, gt=0, le=AGENT_MAX_TRUNCATION_LINES)
    grep_max_line_chars: int | None = Field(default=None, gt=0, le=AGENT_MAX_GREP_LINE_CHARS)


class ReviewPolicyOverrides(_RuntimePolicyModel):
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
    "AGENT_MAX_CONTEXT_WINDOW_TOKENS",
    "AGENT_MAX_GREP_LINE_CHARS",
    "AGENT_MAX_KEEP_RECENT_TOKENS",
    "AGENT_MAX_MODEL_RETRIES",
    "AGENT_MAX_REQUEST_CHARS",
    "AGENT_MAX_RESERVE_TOKENS",
    "AGENT_MAX_RETRY_DELAY_SECONDS",
    "AGENT_MAX_TRUNCATION_BYTES",
    "AGENT_MAX_TRUNCATION_LINES",
    "AGENT_MAX_TOOL_RESULT_CHARS",
    "AGENT_MAX_TOOL_TIMEOUT_SECONDS",
    "AGENT_MAX_VALIDATION_ERRORS",
    "AGENT_RUN_POLICY_SCHEMA_VERSION",
    "AgentRunPolicyOverrides",
    "PI_DEFAULT_GREP_MAX_LINE_CHARS",
    "PI_DEFAULT_KEEP_RECENT_TOKENS",
    "PI_DEFAULT_MAX_PROVIDER_RETRY_DELAY_SECONDS",
    "PI_DEFAULT_MAX_RETRIES",
    "PI_DEFAULT_RESERVE_TOKENS",
    "PI_DEFAULT_RETRY_BASE_DELAY_SECONDS",
    "PI_DEFAULT_TOOL_MAX_BYTES",
    "PI_DEFAULT_TOOL_MAX_LINES",
    "REVIEW_MAX_LEASE_SECONDS",
    "REVIEW_MAX_RETRY_BACKOFF_SECONDS",
    "REVIEW_MAX_TIMEOUT_SECONDS",
    "REVIEW_RETRY_BACKOFF_COUNT",
    "RUNTIME_POLICY_SCHEMA_VERSION",
    "ReviewPolicyOverrides",
    "RuntimePolicyOverrides",
    "finite_number",
]
