"""Pure legacy Preference and historical snapshot decoders."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from morrow.core.domain import canonical_json_bytes
from morrow.core.models import ModelRef, ProviderConfig, utc_now
from morrow.core.preference_documents import (
    GlobalConfig,
    PreferenceEntriesPayload,
    WorkspacePreferenceDocument,
)
from morrow.core.preference_models import (
    PreferenceEntry,
    PreferenceScope,
    normalize_preference_statement,
)
from morrow.core.state_schema import (
    GLOBAL_CONFIG_LEGACY_SCHEMA_VERSION,
    GLOBAL_CONFIG_SCHEMA_VERSION,
    WORKSPACE_PREFERENCE_LEGACY_SCHEMA_VERSION,
    WORKSPACE_PREFERENCE_SCHEMA_VERSION,
)

LEGACY_GLOBAL_SCHEMA_VERSION = GLOBAL_CONFIG_LEGACY_SCHEMA_VERSION
LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION = WORKSPACE_PREFERENCE_LEGACY_SCHEMA_VERSION
GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION = GLOBAL_CONFIG_SCHEMA_VERSION

_DETAIL_SENTENCES = {
    "concise": "回答默认保持简洁。",
    "balanced": "回答默认在简洁与细节之间保持平衡。",
    "detailed": "回答默认提供详细说明。",
}


class PreferenceYamlDecodeError(ValueError):
    """A sanitized refusal to interpret legacy or future Preference YAML."""

    def __init__(self, code: str, message: str = "Preference YAML cannot be interpreted") -> None:
        super().__init__(message)
        self.code = code


def preference_id_from_legacy(
    scope: PreferenceScope | str,
    legacy_path: str,
    original_value: object,
    occurrence_index: int,
) -> str:
    """Derive a stable ID from source identity, never from rendered text."""

    scope_value = scope.value if isinstance(scope, PreferenceScope) else str(scope)
    payload = canonical_json_bytes(
        [scope_value, str(legacy_path), original_value, int(occurrence_index)]
    )
    return f"pref_{hashlib.sha256(payload).hexdigest()[:24]}"


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


def _preference_entry(
    *,
    scope: PreferenceScope,
    path: str,
    original_value: object,
    occurrence_index: int,
    statement: str,
    timestamp: datetime,
) -> PreferenceEntry:
    try:
        return PreferenceEntry(
            preference_id=preference_id_from_legacy(scope, path, original_value, occurrence_index),
            statement=statement,
            scope=scope,
            revision=1,
            evidence_ids=(),
            created_at=timestamp,
            updated_at=timestamp,
        )
    except ValueError as exc:
        raise PreferenceYamlDecodeError("invalid_legacy_preference") from exc


def legacy_entries_from_preferences(
    scope: PreferenceScope | str,
    raw_preferences: Mapping[str, Any] | None,
    *,
    timestamp: datetime | None = None,
    allow_session: bool = False,
) -> tuple[PreferenceEntry, ...]:
    """Map v1 fixed fields in their locked order and deduplicate exact statements."""

    resolved_scope = PreferenceScope(scope)
    if resolved_scope is PreferenceScope.SESSION and not allow_session:
        raise PreferenceYamlDecodeError("session_scope_not_durable")
    raw = dict(raw_preferences or {})
    if not isinstance(raw, dict):
        raise PreferenceYamlDecodeError("invalid_legacy_preferences")
    now = timestamp or utc_now()
    candidates: list[PreferenceEntry] = []

    if "language" in raw and raw["language"] is not None:
        language = raw["language"]
        if not isinstance(language, str):
            raise PreferenceYamlDecodeError("invalid_legacy_language")
        candidates.append(
            _preference_entry(
                scope=resolved_scope,
                path="language",
                original_value=language,
                occurrence_index=0,
                statement=f"回答时默认使用 {language}。",
                timestamp=now,
            )
        )

    if "response_detail" in raw and raw["response_detail"] is not None:
        detail = raw["response_detail"]
        if not isinstance(detail, str) or detail not in _DETAIL_SENTENCES:
            raise PreferenceYamlDecodeError("invalid_legacy_response_detail")
        candidates.append(
            _preference_entry(
                scope=resolved_scope,
                path="response_detail",
                original_value=detail,
                occurrence_index=0,
                statement=_DETAIL_SENTENCES[detail],
                timestamp=now,
            )
        )

    if "instructions" in raw and raw["instructions"] is not None:
        instructions = raw["instructions"]
        if not isinstance(instructions, Sequence) or isinstance(instructions, (str, bytes)):
            raise PreferenceYamlDecodeError("invalid_legacy_instructions")
        for index, instruction in enumerate(instructions):
            if not isinstance(instruction, str):
                raise PreferenceYamlDecodeError("invalid_legacy_instruction")
            candidates.append(
                _preference_entry(
                    scope=resolved_scope,
                    path="instructions",
                    original_value=instruction,
                    occurrence_index=index,
                    statement=instruction,
                    timestamp=now,
                )
            )

    result: list[PreferenceEntry] = []
    seen: set[str] = set()
    for entry in candidates:
        key = normalize_preference_statement(entry.statement).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return tuple(result)


def _mapping(value: object, *, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PreferenceYamlDecodeError(code)
    return dict(value)


def decode_global_config(raw: Mapping[str, Any]) -> GlobalConfig:
    """Decode v1 or v2 global config without publishing a migration."""

    data = _mapping(raw, code="invalid_global_config")
    try:
        schema_version = int(data.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise PreferenceYamlDecodeError("invalid_schema_version") from exc
    if schema_version > GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION:
        raise PreferenceYamlDecodeError("future_global_schema")
    if schema_version == GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION:
        try:
            return GlobalConfig.model_validate(data)
        except ValueError as exc:
            raise PreferenceYamlDecodeError("invalid_global_config") from exc
    if schema_version != LEGACY_GLOBAL_SCHEMA_VERSION:
        raise PreferenceYamlDecodeError("unsupported_global_schema")

    timestamp = _timestamp(data.get("updated_at"), utc_now())
    entries = legacy_entries_from_preferences(
        PreferenceScope.GLOBAL,
        _mapping(data.get("preferences", {}), code="invalid_legacy_preferences"),
        timestamp=timestamp,
    )
    try:
        providers_raw = data.get("providers", {})
        if not isinstance(providers_raw, Mapping):
            raise ValueError("providers must be a mapping")
        providers = {
            str(provider_id): ProviderConfig.model_validate(provider)
            for provider_id, provider in providers_raw.items()
        }
        active_model = (
            None
            if data.get("active_model") is None
            else ModelRef.model_validate(data["active_model"])
        )
        result = GlobalConfig(
            revision=int(data.get("revision", 0)),
            updated_at=timestamp,
            preferences=PreferenceEntriesPayload(entries=entries),
            providers=providers,
            active_model=active_model,
        )
    except (TypeError, ValueError) as exc:
        raise PreferenceYamlDecodeError("invalid_global_config") from exc
    return result


def decode_workspace_preferences(raw: Mapping[str, Any]) -> WorkspacePreferenceDocument:
    """Decode v2 envelope/fixed fields or v3 generic entries."""

    data = _mapping(raw, code="invalid_workspace_preferences")
    try:
        schema_version = int(data.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise PreferenceYamlDecodeError("invalid_schema_version") from exc
    if schema_version > WORKSPACE_PREFERENCE_SCHEMA_VERSION:
        raise PreferenceYamlDecodeError("future_workspace_preference_schema")
    if schema_version == WORKSPACE_PREFERENCE_SCHEMA_VERSION:
        try:
            return WorkspacePreferenceDocument.model_validate(data)
        except ValueError as exc:
            raise PreferenceYamlDecodeError("invalid_workspace_preferences") from exc
    if schema_version not in {1, LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION}:
        raise PreferenceYamlDecodeError("unsupported_workspace_preference_schema")

    timestamp = _timestamp(data.get("updated_at"), utc_now())
    state = data.get("state", "present")
    if state == "cleared":
        entries = None
    else:
        legacy_preferences = _mapping(
            data.get("preferences", {}), code="invalid_legacy_preferences"
        )
        entries = legacy_entries_from_preferences(
            PreferenceScope.WORKSPACE, legacy_preferences, timestamp=timestamp
        )
    try:
        return WorkspacePreferenceDocument(
            revision=int(data.get("revision", 0)),
            updated_at=timestamp,
            state=state,
            entries=entries,
        )
    except (TypeError, ValueError) as exc:
        raise PreferenceYamlDecodeError("invalid_workspace_preferences") from exc


__all__ = [
    "GLOBAL_CONFIG_PREFERENCE_SCHEMA_VERSION",
    "LEGACY_GLOBAL_SCHEMA_VERSION",
    "LEGACY_WORKSPACE_PREFERENCE_SCHEMA_VERSION",
    "WORKSPACE_PREFERENCE_SCHEMA_VERSION",
    "PreferenceYamlDecodeError",
    "decode_global_config",
    "decode_workspace_preferences",
    "legacy_entries_from_preferences",
    "preference_id_from_legacy",
]
