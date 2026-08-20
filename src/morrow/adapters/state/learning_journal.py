"""Codecs and composition root for the v10 Learning SQLite repositories."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import canonical_json_bytes
from morrow.core.learning import (
    LearningCandidate,
    LearningEvidence,
    LearningPolicy,
    LearningReview,
    LearningSuppression,
)
from morrow.core.store import StorageError, StorageErrorCode

_POLICY_COLUMNS = (
    "workspace_id, mode, candidate_ttl_days, max_candidates_per_review, "
    "max_evidence_per_review, row_version, created_at_unix, updated_at_unix"
)
_REVIEW_COLUMNS = (
    "review_id, workspace_id, task_run_id, task_outcome_id, review_version, trigger, status, "
    "policy_snapshot_json, policy_snapshot_bytes, policy_digest, reviewer_provider_id, "
    "reviewer_model_id, reviewer_prompt_version, reviewer_schema_version, supersedes_review_id, "
    "lease_id, lease_expires_at_unix, attempt_count, row_version, created_at_unix, "
    "started_at_unix, completed_at_unix, failure_code"
)
_EVIDENCE_COLUMNS = (
    "evidence_id, workspace_id, origin_review_id, task_run_id, source_kind, source_id, "
    "source_pointer, actor, authority, explicitness, polarity, scope_hint, excerpt_redacted, "
    "excerpt_bytes, content_digest, safety_rejection_code, observed_at_unix, created_at_unix"
)
_EVIDENCE_COLUMNS_QUALIFIED = ", ".join(
    f"e.{column.strip()}" for column in _EVIDENCE_COLUMNS.split(",")
)
_CANDIDATE_COLUMNS = (
    "candidate_id, workspace_id, origin_review_id, candidate_type, operation, semantic_key, "
    "proposed_scope, proposed_payload_json, proposed_payload_bytes, fingerprint, status, "
    "evidence_ids_json, confidence_band, confidence_basis_json, sensitivity, "
    "expected_target_revision, duplicate_of_id, supersedes_id, conflict_refs_json, "
    "expires_at_unix, row_version, created_at_unix, resolved_at_unix, resolved_by, rejection_reason"
)
_SUPPRESSION_COLUMNS = (
    "suppression_id, workspace_id, candidate_type, scope, semantic_key, fingerprint, "
    "source_candidate_id, reason, status, expires_at_unix, row_version, created_at_unix, "
    "updated_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _optional_unix(value: datetime | None) -> int | None:
    return None if value is None else _unix(value)


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _from_optional_unix(value: object) -> datetime | None:
    return None if value is None else _from_unix(value)


def _json_text(value: object, *, label: str) -> str:
    try:
        parsed = json.loads(str(value))
        canonical = canonical_json_bytes(parsed).decode("utf-8")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"learning {label} is invalid") from exc
    return canonical


def _json_list(value: object, *, label: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value))
        if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
            raise ValueError("not a string list")
        canonical = canonical_json_bytes(parsed).decode("utf-8")
        if canonical != str(value):
            raise ValueError("non-canonical list")
        return tuple(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"learning {label} is invalid") from exc


def _json_object(value: object, size: object, *, label: str, maximum: int) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
        if not isinstance(parsed, dict):
            raise ValueError("not an object")
        encoded = canonical_json_bytes(parsed)
        if encoded.decode("utf-8") != str(value) or len(encoded) != int(size):
            raise ValueError("non-canonical or mis-sized object")
        if len(encoded) > maximum:
            raise ValueError("object exceeds budget")
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"learning {label} is invalid") from exc


def _workspace_error(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"learning {label} is outside the workspace")


def _missing(label: str) -> StorageError:
    return StorageError(StorageErrorCode.NOT_FOUND, f"learning {label} is missing")


def _stale(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"learning {label} row version is stale")


def _read_json_list(value: object, *, label: str) -> tuple[str, ...]:
    return _json_list(value, label=label)


def _policy_from_row(row: tuple[object, ...]) -> LearningPolicy:
    try:
        return LearningPolicy(
            workspace_id=str(row[0]),
            mode=str(row[1]),
            candidate_ttl_days=int(row[2]),
            max_candidates_per_review=int(row[3]),
            max_evidence_per_review=int(row[4]),
            row_version=int(row[5]),
            created_at=_from_unix(row[6]),
            updated_at=_from_unix(row[7]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "learning policy is invalid") from exc


def _review_from_row(row: tuple[object, ...]) -> LearningReview:
    try:
        snapshot = _json_object(row[7], row[8], label="review policy snapshot", maximum=8 * 1024)
        return LearningReview(
            review_id=str(row[0]),
            workspace_id=str(row[1]),
            task_run_id=str(row[2]),
            task_outcome_id=str(row[3]),
            review_version=int(row[4]),
            trigger=str(row[5]),
            status=str(row[6]),
            policy_snapshot_json=canonical_json_bytes(snapshot).decode("utf-8"),
            policy_digest=str(row[9]),
            reviewer_provider_id=str(row[10]) if row[10] is not None else None,
            reviewer_model_id=str(row[11]) if row[11] is not None else None,
            reviewer_prompt_version=str(row[12]),
            reviewer_schema_version=str(row[13]),
            supersedes_review_id=str(row[14]) if row[14] is not None else None,
            lease_id=str(row[15]) if row[15] is not None else None,
            lease_expires_at=_from_optional_unix(row[16]),
            attempt_count=int(row[17]),
            row_version=int(row[18]),
            created_at=_from_unix(row[19]),
            started_at=_from_optional_unix(row[20]),
            completed_at=_from_optional_unix(row[21]),
            failure_code=str(row[22]) if row[22] is not None else None,
        )
    except StorageError:
        raise
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "learning review is invalid") from exc


def _evidence_from_row(row: tuple[object, ...]) -> LearningEvidence:
    try:
        excerpt = str(row[12]) if row[12] is not None else None
        if excerpt is not None and len(excerpt.encode("utf-8")) != int(row[13]):
            raise ValueError("evidence excerpt size mismatch")
        if excerpt is None and int(row[13]) != 0:
            raise ValueError("empty evidence excerpt has a non-zero size")
        return LearningEvidence(
            evidence_id=str(row[0]),
            workspace_id=str(row[1]),
            origin_review_id=str(row[2]),
            task_run_id=str(row[3]),
            source_kind=str(row[4]),
            source_id=str(row[5]),
            source_pointer=str(row[6]) or None,
            actor=str(row[7]),
            authority=str(row[8]),
            explicitness=str(row[9]),
            polarity=str(row[10]),
            scope_hint=str(row[11]) if row[11] is not None else None,
            excerpt_redacted=excerpt,
            content_digest=str(row[14]),
            safety_rejection_code=str(row[15]) if row[15] is not None else None,
            observed_at=_from_unix(row[16]),
            created_at=_from_unix(row[17]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "learning evidence is invalid") from exc


def _candidate_from_row(row: tuple[object, ...]) -> LearningCandidate:
    try:
        payload = _json_object(row[7], row[8], label="candidate proposal", maximum=8 * 1024)
        evidence_ids = _read_json_list(row[11], label="candidate evidence IDs")
        confidence_basis = _read_json_list(row[13], label="candidate confidence basis")
        conflict_refs = _read_json_list(row[18], label="candidate conflict references")
        return LearningCandidate(
            candidate_id=str(row[0]),
            workspace_id=str(row[1]),
            origin_review_id=str(row[2]),
            candidate_type=str(row[3]),
            operation=str(row[4]),
            semantic_key=str(row[5]),
            proposed_scope=str(row[6]),
            proposed_payload=payload,
            fingerprint=str(row[9]),
            status=str(row[10]),
            evidence_ids=evidence_ids,
            confidence_band=str(row[12]),
            confidence_basis=confidence_basis,
            sensitivity=str(row[14]),
            expected_target_revision=(int(row[15]) if row[15] is not None else None),
            duplicate_of_id=str(row[16]) if row[16] is not None else None,
            supersedes_id=str(row[17]) if row[17] is not None else None,
            conflict_refs=conflict_refs,
            expires_at=_from_unix(row[19]),
            row_version=int(row[20]),
            created_at=_from_unix(row[21]),
            resolved_at=_from_optional_unix(row[22]),
            resolved_by=str(row[23]) if row[23] is not None else None,
            rejection_reason=str(row[24]) if row[24] is not None else None,
        )
    except StorageError:
        raise
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "learning candidate is invalid") from exc


def _suppression_from_row(row: tuple[object, ...]) -> LearningSuppression:
    try:
        return LearningSuppression(
            suppression_id=str(row[0]),
            workspace_id=str(row[1]),
            candidate_type=str(row[2]),
            scope=str(row[3]),
            semantic_key=str(row[4]) if row[4] is not None else None,
            fingerprint=str(row[5]) if row[5] is not None else None,
            source_candidate_id=str(row[6]) if row[6] is not None else None,
            reason=str(row[7]),
            status=str(row[8]),
            expires_at=_from_optional_unix(row[9]),
            row_version=int(row[10]),
            created_at=_from_unix(row[11]),
            updated_at=_from_unix(row[12]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "learning suppression is invalid"
        ) from exc


from morrow.adapters.state.learning_candidate_journal import (  # noqa: E402
    SqliteLearningCandidateMixin,
)
from morrow.adapters.state.learning_policy_journal import SqliteLearningPolicyMixin  # noqa: E402
from morrow.adapters.state.learning_review_journal import SqliteLearningReviewMixin  # noqa: E402


class SqliteLearningJournal(
    SqliteLearningPolicyMixin,
    SqliteLearningReviewMixin,
    SqliteLearningCandidateMixin,
):
    """Small composition root for the bounded Learning repositories."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend
