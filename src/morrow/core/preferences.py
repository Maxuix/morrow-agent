"""Pure preference merge rules shared across application and services."""

from __future__ import annotations

from morrow.core.models import Preferences


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
