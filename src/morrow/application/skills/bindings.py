"""Pure Skill Binding changes plus YAML-authority access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from morrow.adapters.state.extension_yaml import (
    ExtensionYamlConflict,
    ExtensionYamlLoadStatus,
    ExtensionYamlStore,
    extension_document_digest,
)
from morrow.core.skills.bindings import (
    ExtensionDocument,
    GlobalExtensionDocument,
    SkillBinding,
    SkillSelectionMode,
    WorkspaceExtensionDocument,
)
from morrow.core.skills.catalog import SkillCatalogEntry
from morrow.core.skills.identity import validate_skill_id, validate_skv_id
from morrow.core.skills.trust import SourceKind

Scope = Literal["global", "workspace"]


class SkillBindingError(ValueError):
    """A requested Binding change cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class PreparedBindingChange:
    operation: str
    scope: Scope
    scope_id: str | None
    skill_id: str
    before_document: ExtensionDocument
    after_document: ExtensionDocument
    before_binding: SkillBinding | None
    after_binding: SkillBinding | None


def _binding(
    *,
    skill_id: str,
    scope: Scope,
    scope_id: str | None,
    existing: SkillBinding | None,
    enabled: bool | None = None,
    pinned_version_id: str | None = None,
    set_pin: bool = False,
    source_kind: SourceKind | None = None,
    selection_mode: SkillSelectionMode | str | None = None,
) -> SkillBinding:
    return SkillBinding(
        skill_id=skill_id,
        scope=scope,
        scope_id=scope_id,
        source_kind=(
            source_kind if source_kind is not None else existing.source_kind if existing else None
        ),
        enabled=existing.enabled if existing is not None and enabled is None else bool(enabled),
        pinned_version_id=(
            pinned_version_id
            if set_pin
            else existing.pinned_version_id
            if existing is not None
            else None
        ),
        selection_mode=(
            selection_mode
            if selection_mode is not None
            else existing.selection_mode
            if existing is not None
            else SkillSelectionMode.EXPLICIT
        ),
        revision=(existing.revision + 1 if existing is not None else 1),
    )


def change_document(
    document: ExtensionDocument,
    *,
    operation: str,
    skill_id: str,
    scope: Scope,
    scope_id: str | None,
    version_id: str | None = None,
    source_kind: SourceKind | None = None,
    selection_mode: SkillSelectionMode | str | None = None,
    rollback_version_id: str | None = None,
) -> tuple[ExtensionDocument, SkillBinding | None, SkillBinding | None]:
    """Reduce one lifecycle operation without reading or writing external state."""

    validate_skill_id(skill_id)
    if document.scope != scope or document.scope_id != scope_id:
        raise SkillBindingError("Binding scope does not match the Extension document")
    if version_id is not None:
        validate_skv_id(version_id)
    if rollback_version_id is not None:
        validate_skv_id(rollback_version_id)
    current = next((item for item in document.bindings if item.skill_id == skill_id), None)
    if (
        current is not None
        and source_kind is not None
        and current.source_kind is not None
        and current.source_kind is not source_kind
    ):
        raise SkillBindingError("Binding source does not match the requested source")
    if operation == "enable":
        updated = _binding(
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            existing=current,
            enabled=True,
            source_kind=source_kind,
            selection_mode=selection_mode,
        )
    elif operation == "disable":
        updated = _binding(
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            existing=current,
            enabled=False,
            source_kind=source_kind,
            selection_mode=selection_mode,
        )
    elif operation in {"pin", "unpin"}:
        if operation == "pin" and version_id is None:
            raise SkillBindingError("pin requires an exact version_id")
        updated = _binding(
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            existing=current,
            pinned_version_id=version_id,
            set_pin=True,
            source_kind=source_kind,
            selection_mode=selection_mode,
        )
    elif operation == "rollback":
        if rollback_version_id is None:
            raise SkillBindingError("rollback requires a resolved previous version")
        updated = _binding(
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            existing=current,
            pinned_version_id=rollback_version_id,
            set_pin=True,
            source_kind=source_kind,
            selection_mode=selection_mode,
        )
    elif operation == "remove":
        values = tuple(item for item in document.bindings if item.skill_id != skill_id)
        return document.model_copy(update={"bindings": values}), current, None
    else:
        raise SkillBindingError("unsupported Skill Binding operation")

    values = tuple(updated if item.skill_id == skill_id else item for item in document.bindings)
    if current is None:
        values = (*document.bindings, updated)
    values = tuple(sorted(values, key=lambda item: item.skill_id))
    return document.model_copy(update={"bindings": values}), current, updated


class SkillBindingService:
    """Small YAML-authority service used by lifecycle commands and queries."""

    def __init__(self, yaml_store: ExtensionYamlStore, workspace_id: str | None = None) -> None:
        self.yaml_store = yaml_store
        self.workspace_id = workspace_id

    def load(self, scope: Scope, *, scope_id: str | None = None):
        if scope == "global":
            if scope_id is not None:
                raise SkillBindingError("global Binding scope_id must be null")
            return self.yaml_store.load_global()
        workspace_id = scope_id or self.workspace_id
        if not workspace_id:
            raise SkillBindingError("workspace Binding requires a workspace_id")
        return self.yaml_store.load_workspace(workspace_id)

    def prepare_change(
        self,
        *,
        operation: str,
        skill_id: str,
        scope: Scope,
        scope_id: str | None = None,
        version_id: str | None = None,
        source_kind: SourceKind | None = None,
        selection_mode: SkillSelectionMode | str | None = None,
        rollback_version_id: str | None = None,
    ) -> PreparedBindingChange:
        if scope == "global" and scope_id is not None:
            raise SkillBindingError("global Binding scope_id must be null")
        resolved_scope_id = None if scope == "global" else scope_id or self.workspace_id
        load = self.load(scope, scope_id=resolved_scope_id)
        if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
            raise SkillBindingError(load.error or "Extension YAML is unavailable")
        after, before_binding, after_binding = change_document(
            load.value,
            operation=operation,
            skill_id=skill_id,
            scope=scope,
            scope_id=resolved_scope_id,
            version_id=version_id,
            source_kind=source_kind,
            selection_mode=selection_mode,
            rollback_version_id=rollback_version_id,
        )
        return PreparedBindingChange(
            operation,
            scope,
            resolved_scope_id,
            skill_id,
            load.value,
            after,
            before_binding,
            after_binding,
        )

    def apply_change(self, change: PreparedBindingChange):
        current = self.load(change.scope, scope_id=change.scope_id)
        if current.status is not ExtensionYamlLoadStatus.OK or current.value is None:
            raise SkillBindingError(current.error or "Extension YAML is unavailable")
        if current.revision != change.before_document.revision:
            raise ExtensionYamlConflict("Extension YAML changed during the Binding operation")
        if extension_document_digest(current.value) != extension_document_digest(
            change.before_document
        ):
            raise ExtensionYamlConflict("Extension YAML changed during the Binding operation")
        if current.value.bindings == change.after_document.bindings:
            return current
        if change.scope == "global":
            return self.yaml_store.write_global(
                GlobalExtensionDocument.model_validate(change.after_document),
                expected_revision=current.revision,
                expected_value_digest=current.digest,
            )
        return self.yaml_store.write_workspace(
            change.scope_id or "",
            WorkspaceExtensionDocument.model_validate(change.after_document),
            expected_revision=current.revision,
            expected_value_digest=current.digest,
        )

    def mutate(self, **kwargs):
        change = self.prepare_change(**kwargs)
        return self.apply_change(change)

    def enable(self, skill_id: str, **kwargs):
        return self.mutate(operation="enable", skill_id=skill_id, **kwargs)

    def disable(self, skill_id: str, **kwargs):
        return self.mutate(operation="disable", skill_id=skill_id, **kwargs)

    def pin(self, skill_id: str, version_id: str, **kwargs):
        return self.mutate(operation="pin", skill_id=skill_id, version_id=version_id, **kwargs)

    def unpin(self, skill_id: str, **kwargs):
        return self.mutate(operation="unpin", skill_id=skill_id, **kwargs)

    def rollback(self, skill_id: str, version_id: str, **kwargs):
        return self.mutate(
            operation="rollback",
            skill_id=skill_id,
            rollback_version_id=version_id,
            **kwargs,
        )

    def remove(self, skill_id: str, **kwargs):
        return self.mutate(operation="remove", skill_id=skill_id, **kwargs)

    def resolve_rollback_version(
        self,
        entry: SkillCatalogEntry,
        *,
        current_version_id: str | None = None,
        source_kind: SourceKind | None = None,
    ) -> str:
        versions = [
            item
            for item in entry.versions
            if source_kind is None or item.source_kind is source_kind
        ]
        from datetime import UTC, datetime

        versions.sort(
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        if current_version_id is None:
            current = versions[0] if versions else None
        else:
            current = next(
                (item for item in versions if item.version_id == current_version_id), None
            )
        previous = next(
            (item for item in versions if current is None or item.version_id != current.version_id),
            None,
        )
        if previous is None:
            raise SkillBindingError("no previous Skill version is available for rollback")
        return previous.version_id


__all__ = [
    "PreparedBindingChange",
    "SkillBindingError",
    "SkillBindingService",
    "change_document",
]
