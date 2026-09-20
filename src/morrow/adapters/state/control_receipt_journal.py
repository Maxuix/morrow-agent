"""Durable read/write access to current chat control receipts."""

from __future__ import annotations

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.control_receipts import ControlReceipt

_COLUMNS = "payload_json"


def _row_to_receipt(row) -> ControlReceipt:
    try:
        return ControlReceipt.model_validate_json(str(row[0]))
    except (TypeError, ValueError) as exc:
        raise ValueError("control receipt is invalid") from exc


class ControlReceiptJournal:
    def __init__(self, backend: SqliteJournalBackend) -> None:
        self._backend = backend

    def insert(self, receipt: ControlReceipt) -> None:
        self._backend.executor().execute(
            "INSERT INTO command_receipts(receipt_kind,workspace_id,session_id,receipt_key,"
            "command_id,client_message_id,payload_json,revision,created_at_unix,updated_at_unix) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chat_control",
                receipt.workspace_id,
                receipt.session_id,
                f"{receipt.session_id}:{receipt.command_id}",
                receipt.command_id,
                receipt.client_message_id,
                receipt.model_dump_json(),
                receipt.revision,
                int(receipt.created_at.timestamp()),
                int(receipt.updated_at.timestamp()),
            ),
        )

    def save(self, receipt: ControlReceipt, *, expected_revision: int) -> None:
        self._backend.executor().execute(
            "UPDATE command_receipts SET client_message_id=?, payload_json=?, revision=?, "
            "updated_at_unix=? WHERE receipt_kind='chat_control' AND workspace_id=? "
            "AND session_id=? AND receipt_key=? AND revision=?",
            (
                receipt.client_message_id,
                receipt.model_dump_json(),
                receipt.revision,
                int(receipt.updated_at.timestamp()),
                receipt.workspace_id,
                receipt.session_id,
                f"{receipt.session_id}:{receipt.command_id}",
                expected_revision,
            ),
        )
        current = self.get(receipt.workspace_id, receipt.session_id, receipt.command_id)
        if current is None or current.revision != receipt.revision:
            raise ValueError("control receipt revision changed")

    def get(self, workspace_id: str, session_id: str, command_id: str) -> ControlReceipt | None:
        row = self._backend.read_one(
            "SELECT payload_json FROM command_receipts WHERE receipt_kind='chat_control' "
            "AND workspace_id=? AND session_id=? AND receipt_key=?",
            (workspace_id, session_id, f"{session_id}:{command_id}"),
        )
        return _row_to_receipt(row) if row is not None else None

    def list(
        self, workspace_id: str, session_id: str, *, after_unix: int = 0, limit: int = 50
    ) -> tuple[ControlReceipt, ...]:
        rows = self._backend.read_all(
            "SELECT payload_json FROM command_receipts WHERE receipt_kind='chat_control' "
            "AND workspace_id=? AND session_id=? AND created_at_unix>=? "
            "ORDER BY created_at_unix ASC, receipt_key ASC LIMIT ?",
            (workspace_id, session_id, after_unix, limit),
        )
        return tuple(_row_to_receipt(row) for row in rows)
