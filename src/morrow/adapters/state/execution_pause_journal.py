"""SQLite repository for durable execution pause points (P02, spec 3.2).

The pause point row is the accepted pause control cycle: the frozen
``PauseIntentFact`` payload plus the bounded safe point, continuation refs and
the separate cancel fact. Rows are written through the shared transaction
backend like every other operational journal domain; the lifecycle itself is
advanced by ``morrow.application.execution_pause.ExecutionPauseService`` under
row-version OCC.
"""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.execution_pause import PauseIntentRequest, WorkflowPausePoint
from morrow.core.store import StorageError, StorageErrorCode


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None


class SqliteExecutionPauseJournal:
    """Pause-point storage; composes on the shared journal backend."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def _load(self, row) -> WorkflowPausePoint | None:
        if row is None:
            return None
        try:
            return WorkflowPausePoint.model_validate_json(row[0])
        except ValueError:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Workflow pause point record is corrupt"
            ) from None

    _COLUMNS = (
        "pause_point_id, workspace_id, owner, owner_id, control_generation, command_id, "
        "reason, lifecycle, node_run_id, segment_id, requested_at_unix, suspended_at_unix, "
        "resumed_at_unix, continuation_segment_id, continuation_command_id, "
        "continuation_input, cancel_command_id, cancel_reason, cancelled_at_unix, "
        "row_version, created_at_unix, updated_at_unix, body_json"
    )

    def insert_pause_point(self, value: WorkflowPausePoint) -> WorkflowPausePoint:
        def work():
            fact = value.fact
            self.backend.executor().execute(
                f"INSERT INTO workflow_pause_points ({self._COLUMNS}) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.pause_point_id,
                    value.workspace_id,
                    fact.owner,
                    fact.owner_id,
                    fact.control_generation,
                    fact.command_id,
                    fact.reason,
                    fact.lifecycle,
                    value.node_run_id,
                    value.segment_id,
                    _unix(fact.requested_at),
                    _unix(fact.suspended_at) if fact.suspended_at else None,
                    _unix(fact.resumed_at) if fact.resumed_at else None,
                    value.continuation_segment_id,
                    value.continuation_command_id,
                    value.continuation_input,
                    value.cancel_command_id,
                    value.cancel_reason,
                    _unix(value.cancelled_at) if value.cancelled_at else None,
                    value.row_version,
                    _unix(value.created_at),
                    _unix(value.updated_at),
                    value.model_dump_json(),
                ),
            )
            return value

        return self.backend.transact(work)

    def save_pause_point(
        self, value: WorkflowPausePoint, *, expected_row_version: int
    ) -> WorkflowPausePoint:
        """OCC update of the mutable pause lifecycle columns."""

        def work():
            current = self.backend.read_one(
                "SELECT body_json, row_version FROM workflow_pause_points WHERE pause_point_id=?",
                (value.pause_point_id,),
            )
            if current is None:
                raise ValueError("WorkflowPausePoint is missing")
            if int(current[1]) != expected_row_version:
                raise ValueError("WorkflowPausePoint revision conflict")
            fact = value.fact
            self.backend.executor().execute(
                "UPDATE workflow_pause_points SET lifecycle=?, suspended_at_unix=?, "
                "resumed_at_unix=?, continuation_segment_id=?, continuation_command_id=?, "
                "continuation_input=?, cancel_command_id=?, cancel_reason=?, "
                "cancelled_at_unix=?, row_version=?, updated_at_unix=?, body_json=? "
                "WHERE pause_point_id=? AND row_version=?",
                (
                    fact.lifecycle,
                    _unix(fact.suspended_at) if fact.suspended_at else None,
                    _unix(fact.resumed_at) if fact.resumed_at else None,
                    value.continuation_segment_id,
                    value.continuation_command_id,
                    value.continuation_input,
                    value.cancel_command_id,
                    value.cancel_reason,
                    _unix(value.cancelled_at) if value.cancelled_at else None,
                    value.row_version,
                    _unix(value.updated_at),
                    value.model_dump_json(),
                    value.pause_point_id,
                    expected_row_version,
                ),
            )
            return value

        return self.backend.transact(work)

    def get_pause_point(self, pause_point_id: str) -> WorkflowPausePoint | None:
        return self._load(
            self.backend.read_one(
                "SELECT body_json FROM workflow_pause_points WHERE pause_point_id=?",
                (pause_point_id,),
            )
        )

    def find_pause_point_by_command(
        self, workspace_id: str, request: PauseIntentRequest
    ) -> WorkflowPausePoint | None:
        return self._load(
            self.backend.read_one(
                "SELECT body_json FROM workflow_pause_points "
                "WHERE workspace_id=? AND owner=? AND owner_id=? AND command_id=?",
                (workspace_id, request.owner, request.owner_id, request.command_id),
            )
        )

    def latest_pause_point(
        self, workspace_id: str, *, owner: str, owner_id: str
    ) -> WorkflowPausePoint | None:
        return self._load(
            self.backend.read_one(
                "SELECT body_json FROM workflow_pause_points "
                "WHERE workspace_id=? AND owner=? AND owner_id=? "
                "ORDER BY control_generation DESC LIMIT 1",
                (workspace_id, owner, owner_id),
            )
        )

    def next_control_generation(self, workspace_id: str, *, owner: str, owner_id: str) -> int:
        row = self.backend.read_one(
            "SELECT MAX(control_generation) FROM workflow_pause_points "
            "WHERE workspace_id=? AND owner=? AND owner_id=?",
            (workspace_id, owner, owner_id),
        )
        return int(row[0]) + 1 if row and row[0] is not None else 1

    def list_pause_points(
        self, workspace_id: str, *, owner: str, owner_id: str
    ) -> tuple[WorkflowPausePoint, ...]:
        rows = self.backend.read_all(
            "SELECT body_json FROM workflow_pause_points "
            "WHERE workspace_id=? AND owner=? AND owner_id=? "
            "ORDER BY control_generation",
            (workspace_id, owner, owner_id),
        )
        return tuple(point for row in rows if (point := self._load(row)) is not None)
