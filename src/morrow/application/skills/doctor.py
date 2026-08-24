"""Bounded Stage 6 Skill integrity checks for OperationalDoctor."""

from __future__ import annotations

from pathlib import Path

from morrow.adapters.skills.envelope import read_envelope, verify_envelope_against_tree
from morrow.adapters.skills.tree import PackageTreeError, build_canonical_tree
from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus, ExtensionYamlStore
from morrow.core.doctor import DoctorSeverity
from morrow.core.domain import sha256_digest
from morrow.core.skills.trust import SourceKind


def inspect_skills(
    journal,
    data_root: Path,
    workspace_id: str,
    counts,
    issues,
    *,
    issue_factory,
    schema_version: int | None = None,
):
    """Inspect current-workspace Skill evidence without returning package content."""

    versions = tuple(
        item
        for item in journal.list_skill_versions()
        if item.scope_id is None or item.scope_id == workspace_id
    )
    by_id = {item.version_id: item for item in versions}
    by_skill_scope = {(item.scope_id, item.skill_id): item for item in versions}
    counts["skill_versions"] = len(versions)
    counts["skill_definitions"] = sum(
        item.scope_id is None or item.scope_id == workspace_id
        for item in journal.list_skill_definitions()
    )
    for version in versions:
        if version.source_kind not in {SourceKind.IMPORTED, SourceKind.GENERATED}:
            continue
        if not _verify_managed_package(data_root, version):
            issues.append(
                issue_factory(
                    "skill_package_drift",
                    DoctorSeverity.ERROR,
                    "managed Skill package does not match catalog evidence",
                )
            )

    documents = (
        (None, ExtensionYamlStore(data_root, create=False).load_global()),
        (workspace_id, ExtensionYamlStore(data_root, create=False).load_workspace(workspace_id)),
    )
    counts["skill_bindings"] = 0
    for _scope_id, loaded in documents:
        if loaded.status is not ExtensionYamlLoadStatus.OK or loaded.value is None:
            if loaded.presence != "missing":
                issues.append(
                    issue_factory(
                        "skill_binding_yaml",
                        DoctorSeverity.ERROR,
                        "Skill binding YAML is unavailable",
                    )
                )
            continue
        for binding in loaded.value.bindings:
            counts["skill_bindings"] += 1
            expected_scope = binding.scope_id
            if binding.pinned_version_id:
                version = by_id.get(binding.pinned_version_id)
                if (
                    version is None
                    or version.skill_id != binding.skill_id
                    or version.scope_id != expected_scope
                ):
                    issues.append(
                        issue_factory(
                            "skill_binding_version",
                            DoctorSeverity.ERROR,
                            "Skill binding points to an incompatible version",
                        )
                    )
            elif binding.enabled and (expected_scope, binding.skill_id) not in by_skill_scope:
                issues.append(
                    issue_factory(
                        "skill_binding_missing",
                        DoctorSeverity.ERROR,
                        "enabled Skill binding has no catalog version",
                    )
                )

    counts["skill_selections"] = 0
    counts["skill_contexts"] = 0
    counts["skill_drafts"] = 0
    counts["skill_usages"] = 0
    for session in journal.list_sessions(workspace_id):
        for run in journal.list_session_agent_runs(workspace_id, session.session_id):
            try:
                selections = journal.list_skill_selections(workspace_id, run.agent_run_id)
                contexts = journal.list_skill_contexts(workspace_id, run.agent_run_id)
            except Exception:
                issues.append(
                    issue_factory(
                        "skill_run_evidence",
                        DoctorSeverity.ERROR,
                        "Skill run evidence could not be decoded",
                    )
                )
                continue
            counts["skill_selections"] += len(selections)
            counts["skill_contexts"] += len(contexts)
            selected_ids = {item.selection_id for item in selections}
            for selection in selections:
                version = by_id.get(selection.version_id)
                if (
                    version is None
                    or version.skill_id != selection.skill_id
                    or version.tree_digest != selection.tree_digest
                    or selection.scope_id != version.scope_id
                ):
                    issues.append(
                        issue_factory(
                            "skill_selection_evidence",
                            DoctorSeverity.ERROR,
                            "Skill selection does not match frozen version evidence",
                        )
                    )
            for context in contexts:
                version = by_id.get(context.version_id)
                if (
                    version is None
                    or context.selection_id not in selected_ids
                    or version.tree_digest != context.tree_digest
                    or sha256_digest(context.content.encode("utf-8")) != context.context_digest
                ):
                    issues.append(
                        issue_factory(
                            "skill_context_evidence",
                            DoctorSeverity.ERROR,
                            "Skill context digest or selection link is invalid",
                        )
                    )
            if schema_version is not None and schema_version < 15:
                continue
            for usage in journal.list_skill_usages(workspace_id, agent_run_id=run.agent_run_id):
                counts["skill_usages"] += 1
                version = by_id.get(usage.version_id)
                if version is None or version.skill_id != usage.skill_id:
                    issues.append(
                        issue_factory(
                            "skill_usage_version",
                            DoctorSeverity.ERROR,
                            "Skill Usage points to a missing version",
                        )
                    )
                if usage.selection_id is not None and usage.selection_id not in selected_ids:
                    issues.append(
                        issue_factory(
                            "skill_usage_selection",
                            DoctorSeverity.ERROR,
                            "Skill Usage points to a missing run selection",
                        )
                    )
                for artifact in usage.artifact_refs:
                    if journal.get_artifact(workspace_id, artifact.artifact_id) is None:
                        issues.append(
                            issue_factory(
                                "skill_usage_artifact",
                                DoctorSeverity.ERROR,
                                "Skill Usage points to a missing Artifact",
                            )
                        )

    if schema_version is not None and schema_version < 15:
        return
    for draft in journal.list_skill_drafts(workspace_id, limit=500):
        counts["skill_drafts"] += 1
        if draft.accepted_version_id is not None:
            version = by_id.get(draft.accepted_version_id)
            if (
                version is None
                or version.skill_id != draft.skill_id
                or version.scope_id != draft.scope_id
            ):
                issues.append(
                    issue_factory(
                        "skill_draft_version",
                        DoctorSeverity.ERROR,
                        "accepted Skill Draft points to a missing version",
                    )
                )
        validations = journal.list_skill_draft_validations(workspace_id, draft.draft_id, limit=128)
        if draft.validation_id is not None and not any(
            item.validation_id == draft.validation_id for item in validations
        ):
            issues.append(
                issue_factory(
                    "skill_draft_validation",
                    DoctorSeverity.ERROR,
                    "Skill Draft validation evidence is missing",
                )
            )


def _verify_managed_package(data_root: Path, version) -> bool:
    base = (
        Path("skills")
        if version.scope_id is None
        else Path("workspaces") / version.scope_id / "skills"
    )
    root = data_root / base / version.source_kind.value / version.skill_id / version.version_id
    try:
        tree = build_canonical_tree(root / "package")
        envelope = read_envelope(root)
        verify_envelope_against_tree(envelope, tree)
        return (
            envelope.get("version_id") == version.version_id
            and envelope.get("skill_id") == version.skill_id
            and envelope.get("tree_digest") == version.tree_digest
        )
    except (OSError, ValueError, PackageTreeError):
        return False


__all__ = ["inspect_skills"]
