"""Build the frozen, minimal context used by a Preference Reviewer."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from morrow.core.domain import DurableConversationRecord, sha256_digest
from morrow.core.learning_safety import learning_safety_codes
from morrow.core.preference_documents import (
    FrozenPreferenceSummary,
    PreferenceDocument,
    PreferenceReviewSnapshot,
)
from morrow.core.preference_models import PreferenceStatus
from morrow.core.preference_persistence_models import PreferenceEvidence, PreferenceReviewJob
from morrow.core.preference_review import (
    PREFERENCE_REVIEW_MAX_RECENT_MESSAGES,
    PREFERENCE_REVIEW_REQUEST_MAX_CHARS,
    PreferenceDialogueMessage,
    PreferenceReviewContext,
    PreferenceReviewContextError,
)


def snapshot_from_documents(
    global_document: PreferenceDocument,
    workspace_document: PreferenceDocument,
) -> PreferenceReviewSnapshot:
    """Freeze both durable documents without copying them into a second authority."""

    if global_document.scope != "global" or workspace_document.scope != "workspace":
        raise PreferenceReviewContextError(
            "snapshot_invalid", "Preference snapshot scope is invalid"
        )
    entries = tuple(
        FrozenPreferenceSummary(
            preference_id=entry.preference_id,
            statement=entry.statement,
            status=entry.status,
            scope=entry.scope,
            entry_revision=entry.revision,
            document_revision=global_document.revision
            if entry.scope.value == "global"
            else workspace_document.revision,
        )
        for document in (global_document, workspace_document)
        for entry in document.entries
        if entry.status is not PreferenceStatus.DELETED
    )
    return PreferenceReviewSnapshot(
        entries=entries,
        global_document_revision=global_document.revision,
        workspace_document_revision=workspace_document.revision,
    )


class PreferenceReviewContextBuilder:
    """Construct a delayed context from persisted, typed projections only.

    The builder never reads a full Session projection for the model.  When records are supplied by
    the journal it extracts only message content, discarding system, tool, and terminal payloads.
    """

    def __init__(
        self,
        *,
        journal=None,
        workspace_id: str,
        max_recent_messages: int = PREFERENCE_REVIEW_MAX_RECENT_MESSAGES,
        request_char_limit: int | None = None,
    ) -> None:
        if not 0 <= max_recent_messages <= PREFERENCE_REVIEW_MAX_RECENT_MESSAGES:
            raise ValueError("Preference Review recent dialogue limit is invalid")
        if request_char_limit is not None and (
            isinstance(request_char_limit, bool)
            or not isinstance(request_char_limit, int)
            or request_char_limit < 256
            or request_char_limit > PREFERENCE_REVIEW_REQUEST_MAX_CHARS
        ):
            raise ValueError("Preference Review request budget is invalid")
        self.journal = journal
        self.workspace_id = workspace_id
        self.max_recent_messages = max_recent_messages
        self.request_char_limit = request_char_limit

    def build(
        self,
        *,
        job: PreferenceReviewJob,
        evidence: PreferenceEvidence,
        active_snapshot: PreferenceReviewSnapshot | None = None,
        current_user_record: DurableConversationRecord | Mapping[str, object] | None = None,
        current_user_message: str | None = None,
        current_user_record_id: str | None = None,
        recent_dialogue: Iterable[PreferenceDialogueMessage | Mapping[str, object]] = (),
    ) -> PreferenceReviewContext:
        self._assert_job_and_evidence(job, evidence)
        snapshot = active_snapshot or self._snapshot_from_job(job)
        if (
            snapshot.global_document_revision != job.source_global_revision
            or snapshot.workspace_document_revision != job.source_workspace_revision
        ):
            raise PreferenceReviewContextError(
                "snapshot_invalid", "Preference Review snapshot revisions do not match the job"
            )

        record = current_user_record
        if record is None and current_user_message is None and self.journal is not None:
            record = self._load_current_record(job)
        if record is not None:
            self._assert_record_session(record, job)
            record_id, message = self._record_content(record, expected_turn_id=job.turn_id)
            if current_user_message is not None and current_user_message != message:
                raise PreferenceReviewContextError(
                    "source_mismatch", "current user message does not match its durable record"
                )
            current_user_record_id = record_id
            current_user_message = message
        if current_user_message is None or current_user_record_id is None:
            raise PreferenceReviewContextError(
                "source_missing", "the complete current user message is missing"
            )
        if sha256_digest(current_user_message) != evidence.content_digest:
            raise PreferenceReviewContextError(
                "source_mismatch", "current user message does not match its Evidence digest"
            )
        self._assert_safe_message(current_user_message)

        dialogue = self._dialogue(
            recent_dialogue,
            current_user_record_id=current_user_record_id,
            job=job,
        )
        try:
            context = PreferenceReviewContext(
                workspace_id=self.workspace_id,
                job_id=job.job_id,
                turn_id=job.turn_id,
                current_user_record_id=current_user_record_id,
                current_user_message=current_user_message,
                evidence_id=evidence.evidence_id,
                recent_dialogue=dialogue,
                active_snapshot=snapshot,
                source_global_revision=job.source_global_revision,
                source_workspace_revision=job.source_workspace_revision,
            )
        except ValueError as exc:
            code = "context_budget" if "budget" in str(exc) else "context_invalid"
            raise PreferenceReviewContextError(
                code, "Preference Review context is invalid"
            ) from exc
        if self.request_char_limit is not None and self._estimated_request_chars(context) > (
            self.request_char_limit
        ):
            raise PreferenceReviewContextError(
                "context_budget",
                "Preference Review request cannot hold the complete bounded context",
            )
        return context

    def build_from_job(
        self,
        job: PreferenceReviewJob,
        evidence: PreferenceEvidence,
        *,
        active_snapshot: PreferenceReviewSnapshot | None = None,
    ) -> PreferenceReviewContext:
        return self.build(job=job, evidence=evidence, active_snapshot=active_snapshot)

    def _assert_job_and_evidence(
        self, job: PreferenceReviewJob, evidence: PreferenceEvidence
    ) -> None:
        if job.workspace_id != self.workspace_id or evidence.workspace_id != self.workspace_id:
            raise PreferenceReviewContextError(
                "cross_workspace", "Preference Review source is outside the workspace"
            )
        if evidence.job_id != job.job_id or evidence.turn_id != job.turn_id:
            raise PreferenceReviewContextError(
                "source_mismatch", "Preference Evidence does not match the job"
            )
        if evidence.safety_rejection_code is not None:
            raise PreferenceReviewContextError(
                "safety_rejected", "Preference Evidence was safety rejected"
            )

    @staticmethod
    def _snapshot_from_job(job: PreferenceReviewJob) -> PreferenceReviewSnapshot:
        try:
            return PreferenceReviewSnapshot.model_validate_json(
                job.active_snapshot_json, strict=True
            )
        except ValueError as exc:
            raise PreferenceReviewContextError(
                "snapshot_invalid", "frozen Preference snapshot is invalid"
            ) from exc

    def _load_current_record(self, job: PreferenceReviewJob):
        if self.journal is None:
            return None
        turn = self.journal.get_turn(self.workspace_id, job.turn_id)
        if turn is None:
            raise PreferenceReviewContextError("source_missing", "Review Turn is missing")
        records = self.journal.load_records(self.workspace_id, turn.session_id)
        segments = self._segments(records)
        turns = self.journal.list_session_turns(self.workspace_id, turn.session_id)
        index = next(
            (position for position, item in enumerate(turns) if item.turn_id == job.turn_id),
            -1,
        )
        if index < 0 or index >= len(segments):
            raise PreferenceReviewContextError("source_missing", "current user record is missing")
        candidates = [
            record
            for record in segments[index]
            if record.kind == "message" and record.payload.get("role") == "user"
        ]
        if not candidates:
            raise PreferenceReviewContextError("source_missing", "current user record is missing")
        return candidates[-1]

    @staticmethod
    def _segments(records: tuple[DurableConversationRecord, ...]):
        segments: list[list[DurableConversationRecord]] = [[]]
        for record in records:
            if record.kind == "terminal":
                if segments[-1]:
                    segments.append([])
            else:
                segments[-1].append(record)
        return tuple(tuple(segment) for segment in segments if segment)

    @staticmethod
    def _assert_record_session(
        record: DurableConversationRecord | Mapping[str, object], job: PreferenceReviewJob
    ) -> None:
        expected_session_id = job.session_id
        if expected_session_id is None:
            return
        session_id = (
            record.session_id
            if isinstance(record, DurableConversationRecord)
            else record.get("session_id")
        )
        if session_id is not None and session_id != expected_session_id:
            raise PreferenceReviewContextError(
                "source_mismatch", "current user record belongs to another Session"
            )

    @staticmethod
    def _record_content(
        record: DurableConversationRecord | Mapping[str, object], *, expected_turn_id: str
    ) -> tuple[str, str]:
        if isinstance(record, DurableConversationRecord):
            record_id = record.record_id
            payload = record.payload
        else:
            record_id = record.get("record_id")
            payload_object = record.get("payload", record)
            payload = payload_object if isinstance(payload_object, Mapping) else {}
        if not isinstance(record_id, str):
            raise PreferenceReviewContextError(
                "source_invalid", "current user record ID is invalid"
            )
        if isinstance(record, DurableConversationRecord) and record.kind != "message":
            raise PreferenceReviewContextError(
                "source_invalid", "current user record is not a message"
            )
        if payload.get("role") != "user" or not isinstance(payload.get("content"), str):
            raise PreferenceReviewContextError(
                "source_invalid", "current user record is not user content"
            )
        if isinstance(record, Mapping) and record.get("turn_id") not in (None, expected_turn_id):
            raise PreferenceReviewContextError(
                "source_mismatch", "current user record belongs to another Turn"
            )
        return record_id, payload["content"]

    def _dialogue(
        self,
        values: Iterable[PreferenceDialogueMessage | Mapping[str, object]],
        *,
        current_user_record_id: str,
        job: PreferenceReviewJob,
    ) -> tuple[PreferenceDialogueMessage, ...]:
        result: list[PreferenceDialogueMessage] = []
        candidates = list(values)
        if not candidates and self.journal is not None and job.session_id is not None:
            load_records = getattr(self.journal, "load_effective_records", None)
            if load_records is None:
                load_records = getattr(self.journal, "load_records", None)
            if load_records is None:
                return ()
            records = load_records(self.workspace_id, job.session_id)
            current_position = next(
                (
                    record.conversation_position
                    for record in records
                    if record.record_id == current_user_record_id
                ),
                None,
            )
            candidates = [
                record
                for record in records
                if record.kind == "message"
                and record.record_id != current_user_record_id
                and (current_position is None or record.conversation_position < current_position)
            ][-self.max_recent_messages :]
        for item in candidates[-self.max_recent_messages :]:
            if isinstance(item, PreferenceDialogueMessage):
                message = item
            elif isinstance(item, DurableConversationRecord):
                actor = item.payload.get("role")
                content = item.payload.get("content")
                if actor not in {"user", "assistant"} or not isinstance(content, str):
                    continue
                try:
                    message = PreferenceDialogueMessage(
                        actor=actor,
                        content=content,
                        record_id=item.record_id,
                    )
                except (TypeError, ValueError):
                    continue
            else:
                actor = item.get("actor", item.get("role"))
                content = item.get("content")
                if actor not in {"user", "assistant"} or not isinstance(content, str):
                    continue
                try:
                    message = PreferenceDialogueMessage(
                        actor=actor,
                        content=content,
                        turn_id=item.get("turn_id"),
                        record_id=item.get("record_id"),
                    )
                except (TypeError, ValueError):
                    continue
            if message.record_id == current_user_record_id:
                continue
            if learning_safety_codes(message.content):
                continue
            result.append(message)
        return tuple(result)

    @staticmethod
    def _assert_safe_message(value: str) -> None:
        if learning_safety_codes(value):
            raise PreferenceReviewContextError(
                "safety_rejected", "current user Evidence is safety rejected"
            )

    @staticmethod
    def _estimated_request_chars(context: PreferenceReviewContext) -> int:
        # Include a small fixed allowance for the system instruction and schema.  The model adapter
        # performs the authoritative wire estimate before the Provider call.
        return len(context.model_dump_json().encode("utf-8")) + 4_096


__all__ = [
    "PreferenceReviewContextBuilder",
    "snapshot_from_documents",
]
