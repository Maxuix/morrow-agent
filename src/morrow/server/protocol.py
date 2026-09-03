"""Versioned wire protocol for the local Core API.

Request bodies are strict ``ProtocolModel`` objects (extras rejected), so
clients cannot smuggle unexpected fields — including permission-elevation
attempts — past the transport. Response projections are assembled field by
field in ``projections``; this module carries shapes, not business rules.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, field_validator

from morrow.core.domain import COMMAND_ID_PREFIX, validate_prefixed_id
from morrow.core.models import ProtocolModel
from morrow.core.workflows.patches import FutureGraphPatch

PROTOCOL_VERSION = 1
API_PREFIX = "/v1"

CommandId = Annotated[str, Field(min_length=1, max_length=128)]


def _optional_command_id(value: str | None) -> str | None:
    if value is None:
        return None
    return validate_prefixed_id(value, COMMAND_ID_PREFIX)


class CommandRequest(ProtocolModel):
    """Base for every mutation body: a client-supplied idempotent Command ID."""

    command_id: str | None = None

    @field_validator("command_id")
    @classmethod
    def _valid_command_id(cls, value: str | None) -> str | None:
        return _optional_command_id(value)


class SessionCreateRequest(CommandRequest):
    session_id: str | None = None


class TaskCreateRequest(CommandRequest):
    session_id: str = Field(min_length=1, max_length=128)


class TaskTransitionRequest(CommandRequest):
    expected_row_version: int | None = Field(default=None, ge=1)


class WorkflowStartRequest(CommandRequest):
    workflow_definition_id: str = Field(min_length=1, max_length=128)
    workflow_revision_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    root_task_run_id: str = Field(min_length=1, max_length=128)
    expected_root_row_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=4096)
    client_message_id: str | None = Field(default=None, max_length=128)


class WorkflowControlRequest(CommandRequest):
    """Pause / cancel carry no further facts."""


class WorkflowResumeRequest(CommandRequest):
    drive: bool = True


class WorkflowRerunRequest(CommandRequest):
    full: bool = False


class WorkflowAbandonRequest(CommandRequest):
    expected_row_version: int = Field(ge=1)


class PatchCommandRequest(CommandRequest):
    patch: FutureGraphPatch


class ApprovalResolveRequest(CommandRequest):
    approved: bool


class EventWire(ProtocolModel):
    cursor: int = Field(ge=1)
    event_id: str
    event_type: str
    aggregate_kind: str
    aggregate_id: str
    payload: dict[str, Any]
    created_at: str


class EventsPageWire(ProtocolModel):
    events: tuple[EventWire, ...]
    latest_cursor: int = Field(ge=0)
    has_more: bool


class MetaWire(ProtocolModel):
    protocol_version: int
    workspace_id: str
    latest_cursor: int = Field(ge=0)
