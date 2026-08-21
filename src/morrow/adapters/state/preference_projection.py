"""Small compatibility projections between generic and fixed Preferences."""

from __future__ import annotations

from collections.abc import Iterable

from morrow.core.models import Preferences
from morrow.core.preference_models import PreferenceStatus

_DETAIL_SENTENCES = {
    "concise": "回答默认保持简洁。",
    "balanced": "回答默认在简洁与细节之间保持平衡。",
    "detailed": "回答默认提供详细说明。",
}


def preferences_from_entries(entries: Iterable) -> Preferences:
    language = None
    response_detail = None
    instructions: list[str] = []
    reverse_detail = {value: key for key, value in _DETAIL_SENTENCES.items()}
    for entry in entries:
        if entry.status is not PreferenceStatus.ACTIVE:
            continue
        statement = entry.statement
        if statement.startswith("回答时默认使用 ") and statement.endswith("。"):
            language = statement[len("回答时默认使用 ") : -1]
        elif statement in reverse_detail:
            response_detail = reverse_detail[statement]
        elif statement not in instructions:
            instructions.append(statement)
    return Preferences(
        language=language,
        response_detail=response_detail,
        instructions=instructions,
    )


__all__ = ["preferences_from_entries"]
