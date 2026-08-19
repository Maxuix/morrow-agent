"""Application service for durable Artifact publication and inspection."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.core.artifacts import (
    ARTIFACT_EXCERPT_MAX_BYTES,
    ARTIFACT_MAX_BYTES,
    TASK_ARTIFACT_MAX_BYTES,
    ArtifactBudgetError,
    ArtifactError,
    ArtifactErrorCode,
    ArtifactIntegrityError,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactOrphanCandidate,
    ArtifactOrphanReport,
    ArtifactProvenanceKind,
    ArtifactProvenanceRef,
    ArtifactRead,
    ArtifactRetention,
    ArtifactRetentionReport,
    ArtifactSensitivity,
    ArtifactState,
)
from morrow.core.domain import (
    ARTIFACT_ID_PREFIX,
    ArtifactReference,
    refuse_secret_material,
    sha256_digest,
    utc_now,
)
from morrow.core.ports import IdSource
from morrow.core.store import StorageError, StorageErrorCode

_REDACTED_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|authorization|credential|password|passwd|secret|token)\b"
    r'["\']?\s*[:=]\s*(?:"(?:Bearer\s+)?<redacted>"?|'
    r"'(?:Bearer\s+)?<redacted>'?|(?:Bearer\s+)?<redacted>)(?=$|[\s,;}\]])"
)
_REDACTED_SECRET_TOKEN = re.compile(r'(?i)\bsk-"?<redacted>"?(?=$|[\s,;}\]])')


def bounded_utf8_excerpt(content: bytes, *, limit: int = ARTIFACT_EXCERPT_MAX_BYTES) -> str:
    """Return a text excerpt whose encoded UTF-8 bytes never exceed ``limit``."""

    if limit < 0 or limit > ARTIFACT_EXCERPT_MAX_BYTES:
        raise ArtifactError(ArtifactErrorCode.BUDGET, "artifact excerpt limit is invalid")
    if not content or limit == 0:
        return ""
    return content[:limit].decode("utf-8", errors="ignore")


class ArtifactService:
    """Coordinate SQLite metadata with the managed Artifact filesystem."""

    def __init__(
        self,
        *,
        journal: SqliteOperationalJournal,
        filesystem: FilesystemArtifactStore,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime] = utc_now,
        faults=None,
    ) -> None:
        self.journal = journal
        self.filesystem = filesystem
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.faults = faults

    def publish_bytes(
        self,
        content: bytes,
        *,
        kind: ArtifactKind,
        session_id: str | None = None,
        task_run_id: str | None = None,
        sensitivity: ArtifactSensitivity = ArtifactSensitivity.REDACTED,
        retention: ArtifactRetention = ArtifactRetention.STANDARD,
        provenance_refs: tuple[ArtifactProvenanceRef, ...] = (),
        excerpt: str | None = None,
        artifact_id: str | None = None,
        already_redacted: bool = False,
    ) -> ArtifactMetadata:
        if not isinstance(content, bytes):
            raise ArtifactError(ArtifactErrorCode.INVALID, "artifact content must be bytes")
        if len(content) > ARTIFACT_MAX_BYTES:
            raise ArtifactBudgetError()
        if task_run_id is not None:
            current = self.journal.artifact_bytes_for_task(self.workspace_id, task_run_id)
            if current + len(content) > TASK_ARTIFACT_MAX_BYTES:
                raise ArtifactBudgetError("TaskRun artifact byte budget exceeded")
        selected_excerpt = bounded_utf8_excerpt(content) if excerpt is None else excerpt
        if already_redacted:
            selected_excerpt = self._remove_redacted_secret_assignments(selected_excerpt)
        if len(selected_excerpt.encode("utf-8")) > ARTIFACT_EXCERPT_MAX_BYTES:
            raise ArtifactBudgetError("artifact excerpt budget exceeded")
        try:
            if already_redacted:
                self._refuse_unredacted_secrets(content)
            else:
                self._refuse_secrets(content)
        except ValueError as exc:
            raise ArtifactError(
                ArtifactErrorCode.INVALID, "artifact content is not redacted"
            ) from exc
        try:
            metadata = ArtifactMetadata(
                artifact_id=artifact_id or self.id_source.new_id(ARTIFACT_ID_PREFIX),
                workspace_id=self.workspace_id,
                session_id=session_id,
                task_run_id=task_run_id,
                kind=kind,
                sensitivity=sensitivity,
                state=ArtifactState.STAGING,
                retention=retention,
                sha256=sha256_digest(content),
                byte_size=len(content),
                excerpt=selected_excerpt,
                provenance_refs=provenance_refs,
                created_at=self.clock(),
                updated_at=self.clock(),
            )
        except ValueError as exc:
            raise ArtifactError(ArtifactErrorCode.INVALID, "artifact metadata is invalid") from exc

        reserved = self.journal.reserve_artifact(self.workspace_id, metadata)
        self._check_fault("artifact.after_reserve")
        try:
            self.filesystem.publish(reserved, content, faults=self.faults)
        except ArtifactIntegrityError:
            self._try_mark(reserved, ArtifactState.CORRUPT)
            raise
        self._check_fault("artifact.before_mark_available")
        available = reserved.model_copy(
            update={
                "state": ArtifactState.AVAILABLE,
                "row_version": reserved.row_version + 1,
                "updated_at": self.clock(),
            }
        )
        published = self.journal.save_artifact(
            self.workspace_id,
            available,
            expected_row_version=reserved.row_version,
        )
        self._check_fault("artifact.after_mark_available")
        return published

    def publish_command_output(
        self,
        content: str | bytes,
        *,
        session_id: str,
        task_run_id: str,
        tool_execution_id: str,
    ) -> ArtifactMetadata:
        payload = content.encode("utf-8") if isinstance(content, str) else content
        return self.publish_bytes(
            payload,
            kind=ArtifactKind.COMMAND_OUTPUT,
            session_id=session_id,
            task_run_id=task_run_id,
            provenance_refs=(
                ArtifactProvenanceRef(
                    kind=ArtifactProvenanceKind.TOOL_EXECUTION,
                    reference_id=tool_execution_id,
                    role="command_output",
                ),
            ),
            excerpt=bounded_utf8_excerpt(payload),
            already_redacted=True,
        )

    def get(self, artifact_id: str) -> ArtifactMetadata | None:
        return self.journal.get_artifact(self.workspace_id, artifact_id)

    def read(self, artifact_id: str, *, max_bytes: int) -> ArtifactRead:
        metadata = self.journal.get_artifact(self.workspace_id, artifact_id)
        if metadata is None:
            raise ArtifactError(ArtifactErrorCode.MISSING, "artifact metadata is missing")
        if metadata.state is not ArtifactState.AVAILABLE:
            code = (
                ArtifactErrorCode.MISSING
                if metadata.state is ArtifactState.MISSING
                else ArtifactErrorCode.INTEGRITY
            )
            raise ArtifactError(code, "artifact is not available")
        try:
            content = self.filesystem.read(metadata, max_bytes=max_bytes)
        except ArtifactIntegrityError as exc:
            target = (
                ArtifactState.MISSING
                if exc.code is ArtifactErrorCode.MISSING
                else ArtifactState.CORRUPT
            )
            self._try_mark(metadata, target)
            raise
        return ArtifactRead(metadata=metadata, content=content)

    def finalize_staging(self, artifact_id: str) -> ArtifactMetadata:
        """Finish only a staging row whose already-published final bytes verify."""

        metadata = self.journal.get_artifact(self.workspace_id, artifact_id)
        if metadata is None:
            raise ArtifactError(ArtifactErrorCode.MISSING, "artifact metadata is missing")
        if metadata.state is not ArtifactState.STAGING:
            return metadata
        try:
            self.filesystem.verify(metadata)
        except ArtifactIntegrityError as exc:
            if exc.code is not ArtifactErrorCode.MISSING:
                self._try_mark(metadata, ArtifactState.CORRUPT)
            return self.journal.get_artifact(self.workspace_id, artifact_id) or metadata
        updated = metadata.model_copy(
            update={
                "state": ArtifactState.AVAILABLE,
                "row_version": metadata.row_version + 1,
                "updated_at": self.clock(),
            }
        )
        return self.journal.save_artifact(
            self.workspace_id, updated, expected_row_version=metadata.row_version
        )

    recover_staging = finalize_staging

    def pin(self, artifact_id: str) -> ArtifactMetadata:
        return self._change_retention(artifact_id, ArtifactRetention.PINNED)

    def unpin(self, artifact_id: str) -> ArtifactMetadata:
        return self._change_retention(artifact_id, ArtifactRetention.STANDARD)

    def link_tool_execution(self, artifact_id: str, tool_execution_id: str) -> object:
        execution = self.journal.get_execution(self.workspace_id, tool_execution_id)
        if execution is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational execution is missing")
        reference = ArtifactReference(artifact_id=artifact_id, role="tool_output")
        if reference in execution.artifact_refs:
            return execution
        updated = execution.model_copy(
            update={
                "artifact_refs": (*execution.artifact_refs, reference),
                "row_version": execution.row_version + 1,
            }
        )
        return self.journal.save_execution(
            self.workspace_id, updated, expected_row_version=execution.row_version
        )

    def orphan_report(self) -> ArtifactOrphanReport:
        metadata = self.journal.list_artifacts(self.workspace_id)
        references = frozenset(
            item[0] for item in self.journal.list_artifact_references(self.workspace_id)
        )
        report = self.filesystem.orphan_report(metadata, referenced_ids=references)
        missing_or_corrupt = []
        for item in metadata:
            if item.state is not ArtifactState.AVAILABLE:
                continue
            try:
                self.filesystem.verify(item)
            except ArtifactIntegrityError as exc:
                missing_or_corrupt.append(
                    ArtifactOrphanCandidate(
                        item.artifact_id,
                        self.filesystem.final_path(item.artifact_id),
                        exc.code.value,
                    )
                )
        if not missing_or_corrupt:
            return report
        return ArtifactOrphanReport((*report.candidates, *missing_or_corrupt))

    def retention_report(self) -> ArtifactRetentionReport:
        metadata = self.journal.list_artifacts(self.workspace_id)
        referenced = frozenset(
            item[0] for item in self.journal.list_artifact_references(self.workspace_id)
        )
        pinned = tuple(
            sorted(
                item.artifact_id for item in metadata if item.retention is ArtifactRetention.PINNED
            )
        )
        candidates = tuple(
            sorted(
                item.artifact_id
                for item in metadata
                if item.artifact_id not in referenced
                and item.retention is ArtifactRetention.STANDARD
            )
        )
        return ArtifactRetentionReport(
            referenced=tuple(sorted(referenced)), pinned=pinned, candidates=candidates
        )

    def _change_retention(self, artifact_id: str, retention: ArtifactRetention) -> ArtifactMetadata:
        current = self.journal.get_artifact(self.workspace_id, artifact_id)
        if current is None:
            raise ArtifactError(ArtifactErrorCode.MISSING, "artifact metadata is missing")
        if current.retention is retention:
            return current
        updated = current.model_copy(
            update={
                "retention": retention,
                "row_version": current.row_version + 1,
                "updated_at": self.clock(),
            }
        )
        return self.journal.save_artifact(
            self.workspace_id, updated, expected_row_version=current.row_version
        )

    def _try_mark(self, metadata: ArtifactMetadata, state: ArtifactState) -> None:
        current = self.journal.get_artifact(self.workspace_id, metadata.artifact_id)
        if (
            current is None
            or current.state is not ArtifactState.AVAILABLE
            and state is not ArtifactState.CORRUPT
        ):
            return
        if current.state is state:
            return
        try:
            updated = current.model_copy(
                update={
                    "state": state,
                    "row_version": current.row_version + 1,
                    "updated_at": self.clock(),
                }
            )
            self.journal.save_artifact(
                self.workspace_id, updated, expected_row_version=current.row_version
            )
        except (StorageError, ArtifactError):
            return

    def _check_fault(self, point: str) -> None:
        if self.faults is not None:
            self.faults.check(point)

    @staticmethod
    def _refuse_secrets(content: bytes) -> None:
        text = content.decode("utf-8", errors="replace")
        refuse_secret_material(text, label="artifact content")

    @classmethod
    def _refuse_unredacted_secrets(cls, content: bytes) -> None:
        text = content.decode("utf-8", errors="replace")
        text = cls._remove_redacted_secret_assignments(text)
        refuse_secret_material(text, label="artifact content")

    @staticmethod
    def _remove_redacted_secret_assignments(text: str) -> str:
        text = _REDACTED_SECRET_ASSIGNMENT.sub("", text)
        return _REDACTED_SECRET_TOKEN.sub("", text)
