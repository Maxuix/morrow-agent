"""Preference projection helpers and the generic reducer-facing API."""

from __future__ import annotations

from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import (
    PreferenceEntry,
    PreferenceLifecycleOperation,
    PreferenceOperation,
    PreferenceStatus,
)
from morrow.core.preference_operations import (
    PreferenceOperationError,
    exact_preference_key,
    reduce_preference_lifecycle,
    reduce_preference_operations,
)


def reduce_preference_batch(
    document: PreferenceDocument,
    operations: tuple[PreferenceOperation, ...] = (),
    *,
    lifecycle_operations: tuple[PreferenceLifecycleOperation, ...] = (),
    now=None,
    allocate_id=None,
) -> PreferenceDocument:
    """Apply a generic same-scope batch without exposing persistence details."""

    return reduce_preference_operations(
        document,
        operations,
        lifecycle_operations,
        now=now,
        allocate_id=allocate_id,
    )


def reduce_preference_lifecycle_command(
    document: PreferenceDocument,
    operation: PreferenceLifecycleOperation,
    *,
    now=None,
) -> PreferenceDocument:
    return reduce_preference_lifecycle(document, operation, now=now)


def merge_preference_entries(
    global_entries: tuple[PreferenceEntry, ...] = (),
    workspace_entries: tuple[PreferenceEntry, ...] = (),
    session_entries: tuple[PreferenceEntry, ...] = (),
) -> tuple[PreferenceEntry, ...]:
    """Merge generic scopes in precedence order without reviving tombstones."""

    selected: dict[str, PreferenceEntry] = {}
    for entries in (global_entries, workspace_entries, session_entries):
        for entry in entries:
            key = " ".join(entry.statement.split()).casefold()
            if entry.status in {PreferenceStatus.DELETED, PreferenceStatus.DISABLED}:
                selected.pop(key, None)
            else:
                selected[key] = entry
    return tuple(selected.values())


__all__ = [
    "PreferenceOperationError",
    "exact_preference_key",
    "merge_preference_entries",
    "reduce_preference_batch",
    "reduce_preference_lifecycle_command",
]
