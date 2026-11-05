"""Read-only validation of change/restore payloads and their snapshot edges."""

from pydantic import ValidationError

from morrow.core.artifacts import ArtifactKind
from morrow.core.doctor import DoctorSeverity
from morrow.core.workspace_changes import RestoreOperation, WorkspaceChange


def inspect_workspace_changes(journal, workspace_id, counts, issues, *, issue_factory):
    for table, model in (
        ("workspace_changes", WorkspaceChange),
        ("workspace_restore_operations", RestoreOperation),
    ):
        rows = journal._backend.read_all(
            f"SELECT payload_json FROM {table} WHERE workspace_id=?", (workspace_id,)
        )
        counts[table] = len(rows)
        for (payload,) in rows:
            try:
                value = model.model_validate_json(payload)
                if value.workspace_id != workspace_id:
                    raise ValueError("scope")
                manifests = (
                    [m for item in value.items for m in (item.expected, item.desired)]
                    if isinstance(value, RestoreOperation)
                    else [value.before, value.after]
                )
                for manifest in manifests:
                    if not manifest.snapshot_id:
                        continue
                    artifact = journal.get_artifact(workspace_id, manifest.snapshot_id)
                    if (
                        artifact is None
                        or artifact.kind is not ArtifactKind.WORKSPACE_SNAPSHOT
                        or artifact.sha256 != manifest.sha256
                        or artifact.byte_size != manifest.byte_size
                    ):
                        raise ValueError("snapshot")
                    if not journal.list_artifact_references(workspace_id, manifest.snapshot_id):
                        raise ValueError("reference")
            except (ValueError, ValidationError):
                issues.append(
                    issue_factory(
                        "workspace_change_invalid",
                        DoctorSeverity.ERROR,
                        "Workspace change or restore payload/references are invalid",
                    )
                )
