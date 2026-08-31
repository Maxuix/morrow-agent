"""Frozen per-AgentRun preparation contracts (no SDK objects, no secrets).

The prepared spec is the immutable evidence a rehydrated run is rebuilt from:
sanitized Provider runtime facts, exact Model capabilities, the resolved
RunPolicy and ToolSet digests. It deliberately never carries a credential
value, SDK object, or chat history.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.models import (
    CostMetadata,
    CredentialRef,
    InputModality,
    ModelCapabilityOverrides,
    ModelRef,
    ProtocolModel,
    ProviderToolSupport,
    RunPolicy,
)
from morrow.core.prompt import ProjectInstructionSourceRef, project_source_selection_digest

DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

CapabilityToolProtocol = Literal["none", "openai_function"]


class ProviderCapabilities(ProtocolModel):
    """Adapter-declared capabilities; never a runtime authority by itself.

    Conservative defaults: an adapter only supports what it declares.
    ``safe_request_chars`` mirrors policy input limits, not the config authority.
    """

    streaming_text: bool = True
    tool_protocol: CapabilityToolProtocol = "none"
    multiple_tool_calls: bool = False
    structured_output: bool = False
    safe_request_chars: int | None = Field(default=None, gt=0)
    safe_context_chars: int | None = Field(default=None, gt=0)
    context_window_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    input_types: tuple[InputModality, ...] = ("text",)
    cost_metadata: CostMetadata | None = None


class ModelCapabilities(ModelCapabilityOverrides):
    """Optional per-Model overrides; None fields keep the Adapter default.

    Stage 6 captures these before any Provider/Model control-plane fields exist;
    Subplan 70 adds the configuration surface that feeds them.
    """

    model: ModelRef
    pass


class ExactModelCapabilities(ProtocolModel):
    """Effective capabilities for one exact Model: Adapter defaults with Model overrides."""

    adapter_id: str
    model: ModelRef
    streaming_text: bool
    tool_protocol: CapabilityToolProtocol
    multiple_tool_calls: bool
    structured_output: bool
    safe_request_chars: int | None = None
    safe_context_chars: int | None = None
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    input_types: tuple[InputModality, ...] = ("text",)
    cost_metadata: CostMetadata | None = None


def exact_model_capabilities(
    adapter_id: str,
    adapter: ProviderCapabilities,
    model: ModelRef,
    model_caps: ModelCapabilityOverrides | ModelCapabilities | None = None,
) -> ExactModelCapabilities:
    """Merge Adapter defaults with a Model's narrower overrides.

    A Model override may only narrow; unknown fields keep the Adapter default.
    """

    if model_caps is not None and hasattr(model_caps, "model") and model_caps.model != model:
        raise ValueError("Model capability overrides refer to a different Model")

    def narrowed_bool(model_value: bool | None, default: bool) -> bool:
        return default if model_value is None else default and model_value

    def narrowed_protocol(model_value, default):
        if model_value is None or default == "none":
            return default
        return "none" if model_value == "none" else default

    def narrowed_limit(model_value: int | None, default: int | None) -> int | None:
        if model_value is None:
            return default
        return model_value if default is None else min(default, model_value)

    def narrowed_input_types(model_value, default):
        if model_value is None:
            return default
        allowed = set(model_value)
        return tuple(item for item in default if item in allowed)

    def selected_metadata(model_value, default):
        return default if model_value is None else model_value

    return ExactModelCapabilities(
        adapter_id=adapter_id,
        model=model,
        streaming_text=narrowed_bool(model_caps.streaming_text, adapter.streaming_text)
        if model_caps is not None
        else adapter.streaming_text,
        tool_protocol=narrowed_protocol(model_caps.tool_protocol, adapter.tool_protocol)
        if model_caps is not None
        else adapter.tool_protocol,
        multiple_tool_calls=narrowed_bool(
            model_caps.multiple_tool_calls, adapter.multiple_tool_calls
        )
        if model_caps is not None
        else adapter.multiple_tool_calls,
        structured_output=narrowed_bool(model_caps.structured_output, adapter.structured_output)
        if model_caps is not None
        else adapter.structured_output,
        safe_request_chars=narrowed_limit(model_caps.safe_request_chars, adapter.safe_request_chars)
        if model_caps is not None
        else adapter.safe_request_chars,
        safe_context_chars=narrowed_limit(model_caps.safe_context_chars, adapter.safe_context_chars)
        if model_caps is not None
        else adapter.safe_context_chars,
        context_window_tokens=narrowed_limit(
            model_caps.context_window_tokens, adapter.context_window_tokens
        )
        if model_caps is not None
        else adapter.context_window_tokens,
        max_output_tokens=narrowed_limit(model_caps.max_output_tokens, adapter.max_output_tokens)
        if model_caps is not None
        else adapter.max_output_tokens,
        input_types=narrowed_input_types(model_caps.input_types, adapter.input_types)
        if model_caps is not None
        else adapter.input_types,
        cost_metadata=selected_metadata(model_caps.cost_metadata, adapter.cost_metadata)
        if model_caps is not None
        else adapter.cost_metadata,
    )


class ProviderRuntimeSnapshot(ProtocolModel):
    """Sanitized immutable Provider facts frozen onto one AgentRun.

    Never contains a credential value. ``endpoint`` must not carry userinfo,
    query strings or fragments, which are the usual secret carriers.
    ``config_digest`` is the SHA-256 of the exact ProviderConfig used.
    """

    provider_id: str
    adapter_id: str
    model: ModelRef
    api_model_id: str
    endpoint: str | None = Field(default=None, max_length=2048)
    credential_ref: CredentialRef | None = None
    capabilities: ExactModelCapabilities
    config_revision: int = Field(default=0, ge=0)
    config_digest: str

    @field_validator("provider_id", "adapter_id", "api_model_id")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Provider runtime field must not be empty")
        return value

    @field_validator("endpoint")
    @classmethod
    def sanitized_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ch in value for ch in "?#@ \t\r\n\x00"):
            raise ValueError("endpoint must not carry userinfo, query, fragment or control chars")
        return value

    @field_validator("config_digest")
    @classmethod
    def valid_config_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("config digest must be a SHA-256 hex digest")
        return value


class AgentDefinitionRef(ProtocolModel):
    definition_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    version_id: str = Field(pattern=r"^adev_[A-Za-z0-9_-]+$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkflowAgentRunRef(ProtocolModel):
    workflow_revision_id: str = Field(pattern=r"^wrev_[A-Za-z0-9_-]+$")
    workflow_run_id: str = Field(pattern=r"^wrun_[A-Za-z0-9_-]+$")
    node_run_id: str = Field(pattern=r"^nrun_[A-Za-z0-9_-]+$")
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    attempt: int = Field(ge=1, le=1, strict=True)


class PreparedAgentRunSpec(ProtocolModel):
    """Immutable evidence describing one prepared AgentRun.

    The runtime (Provider, ContextBuilder, ToolExecutor) is rebuilt from this
    spec or from the stored ``AgentRunSnapshot`` evidence; Skills and MCP add
    their reference fields in later subplans without changing this contract.
    """

    definition_ref: AgentDefinitionRef | None = None
    max_agent_generation_requests: int | None = Field(default=None, gt=0, strict=True)
    conversation_session_id: str | None = Field(default=None, pattern=r"^ses_[A-Za-z0-9_-]+$")
    provider_runtime: ProviderRuntimeSnapshot
    run_policy: RunPolicy
    run_policy_digest: str
    tool_schema_digest: str
    tool_count: int = Field(ge=0)
    # Reference-only MCP evidence; full snapshots live in the MCP journal.
    mcp_run_snapshot_ids: tuple[str, ...] = ()
    skill_selection_ids: tuple[str, ...] = ()
    skill_context_ids: tuple[str, ...] = ()
    skill_selection_digest: str | None = None
    skill_context_digest: str | None = None
    prompt_profile_id: str | None = None
    prompt_profile_version: str | None = None
    prompt_profile_digest: str | None = None
    role_prompt_digest: str | None = None
    project_instruction_resolver_version: str | None = None
    project_instruction_sources: tuple[ProjectInstructionSourceRef, ...] = ()
    project_instruction_selection_digest: str | None = None

    @field_validator("run_policy_digest", "tool_schema_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.match(value):
            raise ValueError("prepared digest must be a SHA-256 hex digest")
        return value

    @field_validator("mcp_run_snapshot_ids")
    @classmethod
    def bounded_mcp_snapshot_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 64 or len(value) != len(set(value)):
            raise ValueError("MCP run snapshot references must be unique and bounded")
        if any(
            not item or len(item) > 256 or any(ch in item for ch in "\x00\r\n") for item in value
        ):
            raise ValueError("MCP run snapshot reference is invalid")
        return value

    @field_validator(
        "prompt_profile_digest",
        "role_prompt_digest",
        "project_instruction_selection_digest",
    )
    @classmethod
    def valid_prompt_digest(cls, value: str | None) -> str | None:
        if value is not None and not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("prepared prompt digest must be a SHA-256 hex digest")
        return value

    @field_validator(
        "prompt_profile_id", "prompt_profile_version", "project_instruction_resolver_version"
    )
    @classmethod
    def valid_prompt_tokens(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or len(value) > 64 or any(char in value for char in "\x00\r\n")
        ):
            raise ValueError("prepared prompt evidence token is invalid")
        return value

    @field_validator("project_instruction_sources")
    @classmethod
    def bounded_project_instruction_sources(
        cls, value: tuple[ProjectInstructionSourceRef, ...]
    ) -> tuple[ProjectInstructionSourceRef, ...]:
        if len(value) > 32 or len({(item.path, item.scope) for item in value}) != len(value):
            raise ValueError("prepared project instruction references are invalid")
        if sum(item.byte_count for item in value) > 64 * 1024:
            raise ValueError("prepared project instruction references exceed the byte budget")
        order = [
            (0 if item.scope == "." else len(item.scope.split("/")), item.path) for item in value
        ]
        if order != sorted(order):
            raise ValueError("prepared project instruction sources are not in scope order")
        return value

    @model_validator(mode="after")
    def complete_prompt_evidence(self) -> PreparedAgentRunSpec:
        if (self.definition_ref is None) != (self.conversation_session_id is None):
            raise ValueError("prepared definition conversation evidence is incomplete")
        if self.definition_ref is None and self.max_agent_generation_requests is not None:
            raise ValueError("prepared definition request ceiling has no definition")
        profile_fields = (
            self.prompt_profile_id,
            self.prompt_profile_version,
            self.prompt_profile_digest,
        )
        if any(value is not None for value in profile_fields) and not all(
            value is not None for value in profile_fields
        ):
            raise ValueError("prepared prompt profile evidence is incomplete")
        if self.role_prompt_digest is not None and self.prompt_profile_id is None:
            raise ValueError("prepared role prompt evidence has no profile")
        if self.project_instruction_sources and (
            self.project_instruction_resolver_version is None
            or self.project_instruction_selection_digest is None
        ):
            raise ValueError("prepared project instruction evidence is incomplete")
        if self.project_instruction_resolver_version is None and (
            self.project_instruction_sources
            or self.project_instruction_selection_digest is not None
        ):
            raise ValueError("prepared project instruction resolver evidence is incomplete")
        if self.project_instruction_resolver_version is not None and (
            self.project_instruction_selection_digest
            != project_source_selection_digest(list(self.project_instruction_sources))
        ):
            raise ValueError("prepared project instruction selection digest is invalid")
        return self

    @property
    def provider_support(self) -> ProviderToolSupport:
        return self.run_policy.provider_tool_support

    def has_tools(self) -> bool:
        return self.tool_count > 0 and self.run_policy.provider_tool_support.tool_protocol != "none"
