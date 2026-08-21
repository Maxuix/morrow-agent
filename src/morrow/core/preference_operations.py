"""Pure deterministic reducers for generic Preference documents."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import (
    PREFERENCE_ID_PREFIX,
    PREFERENCE_MAX_OPERATIONS,
    PreferenceEntry,
    PreferenceLifecycleKind,
    PreferenceLifecycleOperation,
    PreferenceOperation,
    PreferenceOperationKind,
    PreferenceScope,
    PreferenceStatus,
    normalize_preference_statement,
)


class PreferenceOperationError(ValueError):
    """Stable, sanitized failure raised before a document is changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


PreferenceIdAllocator = Callable[[PreferenceScope, int, PreferenceOperation], str]


def exact_preference_key(statement: str) -> str:
    """Return the same-scope key used for deterministic duplicate checks."""

    return normalize_preference_statement(statement).casefold()


def _timestamp(value: datetime | None) -> datetime:
    now = value or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Preference operation time must be timezone-aware")
    return now.astimezone(UTC)


def _default_allocator(
    scope: PreferenceScope, index: int, operation: PreferenceOperation, existing: set[str]
) -> str:
    payload = f"{scope.value}\0{index}\0{operation.statement or ''}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    candidate = f"{PREFERENCE_ID_PREFIX}_{digest[:24]}"
    suffix = 1
    while candidate in existing:
        candidate = f"{PREFERENCE_ID_PREFIX}_{digest[:20]}{suffix:04d}"
        suffix += 1
    return candidate


def _validate_operation_list(
    document: PreferenceDocument, operations: Iterable[PreferenceOperation]
) -> tuple[PreferenceOperation, ...]:
    batch = tuple(operations)
    if not 1 <= len(batch) <= PREFERENCE_MAX_OPERATIONS:
        raise PreferenceOperationError(
            "operation_count", "Preference batch must contain one to eight operations"
        )
    if document.scope not in {PreferenceScope.GLOBAL.value, PreferenceScope.WORKSPACE.value}:
        raise PreferenceOperationError("scope", "Preference document scope is not durable")
    expected_scope = PreferenceScope(document.scope)
    if any(operation.scope is not expected_scope for operation in batch):
        raise PreferenceOperationError(
            "mixed_scope", "Preference batch must use one durable document scope"
        )
    targets = [operation.preference_id for operation in batch if operation.preference_id]
    if len(targets) != len(set(targets)):
        raise PreferenceOperationError(
            "duplicate_target", "Preference targets may appear only once in a batch"
        )
    return batch


def _entry_map(document: PreferenceDocument) -> dict[str, PreferenceEntry]:
    return {entry.preference_id: entry for entry in document.entries}


def _assert_target(
    entry: PreferenceEntry | None, operation: PreferenceOperation
) -> PreferenceEntry:
    if entry is None:
        raise PreferenceOperationError("target_missing", "Preference target does not exist")
    if entry.status is PreferenceStatus.DELETED:
        raise PreferenceOperationError("target_deleted", "deleted Preference targets are terminal")
    return entry


def _assert_no_duplicate(
    entries: Iterable[PreferenceEntry], statement: str, *, exclude_id: str | None = None
) -> None:
    key = exact_preference_key(statement)
    for entry in entries:
        if entry.preference_id == exclude_id or entry.status is PreferenceStatus.DELETED:
            continue
        if exact_preference_key(entry.statement) == key:
            if entry.status is PreferenceStatus.DISABLED:
                raise PreferenceOperationError(
                    "disabled_duplicate",
                    "a disabled Preference has the same statement; enable or replace it",
                )
            raise PreferenceOperationError(
                "duplicate", "an active Preference has the same statement"
            )


def _updated_entry(
    entry: PreferenceEntry,
    *,
    now: datetime,
    statement: str | None = None,
    status: PreferenceStatus | None = None,
    evidence_ids: tuple[str, ...] | None = None,
) -> PreferenceEntry:
    return PreferenceEntry(
        preference_id=entry.preference_id,
        statement=statement if statement is not None else entry.statement,
        scope=entry.scope,
        status=status if status is not None else entry.status,
        revision=entry.revision + 1,
        evidence_ids=evidence_ids if evidence_ids is not None else entry.evidence_ids,
        created_at=entry.created_at,
        updated_at=now,
    )


def reduce_preference_document(
    document: PreferenceDocument,
    operations: Iterable[PreferenceOperation],
    *,
    now: datetime | None = None,
    allocate_id: PreferenceIdAllocator | None = None,
) -> PreferenceDocument:
    """Apply one same-scope batch and return a new document.

    All validation happens against an in-memory working copy.  The input is
    immutable and is never returned in a partially changed state.
    """

    batch = _validate_operation_list(document, operations)
    timestamp = _timestamp(now)
    entries = _entry_map(document)
    existing_ids = set(entries)
    allocator = allocate_id

    for index, operation in enumerate(batch):
        if operation.operation is PreferenceOperationKind.ADD:
            assert operation.statement is not None
            _assert_no_duplicate(entries.values(), operation.statement)
            preference_id = (
                allocator(PreferenceScope(document.scope), index, operation)
                if allocator is not None
                else _default_allocator(
                    PreferenceScope(document.scope), index, operation, existing_ids
                )
            )
            if not preference_id.startswith(f"{PREFERENCE_ID_PREFIX}_"):
                raise PreferenceOperationError(
                    "invalid_id", "Preference ID allocator returned an invalid ID"
                )
            if preference_id in existing_ids:
                raise PreferenceOperationError(
                    "duplicate_id", "Preference ID allocator returned a duplicate ID"
                )
            entry = PreferenceEntry(
                preference_id=preference_id,
                statement=operation.statement,
                scope=PreferenceScope(document.scope),
                status=PreferenceStatus.ACTIVE,
                revision=1,
                evidence_ids=operation.evidence_ids,
                created_at=timestamp,
                updated_at=timestamp,
            )
            entries[preference_id] = entry
            existing_ids.add(preference_id)
            continue

        target = _assert_target(entries.get(operation.preference_id), operation)
        if operation.operation is PreferenceOperationKind.REPLACE:
            assert operation.statement is not None
            _assert_no_duplicate(
                entries.values(), operation.statement, exclude_id=target.preference_id
            )
            entries[target.preference_id] = _updated_entry(
                target,
                now=timestamp,
                statement=operation.statement,
                evidence_ids=operation.evidence_ids or target.evidence_ids,
            )
        elif operation.operation is PreferenceOperationKind.REMOVE:
            entries[target.preference_id] = _updated_entry(
                target, now=timestamp, status=PreferenceStatus.DELETED
            )
        else:  # pragma: no cover - Pydantic makes this unreachable.
            raise PreferenceOperationError("operation", "unsupported Preference operation")

    try:
        return PreferenceDocument(
            schema_version=document.schema_version,
            scope=document.scope,
            revision=document.revision + 1,
            updated_at=timestamp,
            entries=tuple(entries.values()),
        )
    except ValueError as exc:
        raise PreferenceOperationError(
            "invalid_result", "Preference batch produced invalid state"
        ) from exc


def reduce_preference_lifecycle(
    document: PreferenceDocument,
    operation: PreferenceLifecycleOperation,
    *,
    now: datetime | None = None,
) -> PreferenceDocument:
    """Apply one explicit enable/disable operation."""

    if operation.scope.value != document.scope:
        raise PreferenceOperationError(
            "mixed_scope", "Preference lifecycle scope does not match document"
        )
    entry = _assert_target(
        next(
            (item for item in document.entries if item.preference_id == operation.preference_id),
            None,
        ),
        PreferenceOperation(
            operation=PreferenceOperationKind.REMOVE,
            scope=operation.scope,
            preference_id=operation.preference_id,
        ),
    )
    target_status = (
        PreferenceStatus.ACTIVE
        if operation.operation is PreferenceLifecycleKind.ENABLE
        else PreferenceStatus.DISABLED
    )
    if entry.status is target_status:
        raise PreferenceOperationError(
            "already_in_state", "Preference is already in the requested state"
        )
    if target_status is PreferenceStatus.ACTIVE:
        _assert_no_duplicate(document.entries, entry.statement, exclude_id=entry.preference_id)
    timestamp = _timestamp(now)
    updated = _updated_entry(entry, now=timestamp, status=target_status)
    entries = tuple(
        updated if item.preference_id == entry.preference_id else item for item in document.entries
    )
    return PreferenceDocument(
        schema_version=document.schema_version,
        scope=document.scope,
        revision=document.revision + 1,
        updated_at=timestamp,
        entries=entries,
    )


__all__ = [
    "PreferenceIdAllocator",
    "PreferenceOperationError",
    "exact_preference_key",
    "reduce_preference_document",
    "reduce_preference_lifecycle",
]
