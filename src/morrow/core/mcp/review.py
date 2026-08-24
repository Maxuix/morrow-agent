"""Narrow, run-bound MCP review evidence.

This module contains facts produced by the local interface after an explicit
review.  It is deliberately not a capability grant: the evidence can only
turn a small set of MCP denials into a per-call approval request.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    DIGEST_PATTERN,
    WORKSPACE_ID_PREFIX,
    canonical_json_bytes,
    refuse_secret_material,
    require_payload_budget,
    sha256_digest,
    validate_prefixed_id,
)
from morrow.core.mcp.contracts import validate_mcp_server_id
from morrow.core.models import ProtocolModel, utc_now

MCP_REVIEW_MAX_ITEMS = 8
MCP_REVIEW_MAX_BYTES = 8 * 1024
MCP_REVIEW_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class McpReviewRisk(StrEnum):
    """Risk classes for which a local review may request approval."""

    NETWORK = "network"
    CREDENTIALS = "credentials"
    LOOPBACK = "loopback"
    EXTERNAL_EFFECT = "external_effect"


class McpReviewEvidence(ProtocolModel):
    """Immutable review facts bound to one exact AgentRun MCP toolset."""

    review_id: str = Field(min_length=1, max_length=128)
    workspace_id: str
    agent_run_id: str
    server_id: str
    config_digest: str
    catalog_digest: str
    toolset_digest: str
    risks: tuple[McpReviewRisk, ...] = ()
    issued_by: Literal["local_interface"] = "local_interface"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("review_id")
    @classmethod
    def valid_review_id(cls, value: str) -> str:
        if not MCP_REVIEW_ID_PATTERN.fullmatch(value):
            raise ValueError("MCP review id must be an opaque bounded token")
        return value

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace_id(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run_id(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("server_id")
    @classmethod
    def valid_server_id(cls, value: str) -> str:
        return validate_mcp_server_id(value)

    @field_validator("config_digest", "catalog_digest", "toolset_digest")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("MCP review digest must be a SHA-256 hex digest")
        return value

    @field_validator("risks")
    @classmethod
    def bounded_risks(cls, values: tuple[McpReviewRisk, ...]) -> tuple[McpReviewRisk, ...]:
        if len(values) > MCP_REVIEW_MAX_ITEMS or len(set(values)) != len(values):
            raise ValueError("MCP review risks must be unique and bounded")
        return values

    @model_validator(mode="after")
    def safe_and_bounded(self) -> McpReviewEvidence:
        payload = canonical_json_bytes(self.model_dump(mode="json"))
        require_payload_budget(payload, MCP_REVIEW_MAX_BYTES, label="MCP review evidence")
        refuse_secret_material(payload, label="MCP review evidence")
        return self

    def matches(
        self,
        *,
        workspace_id: str,
        agent_run_id: str,
        server_id: str,
        config_digest: str,
        catalog_digest: str,
        toolset_digest: str,
    ) -> bool:
        return (
            self.workspace_id == workspace_id
            and self.agent_run_id == agent_run_id
            and self.server_id == server_id
            and self.config_digest == config_digest
            and self.catalog_digest == catalog_digest
            and self.toolset_digest == toolset_digest
        )

    def covers(self, risks: set[McpReviewRisk]) -> bool:
        return risks.issubset(set(self.risks))

    @property
    def digest(self) -> str:
        return sha256_digest(canonical_json_bytes(self.model_dump(mode="json")))


__all__ = ["MCP_REVIEW_MAX_BYTES", "MCP_REVIEW_MAX_ITEMS", "McpReviewEvidence", "McpReviewRisk"]
