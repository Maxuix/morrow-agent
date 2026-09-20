"""Chat admission DTOs; queues are input authority, never conversation writers."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_serializer, field_validator, model_validator

from morrow.core.domain import CLIENT_MESSAGE_ID_PATTERN
from morrow.core.models import AttachmentRef, ChatSettings, ProtocolModel
from morrow.core.orchestration import GraphPlanningRequest


class InteractionStatus(StrEnum):
    QUEUED = "queued"
    CONSUMED = "consumed"
    WITHDRAWN = "withdrawn"
    BLOCKED = "blocked"
    SETTLED = "settled"


class WorkflowSelection(ProtocolModel):
    promoted_plan: GraphPlanningRequest | None = None
    workflow_definition_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    workflow_revision_id: str = Field(pattern=r"^wrev_[A-Za-z0-9_-]+$")
    root_task_run_id: str | None = Field(default=None, pattern=r"^task_[A-Za-z0-9_-]+$")
    expected_root_row_version: int | None = Field(default=None, ge=1)


class InteractionRequest(ProtocolModel):
    client_message_id: str = Field(min_length=1, max_length=128)
    intent: Literal["send", "steer", "follow_up", "explicit_workflow"] = "send"
    text: str = Field(default="", max_length=4096)
    attachments: tuple[AttachmentRef, ...] = Field(default=(), max_length=8)
    settings: ChatSettings = Field(default_factory=ChatSettings)
    allow_unconfined_host: bool = Field(default=False, strict=True)

    @field_serializer("settings")
    def settings_wire(self, value):
        return value.model_dump(mode="json", exclude_none=True)

    target_agent_run_id: str | None = Field(default=None, max_length=128)
    workflow: WorkflowSelection | None = None

    @field_validator("client_message_id")
    @classmethod
    def valid_key(cls, value):
        if not CLIENT_MESSAGE_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid client message ID")
        return value

    @model_validator(mode="after")
    def supported_intent(self):
        if not self.text.strip() and not self.attachments:
            raise ValueError("Text and attachments cannot both be empty")
        if len({a.attachment_id for a in self.attachments}) != len(self.attachments):
            raise ValueError("Duplicate attachment reference")
        if (self.intent == "explicit_workflow") != (self.workflow is not None):
            raise ValueError("Explicit Workflow requires an exact selection")
        if self.intent == "explicit_workflow" and not self.text.strip():
            raise ValueError("Workflow requires a task objective")
        if (self.intent in {"send", "explicit_workflow"}) != (self.target_agent_run_id is None):
            raise ValueError("Control input requires an explicit run target; send forbids it")
        return self
