"""Read-only diagnosis of the SQLite and managed Artifact halves."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.core.artifacts import ArtifactIntegrityError, ArtifactState
from morrow.core.doctor import DoctorHealth, DoctorIssue, DoctorReport, DoctorSeverity
from morrow.core.domain import WORKSPACE_ID_PREFIX, validate_prefixed_id
from morrow.core.execution import ToolExecutionState
from morrow.core.store import StorageError, StoreHealth, StoreOpenMode
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
            checks.extend(("conversation_history", "tasks_and_executions", "checkpoints_and_forks"))
            self._inspect_domains(journal, workspace_id, counts, issues)
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
            orphan = filesystem.orphan_report(
                metadata, referenced_ids=frozenset(item[0] for item in references)
            )
            counts["orphan_candidates"] = len(orphan.candidates)
            if orphan.candidates:
                issues.append(
                    self._issue(
                        "artifact_orphan",
                        DoctorSeverity.WARNING,
                        "managed Artifact paths include unreferenced candidates",
                        count=len(orphan.candidates),
                    )
                )
        except StorageError:
            raise

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
