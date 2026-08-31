"""One ordinary-entry guard shared by Task and Turn repositories."""

from morrow.core.domain import TaskRunPurpose
from morrow.core.store import StorageError, StorageErrorCode


def require_user_task(backend, task):
    owned = task.purpose == TaskRunPurpose.WORKFLOW_NODE
    if not owned and backend.schema_version() >= 24:
        owned = (
            backend.read_one(
                "SELECT 1 FROM workflow_runs WHERE workspace_id=? AND root_task_run_id=? "
                "AND status IN ('queued','running','blocked')",
                (task.workspace_id, task.task_run_id),
            )
            is not None
        )
    if owned:
        raise StorageError(StorageErrorCode.UNAVAILABLE, "Task is owned by Workflow lifecycle")
