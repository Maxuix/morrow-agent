"""SQLite persistence for Recovery reports and idempotency receipts."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import canonical_json_bytes
from morrow.core.recovery import RecoveryReceipt, RecoveryReport, RecoveryResolution
from morrow.core.store import StorageError, StorageErrorCode

_REPORT_COLUMNS = (
    "report_id, workspace_id, session_id, turn_id, agent_run_id, status, "
    "payload_json, payload_bytes, created_at_unix, resolved_at_unix"
)
_RECEIPT_COLUMNS = "session_id, command_id, request_digest, report_id, item_id, resolution"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _optional_unix(value: datetime | None) -> int | None:
    return _unix(value) if value is not None else None


class SqliteRecoveryJournal:
    """Bounded Recovery repository sharing the journal's outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        session_exists: Callable[[str, str], bool],
    ) -> None:
        self.backend = backend
        self.session_exists = session_exists

    def put_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        if report.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational recovery report is outside the workspace"
            )

        def work() -> RecoveryReport:
            if not self.session_exists(workspace_id, report.session_id):
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            payload = canonical_json_bytes(report.model_dump(mode="json"))
            self.backend.executor().execute(
                f"INSERT INTO recovery_reports({_REPORT_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report.report_id,
                    report.workspace_id,
                    report.session_id,
                    report.turn_id,
                    report.agent_run_id,
                    report.status.value,
                    payload.decode("utf-8"),
                    len(payload),
                    _unix(report.created_at),
                    _optional_unix(report.resolved_at),
                ),
            )
            loaded = self.get_open_report(workspace_id, report.session_id)
            if loaded is None:
                loaded = self.get_report(workspace_id, report.report_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational recovery report could not be read",
                )
            return loaded

        return self.backend.transact(work)

    def get_report(self, workspace_id: str, report_id: str) -> RecoveryReport | None:
        row = self.backend.read_one(
            f"SELECT {_REPORT_COLUMNS} FROM recovery_reports "
            "WHERE report_id = ? AND workspace_id = ?",
            (report_id, workspace_id),
        )
        return _report_from_row(row) if row is not None else None

    def get_open_report(self, workspace_id: str, session_id: str) -> RecoveryReport | None:
        row = self.backend.read_one(
            f"SELECT {_REPORT_COLUMNS} FROM recovery_reports "
            "WHERE workspace_id = ? AND session_id = ? AND status = 'open'",
            (workspace_id, session_id),
        )
        return _report_from_row(row) if row is not None else None

    def list_recovery_reports(
        self, workspace_id: str, session_id: str
    ) -> tuple[RecoveryReport, ...]:
        rows = self.backend.read_all(
            f"SELECT {_REPORT_COLUMNS} FROM recovery_reports "
            "WHERE workspace_id = ? AND session_id = ? "
            "ORDER BY created_at_unix ASC, report_id ASC",
            (workspace_id, session_id),
        )
        return tuple(_report_from_row(row) for row in rows)

    def save_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        if report.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational recovery report is outside the workspace"
            )

        def work() -> RecoveryReport:
            if self.get_report(workspace_id, report.report_id) is None:
                raise StorageError(
                    StorageErrorCode.NOT_FOUND, "operational recovery report is missing"
                )
            payload = canonical_json_bytes(report.model_dump(mode="json"))
            self.backend.executor().execute(
                """
                UPDATE recovery_reports
                SET status = ?, payload_json = ?, payload_bytes = ?, resolved_at_unix = ?
                WHERE report_id = ? AND workspace_id = ?
                """,
                (
                    report.status.value,
                    payload.decode("utf-8"),
                    len(payload),
                    _optional_unix(report.resolved_at),
                    report.report_id,
                    workspace_id,
                ),
            )
            loaded = self.get_report(workspace_id, report.report_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational recovery report could not be read",
                )
            return loaded

        return self.backend.transact(work)

    def get_recovery_receipt(
        self, workspace_id: str, session_id: str, command_id: str
    ) -> RecoveryReceipt | None:
        if not self.session_exists(workspace_id, session_id):
            return None
        row = self.backend.read_one(
            f"SELECT {_RECEIPT_COLUMNS} FROM recovery_receipts "
            "WHERE session_id = ? AND command_id = ?",
            (session_id, command_id),
        )
        return _receipt_from_row(row) if row is not None else None

    def put_recovery_receipt(self, workspace_id: str, receipt: RecoveryReceipt) -> RecoveryReceipt:
        def work() -> RecoveryReceipt:
            if not self.session_exists(workspace_id, receipt.session_id):
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            self.backend.executor().execute(
                f"INSERT INTO recovery_receipts({_RECEIPT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    receipt.session_id,
                    receipt.command_id,
                    receipt.request_digest,
                    receipt.report_id,
                    receipt.item_id,
                    receipt.resolution.value,
                ),
            )
            loaded = self.get_recovery_receipt(workspace_id, receipt.session_id, receipt.command_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational recovery receipt could not be read",
                )
            return loaded

        return self.backend.transact(work)


def _report_from_row(row: tuple[object, ...]) -> RecoveryReport:
    payload = json.loads(str(row[6]))
    if not isinstance(payload, dict):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "recovery report is not a mapping")
    return RecoveryReport.model_validate(payload)


def _receipt_from_row(row: tuple[object, ...]) -> RecoveryReceipt:
    return RecoveryReceipt(
        session_id=str(row[0]),
        command_id=str(row[1]),
        request_digest=str(row[2]),
        report_id=str(row[3]),
        item_id=str(row[4]) if row[4] is not None else None,
        resolution=RecoveryResolution(str(row[5])),
    )
