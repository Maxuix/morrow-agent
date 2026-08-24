"""Deterministic validation for generated Skill Draft package roots."""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

from morrow.adapters.skills.managed_store import (
    ManagedSkillPackageStore,
    SkillPackageError,
    prepare_local_skill,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning_safety import LearningSafetyCode, scan_learning_text
from morrow.core.models import utc_now
from morrow.core.skills.drafts import (
    SkillDraft,
    SkillDraftValidationReport,
    SkillFindingSeverity,
    SkillValidationFinding,
)
from morrow.core.skills.trust import SourceKind

_DECLARATION = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_ERROR_CODES = {
    LearningSafetyCode.SECRET_MATERIAL.value,
    LearningSafetyCode.PROHIBITED_PERSONAL_DATA.value,
    LearningSafetyCode.HIDDEN_UNICODE_CONTROL.value,
}


class SkillDraftValidationError(ValueError):
    """A Draft package cannot be safely inspected."""


def validation_digest(report: SkillDraftValidationReport) -> str:
    payload = report.model_dump(mode="json")
    payload.pop("report_digest", None)
    return sha256_digest(canonical_json_bytes(payload))


class SkillDraftValidationService:
    """Read-only package validator; it never executes scripts or changes Bindings."""

    def __init__(
        self,
        package_store: ManagedSkillPackageStore,
        *,
        available_tools=None,
        available_mcp_servers=None,
        platform_name: str | None = None,
    ) -> None:
        self.package_store = package_store
        self.available_tools = None if available_tools is None else frozenset(available_tools)
        self.available_mcp_servers = (
            None if available_mcp_servers is None else frozenset(available_mcp_servers)
        )
        self.platform_name = (platform_name or sys.platform).casefold()

    def package_root(self, draft: SkillDraft) -> Path:
        ref = draft.package_ref.replace("\\", "/")
        if ref.startswith("/") or any(part in {"", ".", ".."} for part in ref.split("/")):
            raise SkillDraftValidationError("Skill Draft package reference is invalid")
        root = (self.package_store.data_root / ref).resolve(strict=False)
        managed = (self.package_store.data_root / "skill-drafts").resolve(strict=False)
        try:
            root.relative_to(managed)
        except ValueError as exc:
            raise SkillDraftValidationError(
                "Skill Draft package reference is outside its root"
            ) from exc
        return root

    def validate(
        self, draft: SkillDraft, *, validation_id: str, now=None
    ) -> SkillDraftValidationReport:
        findings: list[SkillValidationFinding] = []
        tree_digest = draft.tree_digest
        file_count = draft.file_count
        total_bytes = draft.total_bytes
        requested_permissions: tuple[str, ...] = ()
        dependencies: tuple[str, ...] = ()
        platforms: tuple[str, ...] = ()
        scripts: tuple[str, ...] = ()
        samples: tuple[str, ...] = ()

        try:
            prepared = prepare_local_skill(
                self.package_root(draft),
                source_kind=SourceKind.GENERATED,
                scope_id=draft.workspace_id,
            )
        except (OSError, SkillPackageError, ValueError):
            findings.append(self._finding("package_unavailable", SkillFindingSeverity.ERROR))
            return self._report(
                draft,
                validation_id=validation_id,
                findings=findings,
                requested_permissions=requested_permissions,
                dependencies=dependencies,
                platforms=platforms,
                scripts=scripts,
                samples=samples,
                tree_digest=tree_digest,
                file_count=file_count,
                total_bytes=total_bytes,
                now=now,
            )

        tree_digest = prepared.tree.tree_digest
        file_count = prepared.tree.file_count
        total_bytes = prepared.tree.total_bytes
        if tree_digest != draft.tree_digest:
            findings.append(self._finding("tree_drift", SkillFindingSeverity.ERROR))
        if prepared.skill_id != draft.skill_id:
            findings.append(self._finding("skill_identity_changed", SkillFindingSeverity.ERROR))

        manifest = prepared.manifest
        requested_permissions = tuple(manifest.requested_permissions[:16])
        dependencies = tuple(
            [f"tool:{item}" for item in manifest.required_tools]
            + [f"mcp:{item}" for item in manifest.required_mcp_servers]
        )[:32]
        platforms = tuple(manifest.platform_constraints[:16])
        scripts = tuple(
            entry.relative_path
            for entry in prepared.tree.entries
            if entry.relative_path.startswith("scripts/")
        )[:64]
        samples = tuple(
            entry.relative_path
            for entry in prepared.tree.entries
            if entry.relative_path.startswith(("fixtures/", "samples/"))
        )[:32]

        for permission in requested_permissions:
            if _DECLARATION.fullmatch(permission) is None:
                findings.append(
                    self._finding("permission_declaration_invalid", SkillFindingSeverity.ERROR)
                )
        if self.available_tools is not None:
            for tool in manifest.required_tools:
                if tool not in self.available_tools:
                    findings.append(
                        self._finding("tool_dependency_unavailable", SkillFindingSeverity.ERROR)
                    )
        if self.available_mcp_servers is not None:
            for server in manifest.required_mcp_servers:
                if server not in self.available_mcp_servers:
                    findings.append(
                        self._finding("mcp_dependency_unavailable", SkillFindingSeverity.ERROR)
                    )
        if platforms and not self._platform_matches(platforms):
            findings.append(self._finding("platform_unavailable", SkillFindingSeverity.ERROR))

        for path, raw in prepared.contents.items():
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                findings.append(
                    self._finding("file_encoding_invalid", SkillFindingSeverity.ERROR, path)
                )
                continue
            for safety in scan_learning_text(text):
                severity = (
                    SkillFindingSeverity.ERROR
                    if safety.code.value in _ERROR_CODES
                    else SkillFindingSeverity.WARNING
                )
                findings.append(self._finding(safety.code.value, severity, path))

        valid = not any(item.severity is SkillFindingSeverity.ERROR for item in findings)
        return self._report(
            draft,
            validation_id=validation_id,
            findings=findings,
            requested_permissions=requested_permissions,
            dependencies=dependencies,
            platforms=platforms,
            scripts=scripts,
            samples=samples,
            tree_digest=tree_digest,
            file_count=file_count,
            total_bytes=total_bytes,
            valid=valid,
            now=now,
        )

    @staticmethod
    def _finding(
        code: str, severity: SkillFindingSeverity, path: str | None = None
    ) -> SkillValidationFinding:
        return SkillValidationFinding(code=code, severity=severity, path=path)

    def _platform_matches(self, constraints: tuple[str, ...]) -> bool:
        values = {self.platform_name, platform.system().casefold()}
        if self.platform_name == "darwin":
            values.update({"mac", "macos", "osx"})
        if self.platform_name.startswith("linux"):
            values.add("linux")
        if self.platform_name.startswith("win"):
            values.update({"windows", "win"})
        return any(
            item.casefold() in {"any", "all", "*"} or item.casefold() in values
            for item in constraints
        )

    @staticmethod
    def _report(
        draft: SkillDraft,
        *,
        validation_id: str,
        findings: list[SkillValidationFinding],
        requested_permissions: tuple[str, ...],
        dependencies: tuple[str, ...],
        platforms: tuple[str, ...],
        scripts: tuple[str, ...],
        samples: tuple[str, ...],
        tree_digest: str,
        file_count: int,
        total_bytes: int,
        valid: bool | None = None,
        now=None,
    ) -> SkillDraftValidationReport:
        report = SkillDraftValidationReport(
            validation_id=validation_id,
            draft_id=draft.draft_id,
            revision=draft.revision,
            valid=(
                not any(item.severity is SkillFindingSeverity.ERROR for item in findings)
                if valid is None
                else valid
            ),
            findings=tuple(findings[:64]),
            requested_permissions=requested_permissions,
            dependencies=dependencies,
            platforms=platforms,
            scripts=scripts,
            sample_fixtures=samples,
            tree_digest=tree_digest,
            file_count=file_count,
            total_bytes=total_bytes,
            report_digest="0" * 64,
            created_at=now or utc_now(),
        )
        return report.model_copy(update={"report_digest": validation_digest(report)})


__all__ = ["SkillDraftValidationError", "SkillDraftValidationService", "validation_digest"]
