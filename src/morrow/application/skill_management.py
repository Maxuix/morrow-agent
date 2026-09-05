"""Skill management projections; package bytes stay behind the Skill services."""

from difflib import unified_diff

from morrow.application.context_management import management_wire
from morrow.application.skills.drafts import SkillDraftServiceError
from morrow.core.domain import redact_workflow_text


class SkillManagementQueries:
    def __init__(self, services, workspace_id: str) -> None:
        self.services = services
        self.workspace_id = workspace_id

    def scope_id(self, scope: str):
        if scope not in {"global", "workspace"}:
            raise ValueError("Skill scope is invalid")
        return self.workspace_id if scope == "workspace" else None

    def binding_digest(self, scope: str):
        load = self.services.bindings.load(scope, scope_id=self.scope_id(scope))
        if load.value is None or load.status.value != "ok":
            raise ValueError("Skill bindings are unavailable")
        return load.digest

    def catalog(self, scope: str):
        scope_id = self.scope_id(scope)
        rows = []
        for status in self.services.queries.list(scope_id=scope_id, limit=256):
            versions = []
            for version in status.versions:
                details = {
                    "version": version,
                    "summary": None,
                    "manifest": None,
                    "scripts": [],
                    "inspection_error": None,
                }
                try:
                    package = self.services.packages.read_frozen_package(
                        skill_id=status.skill_id,
                        version_id=version.version_id,
                        source_kind=version.source_kind,
                        scope_id=scope_id,
                        expected_tree_digest=version.tree_digest,
                    )
                    details.update(
                        {
                            "summary": package.manifest.description,
                            "manifest": package.manifest,
                            "scripts": [
                                entry.relative_path
                                for entry in package.tree.entries
                                if entry.relative_path.startswith("scripts/")
                            ],
                        }
                    )
                except (ValueError, OSError):
                    details["inspection_error"] = "package_unavailable"
                versions.append(details)
            rows.append(
                {
                    "status": status,
                    "enabled": status.enabled,
                    "versions": versions,
                    "usage": self.services.usage.list(skill_id=status.skill_id, limit=100)
                    if self.services.usage
                    else (),
                }
            )
        return management_wire(
            {"skills": rows, "scope": scope, "binding_digest": self.binding_digest(scope)}
        )

    def drafts(self, page=0):
        service = self.services.drafts
        rows = []
        drafts = service.list(limit=51, offset=page * 50)
        for draft in drafts[:50]:
            details = {
                "validation": None,
                "diff": None,
                "skill_md": None,
                "text_diff": None,
                "editable": False,
                "inspection_error": None,
            }
            try:
                view = service.show(draft.draft_id)
                original = service.read_skill_md(draft.draft_id)
                document = redact_workflow_text(original)[0]
                previous = (
                    service.read_skill_md(draft.parent_draft_id) if draft.parent_draft_id else ""
                )
                details.update(
                    validation=view.validation,
                    diff=view.diff,
                    skill_md=document,
                    editable=document == original,
                    text_diff="".join(
                        unified_diff(
                            redact_workflow_text(previous)[0].splitlines(keepends=True),
                            document.splitlines(keepends=True),
                            fromfile="previous/SKILL.md",
                            tofile="current/SKILL.md",
                        )
                    )[:131072],
                )
            except SkillDraftServiceError:
                details["inspection_error"] = "draft_unavailable"
            rows.append(
                {
                    "draft": draft.model_dump(mode="json", exclude={"package_ref"}),
                    **details,
                }
            )
        return management_wire(
            {
                "drafts": rows,
                "limit": 50,
                "next_cursor": str((page + 1) * 50) if len(drafts) > 50 else None,
            }
        )
