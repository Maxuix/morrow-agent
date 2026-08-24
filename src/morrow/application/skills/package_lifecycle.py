"""Local Skill package validation, installation and removal operations."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from morrow.adapters.skills.managed_store import (
    PreparedLocalSkill,
    SkillPackageError,
    prepare_local_skill,
)
from morrow.application.skills.recovery import SkillOperationRecord
from morrow.core.skills.bindings import SkillLifecycleResult, SkillValidationReport
from morrow.core.skills.drafts import SkillDraftStatus
from morrow.core.skills.identity import validate_skill_id, validate_skv_id
from morrow.core.skills.trust import SourceKind

from .errors import SkillLifecycleError, SkillLifecycleNeedsResolution


class SkillPackageLifecycleMixin:
    """Filesystem-facing lifecycle methods; no AgentRun or tool registration."""

    def validate(
        self,
        source_path: Path,
        *,
        source_kind: SourceKind = SourceKind.IMPORTED,
        scope_id: str | None = None,
    ) -> SkillValidationReport:
        try:
            prepared = prepare_local_skill(
                source_path,
                source_kind=source_kind,
                scope_id=scope_id,
            )
            return self._with_conflicts(prepared)
        except (SkillPackageError, ValueError) as exc:
            return SkillValidationReport(
                valid=False,
                source_kind=source_kind,
                errors=(str(exc)[:256],),
            )

    def preview_install(
        self,
        source_path: Path,
        *,
        source_kind: SourceKind = SourceKind.IMPORTED,
        scope_id: str | None = None,
    ) -> tuple[PreparedLocalSkill, SkillValidationReport]:
        try:
            prepared = prepare_local_skill(
                source_path,
                source_kind=source_kind,
                scope_id=scope_id,
            )
        except (SkillPackageError, ValueError) as exc:
            raise SkillLifecycleError("invalid", str(exc)[:256]) from exc
        return prepared, self._with_conflicts(prepared)

    def install(
        self,
        source_path: Path,
        *,
        scope_id: str | None = None,
        source_kind: SourceKind = SourceKind.IMPORTED,
        command_id: str | None = None,
        confirmed: bool = False,
    ) -> SkillLifecycleResult:
        prepared, report = self.preview_install(
            source_path,
            source_kind=source_kind,
            scope_id=scope_id,
        )
        return self.install_prepared(
            prepared,
            report=report,
            command_id=command_id,
            confirmed=confirmed,
        )

    def install_prepared(
        self,
        prepared: PreparedLocalSkill,
        *,
        report: SkillValidationReport | None = None,
        command_id: str | None = None,
        confirmed: bool = False,
        evidence_refs: tuple[str, ...] = (),
        controlled_approval_ref: str | None = None,
    ) -> SkillLifecycleResult:
        """Publish one already-captured package after the caller confirms its preview."""
        self._assert_generated_approval(prepared, controlled_approval_ref)
        # A preview report is advisory. Recompute it here and again while the
        # package-store lock is held immediately before publication.
        report = self._with_conflicts(prepared)
        if not report.valid or report.conflicts:
            raise SkillLifecycleError(
                "conflict", "Skill package conflicts with an existing identity"
            )
        if not confirmed:
            raise SkillLifecycleError(
                "confirmation_required", "Skill installation requires confirmation"
            )
        command_id, request_digest, replay = self._prepare_command(
            "import",
            {
                "scope_id": prepared.scope_id,
                "skill_id": prepared.skill_id,
                "source_kind": prepared.source_kind.value,
                "tree_digest": prepared.tree.tree_digest,
            },
            command_id,
        )
        if replay is not None:
            return replay
        version_id = self._allocate_version_id(prepared.tree.tree_digest)
        record = SkillOperationRecord(
            command_id=command_id,
            request_digest=request_digest,
            operation="import",
            skill_id=prepared.skill_id,
            scope="global" if prepared.scope_id is None else "workspace",
            scope_id=prepared.scope_id,
            version_id=version_id,
            source_kind=prepared.source_kind.value,
            tree_digest=prepared.tree.tree_digest,
            before_yaml_digest=None,
            after_yaml_digest=None,
            phase="prepared",
        )
        self.operations.save(record)
        try:
            self.package_store.publish(
                prepared,
                version_id=version_id,
                evidence_refs=evidence_refs,
                controlled_approval_ref=controlled_approval_ref,
                before_publish=lambda: self._assert_installable(prepared),
            )
            record = replace(record, phase="package_applied")
            self.operations.save(record)
            result = self._finalize(
                command_id=command_id,
                request_digest=request_digest,
                operation="import",
                skill_id=prepared.skill_id,
                scope_id=prepared.scope_id,
                version_id=version_id,
                source_kind=prepared.source_kind,
                tree_digest=prepared.tree.tree_digest,
                name=prepared.name,
                display_version=prepared.display_version,
                file_count=prepared.tree.file_count,
                total_bytes=prepared.tree.total_bytes,
                evidence_refs=evidence_refs,
                controlled_approval_ref=controlled_approval_ref,
            )
            self.operations.clear(command_id)
            return result
        except SkillLifecycleError as exc:
            if exc.code == "conflict" and record.phase == "prepared":
                self.operations.clear(command_id)
            raise
        except Exception as exc:
            raise SkillLifecycleNeedsResolution() from exc

    def _assert_installable(self, prepared: PreparedLocalSkill) -> None:
        report = self._with_conflicts(prepared)
        if not report.valid or report.conflicts:
            raise SkillLifecycleError(
                "conflict", "Skill package conflicts with an existing identity"
            )

    def _assert_generated_approval(
        self, prepared: PreparedLocalSkill, controlled_approval_ref: str | None
    ) -> None:
        if prepared.source_kind is not SourceKind.GENERATED:
            return
        if not controlled_approval_ref or self.journal is None or self.workspace_id is None:
            raise SkillLifecycleError(
                "approval_required", "Generated Skill installation requires an accepted Draft"
            )
        draft = self.journal.get_skill_draft(self.workspace_id, controlled_approval_ref)
        if (
            draft is None
            or draft.status is not SkillDraftStatus.VALIDATED
            or draft.skill_id != prepared.skill_id
            or draft.tree_digest != prepared.tree.tree_digest
        ):
            raise SkillLifecycleError(
                "approval_invalid", "Generated Skill approval does not match the package"
            )

    def remove(
        self,
        skill_id: str,
        *,
        scope_id: str | None = None,
        version_id: str | None = None,
        source_kind: SourceKind | None = None,
        confirmed: bool = False,
        command_id: str | None = None,
    ) -> SkillLifecycleResult:
        """Remove a scope Binding, or an explicitly confirmed managed version."""

        validate_skill_id(skill_id)
        if version_id is None:
            return self._binding_operation(
                "remove",
                skill_id,
                scope_id=scope_id,
                source_kind=source_kind,
                command_id=command_id,
            )
        if not confirmed:
            raise SkillLifecycleError(
                "confirmation_required", "Skill package removal requires confirmation"
            )
        validate_skv_id(version_id)
        entry, resolved_source = self._resolve_entry(
            skill_id, scope_id, source_kind, allow_conflicted=True
        )
        version = next(
            (item for item in entry.versions if item.version_id == version_id),
            None,
        )
        if version is None or version.source_kind is not resolved_source:
            raise SkillLifecycleError("not_found", "requested Skill version is unavailable")
        if resolved_source not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
            raise SkillLifecycleError("invalid", "builtin/user Skills cannot be removed")
        self._check_references(version_id)
        payload = {
            "scope_id": scope_id,
            "skill_id": entry.definition.skill_id,
            "version_id": version_id,
            "source_kind": resolved_source.value,
        }
        command_id, request_digest, replay = self._prepare_command("remove", payload, command_id)
        if replay is not None:
            return replay
        change = self.bindings.prepare_change(
            operation="remove",
            skill_id=entry.definition.skill_id,
            scope="global" if scope_id is None else "workspace",
            scope_id=scope_id,
            source_kind=resolved_source,
        )
        record = self._operation_record(
            command_id,
            request_digest,
            "remove",
            change,
            version_id=version_id,
            source_kind=resolved_source,
            tree_digest=version.tree_digest,
        )
        self.operations.save(record)
        try:
            self.package_store.remove_version(
                skill_id=entry.definition.skill_id,
                version_id=version_id,
                source_kind=resolved_source,
                scope_id=scope_id,
            )
            self.operations.save(replace(record, phase="package_applied"))
            self.bindings.apply_change(change)
            self.operations.save(replace(record, phase="yaml_applied"))
            result = self._finalize(
                command_id=command_id,
                request_digest=request_digest,
                operation="remove",
                skill_id=entry.definition.skill_id,
                scope_id=scope_id,
                version_id=version_id,
                source_kind=resolved_source,
                tree_digest=version.tree_digest,
                delete_version=True,
            )
            self.operations.clear(command_id)
            return result
        except Exception as exc:
            if isinstance(exc, SkillLifecycleError):
                raise
            raise SkillLifecycleNeedsResolution() from exc


__all__ = ["SkillPackageLifecycleMixin"]
