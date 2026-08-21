"""Read-only queries over the YAML-authoritative Preference documents."""

from __future__ import annotations

from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.adapters.state.preference_yaml_types import PreferenceYamlLoadStatus
from morrow.application.preferences.writer import PreferenceWriter, PreferenceWriterError
from morrow.core.preference_models import PreferenceScope, PreferenceStatus


class PreferenceQueries:
    """Load one durable Preference scope without mutating it."""

    def __init__(self, yaml_store: PreferenceYamlStore, workspace_id: str) -> None:
        self.yaml_store = yaml_store
        self.workspace_id = workspace_id

    def document(self, scope: PreferenceScope | str):
        try:
            durable_scope = scope if isinstance(scope, PreferenceScope) else PreferenceScope(scope)
        except ValueError as exc:
            raise PreferenceWriterError("invalid_scope", "Preference scope is invalid") from exc
        if durable_scope is PreferenceScope.SESSION:
            raise PreferenceWriterError(
                "invalid_scope", "session Preference scope is not YAML-backed"
            )
        load = (
            self.yaml_store.load_global()
            if durable_scope is PreferenceScope.GLOBAL
            else self.yaml_store.load_workspace(self.workspace_id)
        )
        if load.status is not PreferenceYamlLoadStatus.OK or load.value is None:
            raise PreferenceWriterError("unavailable", "Preference YAML could not be read")
        return PreferenceWriter._document_from_value(durable_scope, load.value)

    def list(
        self,
        scope: PreferenceScope | str,
        *,
        status: PreferenceStatus | str | None = None,
        include_deleted: bool = False,
    ):
        document = self.document(scope)
        selected_status = None
        if status is not None:
            try:
                selected_status = (
                    status if isinstance(status, PreferenceStatus) else PreferenceStatus(status)
                )
            except ValueError as exc:
                raise PreferenceWriterError(
                    "invalid_status", "Preference status is invalid"
                ) from exc
        return tuple(
            entry
            for entry in document.entries
            if (include_deleted or entry.status is not PreferenceStatus.DELETED)
            and (selected_status is None or entry.status is selected_status)
        )

    def get(
        self,
        scope: PreferenceScope | str,
        preference_id: str,
        *,
        include_deleted: bool = False,
    ):
        return next(
            (
                entry
                for entry in self.list(scope, include_deleted=include_deleted)
                if entry.preference_id == preference_id
            ),
            None,
        )


__all__ = ["PreferenceQueries"]
