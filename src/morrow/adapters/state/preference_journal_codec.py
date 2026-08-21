"""Strict SQLite codecs for the Preference v13 repositories."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from morrow.core.domain import canonical_json_bytes
from morrow.core.preference_documents import PreferenceReviewSnapshot
from morrow.core.preference_models import PreferenceOperation
from morrow.core.preference_persistence_models import (
    PreferenceEvidence,
    PreferenceProposal,
    PreferenceReviewJob,
    PreferenceWriteBatch,
)
from morrow.core.store import StorageError, StorageErrorCode

_JOB_COLUMNS = (
    "job_id, workspace_id, session_id, turn_id, review_version, status, "
    "source_global_revision, source_workspace_revision, active_snapshot_json, "
    "active_snapshot_count, active_snapshot_bytes, active_snapshot_digest, "
    "reviewer_provider_id, reviewer_model_id, reviewer_prompt_version, "
    "reviewer_schema_version, lease_id, lease_expires_at_unix, attempt_count, "
    "row_version, created_at_unix, started_at_unix, completed_at_unix, failure_code"
)
_EVIDENCE_COLUMNS = (
    "evidence_id, workspace_id, job_id, turn_id, source_kind, actor, excerpt_redacted, "
    "excerpt_bytes, content_digest, safety_rejection_code, observed_at_unix, created_at_unix"
)
_PROPOSAL_COLUMNS = (
    "proposal_id, workspace_id, job_id, evidence_id, operation, scope, preference_id, "
    "operation_json, operation_bytes, operation_digest, expected_target_revision, "
    "expected_document_revision, status, final_operation_json, final_operation_bytes, "
    "decision_command_id, decision_reason, row_version, created_at_unix, resolved_at_unix"
)
_BATCH_COLUMNS = (
    "batch_id, workspace_id, scope, command_id, operations_json, operations_bytes, "
    "allocated_add_ids_json, proposal_ids_json, expected_document_revision, "
    "before_document_revision, before_document_digest, after_document_revision, "
    "after_document_digest, before_document_json, after_document_json, status, "
    "recovery_code, row_version, created_at_unix, prepared_at_unix, applied_at_unix, "
    "finalized_at_unix"
)


class PreferenceJournalFailure(StrEnum):
    INVALID_JSON = "invalid_json"
    WORKSPACE_MISMATCH = "workspace_mismatch"
    MISSING_REFERENCE = "missing_reference"
    STALE_ROW = "stale_row"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _optional_unix(value: datetime | None) -> int | None:
    return None if value is None else _unix(value)


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _from_optional_unix(value: object) -> datetime | None:
    return None if value is None else _from_unix(value)


def _workspace_error(label: str) -> StorageError:
    return StorageError(
        StorageErrorCode.UNAVAILABLE, f"Preference {label} is outside the workspace"
    )


def _missing(label: str) -> StorageError:
    return StorageError(StorageErrorCode.NOT_FOUND, f"Preference {label} is missing")


def _stale(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"Preference {label} row version is stale")


def _canonical_json(value: object, *, maximum: int, label: str) -> tuple[str, int]:
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"Preference {label} is invalid") from exc
    if not 2 <= len(encoded) <= maximum:
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"Preference {label} exceeds its budget")
    return encoded.decode("utf-8"), len(encoded)


def _load_json(value: object, size: object, *, maximum: int, label: str) -> Any:
    text = str(value)
    try:
        encoded = text.encode("utf-8")
        parsed = json.loads(text)
        canonical = canonical_json_bytes(parsed)
        if canonical != encoded or len(encoded) != int(size) or len(encoded) > maximum:
            raise ValueError("non-canonical or mis-sized JSON")
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError, UnicodeError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"Preference {label} is invalid") from exc


def _snapshot_json(job: PreferenceReviewJob) -> tuple[str, int]:
    parsed = _load_json(
        job.active_snapshot_json,
        job.active_snapshot_bytes,
        maximum=192 * 1024,
        label="Review snapshot",
    )
    try:
        snapshot = PreferenceReviewSnapshot.model_validate(parsed)
    except ValueError as exc:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE, "Preference Review snapshot is invalid"
        ) from exc
    encoded = canonical_json_bytes(snapshot.model_dump(mode="json"))
    if len(snapshot.entries) != job.active_snapshot_count:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE, "Preference Review snapshot count is invalid"
        )
    digest = hashlib.sha256(encoded).hexdigest()
    if digest != job.active_snapshot_digest:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE, "Preference Review snapshot digest is invalid"
        )
    return encoded.decode("utf-8"), len(encoded)


def _job_from_row(row: tuple[object, ...]) -> PreferenceReviewJob:
    try:
        snapshot = _load_json(row[8], row[10], maximum=192 * 1024, label="Review snapshot")
        canonical = canonical_json_bytes(snapshot).decode("utf-8")
        if canonical != str(row[8]):
            raise ValueError("snapshot is not canonical")
        job = PreferenceReviewJob(
            job_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]) if row[2] is not None else None,
            turn_id=str(row[3]),
            review_version=int(row[4]),
            status=str(row[5]),
            source_global_revision=int(row[6]),
            source_workspace_revision=int(row[7]),
            active_snapshot_json=canonical,
            active_snapshot_count=int(row[9]),
            active_snapshot_bytes=int(row[10]),
            active_snapshot_digest=str(row[11]),
            reviewer_provider_id=str(row[12]) if row[12] is not None else None,
            reviewer_model_id=str(row[13]) if row[13] is not None else None,
            reviewer_prompt_version=str(row[14]),
            reviewer_schema_version=str(row[15]),
            lease_id=str(row[16]) if row[16] is not None else None,
            lease_expires_at=_from_optional_unix(row[17]),
            attempt_count=int(row[18]),
            row_version=int(row[19]),
            created_at=_from_unix(row[20]),
            started_at=_from_optional_unix(row[21]),
            completed_at=_from_optional_unix(row[22]),
            failure_code=str(row[23]) if row[23] is not None else None,
        )
        _snapshot_json(job)
        return job
    except StorageError:
        raise
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "Preference Review job is invalid"
        ) from exc


def _evidence_from_row(row: tuple[object, ...]) -> PreferenceEvidence:
    try:
        excerpt = str(row[6]) if row[6] is not None else None
        return PreferenceEvidence(
            evidence_id=str(row[0]),
            workspace_id=str(row[1]),
            job_id=str(row[2]),
            turn_id=str(row[3]),
            source_kind=str(row[4]),
            actor=str(row[5]),
            excerpt_redacted=excerpt,
            excerpt_bytes=int(row[7]),
            content_digest=str(row[8]),
            safety_rejection_code=str(row[9]) if row[9] is not None else None,
            observed_at=_from_unix(row[10]),
            created_at=_from_unix(row[11]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Preference Evidence is invalid") from exc


def _operation_from_json(value: object, size: object, *, label: str) -> PreferenceOperation:
    payload = _load_json(value, size, maximum=8192, label=label)
    try:
        return PreferenceOperation.model_validate(payload)
    except ValueError as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"Preference {label} is invalid") from exc


def _proposal_from_row(row: tuple[object, ...]) -> PreferenceProposal:
    try:
        operation = _operation_from_json(row[7], row[8], label="proposal operation")
        final = (
            None
            if row[13] is None
            else _operation_from_json(row[13], row[14], label="final proposal operation")
        )
        return PreferenceProposal(
            proposal_id=str(row[0]),
            workspace_id=str(row[1]),
            job_id=str(row[2]),
            evidence_id=str(row[3]),
            operation=operation,
            fingerprint=str(row[9]),
            expected_target_revision=(int(row[10]) if row[10] is not None else None),
            expected_document_revision=int(row[11]),
            status=str(row[12]),
            final_operation=final,
            decision_command_id=str(row[15]) if row[15] is not None else None,
            decision_reason=str(row[16]) if row[16] is not None else None,
            row_version=int(row[17]),
            created_at=_from_unix(row[18]),
            resolved_at=_from_optional_unix(row[19]),
        )
    except StorageError:
        raise
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Preference proposal is invalid") from exc


def _batch_from_row(row: tuple[object, ...]) -> PreferenceWriteBatch:
    try:
        operations_payload = _load_json(
            row[4], row[5], maximum=196608, label="write batch operations"
        )
        if not isinstance(operations_payload, list):
            raise ValueError("operations must be a list")
        operations = tuple(PreferenceOperation.model_validate(item) for item in operations_payload)
        allocated = _load_json(
            row[6], len(str(row[6]).encode("utf-8")), maximum=4096, label="allocated IDs"
        )
        proposals = _load_json(
            row[7], len(str(row[7]).encode("utf-8")), maximum=8192, label="proposal IDs"
        )
        return PreferenceWriteBatch(
            batch_id=str(row[0]),
            workspace_id=str(row[1]),
            scope=str(row[2]),
            command_id=str(row[3]),
            operations=operations,
            allocated_add_ids=tuple(allocated),
            proposal_ids=tuple(proposals),
            expected_document_revision=int(row[8]),
            before_document_revision=int(row[9]),
            before_document_digest=str(row[10]),
            after_document_revision=int(row[11]),
            after_document_digest=str(row[12]),
            before_document_json=str(row[13]) if row[13] is not None else None,
            after_document_json=str(row[14]) if row[14] is not None else None,
            status=str(row[15]),
            recovery_code=str(row[16]) if row[16] is not None else None,
            row_version=int(row[17]),
            created_at=_from_unix(row[18]),
            prepared_at=_from_optional_unix(row[19]),
            applied_at=_from_optional_unix(row[20]),
            finalized_at=_from_optional_unix(row[21]),
        )
    except StorageError:
        raise
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "Preference write batch is invalid"
        ) from exc


__all__ = [
    "PreferenceJournalFailure",
    "_BATCH_COLUMNS",
    "_EVIDENCE_COLUMNS",
    "_JOB_COLUMNS",
    "_PROPOSAL_COLUMNS",
    "_batch_from_row",
    "_canonical_json",
    "_evidence_from_row",
    "_from_optional_unix",
    "_job_from_row",
    "_missing",
    "_operation_from_json",
    "_optional_unix",
    "_proposal_from_row",
    "_snapshot_json",
    "_unix",
    "_workspace_error",
]
