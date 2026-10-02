"""Closed exact-token security query for the SDK's guarded-input contract.

Native IDs and tokens are private, transient arguments. This is an internal
adapter capability, never an arbitrary model-facing SDK tool entry point.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from morrow.adapters.computer_use.action_inputs import ElementSafetySubject
from morrow.core.computer_use import ComputerUseContractError
from morrow.core.domain import canonical_json_bytes

SecurityReason = Literal[
    "invalid_arguments",
    "token_unconfirmed",
    "target_unconfirmed",
    "query_failed",
    "ancestry_unconfirmed",
    "subrole_unreadable",
    "leaf_subrole_unreadable",
    "ancestor_subrole_unreadable",
    "unsupported_role",
    "unsupported_subrole",
    "generic_leaf_subrole",
    "generic_ancestor_subrole",
    "leaf_custom_subrole",
    "ancestor_custom_subrole",
    "ancestor_section_list",
    "ancestor_collection_list",
    "ancestor_content_list",
    "ancestor_other_window",
]


class ElementSecurityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int = Field(ge=2, le=2)
    input_guard_version: int = Field(ge=1, le=1)
    classification: Literal["non_sensitive", "sensitive", "unknown"]
    binding_verified: bool
    reason: SecurityReason | None

    @model_validator(mode="after")
    def positive_requires_binding(self):
        if self.classification != "unknown" and (
            self.binding_verified is not True or self.reason is not None
        ):
            raise ValueError("element_security_invalid")
        return self


def parse_element_security(content: object) -> ElementSecurityResponse:
    if not isinstance(content, str) or len(content) > 4096:
        raise ComputerUseContractError("element_security_invalid")
    try:

        def closed_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError
                value[key] = item
            return value

        return ElementSecurityResponse.model_validate(
            json.loads(content, object_pairs_hook=closed_object)
        )
    except ValueError:
        raise ComputerUseContractError("element_security_invalid") from None


class _SecurityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    pid: int = Field(gt=0, le=2**31 - 1, repr=False)
    window_id: int = Field(gt=0, le=2**32 - 1, repr=False)
    element_token: str = Field(min_length=1, max_length=1024, repr=False)
    session: str = Field(min_length=1, max_length=128, repr=False)


class NativeElementSafetyProbe:
    """Run on the session owner loop inside its existing NativeCalls admission."""

    def __init__(self, native, session_name: Callable[[], str]) -> None:
        self._native = native
        self._session_name = session_name

    def __repr__(self) -> str:
        return "NativeElementSafetyProbe()"

    async def __call__(self, subject: ElementSafetySubject) -> bool:
        try:
            payload = _SecurityInput(
                pid=subject.pid,
                window_id=subject.window_id,
                element_token=subject.token,
                session=self._session_name(),
            )
            result = await self._native.call_tool(
                "get_element_security", canonical_json_bytes(payload.model_dump()).decode()
            )
            if getattr(result, "is_error", None) is not False:
                return False
            response = parse_element_security(getattr(result, "structured_json", None))
            return response.classification == "non_sensitive" and response.binding_verified is True
        except Exception:
            # Unknown/missing contracts and private native errors never grant input.
            # Cancellation is BaseException and remains visible to NativeCalls.
            return False
