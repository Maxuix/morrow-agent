"""Preference projection helpers and the generic reducer-facing API."""

from __future__ import annotations

from morrow.core.models import Preferences
from morrow.core.preference_models import (
    PreferenceDocument,
    PreferenceLifecycleOperation,
    PreferenceOperation,
)
from morrow.core.preference_operations import (
    PreferenceOperationError,
    exact_preference_key,
    reduce_preference_document,
    reduce_preference_lifecycle,
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
    operations: tuple[PreferenceOperation, ...],
    *,
    now=None,
    allocate_id=None,
) -> PreferenceDocument:
    """Apply a generic same-scope batch without exposing persistence details."""

    return reduce_preference_document(document, operations, now=now, allocate_id=allocate_id)


def reduce_preference_lifecycle_command(
    document: PreferenceDocument,
    operation: PreferenceLifecycleOperation,
    *,
    now=None,
) -> PreferenceDocument:
    return reduce_preference_lifecycle(document, operation, now=now)


__all__ = [
    "PreferenceOperationError",
    "exact_preference_key",
    "merge_preferences",
    "reduce_preference_batch",
    "reduce_preference_lifecycle_command",
]
