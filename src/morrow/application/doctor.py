"""Read-only diagnosis of the SQLite and managed Artifact halves."""

from __future__ import annotations

import os
import stat
from collections import Counter
from pathlib import Path

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.core.artifacts import (
    ARTIFACT_FILE_SUFFIX,
    ARTIFACT_TEMP_SUFFIX,
    ArtifactIntegrityError,
    ArtifactState,
)
from morrow.core.doctor import DoctorHealth, DoctorIssue, DoctorReport, DoctorSeverity
from morrow.core.domain import (
    WORKSPACE_ID_PREFIX,
    SessionLifecycle,
    validate_prefixed_id,
)
from morrow.core.execution import ToolExecutionState
from morrow.core.permissions import capability_grant_digest
from morrow.core.store import DIRECTORY_MODE, FILE_MODE, StorageError, StoreHealth, StoreOpenMode
from morrow.runtime.conversation import ConversationSnapshot
from morrow.runtime.durable_log import conversation_record_from_durable


class OperationalDoctor:
    """Inspect without mutating the store, YAML, credentials, or Artifact metadata."""

    def __init__(self, store: OperationalStore, *, data_root: Path | None = None) -> None:
        self.store = store
        self.data_root = data_root or store.layout.data_root

    def inspect(self, workspace_id: str) -> DoctorReport:
        workspace_id = validate_prefixed_id(workspace_id, WORKSPACE_ID_PREFIX)
        issues: list[DoctorIssue] = []
        counts: Counter[str] = Counter()
        checks = ["store_identity", "sqlite_integrity", "foreign_keys"]
        try:
            classification = self.store.classify()
        except StorageError as exc:
            return self._report(
                workspace_id,
                DoctorHealth.NEEDS_REPAIR,
                None,
                checks,
                counts,
                [
                    self._issue(
                        f"store_{exc.code.value}",
                        DoctorSeverity.ERROR,
                        "operational store is unavailable",
                    )
                ],
            )
        if not classification.present:
            return self._report(
                workspace_id,
                DoctorHealth.NEEDS_REPAIR,
                None,
                checks,
                counts,
                [
                    self._issue(
                        "store_missing", DoctorSeverity.ERROR, "operational store is missing"
                    )
                ],
            )
        if classification.health is StoreHealth.FUTURE_SCHEMA:
            return self._report(
                workspace_id,
                DoctorHealth.FUTURE_SCHEMA,
                classification.schema_version,
                checks,
                counts,
                [
                    self._issue(
                        "future_schema",
                        DoctorSeverity.ERROR,
                        "operational schema is newer than this client",
                    )
                ],
            )

        handle = None
        try:
            handle = self.store.open(StoreOpenMode.DIAGNOSE)
            journal = SqliteOperationalJournal(handle)
            integrity, foreign_keys = handle.run_read(self._sqlite_checks)
            if integrity != "ok":
                issues.append(
                    self._issue(
                        "sqlite_integrity", DoctorSeverity.ERROR, "SQLite integrity check failed"
                    )
                )
            if foreign_keys:
                issues.append(
                    self._issue(
                        "foreign_key_violation",
                        DoctorSeverity.ERROR,
                        "SQLite foreign-key check found inconsistent rows",
                        count=len(foreign_keys),
                    )
                )
            checks.extend(
                (
                    "conversation_history",
                    "tasks_and_executions",
                    "checkpoints_and_forks",
                    "grants_and_permission_snapshots",
                )
            )
            self._inspect_domains(journal, workspace_id, counts, issues)
            self._inspect_permissions(journal, workspace_id, counts, issues)
            checks.extend(("artifacts_and_references", "application_events"))
            self._inspect_artifacts(journal, workspace_id, counts, issues)
            self._inspect_events(journal, workspace_id, counts, issues)
        except StorageError as exc:
            issues.append(
                self._issue(
                    f"diagnostic_{exc.code.value}",
                    DoctorSeverity.ERROR,
                    "operational diagnosis could not read the store",
                )
            )
        finally:
            if handle is not None:
                handle.close()

        if classification.health is StoreHealth.READ_ONLY:
            health = DoctorHealth.READ_ONLY
        elif any(issue.severity is DoctorSeverity.ERROR for issue in issues):
            health = DoctorHealth.NEEDS_REPAIR
        elif any(
            issue.code in {"open_turn", "interrupted_execution", "artifact_staging"}
            for issue in issues
        ):
            health = DoctorHealth.NEEDS_RECOVERY
        else:
            health = DoctorHealth.OK
        return self._report(
            workspace_id,
            health,
            classification.schema_version,
            checks,
            counts,
            issues,
        )

    @staticmethod
    def _sqlite_checks(executor):
        integrity_rows = executor.execute("PRAGMA integrity_check")
        foreign_rows = executor.execute("PRAGMA foreign_key_check")
        integrity = str(integrity_rows[0][0]) if integrity_rows else "missing"
        return integrity, foreign_rows

    def _inspect_domains(self, journal, workspace_id, counts, issues) -> None:
        sessions = journal.list_sessions(workspace_id)
        counts["sessions"] = len(sessions)
        for session in sessions:
            if session.health.value != "ok":
                issues.append(
                    self._issue(
                        "session_health",
                        DoctorSeverity.WARNING,
                        f"Session health is {session.health.value}",
                    )
                )
            try:
                records = journal.load_effective_records(workspace_id, session.session_id)
                counts["conversation_records"] += len(records)
                if len(records) != session.conversation_position:
                    issues.append(
                        self._issue(
                            "conversation_position",
                            DoctorSeverity.ERROR,
                            "conversation position does not match durable records",
                        )
                    )
                snapshot = ConversationSnapshot(
                    records=tuple(conversation_record_from_durable(record) for record in records)
                )
                turns = snapshot.public_turns(require_closed=False)
                if turns and not turns[-1].is_closed:
                    issues.append(
                        self._issue("open_turn", DoctorSeverity.WARNING, "Session has an open Turn")
                    )
            except StorageError:
                raise
            except Exception:
                issues.append(
                    self._issue(
                        "conversation_history",
                        DoctorSeverity.ERROR,
                        "conversation history is not a valid message grammar",
                    )
                )
            tasks = journal.list_task_runs(workspace_id, session.session_id)
            counts["task_runs"] += len(tasks)
            current_task = next(
                (task for task in tasks if task.task_run_id == session.current_task_run_id),
                None,
            )
            if session.current_task_run_id is not None and current_task is None:
                issues.append(
                    self._issue(
                        "task_pointer",
                        DoctorSeverity.ERROR,
                        "Session points at a missing TaskRun",
                    )
                )
            elif (
                session.lifecycle is SessionLifecycle.ARCHIVED
                and current_task is not None
                and not current_task.status.is_terminal
            ):
                issues.append(
                    self._issue(
                        "archived_active_task",
                        DoctorSeverity.ERROR,
                        "archived Session points at an active TaskRun",
                    )
                )
            for task in tasks:
                transitions = journal.list_task_transitions(workspace_id, task.task_run_id)
                counts["task_transitions"] += len(transitions)
                if task.status.is_terminal and session.current_task_run_id == task.task_run_id:
                    issues.append(
                        self._issue(
                            "task_pointer",
                            DoctorSeverity.ERROR,
                            "Session points at a terminal TaskRun",
                        )
                    )
                for execution in journal.list_task_executions(workspace_id, task.task_run_id):
                    counts["tool_executions"] += 1
                    if execution.state is not ToolExecutionState.CLOSED:
                        issues.append(
                            self._issue(
                                "interrupted_execution",
                                DoctorSeverity.WARNING,
                                "ToolExecution requires recovery classification",
                            )
                        )
            for checkpoint in journal.list_context_checkpoints(workspace_id, session.session_id):
                counts["checkpoints"] += 1
                if checkpoint.session_id != session.session_id:
                    issues.append(
                        self._issue(
                            "checkpoint_scope",
                            DoctorSeverity.ERROR,
                            "checkpoint is outside its Session",
                        )
                    )

    def _inspect_artifacts(self, journal, workspace_id, counts, issues) -> None:
        metadata = journal.list_artifacts(workspace_id)
        counts["artifacts"] = len(metadata)
        references = journal.list_artifact_references(workspace_id)
        counts["artifact_references"] = len(references)
        for artifact_id, _owner_kind, _owner_id, _role in references:
            if journal.get_artifact(workspace_id, artifact_id) is None:
                issues.append(
                    self._issue(
                        "artifact_reference",
                        DoctorSeverity.ERROR,
                        "Artifact reference points to missing metadata",
                    )
                )
        filesystem = FilesystemArtifactStore(self.store.layout)
        for item in metadata:
            if item.state is ArtifactState.STAGING:
                issues.append(
                    self._issue(
                        "artifact_staging", DoctorSeverity.WARNING, "Artifact remains in staging"
                    )
                )
        unsafe_layout = self._unsafe_artifact_layout_count(filesystem)
        if unsafe_layout:
            counts["orphan_candidates"] = unsafe_layout
            counts["artifact_managed_unreferenced"] = 0
            counts["artifact_unmanaged_removable"] = 0
            counts["artifact_unsafe_refused"] = unsafe_layout
            issues.append(
                self._issue(
                    "artifact_unsafe_refused",
                    DoctorSeverity.ERROR,
                    "unsafe Artifact layout was refused before candidate inspection",
                    count=unsafe_layout,
                )
            )
            return
        for item in metadata:
            if item.state is not ArtifactState.AVAILABLE:
                continue
            try:
                filesystem.verify(item)
            except ArtifactIntegrityError as exc:
                code = (
                    "artifact_missing" if exc.code.value.endswith("missing") else "artifact_corrupt"
                )
                issues.append(
                    self._issue(code, DoctorSeverity.ERROR, "Artifact bytes do not match metadata")
                )
        try:
            global_metadata, global_references = self._global_artifact_authority(journal)
            orphan = filesystem.orphan_report(
                global_metadata,
                referenced_ids=global_references,
            )
            known_ids = {item.artifact_id for item in global_metadata}
            managed_unreferenced = 0
            unmanaged_removable = 0
            unsafe_refused = 0
            classified_paths: set[tuple[str | None, Path]] = set()
            for candidate in orphan.candidates:
                key = (candidate.artifact_id, candidate.path.absolute())
                if key in classified_paths:
                    continue
                classified_paths.add(key)
                if candidate.artifact_id in known_ids:
                    managed_unreferenced += 1
                elif (
                    candidate.artifact_id not in global_references
                    and self._is_removable_unmanaged(filesystem, candidate)
                ):
                    unmanaged_removable += 1
                else:
                    unsafe_refused += 1
            counts["orphan_candidates"] = (
                managed_unreferenced + unmanaged_removable + unsafe_refused
            )
            counts["artifact_managed_unreferenced"] = managed_unreferenced
            counts["artifact_unmanaged_removable"] = unmanaged_removable
            counts["artifact_unsafe_refused"] = unsafe_refused
            if managed_unreferenced:
                issues.append(
                    self._issue(
                        "artifact_managed_unreferenced",
                        DoctorSeverity.WARNING,
                        "managed Artifact metadata is currently unreferenced",
                        count=managed_unreferenced,
                    )
                )
            if unmanaged_removable:
                issues.append(
                    self._issue(
                        "artifact_unmanaged_removable",
                        DoctorSeverity.WARNING,
                        "private unmanaged Artifact files are eligible for cleanup",
                        count=unmanaged_removable,
                    )
                )
            if unsafe_refused:
                issues.append(
                    self._issue(
                        "artifact_unsafe_refused",
                        DoctorSeverity.WARNING,
                        "unsafe Artifact paths were refused as cleanup targets",
                        count=unsafe_refused,
                    )
                )
        except StorageError:
            raise

    @staticmethod
    def _global_artifact_authority(journal):
        metadata = []
        referenced: set[str] = set()
        for workspace_id in journal.list_workspace_ids():
            metadata.extend(journal.list_artifacts(workspace_id))
            referenced.update(item[0] for item in journal.list_artifact_references(workspace_id))
        return tuple(metadata), frozenset(referenced)

    @staticmethod
    def _is_removable_unmanaged(filesystem, candidate) -> bool:
        if candidate.artifact_id is None:
            return False
        path = candidate.path.absolute()
        allowed = {
            filesystem.artifacts_dir.absolute(): ARTIFACT_FILE_SUFFIX,
            filesystem.artifacts_tmp.absolute(): ARTIFACT_TEMP_SUFFIX,
        }
        suffix = allowed.get(path.parent)
        if suffix is None or path.name != f"{candidate.artifact_id}{suffix}":
            return False
        if not OperationalDoctor._is_safe_managed_directory(filesystem.root, path.parent):
            return False
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == FILE_MODE
        )

    @staticmethod
    def _is_safe_managed_directory(root: Path, directory: Path) -> bool:
        root = root.absolute()
        try:
            relative = directory.absolute().relative_to(root)
        except ValueError:
            return False
        if not OperationalDoctor._is_safe_directory(root, root=True):
            return False
        current = root
        for part in relative.parts:
            current /= part
            if not OperationalDoctor._is_safe_directory(current, root=False):
                return False
        return True

    @staticmethod
    def _unsafe_artifact_layout_count(filesystem: FilesystemArtifactStore) -> int:
        """Validate the managed chain before any filesystem candidate traversal."""

        root = filesystem.root.absolute()
        if not OperationalDoctor._is_safe_directory(root, root=True):
            return 1
        if not OperationalDoctor._is_safe_managed_directory(root, filesystem.artifacts_dir):
            return 1
        if not OperationalDoctor._is_safe_managed_directory(root, filesystem.artifacts_tmp):
            return 1
        return 0

    @staticmethod
    def _is_safe_directory(path: Path, *, root: bool) -> bool:
        """Reject links and unsafe modes, confirming the lstat target with O_NOFOLLOW."""

        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            return False
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode):
            return False
        if root and mode & 0o022:
            return False
        if not root and mode != DIRECTORY_MODE:
            return False

        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            return stat.S_ISDIR(opened.st_mode) and (opened.st_dev, opened.st_ino) == (
                info.st_dev,
                info.st_ino,
            )
        except OSError:
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _inspect_permissions(self, journal, workspace_id, counts, issues) -> None:
        grants = journal.list_capability_grants(workspace_id)
        snapshots = journal.list_permission_snapshots(workspace_id)
        counts["capability_grants"] = len(grants)
        counts["permission_snapshots"] = len(snapshots)
        for grant in grants:
            task = journal.get_task_run(workspace_id, grant.task_run_id)
            run = journal.get_agent_run(workspace_id, grant.agent_run_id)
            if task is None or run is None or task.session_id != run.session_id:
                issues.append(
                    self._issue(
                        "grant_scope",
                        DoctorSeverity.ERROR,
                        "CapabilityGrant subject or frozen link is invalid",
                    )
                )
            if grant.revoked_at is not None:
                counts["revoked_grants"] += 1
            elif not grant.is_active(self.store.clock.now()):
                counts["expired_grants"] += 1
        for snapshot in snapshots:
            counts["permission_evidence"] += 1
            run = journal.get_agent_run(workspace_id, snapshot.agent_run_id)
            task = journal.get_task_run(workspace_id, snapshot.task_run_id)
            turn = journal.get_turn(workspace_id, snapshot.turn_id)
            if (
                run is None
                or task is None
                or turn is None
                or run.permission_snapshot_id != snapshot.permission_snapshot_id
                or task.session_id != snapshot.session_id
                or turn.session_id != snapshot.session_id
                or turn.task_run_id != snapshot.task_run_id
                or run.session_id != snapshot.session_id
                or run.turn_id != snapshot.turn_id
            ):
                issues.append(
                    self._issue(
                        "permission_snapshot_link",
                        DoctorSeverity.ERROR,
                        "PermissionSnapshot is not linked to its AgentRun",
                    )
                )
            if snapshot.grant_id is not None:
                grant = journal.get_capability_grant(workspace_id, snapshot.grant_id)
                if (
                    grant is None
                    or snapshot.grant_digest != capability_grant_digest(grant)
                    or snapshot.task_run_id != grant.task_run_id
                    or snapshot.agent_run_id != grant.agent_run_id
                ):
                    issues.append(
                        self._issue(
                            "permission_grant_evidence",
                            DoctorSeverity.ERROR,
                            "PermissionSnapshot grant evidence is inconsistent",
                        )
                    )
        for grant in grants:
            for approval in journal.list_approvals_for_grant(workspace_id, grant.grant_id):
                if grant.revoked_at is not None and approval.resolution.value == "pending":
                    issues.append(
                        self._issue(
                            "revoked_pending_approval",
                            DoctorSeverity.ERROR,
                            "revoked CapabilityGrant still has a pending Approval",
                        )
                    )
            for execution in journal.list_executions_for_grant(workspace_id, grant.grant_id):
                if grant.revoked_at is None or execution.state is ToolExecutionState.CLOSED:
                    continue
                if (
                    execution.state is ToolExecutionState.EXECUTING
                    and execution.cancel_requested_at is not None
                ):
                    continue
                issues.append(
                    self._issue(
                        "revoked_active_execution",
                        DoctorSeverity.ERROR,
                        "revoked CapabilityGrant execution remains non-terminal",
                    )
                )

    def _inspect_events(self, journal, workspace_id, counts, issues) -> None:
        after = 0
        expected = 1
        while True:
            page = journal.list_application_events(workspace_id, after_cursor=after, limit=100)
            if not page:
                break
            for event in page:
                counts["application_events"] += 1
                if event.cursor != expected:
                    issues.append(
                        self._issue(
                            "application_event_cursor",
                            DoctorSeverity.ERROR,
                            "application event cursor is not contiguous",
                        )
                    )
                expected = event.cursor + 1
                after = event.cursor

    @staticmethod
    def _issue(code, severity, summary, *, count=1):
        return DoctorIssue(code=code, severity=severity, summary=summary, count=count)

    @staticmethod
    def _report(workspace_id, health, schema_version, checks, counts, issues):
        aggregated: dict[tuple[str, DoctorSeverity, str], DoctorIssue] = {}
        ordered: list[DoctorIssue] = []
        for issue in issues:
            key = (issue.code, issue.severity, issue.summary)
            previous = aggregated.get(key)
            if previous is None:
                aggregated[key] = issue
                ordered.append(issue)
            else:
                replacement = previous.model_copy(update={"count": previous.count + issue.count})
                aggregated[key] = replacement
                ordered[ordered.index(previous)] = replacement
        normalized_counts = dict(counts)
        if len(ordered) > 63:
            omitted = sum(issue.count for issue in ordered[63:])
            ordered = [
                *ordered[:63],
                DoctorIssue(
                    code="issues_truncated",
                    severity=DoctorSeverity.WARNING,
                    summary="additional diagnosis issues were omitted",
                    count=omitted,
                ),
            ]
            normalized_counts["issues_truncated"] = omitted
        return DoctorReport(
            workspace_id=workspace_id,
            health=health,
            schema_version=schema_version,
            checks=tuple(dict.fromkeys(checks)),
            counts=normalized_counts,
            issues=tuple(ordered),
        )
