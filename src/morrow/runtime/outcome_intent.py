"""No-tool semantic intent resolution for runtime-owned completion obligations."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum

from pydantic import Field, model_validator

from morrow.core.completion import (
    OutcomeContract,
    OutcomeMode,
    ValidationRequirement,
)
from morrow.core.models import ModelRef, ProtocolModel, SystemMessage, UserMessage
from morrow.core.validation import VALIDATOR_KINDS

OUTCOME_INTENT_PROMPT_VERSION = "outcome-intent.v1"
OUTCOME_INTENT_SYSTEM_MARKER = "MORROW_OUTCOME_INTENT_RESOLVER_V1"


class IntentCertainty(StrEnum):
    CLEAR = "clear"
    AMBIGUOUS = "ambiguous"


class OutcomeIntentDraft(ProtocolModel):
    """Strict model output; it carries no verifier authority or free-form rationale."""

    mode: OutcomeMode
    certainty: IntentCertainty
    target_paths: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] | None = None
    forbidden_paths: tuple[str, ...] = ()
    required_validations: tuple[ValidationRequirement, ...] = Field(default=(), max_length=32)
    no_change_allowed: bool = False

    @model_validator(mode="after")
    def semantic_consistency(self) -> OutcomeIntentDraft:
        if self.certainty is IntentCertainty.AMBIGUOUS:
            if self.mode is not OutcomeMode.UNSPECIFIED:
                raise ValueError("ambiguous intent must use unspecified mode")
            if self.target_paths or self.allowed_paths is not None or self.required_validations:
                raise ValueError("ambiguous intent must not invent proof obligations")
            if not self.no_change_allowed:
                raise ValueError("ambiguous intent must allow no change")
        elif self.mode is OutcomeMode.CHANGE and self.no_change_allowed:
            raise ValueError("clear change intent cannot allow no change")
        elif self.mode is not OutcomeMode.CHANGE:
            if self.target_paths or self.allowed_paths is not None or self.forbidden_paths:
                raise ValueError("non-change intent must not declare workspace paths")
            if self.required_validations:
                raise ValueError("non-change intent must not declare validators")
            if not self.no_change_allowed:
                raise ValueError("non-change intent must allow no change")
        unknown = {item.validator_kind for item in self.required_validations} - VALIDATOR_KINDS
        if unknown:
            raise ValueError("intent declared an unsupported validator kind")
        return self

    def to_contract(self) -> OutcomeContract:
        return OutcomeContract(
            mode=self.mode,
            target_paths=self.target_paths,
            allowed_paths=self.allowed_paths,
            forbidden_paths=self.forbidden_paths,
            required_validations=self.required_validations,
            no_change_allowed=self.no_change_allowed,
            preparation_version=OUTCOME_INTENT_PROMPT_VERSION,
        )


class OutcomeIntentResolutionError(RuntimeError):
    """The semantic resolver could not produce a trusted contract."""


class OutcomeIntentResolver:
    """Resolve user intent through a bounded, strict, no-tool model request."""

    def __init__(self, provider, model: ModelRef, context_builder, *, timeout: float = 30.0):
        self.provider = provider
        self.model = model
        self.context_builder = context_builder
        self.timeout = timeout

    def request_messages(self, user_input: str):
        schema = OutcomeIntentDraft.model_json_schema()
        validators = ", ".join(sorted(VALIDATOR_KINDS))
        system = SystemMessage(
            content=(
                f"{OUTCOME_INTENT_SYSTEM_MARKER}\n"
                "You are a semantic intent parser, not a task executor. Interpret the entire "
                "request, including negation, references, and mixed audit/fix instructions. "
                "Return exactly one JSON object matching the supplied schema and no other text. "
                "Use change only when the requested outcome requires workspace mutation; use "
                "explanation for read-only analysis; use unspecified/ambiguous when meaning is "
                "not clear. Paths must be workspace-relative and must not be guessed. Only "
                f"these validator kinds are valid: {validators}. Never invent a verifier."
            )
        )
        instruction = UserMessage(
            content=(
                "Resolve the completion intent for this user request.\n"
                f"JSON Schema: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n"
                f"User request:\n{user_input}"
            )
        )
        return [system, instruction]

    async def resolve(self, user_input: str) -> OutcomeContract:
        messages = self.request_messages(user_input)
        try:
            self.context_builder.validate_request(messages, ())
            raw = await asyncio.wait_for(
                self.provider.complete(self.model, messages), timeout=self.timeout
            )
            return self._parse(raw).to_contract()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise OutcomeIntentResolutionError(type(error).__name__) from None

    @staticmethod
    def _parse(raw: str) -> OutcomeIntentDraft:
        if not isinstance(raw, str):
            raise ValueError("intent response must be text")
        candidate = raw.strip()
        if not candidate.startswith("{") or not candidate.endswith("}"):
            raise ValueError("intent response must be one JSON object")
        return OutcomeIntentDraft.model_validate_json(candidate)


__all__ = [
    "IntentCertainty",
    "OUTCOME_INTENT_PROMPT_VERSION",
    "OUTCOME_INTENT_SYSTEM_MARKER",
    "OutcomeIntentDraft",
    "OutcomeIntentResolutionError",
    "OutcomeIntentResolver",
]
