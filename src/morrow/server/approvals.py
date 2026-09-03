"""Live approval waiters resumed after the Core API commits their decisions."""

from __future__ import annotations

from morrow.core.application import APPROVAL_REQUESTED_EVENT
from morrow.core.models import ToolApprovalDecision, ToolApprovalRequest

_PREVIEW_LINES_MAX = 8


class ServerApprovalPort:
    def __init__(self, waiters, emitter=None) -> None:
        self._waiters = waiters
        self.emitter = emitter

    async def request(self, request: ToolApprovalRequest) -> ToolApprovalDecision:
        if request.approval_id is None:
            return ToolApprovalDecision(approved=False)
        future = self._waiters.begin(request.approval_id)
        try:
            self._emit_requested(request)
            approved = await future
        finally:
            self._waiters.forget(request.approval_id)
        return ToolApprovalDecision(approved=approved)

    def _emit_requested(self, request: ToolApprovalRequest) -> None:
        if self.emitter is None:
            return
        self.emitter.emit(
            APPROVAL_REQUESTED_EVENT,
            "approval",
            request.approval_id,
            {
                "effect": request.effect.value,
                "reason_codes": list(request.reason_codes),
                # The approval query owns the bounded preview. The lifecycle
                # event is only a pull hint and must never copy user values.
                "preview_line_count": min(len(request.preview), _PREVIEW_LINES_MAX),
            },
        )
