"""Deterministic bounded generic Preference projection for one AgentRun."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.domain import (
    AGENT_RUN_PREFERENCE_MAX_BYTES,
    AGENT_RUN_PREFERENCE_MAX_ENTRIES,
    FrozenRunPreference,
    sha256_digest,
)
from morrow.core.preference_models import PreferenceEntry, PreferenceStatus

_SCOPE_PRIORITY = {"session": 0, "workspace": 1, "global": 2}
_RENDER_SCOPE = {"global": 0, "workspace": 1, "session": 2}


@dataclass(frozen=True, slots=True)
class PreferenceRunSelection:
    entries: tuple[FrozenRunPreference, ...]
    block: str
    digest: str
    omitted_count: int
    source_scopes: tuple[str, ...]


def select_run_preferences(
    global_entries: tuple[PreferenceEntry, ...] = (),
    workspace_entries: tuple[PreferenceEntry, ...] = (),
    session_entries: tuple[PreferenceEntry, ...] = (),
) -> PreferenceRunSelection:
    """Select by scope priority, then render in stable low-to-high scope order."""

    effective: dict[str, PreferenceEntry] = {}
    for entries in (global_entries, workspace_entries, session_entries):
        for entry in entries:
            key = " ".join(entry.statement.split()).casefold()
            if entry.status is PreferenceStatus.ACTIVE:
                effective[key] = entry
            else:
                effective.pop(key, None)

    candidates = sorted(
        effective.values(),
        key=lambda entry: (
            _SCOPE_PRIORITY[entry.scope.value],
            -entry.updated_at.timestamp(),
            entry.preference_id,
        ),
    )
    selected: list[PreferenceEntry] = []
    selected_bytes = 0
    for entry in candidates:
        line = _render_entry(entry.scope.value, entry.preference_id, entry.statement)
        line_bytes = len(line.encode("utf-8"))
        if (
            len(selected) >= AGENT_RUN_PREFERENCE_MAX_ENTRIES
            or selected_bytes + line_bytes > AGENT_RUN_PREFERENCE_MAX_BYTES
        ):
            continue
        selected.append(entry)
        selected_bytes += line_bytes

    rendered = sorted(
        selected,
        key=lambda entry: (
            _RENDER_SCOPE[entry.scope.value],
            entry.updated_at.timestamp(),
            entry.preference_id,
        ),
    )
    frozen = tuple(
        FrozenRunPreference(
            preference_id=entry.preference_id,
            statement=entry.statement,
            scope=entry.scope.value,
            revision=entry.revision,
            updated_at=entry.updated_at,
        )
        for entry in rendered
    )
    block = "".join(
        _render_entry(entry.scope, entry.preference_id, entry.statement) for entry in frozen
    )
    scopes = tuple(
        scope
        for scope in ("global", "workspace", "session")
        if any(entry.scope == scope for entry in frozen)
    )
    return PreferenceRunSelection(
        entries=frozen,
        block=block,
        digest=sha256_digest(block),
        omitted_count=len(candidates) - len(selected),
        source_scopes=scopes,
    )


def render_frozen_run_preferences(entries: tuple[FrozenRunPreference, ...]) -> str:
    """Render an already-frozen projection without consulting live Preference state."""

    return "".join(
        _render_entry(entry.scope, entry.preference_id, entry.statement) for entry in entries
    )


def _render_entry(scope: str, preference_id: str, statement: str) -> str:
    return f"- [{scope}:{preference_id}] {statement}\n"


__all__ = [
    "PreferenceRunSelection",
    "render_frozen_run_preferences",
    "select_run_preferences",
]
