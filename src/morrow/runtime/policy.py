"""Validated developer policy and exact-model effective run limits."""

from __future__ import annotations

import tomllib
from enum import StrEnum
from importlib import resources
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from morrow.core.models import ModelRef, ProtocolModel, ToolEffect


class PolicyLoadError(RuntimeError):
    """The packaged developer policy is missing or invalid."""


class ToolApproval(StrEnum):
    """Local approval requirement; it is not part of the Provider protocol."""

    NEVER = "never"
    REQUIRED = "required"


class ToolExecutionPolicy(ProtocolModel):
    """Immutable local effect and approval metadata for one registered tool."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    effect: ToolEffect = ToolEffect.NONE
    approval: ToolApproval = ToolApproval.NEVER


class ProviderToolSupport(ProtocolModel):
    tool_protocol: Literal["none", "openai_function"]
    multiple_tool_calls: bool
    safe_request_chars: int | None = Field(default=None, gt=0)


class RunPolicy(ProtocolModel):
    max_tool_rounds: int = Field(gt=0)
    max_model_attempts: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_tool_calls_per_cycle: int = Field(gt=0)
    max_run_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    model_retry_limit: int = Field(ge=0)
    effective_request_chars: int = Field(gt=0)
    effective_result_limit: int = Field(gt=0)
    effective_cycle_limit: int = Field(gt=0)
    max_validation_errors: int = Field(gt=0)
    loop_detection_enabled: bool
    loop_repeat_limit: int = Field(ge=2)
    loop_max_pattern_cycles: int = Field(gt=0)
    provider_tool_support: ProviderToolSupport


class AgentPolicy(ProtocolModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_tool_rounds: int = Field(gt=0)
    max_model_attempts: int = Field(gt=0)
    max_tool_calls: int = Field(gt=0)
    max_tool_calls_per_cycle: int = Field(gt=0)
    max_run_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    model_retry_limit: int = Field(ge=0)
    requested_context_chars: int = Field(gt=0)
    unknown_model_fallback_chars: int = Field(gt=0)
    max_tool_result_chars: int = Field(gt=0)
    max_tool_result_request_ratio: float = Field(gt=0, le=1)
    max_tool_cycle_chars: int = Field(gt=0)
    max_tool_cycle_request_ratio: float = Field(gt=0, le=1)
    max_validation_errors: int = Field(gt=0)
    loop_detection_enabled: bool
    loop_repeat_limit: int = Field(ge=2)
    loop_max_pattern_cycles: int = Field(gt=0)
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
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in self.model_safe_request_chars.values()
        ):
            raise ValueError("model safe sizes must be positive integers")
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


def parse_agent_policy(data: bytes) -> AgentPolicy:
    try:
        payload = tomllib.loads(data.decode("utf-8"))
        return AgentPolicy.model_validate(payload, strict=True)
    except Exception as exc:
        raise PolicyLoadError("developer agent policy is invalid") from exc


def load_agent_policy(
    *, package: str = "morrow.resources", resource_name: str = "agent-policy.toml"
) -> AgentPolicy:
    try:
        data = resources.files(package).joinpath(resource_name).read_bytes()
    except Exception as exc:
        raise PolicyLoadError("developer agent policy resource is missing") from exc
    return parse_agent_policy(data)
