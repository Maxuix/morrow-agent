"""The server approval port: live waiters resolved through the Core API.

A driver hitting an approval gate durably creates the pending Approval through
the existing tool-cycle machinery, then parks here. The API resolution surface
delivers the user's decision to the waiter, and the tool cycle performs the
durable consume — exactly one writer, identical to the terminal path. When no
live waiter exists (recovered or externally driven runs), the API resolves
through the durable ``resolve_approval`` path instead.
"""

from __future__ import annotations

from morrow.core.application import APPROVAL_REQUESTED_EVENT
from morrow.core.models import ToolApprovalDecision, ToolApprovalRequest

_PREVIEW_LINE_MAX = 200
_PREVIEW_LINES_MAX = 8


class ServerApprovalPort:
    def __init__(self, waiters, emitter=None) -> None:
        self._waiters = waiters
        # Late-bound: the emitter needs the journal built after this port is
        # handed to the session composition.
        self.emitter = emitter

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        if request.approval_id is None:
            return ToolApprovalDecision(approved=False)
        self._emit_requested(request)
        future = self._waiters.begin(request.approval_id)
        try:
            approved = await future
        finally:
            self._waiters.forget(request.approval_id)
        return ToolApprovalDecision(approved=approved)

    def _emit_requested(self, request: ToolApprovalRequest) -> None:
        if self.emitter is None:
            return
        preview = [line[:_PREVIEW_LINE_MAX] for line in request.preview[:_PREVIEW_LINES_MAX]]
        self.emitter.emit(
            APPROVAL_REQUESTED_EVENT,
            "approval",
            request.approval_id,
            {
                "effect": request.effect.value,
                "reason_codes": list(request.reason_codes),
                "preview": preview,
            },
        )
