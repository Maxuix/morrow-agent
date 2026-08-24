"""Binding enable/disable/pin/rollback command reduction."""

from __future__ import annotations

from dataclasses import replace

from morrow.core.skills.bindings import SkillLifecycleResult, SkillSelectionMode
from morrow.core.skills.identity import validate_skill_id, validate_skv_id
from morrow.core.skills.trust import SourceKind

from .errors import SkillLifecycleError, SkillLifecycleNeedsResolution


class SkillBindingLifecycleMixin:
    """YAML-authority Binding operations used by the public lifecycle service."""

    def enable(
        self,
        skill_id: str,
        *,
        scope_id: str | None = None,
        source_kind: SourceKind | None = None,
        selection_mode: SkillSelectionMode | str | None = None,
        command_id: str | None = None,
    ) -> SkillLifecycleResult:
        return self._binding_operation(
            "enable",
            skill_id,
            scope_id=scope_id,
            source_kind=source_kind,
            selection_mode=selection_mode,
            command_id=command_id,
        )

    def disable(
        self,
        skill_id: str,
        *,
        scope_id: str | None = None,
        source_kind: SourceKind | None = None,
        command_id: str | None = None,
    ) -> SkillLifecycleResult:
        return self._binding_operation(
            "disable",
            skill_id,
            scope_id=scope_id,
            source_kind=source_kind,
            command_id=command_id,
        )

    def pin(
        self,
        skill_id: str,
        version_id: str,
        *,
        scope_id: str | None = None,
        source_kind: SourceKind | None = None,
        command_id: str | None = None,
    ) -> SkillLifecycleResult:
        validate_skv_id(version_id)
        return self._binding_operation(
            "pin",
            skill_id,
            scope_id=scope_id,
            source_kind=source_kind,
            version_id=version_id,
            command_id=command_id,
        )

    def rollback(
        self,
        skill_id: str,
        *,
        scope_id: str | None = None,
        source_kind: SourceKind | None = None,
        command_id: str | None = None,
    ) -> SkillLifecycleResult:
        entry, resolved_source = self._resolve_entry(skill_id, scope_id, source_kind)
        binding = self.bindings.load(
            "global" if scope_id is None else "workspace", scope_id=scope_id
        )
        current = binding.value and next(
            (item for item in binding.value.bindings if item.skill_id == entry.definition.skill_id),
            None,
        )
        previous = self.bindings.resolve_rollback_version(
            entry,
            current_version_id=current.pinned_version_id if current else None,
            source_kind=resolved_source,
        )
        return self._binding_operation(
            "rollback",
            entry.definition.skill_id,
            scope_id=scope_id,
            source_kind=resolved_source,
            rollback_version_id=previous,
            command_id=command_id,
        )

    def _binding_operation(
        self,
        operation: str,
        skill_id: str,
        *,
        scope_id: str | None,
        source_kind: SourceKind | None,
        version_id: str | None = None,
        selection_mode: SkillSelectionMode | str | None = None,
        rollback_version_id: str | None = None,
        command_id: str | None,
    ) -> SkillLifecycleResult:
        validate_skill_id(skill_id)
        scope = "global" if scope_id is None else "workspace"
        existing_source = self._binding_source(skill_id, scope_id)
        requested_source = source_kind or existing_source
        if operation in {"enable", "pin", "rollback"}:
            entry, requested_source = self._resolve_entry(skill_id, scope_id, requested_source)
            skill_id = entry.definition.skill_id
            if operation == "pin":
                self._resolve_version(entry, version_id or "", requested_source)
            if operation == "rollback":
                self._resolve_version(entry, rollback_version_id or "", requested_source)
            binding = self._binding_for(skill_id, scope_id)
            selected_version_id = (
                version_id
                or rollback_version_id
                or (binding.pinned_version_id if binding else None)
                or (entry.newest_available.version_id if entry.newest_available else None)
            )
            self._check_dependencies(entry, requested_source, selected_version_id, scope_id)
        elif operation == "disable" and requested_source is not None:
            self._resolve_entry(skill_id, scope_id, requested_source)
        payload = {
            "scope_id": scope_id,
            "skill_id": skill_id,
            "source_kind": requested_source.value if requested_source else None,
            "version_id": version_id,
            "rollback_version_id": rollback_version_id,
            "selection_mode": str(selection_mode) if selection_mode is not None else None,
        }
        command_id, request_digest, replay = self._prepare_command(operation, payload, command_id)
        if replay is not None:
            return replay
        change = self.bindings.prepare_change(
            operation=operation,
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            version_id=version_id,
            source_kind=requested_source,
            selection_mode=selection_mode,
            rollback_version_id=rollback_version_id,
        )
        self.operations.save(
            self._operation_record(
                command_id,
                request_digest,
                operation,
                change,
                version_id=(
                    version_id
                    or rollback_version_id
                    or (change.after_binding.pinned_version_id if change.after_binding else None)
                ),
                source_kind=requested_source,
                tree_digest=None,
            )
        )
        try:
            self.bindings.apply_change(change)
            pending = self.operations.load(command_id)
            if pending is None:
                raise SkillLifecycleNeedsResolution("Skill recovery record is unavailable")
            self.operations.save(replace(pending, phase="yaml_applied"))
            result = self._finalize(
                command_id=command_id,
                request_digest=request_digest,
                operation=operation,
                skill_id=skill_id,
                scope_id=scope_id,
                version_id=(
                    version_id
                    or rollback_version_id
                    or (change.after_binding.pinned_version_id if change.after_binding else None)
                ),
                source_kind=requested_source,
                tree_digest=None,
                enabled=change.after_binding.enabled if change.after_binding else None,
                pinned_version_id=(
                    change.after_binding.pinned_version_id if change.after_binding else None
                ),
            )
            self.operations.clear(command_id)
            return result
        except Exception as exc:
            if isinstance(exc, SkillLifecycleError):
                raise
            raise SkillLifecycleNeedsResolution() from exc


__all__ = ["SkillBindingLifecycleMixin"]
