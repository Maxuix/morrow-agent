"""Durable acceptance of typed chat control inputs (plan D06/D07/D08).

Acceptance happens before interpretation: the raw text and its identity are
committed first, the resolved outcome is written back afterwards. A retry with
the same command id replays the stored receipt and never re-executes the
command; the same id with a different payload is a conflict.
"""

from __future__ import annotations

import json

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.control_receipts import TERMINAL_CONTROL_STATUSES, ControlReceipt

MAX_RECEIPT_PAGE = 200
OUTCOME_TEXT_LIMIT = 2048


def _bounded(value, limit: int = OUTCOME_TEXT_LIMIT):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def bounded_outcome(outcome: dict) -> dict:
    """The durable, content-free summary of one control outcome."""

    stored = {}
    for key in ("disposition", "intent", "message", "next_action", "replayed"):
        if outcome.get(key) is not None:
            stored[key] = _bounded(outcome[key])
    if outcome.get("target_agent_run_id"):
        stored["target_agent_run_id"] = outcome["target_agent_run_id"]
    run = outcome.get("run")
    if isinstance(run, dict):
        stored["run"] = {
            key: run[key]
            for key in (
                "workflow_run_id",
                "status",
                "result_status",
                "row_version",
                "pause_requested",
            )
            if key in run
        }
    binding = outcome.get("binding")
    if isinstance(binding, dict):
        stored["binding"] = {
            key: binding[key]
            for key in ("planning_binding_id", "mode", "parent_run_id")
            if key in binding
        }
    operation = outcome.get("operation")
    if isinstance(operation, dict):
        stored["operation"] = {
            key: operation[key]
            for key in ("planning_operation_id", "status", "error_code")
            if key in operation
        }
    return json.loads(json.dumps(stored, ensure_ascii=False, default=str))


class ControlReceiptService:
    def __init__(self, manager, journal, workspace_id, id_source):
        self.manager = manager
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.records = journal.control_receipts

    def accept(
        self,
        session_id: str,
        *,
        command_id: str,
        text: str,
        client_message_id: str | None = None,
    ) -> tuple[ControlReceipt, bool]:
        """Persist acceptance first; return the receipt and whether it replayed."""

        self.manager.require_session(session_id)

        def work(_):
            existing = self.records.get(self.workspace_id, session_id, command_id)
            if existing is not None:
                if existing.text != text:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "control command ID was reused with a different message",
                    )
                return existing, True
            now = self.journal.now()
            receipt = ControlReceipt(
                workspace_id=self.workspace_id,
                session_id=session_id,
                command_id=command_id,
                client_message_id=client_message_id,
                text=text,
                status="accepted",
                created_at=now,
                updated_at=now,
            )
            self.records.insert(receipt)
            return receipt, False

        return self.journal.transact(work)

    def settle(
        self,
        receipt: ControlReceipt,
        *,
        state: str | None,
        intent: str | None,
        outcome: dict,
    ) -> ControlReceipt:
        disposition = outcome.get("disposition")
        status = disposition if disposition in TERMINAL_CONTROL_STATUSES else "executed"
        return self._save(
            receipt,
            status=status,
            state=state,
            intent=intent or outcome.get("intent"),
            disposition=disposition,
            message=_bounded(outcome.get("message")),
            result_kind="workflow_run" if outcome.get("run") else None,
            result_id=(outcome.get("run") or {}).get("workflow_run_id")
            if isinstance(outcome.get("run"), dict)
            else None,
            error_code=None,
            outcome=bounded_outcome(outcome),
        )

    def fail(
        self, receipt: ControlReceipt, *, error_code: str, message: str | None = None
    ) -> ControlReceipt:
        return self._save(
            receipt,
            status="failed",
            state=None,
            intent=None,
            disposition="failed",
            message=_bounded(message),
            result_kind=None,
            result_id=None,
            error_code=error_code,
            outcome={},
        )

    def _save(
        self,
        receipt: ControlReceipt,
        *,
        status,
        state,
        intent,
        disposition,
        message,
        result_kind,
        result_id,
        error_code,
        outcome,
    ) -> ControlReceipt:
        updated = receipt.model_copy(
            update={
                "status": status,
                "state": state or receipt.state,
                "intent": intent,
                "disposition": disposition,
                "message": message,
                "result_kind": result_kind,
                "result_id": result_id,
                "error_code": error_code,
                "outcome": outcome,
                "revision": receipt.revision + 1,
                "updated_at": self.journal.now(),
            }
        )

        def work(_):
            self.records.save(updated, expected_revision=receipt.revision)
            return updated

        return self.journal.transact(work)

    def get(self, session_id: str, command_id: str) -> ControlReceipt:
        self.manager.require_session(session_id)
        receipt = self.records.get(self.workspace_id, session_id, command_id)
        if receipt is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Control receipt is missing")
        return receipt

    def list(self, session_id: str, *, after_unix: int = 0, limit: int = 50) -> dict:
        self.manager.require_session(session_id)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_RECEIPT_PAGE:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Invalid receipt limit")
        items = self.records.list(
            self.workspace_id, session_id, after_unix=max(0, after_unix), limit=limit
        )
        return {"items": [self.wire(item) for item in items]}

    @staticmethod
    def wire(receipt: ControlReceipt) -> dict:
        return {
            "command_id": receipt.command_id,
            "client_message_id": receipt.client_message_id,
            "session_id": receipt.session_id,
            "text": receipt.text,
            "state": receipt.state,
            "intent": receipt.intent,
            "status": receipt.status,
            "disposition": receipt.disposition,
            "message": receipt.message,
            "result_kind": receipt.result_kind,
            "result_id": receipt.result_id,
            "error_code": receipt.error_code,
            "outcome": receipt.outcome,
            "revision": receipt.revision,
            "created_at": receipt.created_at.isoformat(),
            "updated_at": receipt.updated_at.isoformat(),
        }
