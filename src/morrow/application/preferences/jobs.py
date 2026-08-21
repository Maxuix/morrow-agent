"""Durable Preference Review job creation at the terminal Turn boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from morrow.adapters.state.preference_migration import legacy_entries_from_preferences
from morrow.application.preferences.context import snapshot_from_documents
from morrow.core.domain import sha256_digest
from morrow.core.execution import ToolExecutionDisposition
from morrow.core.learning import LearningMode
from morrow.core.learning_safety import learning_safety_codes, normalize_learning_text
from morrow.core.models import UserMessage
from morrow.core.ports import IdSource
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import (
    PREFERENCE_EVIDENCE_ID_PREFIX,
    PREFERENCE_MAX_EXCERPT_CHARS,
    PREFERENCE_REVIEW_JOB_ID_PREFIX,
)
from morrow.core.preference_persistence_models import (
    PreferenceEvidence,
    PreferenceReviewFailureCode,
    PreferenceReviewJob,
    PreferenceReviewJobStatus,
)
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.conversation import ConversationSnapshot, TurnTerminalRecord
from morrow.runtime.session import Session


def _utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class PreferenceReviewEnqueueResult:
    """Bounded result used by the terminal hook and deterministic tests."""

    job: PreferenceReviewJob | None = None
    evidence: PreferenceEvidence | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PreferenceReviewJobView:
    """Sanitized query projection for one durable Preference Review job.

    The frozen snapshot and Reviewer/provider metadata remain internal to the worker boundary.  A
    status surface needs enough information to resume or diagnose a job without turning the CLI or
    application query API into a second context or model-details channel.
    """

    job_id: str
    session_id: str | None
    turn_id: str
    review_version: int
    status: PreferenceReviewJobStatus
    source_global_revision: int
    source_workspace_revision: int
    active_snapshot_count: int
    active_snapshot_bytes: int
    attempt_count: int
    row_version: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    lease_expires_at: datetime | None
    failure_code: PreferenceReviewFailureCode | None
    evidence_id: str | None = None

    @classmethod
    def from_job(
        cls,
        job: PreferenceReviewJob,
        *,
        evidence_id: str | None = None,
    ) -> PreferenceReviewJobView:
        return cls(
            job_id=job.job_id,
            session_id=job.session_id,
            turn_id=job.turn_id,
            review_version=job.review_version,
            status=job.status,
            source_global_revision=job.source_global_revision,
            source_workspace_revision=job.source_workspace_revision,
            active_snapshot_count=job.active_snapshot_count,
            active_snapshot_bytes=job.active_snapshot_bytes,
            attempt_count=job.attempt_count,
            row_version=job.row_version,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            lease_expires_at=job.lease_expires_at,
            failure_code=job.failure_code,
            evidence_id=evidence_id,
        )


@dataclass(frozen=True)
class PreferenceReviewStatusView:
    """Bounded aggregate status for the Preference Review queue."""

    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    exhausted: int = 0
    cancelled: int = 0
    superseded: int = 0
    total: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "total",
            self.pending
            + self.running
            + self.completed
            + self.failed
            + self.exhausted
            + self.cancelled
            + self.superseded,
        )


class PreferenceReviewJobEnqueuer:
    """Create one durable Review job without calling a model or touching YAML."""

    def __init__(
        self,
        *,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
        review_version: int = 1,
    ) -> None:
        if isinstance(review_version, bool) or not isinstance(review_version, int):
            raise ValueError("Preference Review version is invalid")
        if review_version < 1:
            raise ValueError("Preference Review version is invalid")
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.review_version = review_version

    def enqueue_terminal_turn(
        self,
        txn,
        *,
        session: Session,
        turn_id: str | None,
        conversation: ConversationSnapshot,
        terminal: TurnTerminalRecord,
    ) -> PreferenceReviewEnqueueResult:
        """Enqueue the closed current User Turn on the caller's active transaction."""

        if turn_id is None:
            return PreferenceReviewEnqueueResult(reason="turn_missing")
        if not session.session_id.strip():
            return PreferenceReviewEnqueueResult(reason="session_missing")
        turn = txn.get_turn(self.workspace_id, turn_id)
        if turn is None or turn.session_id != session.session_id:
            return PreferenceReviewEnqueueResult(reason="turn_missing")

        turns = conversation.public_turns(require_closed=True)
        if not turns or turns[-1].terminal != terminal:
            return PreferenceReviewEnqueueResult(reason="terminal_not_current")
        current = turns[-1]
        if not isinstance(current.user.message, UserMessage):
            return PreferenceReviewEnqueueResult(reason="user_message_missing")
        content = current.user.message.content

        # The terminal replay key is checked before mutable policy/suppression state so a replay
        # returns the original durable result rather than creating a second version.
        existing = txn.get_preference_review_job_for_turn(
            self.workspace_id,
            turn_id,
            review_version=self.review_version,
        )
        if existing is not None:
            evidence = txn.get_preference_evidence_for_job(self.workspace_id, existing.job_id)
            if evidence is None:
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR,
                    "Preference Review job has no Evidence",
                )
            if evidence.turn_id != turn_id or evidence.content_digest != sha256_digest(content):
                raise StorageError(
                    StorageErrorCode.NEEDS_REPAIR,
                    "Preference Review replay source changed",
                )
            return PreferenceReviewEnqueueResult(job=existing, evidence=evidence, reason="replayed")

        if content.lstrip().startswith("/"):
            return PreferenceReviewEnqueueResult(reason="control_command")
        if txn.get_effective_learning_policy(self.workspace_id).mode is LearningMode.OFF:
            return PreferenceReviewEnqueueResult(reason="learning_policy_off")
        if learning_safety_codes(content):
            return PreferenceReviewEnqueueResult(reason="safety_rejected")
        if self._has_successful_preference_write(
            txn, self.workspace_id, session.session_id, turn_id
        ):
            return PreferenceReviewEnqueueResult(reason="preference_write_completed")

        global_document, workspace_document = _active_documents(session, now=_utc(self.clock))
        snapshot = snapshot_from_documents(global_document, workspace_document)
        encoded_snapshot = snapshot.serialized_bytes
        stamp = _utc(self.clock)
        excerpt = _excerpt(content)
        job = PreferenceReviewJob(
            job_id=self.id_source.new_id(PREFERENCE_REVIEW_JOB_ID_PREFIX),
            workspace_id=self.workspace_id,
            session_id=session.session_id,
            turn_id=turn_id,
            review_version=self.review_version,
            source_global_revision=snapshot.global_document_revision,
            source_workspace_revision=snapshot.workspace_document_revision,
            active_snapshot_json=encoded_snapshot.decode("utf-8"),
            active_snapshot_count=len(snapshot.entries),
            active_snapshot_bytes=len(encoded_snapshot),
            active_snapshot_digest=snapshot.digest,
            created_at=stamp,
        )
        evidence = PreferenceEvidence(
            evidence_id=self.id_source.new_id(PREFERENCE_EVIDENCE_ID_PREFIX),
            workspace_id=self.workspace_id,
            job_id=job.job_id,
            turn_id=turn_id,
            excerpt_redacted=excerpt,
            excerpt_bytes=len(excerpt.encode("utf-8")),
            content_digest=sha256_digest(content),
            observed_at=stamp,
            created_at=stamp,
        )
        stored_job, stored_evidence = txn.put_preference_job_with_evidence(
            self.workspace_id, job, evidence
        )
        return PreferenceReviewEnqueueResult(job=stored_job, evidence=stored_evidence)

    @staticmethod
    def _has_successful_preference_write(
        txn, workspace_id: str, session_id: str, turn_id: str
    ) -> bool:
        executions = txn.list_session_executions(workspace_id, session_id)
        return any(
            execution.turn_id == turn_id
            and execution.tool_name == "manage_preferences"
            and execution.disposition is ToolExecutionDisposition.SUCCEEDED
            for execution in executions
        )


def _active_documents(
    session: Session, *, now: datetime
) -> tuple[PreferenceDocument, PreferenceDocument]:
    global_document = session.generic_global_preferences
    if global_document is None:
        global_document = PreferenceDocument(
            scope="global",
            revision=session.global_preferences_revision,
            updated_at=now,
            entries=legacy_entries_from_preferences(
                "global", session.global_preferences.model_dump(mode="python")
            ),
        )
    workspace_document = session.generic_workspace_preferences
    if workspace_document is None:
        workspace_document = PreferenceDocument(
            scope="workspace",
            revision=session.preferences_revision,
            updated_at=now,
            entries=legacy_entries_from_preferences(
                "workspace", session.workspace_preferences.model_dump(mode="python")
            ),
        )
    return global_document, workspace_document


def _excerpt(content: str) -> str:
    normalized = " ".join(content.split())
    return normalize_learning_text(
        normalized[:PREFERENCE_MAX_EXCERPT_CHARS].rstrip(),
        label="Preference evidence excerpt",
        maximum=PREFERENCE_MAX_EXCERPT_CHARS,
    )


# Keep the service-shaped name available to callers while the implementation remains an explicit
# enqueue collaborator at the Turn boundary.
PreferenceReviewJobService = PreferenceReviewJobEnqueuer


__all__ = [
    "PreferenceReviewEnqueueResult",
    "PreferenceReviewJobView",
    "PreferenceReviewStatusView",
    "PreferenceReviewJobEnqueuer",
    "PreferenceReviewJobService",
]
