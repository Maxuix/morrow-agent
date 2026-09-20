"""Durable chat control receipts: acceptance, outcome and replay identity.

A control input is *accepted* before it is interpreted. The receipt is the one
durable record of that acceptance — raw text, resolved intent, disposition and a
bounded outcome — so a retried request replays the stored result instead of
executing twice, and a reloaded page can still show what the user asked.

This owner never writes conversation records: it is a read-only projection
source for the chat transcript.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from morrow.core.models import ProtocolModel

ControlReceiptStatus = Literal[
    "accepted",
    "executed",
    "acknowledged",
    "answered",
    "unresolved",
    "needs_choice",
    "steer",
    "failed",
]

CONTROL_RECEIPT_STATUSES: tuple[str, ...] = (
    "accepted",
    "executed",
    "acknowledged",
    "answered",
    "unresolved",
    "needs_choice",
    "steer",
    "failed",
)

#: Statuses that mean "the command finished"; a retry must replay, never re-run.
TERMINAL_CONTROL_STATUSES = frozenset(CONTROL_RECEIPT_STATUSES) - {"accepted"}


class ControlReceipt(ProtocolModel):
    workspace_id: str
    session_id: str
    command_id: str = Field(min_length=1, max_length=128)
    client_message_id: str | None = Field(default=None, max_length=128)
    text: str = Field(min_length=1, max_length=4096)
    state: str | None = Field(default=None, max_length=32)
    intent: str | None = Field(default=None, max_length=32)
    status: ControlReceiptStatus = "accepted"
    disposition: str | None = Field(default=None, max_length=32)
    message: str | None = Field(default=None, max_length=2048)
    result_kind: str | None = Field(default=None, max_length=32)
    result_id: str | None = Field(default=None, max_length=128)
    error_code: str | None = Field(default=None, max_length=64)
    outcome: dict = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
