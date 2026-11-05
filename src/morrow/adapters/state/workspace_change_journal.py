"""Two payload authorities and indexed scope/identity; enums validated in Python."""

from __future__ import annotations

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import ArtifactReference
from morrow.core.workspace_changes import RestoreOperation, WorkspaceChange

CHANGE_SCHEMA = (
    """CREATE TABLE workspace_changes (
        workspace_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        change_id TEXT NOT NULL UNIQUE, workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL, task_run_id TEXT NOT NULL,
        path TEXT NOT NULL, mutation_key TEXT NOT NULL,
        payload_json TEXT NOT NULL, row_version INTEGER NOT NULL,
        UNIQUE(workspace_id, mutation_key)
    )""",
    "CREATE INDEX workspace_changes_task ON workspace_changes(workspace_id,task_run_id,workspace_sequence)",
    "CREATE INDEX workspace_changes_path ON workspace_changes(workspace_id,path,workspace_sequence)",
    """CREATE TABLE workspace_restore_operations (
        operation_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
        session_id TEXT NOT NULL, command_id TEXT NOT NULL,
        apply_command_id TEXT, payload_json TEXT NOT NULL, row_version INTEGER NOT NULL,
        UNIQUE(workspace_id,command_id), UNIQUE(workspace_id,apply_command_id)
    )""",
)


class WorkspaceChangeJournal:
    def __init__(self, backend, journal):
        self.backend = backend
        self.journal = journal

    def get(self, workspace_id, change_id):
        row = self.backend.read_one(
            "SELECT payload_json,workspace_sequence FROM workspace_changes WHERE workspace_id=? AND change_id=?",
            (workspace_id, change_id),
        )
        return self._change(row)

    @staticmethod
    def _change(row):
        if row is None:
            return None
        return WorkspaceChange.model_validate_json(row[0]).model_copy(
            update={"workspace_sequence": int(row[1])}
        )

    def find(self, workspace_id, mutation_key):
        return self._change(
            self.backend.read_one(
                "SELECT payload_json,workspace_sequence FROM workspace_changes WHERE workspace_id=? AND mutation_key=?",
                (workspace_id, mutation_key),
            )
        )

    def list(self, workspace_id, *, task_ids=(), path=None, after=0, through=None, limit=200):
        sql = "SELECT payload_json,workspace_sequence FROM workspace_changes WHERE workspace_id=? AND workspace_sequence>?"
        params = [workspace_id, after]
        if task_ids:
            sql += " AND task_run_id IN (" + ",".join("?" for _ in task_ids) + ")"
            params.extend(task_ids)
        if path is not None:
            sql += " AND path=?"
            params.append(path)
        if through is not None:
            sql += " AND workspace_sequence<=?"
            params.append(through)
        sql += " ORDER BY workspace_sequence LIMIT ?"
        params.append(limit)
        return tuple(self._change(r) for r in self.backend.read_all(sql, tuple(params)))

    def high_watermark(self, workspace_id):
        return int(
            self.backend.read_one(
                "SELECT COALESCE(MAX(workspace_sequence),0) FROM workspace_changes WHERE workspace_id=?",
                (workspace_id,),
            )[0]
        )

    def _link(self, value, *, restore=False):
        manifests = (
            [m for item in value.items for m in (item.expected, item.desired)]
            if restore
            else [value.before, value.after]
        )
        refs = tuple(
            ArtifactReference(artifact_id=m.snapshot_id, role="restore_snapshot")
            for m in manifests
            if m.snapshot_id
        )
        self.journal._replace_artifact_references(
            value.workspace_id,
            owner_kind="workspace_restore" if restore else "workspace_change",
            owner_id=value.operation_id if restore else value.change_id,
            references=refs,
            created_at=value.created_at,
        )

    def insert(self, change):
        change = WorkspaceChange.model_validate(change)

        def work():
            previous = self.find(change.workspace_id, change.mutation_key)
            if previous is not None:
                return previous
            self.backend.executor().execute(
                "INSERT INTO workspace_changes(change_id,workspace_id,session_id,task_run_id,path,mutation_key,payload_json,row_version) VALUES(?,?,?,?,?,?,?,?)",
                (
                    change.change_id,
                    change.workspace_id,
                    change.session_id,
                    change.task_run_id,
                    change.path,
                    change.mutation_key,
                    change.model_dump_json(),
                    change.row_version,
                ),
            )
            self._link(change)
            return self.get(change.workspace_id, change.change_id)

        return self.backend.transact(work)

    def save(self, change):
        change = WorkspaceChange.model_validate(change)

        def work():
            current = self.get(change.workspace_id, change.change_id)
            if current is None or current.row_version != change.row_version:
                raise ApplicationError(ApplicationErrorCode.STALE, "修改记录已变化")
            updated = change.model_copy(update={"row_version": change.row_version + 1})
            self.backend.executor().execute(
                "UPDATE workspace_changes SET payload_json=?,row_version=? WHERE workspace_id=? AND change_id=?",
                (
                    updated.model_dump_json(),
                    updated.row_version,
                    change.workspace_id,
                    change.change_id,
                ),
            )
            self._link(updated)
            return updated

        return self.backend.transact(work)

    def operation(self, workspace_id, operation_id=None, command_id=None, apply_command_id=None):
        field, value = (
            ("operation_id", operation_id)
            if operation_id is not None
            else ("command_id", command_id)
            if command_id is not None
            else ("apply_command_id", apply_command_id)
        )
        row = self.backend.read_one(
            f"SELECT payload_json FROM workspace_restore_operations WHERE workspace_id=? AND {field}=?",
            (workspace_id, value),
        )
        return RestoreOperation.model_validate_json(row[0]) if row else None

    def put_operation(self, operation, *, new=False):
        operation = RestoreOperation.model_validate(operation)

        def work():
            current = self.operation(operation.workspace_id, operation.operation_id)
            if (
                new
                and current is not None
                or not new
                and (current is None or current.row_version != operation.row_version)
            ):
                raise ApplicationError(ApplicationErrorCode.STALE, "恢复操作已变化")
            updated = (
                operation
                if new
                else operation.model_copy(update={"row_version": operation.row_version + 1})
            )
            if new:
                self.backend.executor().execute(
                    "INSERT INTO workspace_restore_operations(operation_id,workspace_id,session_id,command_id,apply_command_id,payload_json,row_version) VALUES(?,?,?,?,?,?,?)",
                    (
                        updated.operation_id,
                        updated.workspace_id,
                        updated.session_id,
                        updated.command_id,
                        updated.apply_command_id,
                        updated.model_dump_json(),
                        updated.row_version,
                    ),
                )
            else:
                self.backend.executor().execute(
                    "UPDATE workspace_restore_operations SET apply_command_id=?,payload_json=?,row_version=? WHERE workspace_id=? AND operation_id=?",
                    (
                        updated.apply_command_id,
                        updated.model_dump_json(),
                        updated.row_version,
                        updated.workspace_id,
                        updated.operation_id,
                    ),
                )
            self._link(updated, restore=True)
            return updated

        return self.backend.transact(work)

    def pending(self, workspace_id):
        rows = self.backend.read_all(
            "SELECT payload_json FROM workspace_restore_operations WHERE workspace_id=?",
            (workspace_id,),
        )
        return tuple(
            op
            for r in rows
            if (op := RestoreOperation.model_validate_json(r[0])).state
            in {"prepared", "applying", "needs_reconciliation"}
        )
