"""Preference projection helpers and the generic reducer-facing API."""

from __future__ import annotations

from morrow.core.models import Preferences
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


def merge_preferences(
    global_prefs: Preferences, workspace_prefs: Preferences, session_prefs: Preferences
) -> Preferences:
    def pick(name: str):
        for source in (session_prefs, workspace_prefs, global_prefs):
            value = getattr(source, name)
            if value is not None:
                return value
        return None

    instructions: list[str] = []
    for source in (global_prefs, workspace_prefs, session_prefs):
        for item in source.instructions:
            normalized = " ".join(item.split()).casefold()
            instructions = [
                existing
                for existing in instructions
                if " ".join(existing.split()).casefold() != normalized
            ]
            instructions.append(item)
    return Preferences(
        language=pick("language"),
        response_detail=pick("response_detail"),
        instructions=instructions,
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
    "merge_preferences",
    "merge_preference_entries",
    "reduce_preference_batch",
    "reduce_preference_lifecycle_command",
]
