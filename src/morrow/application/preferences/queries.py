"""Read-only queries over the YAML-authoritative Preference documents."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.adapters.state.preference_yaml_types import PreferenceYamlLoadStatus
from morrow.application.preferences.writer import PreferenceWriter, PreferenceWriterError
from morrow.core.models import ProtocolModel
from morrow.core.preference_models import PreferenceScope, PreferenceStatus


class PreferenceScopeStatus(ProtocolModel):
    revision: int = Field(ge=0)
    active: int = Field(ge=0)
    disabled: int = Field(ge=0)
    deleted: int = Field(ge=0)
    load_status: Literal["ok", "corrupt", "unsupported_schema"] = "ok"
    error: str | None = Field(default=None, max_length=128)


class PreferenceContextStatusView(ProtocolModel):
    """Keep live Preference and frozen Knowledge diagnostics explicitly separate."""

    global_preferences: PreferenceScopeStatus
    workspace_preferences: PreferenceScopeStatus
    injected_count: int = Field(ge=0)
    injected_digest: str | None = None
    omitted_count: int = Field(ge=0)
    source_scopes: tuple[Literal["global", "workspace", "session"], ...] = ()
    refresh_status: Literal["ok", "degraded"] = "ok"
    refresh_error: str | None = Field(default=None, max_length=128)
    memory_selection_id: str | None = None
    memory_selection_revision: int | None = Field(default=None, ge=0)
    memory_selection_item_count: int = Field(default=0, ge=0)


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

    def context_status(
        self, *, snapshot=None, memory_selection=None
    ) -> PreferenceContextStatusView:
        global_load = self.yaml_store.load_global()
        workspace_load = self.yaml_store.load_workspace(self.workspace_id)
        return PreferenceContextStatusView(
            global_preferences=_scope_load_status(PreferenceScope.GLOBAL, global_load),
            workspace_preferences=_scope_load_status(PreferenceScope.WORKSPACE, workspace_load),
            injected_count=len(snapshot.frozen_preferences) if snapshot is not None else 0,
            injected_digest=(
                snapshot.preference_projection_digest if snapshot is not None else None
            ),
            omitted_count=snapshot.preference_omitted_count if snapshot is not None else 0,
            source_scopes=snapshot.preference_source_scopes if snapshot is not None else (),
            refresh_status=(snapshot.preference_refresh_status if snapshot is not None else "ok"),
            refresh_error=(snapshot.preference_refresh_error if snapshot is not None else None),
            memory_selection_id=(
                memory_selection.selection_id if memory_selection is not None else None
            ),
            memory_selection_revision=(
                memory_selection.source_memory_revision if memory_selection is not None else None
            ),
            memory_selection_item_count=(
                memory_selection.item_count if memory_selection is not None else 0
            ),
        )


def _scope_status(document) -> PreferenceScopeStatus:
    counts = {status: 0 for status in PreferenceStatus}
    for entry in document.entries:
        counts[entry.status] += 1
    return PreferenceScopeStatus(
        revision=document.revision,
        active=counts[PreferenceStatus.ACTIVE],
        disabled=counts[PreferenceStatus.DISABLED],
        deleted=counts[PreferenceStatus.DELETED],
    )


def _scope_load_status(scope: PreferenceScope, load) -> PreferenceScopeStatus:
    if load.status is not PreferenceYamlLoadStatus.OK or load.value is None:
        return PreferenceScopeStatus(
            revision=max(0, load.revision),
            active=0,
            disabled=0,
            deleted=0,
            load_status=load.status.value,
            error=load.error or load.status.value,
        )
    return _scope_status(PreferenceWriter._document_from_value(scope, load.value))


__all__ = ["PreferenceContextStatusView", "PreferenceQueries", "PreferenceScopeStatus"]
