"""Candidate-to-Draft review workflow for generated Skills."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from morrow.adapters.skills.managed_store import (
    ManagedSkillPackageStore,
    SkillPackageError,
    prepare_local_skill,
)
from morrow.adapters.skills.tree import build_canonical_tree
from morrow.application.skills.lifecycle import SkillLifecycleService
from morrow.application.skills.validation import (
    SkillDraftValidationService,
    validation_digest,
)
from morrow.core.learning import (
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
)
from morrow.core.learning_payloads import SkillCandidatePayload
from morrow.core.skills.drafts import (
    DRAFT_ID_PREFIX,
    SkillDraft,
    SkillDraftDiff,
    SkillDraftStatus,
    SkillDraftValidationReport,
)
from morrow.core.skills.identity import skill_id_from_name, validate_skill_id
from morrow.core.skills.trust import SourceKind
from morrow.runtime.ids import RandomIdSource


class SkillDraftServiceError(RuntimeError):
    """A Draft command cannot safely proceed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SkillDraftView:
    draft: SkillDraft
    validation: SkillDraftValidationReport | None
    diff: SkillDraftDiff | None = None


def _now(clock) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SkillDraftService:
    """Reviewable generated package workflow; acceptance never changes a Binding."""

    def __init__(
        self,
        journal,
        package_store: ManagedSkillPackageStore,
        lifecycle: SkillLifecycleService | None = None,
        *,
        workspace_id: str,
        id_source=None,
        clock=None,
        available_tools=None,
        available_mcp_servers=None,
        platform_name: str | None = None,
    ) -> None:
        self.journal = journal
        self.package_store = package_store
        self.lifecycle = lifecycle
        self.workspace_id = workspace_id
        self.id_source = id_source or RandomIdSource()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.validation = SkillDraftValidationService(
            package_store,
            available_tools=available_tools,
            available_mcp_servers=available_mcp_servers,
            platform_name=platform_name,
        )

    def create_from_candidate(
        self, candidate_id: str, *, command_id: str | None = None
    ) -> SkillDraft:
        existing = self.journal.get_skill_draft_by_candidate(self.workspace_id, candidate_id)
        if existing is not None:
            return existing
        candidate = self.journal.get_learning_candidate(self.workspace_id, candidate_id)
        self._assert_candidate(candidate)
        payload = candidate.proposed_payload
        if not isinstance(payload, SkillCandidatePayload):
            raise SkillDraftServiceError("invalid", "Skill Candidate payload is unavailable")
        draft_id = self.id_source.new_id(DRAFT_ID_PREFIX)
        skill_id = validate_skill_id(skill_id_from_name(payload.title) or "")
        package_ref = self._package_ref(draft_id, 1)
        self._write_package(
            package_ref,
            {
                "SKILL.md": self._generated_skill_md(payload),
            },
        )
        validation_id = self.id_source.new_id("sdv")
        draft = SkillDraft(
            draft_id=draft_id,
            workspace_id=self.workspace_id,
            candidate_id=candidate.candidate_id,
            root_draft_id=draft_id,
            revision=1,
            status=SkillDraftStatus.DRAFT,
            skill_id=skill_id,
            name=payload.title,
            display_version="0.1.0",
            scope_id=self.workspace_id,
            candidate_fingerprint=candidate.fingerprint,
            evidence_refs=tuple((candidate.candidate_id, *candidate.evidence_ids)[:16]),
            package_ref=package_ref,
            tree_digest="0" * 64,
            file_count=0,
            total_bytes=0,
            validation_id=validation_id,
            created_at=_now(self.clock),
            updated_at=_now(self.clock),
        )
        prepared = self._prepared(draft)
        draft = draft.model_copy(
            update={
                "tree_digest": prepared.tree.tree_digest,
                "file_count": prepared.tree.file_count,
                "total_bytes": prepared.tree.total_bytes,
            }
        )
        report = self.validation.validate(draft, validation_id=validation_id, now=_now(self.clock))
        draft = draft.model_copy(
            update={
                "status": SkillDraftStatus.VALIDATED if report.valid else SkillDraftStatus.DRAFT
            }
        )
        try:

            def work(txn):
                replay = txn.get_skill_draft_by_candidate(self.workspace_id, candidate_id)
                if replay is not None:
                    return replay
                txn.put_skill_draft(self.workspace_id, draft)
                txn.put_skill_draft_validation(self.workspace_id, report)
                txn.record_skill_operation(
                    operation_id=command_id or self.id_source.new_id("cmd"),
                    scope="workspace",
                    scope_id=self.workspace_id,
                    skill_id=draft.skill_id,
                    version_id=None,
                    operation="draft_create",
                    disposition="applied",
                    evidence_digest=draft.candidate_fingerprint,
                    reason="accepted_skill_candidate",
                    created_at=_now(self.clock),
                )
                return draft

            return self.journal.transact(work)
        except Exception:
            self._remove_package(package_ref)
            raise

    def show(self, draft_id: str) -> SkillDraftView:
        draft = self._get(draft_id)
        report = (
            self.journal.get_skill_draft_validation(
                self.workspace_id, draft.draft_id, draft.validation_id
            )
            if draft.validation_id
            else None
        )
        return SkillDraftView(draft=draft, validation=report, diff=self.diff(draft_id))

    def get(self, draft_id: str) -> SkillDraft:
        """Return one workspace-scoped Draft for command handlers and tests."""

        return self._get(draft_id)

    def list(self, *, status: SkillDraftStatus | str | None = None, limit: int = 100):
        return self.journal.list_skill_drafts(
            self.workspace_id,
            status=status.value if isinstance(status, SkillDraftStatus) else status,
            limit=limit,
        )

    def diff(self, draft_id: str) -> SkillDraftDiff | None:
        draft = self._get(draft_id)
        if draft.parent_draft_id is None:
            return None
        parent = self._get(draft.parent_draft_id)
        current_tree = self._tree(draft)
        parent_tree = self._tree(parent)
        current = {item.relative_path: item.sha256 for item in current_tree.entries}
        previous = {item.relative_path: item.sha256 for item in parent_tree.entries}
        added = tuple(sorted(set(current) - set(previous)))
        removed = tuple(sorted(set(previous) - set(current)))
        changed = tuple(
            sorted(path for path in set(current) & set(previous) if current[path] != previous[path])
        )
        unchanged = sum(
            1 for path in set(current) & set(previous) if current[path] == previous[path]
        )
        return SkillDraftDiff(
            draft_id=draft.draft_id,
            parent_draft_id=parent.draft_id,
            added=added,
            removed=removed,
            changed=changed,
            unchanged_count=unchanged,
        )

    def edit(
        self,
        draft_id: str,
        *,
        skill_md: str | None = None,
        files: dict[str, bytes | str] | None = None,
        command_id: str | None = None,
    ) -> SkillDraft:
        current = self._get(draft_id)
        if current.status is SkillDraftStatus.REJECTED:
            raise SkillDraftServiceError("conflict", "rejected Draft cannot be edited")
        if skill_md is None and files is None:
            raise SkillDraftServiceError("invalid", "Draft edit requires package content")
        source = self._prepared(current)
        content = dict(source.contents)
        if files is not None:
            for path, value in files.items():
                content[path] = value.encode("utf-8") if isinstance(value, str) else value
        if skill_md is not None:
            content["SKILL.md"] = skill_md.encode("utf-8")
        revision = current.revision + 1
        new_id = self.id_source.new_id(DRAFT_ID_PREFIX)
        package_ref = self._package_ref(new_id, revision)
        self._write_package(package_ref, content)
        validation_id = self.id_source.new_id("sdv")
        # The tree facts are captured before creating the new immutable row.
        prepared = prepare_local_skill(
            self._package_root(package_ref),
            source_kind=SourceKind.GENERATED,
            scope_id=self.workspace_id,
        )
        draft = SkillDraft(
            draft_id=new_id,
            workspace_id=self.workspace_id,
            candidate_id=current.candidate_id,
            root_draft_id=current.root_draft_id,
            parent_draft_id=current.draft_id,
            revision=revision,
            status=SkillDraftStatus.DRAFT,
            skill_id=current.skill_id,
            name=current.name,
            display_version=current.display_version,
            scope_id=self.workspace_id,
            candidate_fingerprint=current.candidate_fingerprint,
            evidence_refs=current.evidence_refs,
            package_ref=package_ref,
            tree_digest=prepared.tree.tree_digest,
            file_count=prepared.tree.file_count,
            total_bytes=prepared.tree.total_bytes,
            validation_id=validation_id,
            created_at=_now(self.clock),
            updated_at=_now(self.clock),
        )
        report = self.validation.validate(draft, validation_id=validation_id, now=_now(self.clock))
        draft = draft.model_copy(
            update={
                "status": SkillDraftStatus.VALIDATED if report.valid else SkillDraftStatus.DRAFT
            }
        )
        try:

            def work(txn):
                txn.put_skill_draft(self.workspace_id, draft)
                txn.put_skill_draft_validation(self.workspace_id, report)
                if current.status not in {SkillDraftStatus.ACCEPTED, SkillDraftStatus.REJECTED}:
                    updated = current.model_copy(
                        update={
                            "status": SkillDraftStatus.SUPERSEDED,
                            "row_version": current.row_version + 1,
                            "updated_at": _now(self.clock),
                        }
                    )
                    txn.save_skill_draft(
                        self.workspace_id, updated, expected_row_version=current.row_version
                    )
                return draft

            return self.journal.transact(work)
        except Exception:
            self._remove_package(package_ref)
            raise

    def revalidate(self, draft_id: str) -> SkillDraftValidationReport:
        draft = self._get(draft_id)
        validation_id = self.id_source.new_id("sdv")
        report = self.validation.validate(draft, validation_id=validation_id, now=_now(self.clock))
        status = SkillDraftStatus.VALIDATED if report.valid else SkillDraftStatus.DRAFT
        updated = draft.model_copy(
            update={
                "status": status if draft.status is not SkillDraftStatus.ACCEPTED else draft.status,
                "validation_id": validation_id,
                "row_version": draft.row_version + 1,
                "updated_at": _now(self.clock),
            }
        )
        self.journal.transact(
            lambda txn: (
                txn.put_skill_draft_validation(self.workspace_id, report),
                txn.save_skill_draft(
                    self.workspace_id, updated, expected_row_version=draft.row_version
                ),
            )
        )
        return report

    def accept(self, draft_id: str, *, command_id: str | None = None):
        draft = self._get(draft_id)
        if draft.status is SkillDraftStatus.ACCEPTED and draft.accepted_version_id:
            return self._accepted_result(
                draft, command_id or draft.acceptance_command_id or "cmd_replayed"
            )
        if draft.status is not SkillDraftStatus.VALIDATED:
            raise SkillDraftServiceError("conflict", "only a valid Draft can be accepted")
        report = self.journal.get_skill_draft_validation(
            self.workspace_id, draft.draft_id, draft.validation_id or ""
        )
        if report is None or not report.valid or validation_digest(report) != report.report_digest:
            raise SkillDraftServiceError("conflict", "Draft validation is missing or stale")
        if self.lifecycle is None:
            raise SkillDraftServiceError("unavailable", "Skill lifecycle service is unavailable")
        try:
            prepared = self._prepared(draft)
            if prepared.tree.tree_digest != draft.tree_digest:
                raise SkillDraftServiceError("conflict", "Draft package drifted before acceptance")
            result = self.lifecycle.install_prepared(
                prepared,
                report=prepared.report,
                command_id=command_id,
                confirmed=True,
                evidence_refs=(draft.candidate_id, *draft.evidence_refs)[:8],
                controlled_approval_ref=draft.draft_id,
            )
        except SkillDraftServiceError:
            raise
        except (SkillPackageError, ValueError) as exc:
            raise SkillDraftServiceError("unavailable", "Draft package is unavailable") from exc
        accepted = draft.model_copy(
            update={
                "status": SkillDraftStatus.ACCEPTED,
                "accepted_version_id": result.version_id,
                "acceptance_command_id": result.command_id,
                "row_version": draft.row_version + 1,
                "updated_at": _now(self.clock),
            }
        )
        try:
            self.journal.transact(
                lambda txn: self._persist_acceptance(txn, accepted, draft, result.version_id)
            )
        except Exception as exc:
            raise SkillDraftServiceError(
                "needs_resolution", "Draft acceptance needs recovery"
            ) from exc
        return result

    def reject(self, draft_id: str, *, reason: str) -> SkillDraft:
        draft = self._get(draft_id)
        if draft.status is SkillDraftStatus.ACCEPTED:
            raise SkillDraftServiceError("conflict", "accepted Draft cannot be rejected")
        updated = draft.model_copy(
            update={
                "status": SkillDraftStatus.REJECTED,
                "rejection_reason": reason,
                "row_version": draft.row_version + 1,
                "updated_at": _now(self.clock),
            }
        )
        return self.journal.transact(
            lambda txn: txn.save_skill_draft(
                self.workspace_id, updated, expected_row_version=draft.row_version
            )
        )

    def _persist_acceptance(self, txn, accepted: SkillDraft, previous: SkillDraft, version_id):
        updated = txn.save_skill_draft(
            self.workspace_id, accepted, expected_row_version=previous.row_version
        )
        txn.record_skill_operation(
            operation_id=f"{accepted.acceptance_command_id}:draft_accept",
            scope="workspace",
            scope_id=self.workspace_id,
            skill_id=accepted.skill_id,
            version_id=version_id,
            operation="draft_accept",
            disposition="applied",
            evidence_digest=accepted.tree_digest,
            reason="reviewed_draft_accepted_binding_unchanged",
            created_at=_now(self.clock),
        )
        return updated

    def _accepted_result(self, draft: SkillDraft, command_id: str):
        from morrow.core.skills.bindings import SkillLifecycleResult

        return SkillLifecycleResult(
            operation="draft_accept",
            command_id=command_id,
            skill_id=draft.skill_id,
            scope="workspace",
            scope_id=self.workspace_id,
            version_id=draft.accepted_version_id,
            status="replayed",
            message="replayed accepted Draft; Binding remains unchanged",
        )

    def _assert_candidate(self, candidate: LearningCandidate | None) -> None:
        if candidate is None:
            raise SkillDraftServiceError("not_found", "Skill Candidate is missing")
        if candidate.workspace_id != self.workspace_id:
            raise SkillDraftServiceError(
                "cross_workspace", "Skill Candidate is outside the workspace"
            )
        if candidate.candidate_type is not LearningCandidateType.SKILL_CANDIDATE:
            raise SkillDraftServiceError("invalid", "candidate is not a Skill Candidate")
        if candidate.status not in {
            LearningCandidateStatus.ACCEPTED,
            LearningCandidateStatus.EDITED_AND_ACCEPTED,
        }:
            raise SkillDraftServiceError(
                "conflict", "only an accepted Skill Candidate can create a Draft"
            )

    def _get(self, draft_id: str) -> SkillDraft:
        draft = self.journal.get_skill_draft(self.workspace_id, draft_id)
        if draft is None:
            raise SkillDraftServiceError("not_found", "Skill Draft is missing")
        return draft

    def _package_ref(self, draft_id: str, revision: int) -> str:
        return f"skill-drafts/{self.workspace_id}/{draft_id}/rev-{revision}"

    def _package_root(self, package_ref: str) -> Path:
        return (self.package_store.data_root / package_ref).resolve(strict=False)

    def _prepared(self, draft: SkillDraft):
        try:
            return prepare_local_skill(
                self.validation.package_root(draft),
                source_kind=SourceKind.GENERATED,
                scope_id=self.workspace_id,
            )
        except (OSError, SkillPackageError, ValueError) as exc:
            raise SkillDraftServiceError("unavailable", "Draft package is unavailable") from exc

    def _tree(self, draft: SkillDraft):
        prepared = self._prepared(draft)
        if prepared.tree.tree_digest != draft.tree_digest:
            raise SkillDraftServiceError("needs_recovery", "Draft package tree drifted")
        return prepared.tree

    def _write_package(self, package_ref: str, files: dict[str, bytes | str]) -> None:
        target = self._package_root(package_ref)
        parent = target.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=parent))
        try:
            for relative, value in files.items():
                normalized = relative.replace("\\", "/")
                if (
                    not normalized
                    or normalized.startswith("/")
                    or "\x00" in normalized
                    or any(part in {"", ".", ".."} for part in normalized.split("/"))
                ):
                    raise SkillDraftServiceError("invalid", "Draft package path is invalid")
                raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
                destination = temporary / normalized
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                try:
                    os.write(fd, raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            build_canonical_tree(temporary)
            os.replace(temporary, target)
            temporary = Path()
        finally:
            if temporary != Path() and temporary.exists():
                for child in sorted(temporary.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                temporary.rmdir()

    def _remove_package(self, package_ref: str) -> None:
        target = self._package_root(package_ref)
        managed = (self.package_store.data_root / "skill-drafts").resolve(strict=False)
        try:
            target.relative_to(managed)
        except ValueError:
            return
        if not target.exists() or target.is_symlink():
            return
        for child in sorted(target.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        target.rmdir()

    @staticmethod
    def _generated_skill_md(payload: SkillCandidatePayload) -> str:
        tools = "\n".join(f"  - {item}" for item in payload.tool_names)
        steps = "\n".join(
            f"{index}. {item}" for index, item in enumerate(payload.observed_steps, 1)
        )
        title = payload.title.replace("'", "''")
        problem_pattern = payload.problem_pattern.replace("'", "''")
        return (
            "---\n"
            f"name: '{title}'\n"
            f"description: '{problem_pattern}'\n"
            "version: 0.1.0\n" + (f"morrow.required_tools:\n{tools}\n" if tools else "") + "---\n\n"
            f"# {payload.title}\n\n"
            "## Problem pattern\n\n"
            f"{payload.problem_pattern}\n\n"
            "## Observed steps\n\n"
            f"{steps}\n"
        )


__all__ = ["SkillDraftService", "SkillDraftServiceError", "SkillDraftView"]
