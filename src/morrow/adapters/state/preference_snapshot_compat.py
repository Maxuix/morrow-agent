"""Read-only decoder for historical AgentRun Preference snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from morrow.adapters.state.preference_migration import (
    PreferenceYamlDecodeError,
    legacy_entries_from_preferences,
)
from morrow.core.preference_models import (
    PreferenceEntry,
    PreferenceScope,
    normalize_preference_statement,
)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PreferenceYamlDecodeError("invalid_agent_run_snapshot")
    return dict(value)


def _timestamp(value: object, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise PreferenceYamlDecodeError("invalid_timestamp") from exc
    raise PreferenceYamlDecodeError("invalid_timestamp")


def decode_legacy_agent_run_preferences(raw: object) -> tuple[PreferenceEntry, ...]:
    """Decode immutable old AgentRun shapes without rewriting the stored snapshot."""

    from morrow.core.models import utc_now

    data = _mapping(raw)
    entries: list[PreferenceEntry] = []
    timestamp = _timestamp(data.get("created_at"), utc_now())

    if isinstance(data.get("preference_entries"), Sequence):
        try:
            entries.extend(
                PreferenceEntry.model_validate(item) for item in data["preference_entries"]
            )
        except (TypeError, ValueError) as exc:
            raise PreferenceYamlDecodeError("invalid_agent_run_preferences") from exc
    elif isinstance(data.get("preferences"), Mapping) and "entries" in data["preferences"]:
        try:
            entries.extend(
                PreferenceEntry.model_validate(item) for item in data["preferences"]["entries"]
            )
        except (TypeError, ValueError) as exc:
            raise PreferenceYamlDecodeError("invalid_agent_run_preferences") from exc
    else:
        for key, scope in (
            ("global_preferences", PreferenceScope.GLOBAL),
            ("workspace_preferences", PreferenceScope.WORKSPACE),
            ("session_preferences", PreferenceScope.SESSION),
        ):
            value = data.get(key)
            if isinstance(value, Mapping):
                entries.extend(
                    legacy_entries_from_preferences(
                        scope, value, timestamp=timestamp, allow_session=True
                    )
                )
        if not entries and isinstance(data.get("preferences"), Mapping):
            entries.extend(
                legacy_entries_from_preferences(
                    PreferenceScope.WORKSPACE, data["preferences"], timestamp=timestamp
                )
            )

    result: list[PreferenceEntry] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry.scope.value, normalize_preference_statement(entry.statement).casefold())
        if key not in seen:
            seen.add(key)
            result.append(entry)
    return tuple(result)


__all__ = ["decode_legacy_agent_run_preferences"]
