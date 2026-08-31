"""Preference projection helpers and the generic reducer-facing API."""

from __future__ import annotations

from morrow.core.preference_models import (
    PreferenceEntry,
    PreferenceStatus,
)


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
    "merge_preference_entries",
]
