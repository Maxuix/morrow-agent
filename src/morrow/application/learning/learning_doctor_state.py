"""Read-only promotion and Project Knowledge invariants for the Learning doctor."""

from __future__ import annotations

from collections.abc import Callable

from morrow.core.configuration_promotion import (
    ConfigurationActivationStatus,
    PromotionOperationState,
)
from morrow.core.doctor import DoctorIssue, DoctorSeverity
from morrow.core.learning import LearningCandidateStatus
from morrow.core.learning_memory import ProjectKnowledgeStatus

_PAGE_LIMIT = 500


def inspect_promotions(
    workspace_id,
    operations,
    operations_by_id,
    candidates_by_id,
    decisions_by_id,
    activations,
    issues: list[DoctorIssue],
    issue_factory: Callable[..., DoctorIssue],
) -> None:
    for operation in operations:
        candidate = candidates_by_id.get(operation.candidate_id)
        if candidate is None or candidate.workspace_id != workspace_id:
            issues.append(
                issue_factory(
                    "learning_promotion_candidate",
                    DoctorSeverity.ERROR,
                    "configuration Promotion references a missing or foreign Candidate",
                )
            )
            continue
        expected_target = (
            "preferences" if candidate.candidate_type.value == "preference" else "profile"
        )
        if (
            candidate.candidate_type.value not in {"preference", "profile"}
            or operation.target != expected_target
        ):
            issues.append(
                issue_factory(
                    "learning_promotion_target",
                    DoctorSeverity.ERROR,
                    "configuration Promotion target does not match Candidate type",
                )
            )
        if (
            operation.state
            in {
                PromotionOperationState.PREPARED,
                PromotionOperationState.NEEDS_RESOLUTION,
            }
            and candidate.status is not LearningCandidateStatus.PROMOTING
        ):
            issues.append(
                issue_factory(
                    "learning_promotion_recovery",
                    DoctorSeverity.WARNING,
                    "unfinalized configuration Promotion is not linked to a promoting Candidate",
                )
            )
        if (
            operation.state is PromotionOperationState.FINALIZED
            and operation.applied_revision is None
        ):
            issues.append(
                issue_factory(
                    "learning_promotion_state",
                    DoctorSeverity.ERROR,
                    "finalized configuration Promotion has no applied revision",
                )
            )
    seen_activation_ids: set[str] = set()
    for activation in activations:
        if activation.activation_id in seen_activation_ids:
            issues.append(
                issue_factory(
                    "learning_activation_duplicate",
                    DoctorSeverity.ERROR,
                    "configuration Activation ID is duplicated",
                )
            )
        seen_activation_ids.add(activation.activation_id)
        candidate = candidates_by_id.get(activation.candidate_id)
        operation = operations_by_id.get(activation.operation_id)
        decision = decisions_by_id.get(activation.decision_id)
        if (
            candidate is None
            or operation is None
            or decision is None
            or candidate.workspace_id != workspace_id
            or operation.workspace_id != workspace_id
            or decision.workspace_id != workspace_id
            or decision.candidate_id != candidate.candidate_id
            or operation.candidate_id != candidate.candidate_id
            or activation.target != operation.target
            or activation.path != operation.path
        ):
            issues.append(
                issue_factory(
                    "learning_activation_link",
                    DoctorSeverity.ERROR,
                    "configuration Activation links are inconsistent",
                )
            )
        if activation.status is ConfigurationActivationStatus.ACTIVE and (
            operation is None or operation.state is not PromotionOperationState.FINALIZED
        ):
            issues.append(
                issue_factory(
                    "learning_activation_state",
                    DoctorSeverity.ERROR,
                    "active configuration Activation has no finalized Promotion",
                )
            )


def inspect_knowledge(
    journal,
    workspace_id,
    candidates_by_id,
    decisions_by_id,
    counts,
    issues: list[DoctorIssue],
    issue_factory: Callable[..., DoctorIssue],
) -> None:
    heads = journal.list_project_knowledge_heads(
        workspace_id,
        include_deleted=True,
        limit=_PAGE_LIMIT,
    )
    counts["learning_knowledge_heads"] = len(heads)
    active_heads = 0
    for head in heads:
        if head.status is ProjectKnowledgeStatus.ACTIVE:
            active_heads += 1
        if head.current_revision_id is None:
            if head.status is ProjectKnowledgeStatus.ACTIVE:
                issues.append(
                    issue_factory(
                        "learning_knowledge_current_revision",
                        DoctorSeverity.ERROR,
                        "active Project Knowledge head has no current revision",
                    )
                )
            continue
        revision = journal.get_project_knowledge_revision(workspace_id, head.current_revision_id)
        if revision is None or revision.knowledge_id != head.knowledge_id:
            issues.append(
                issue_factory(
                    "learning_knowledge_current_revision",
                    DoctorSeverity.ERROR,
                    "Project Knowledge head points to an invalid current revision",
                )
            )
            continue
        if (
            revision.source_candidate_id is not None
            and revision.source_candidate_id not in candidates_by_id
        ):
            issues.append(
                issue_factory(
                    "learning_knowledge_provenance",
                    DoctorSeverity.ERROR,
                    "Project Knowledge revision points to a missing Candidate",
                )
            )
        if (
            revision.source_decision_id is not None
            and revision.source_decision_id not in decisions_by_id
        ):
            issues.append(
                issue_factory(
                    "learning_knowledge_provenance",
                    DoctorSeverity.ERROR,
                    "Project Knowledge revision points to a missing decision",
                )
            )
        links = journal.list_project_knowledge_evidence(
            workspace_id, revision.knowledge_revision_id
        )
        counts["learning_knowledge_evidence_links"] += len(links)
        for link in links:
            evidence = journal.get_learning_evidence(workspace_id, link.evidence_id)
            if evidence is None or evidence.workspace_id != workspace_id:
                issues.append(
                    issue_factory(
                        "learning_knowledge_evidence",
                        DoctorSeverity.ERROR,
                        "Project Knowledge revision points to missing Evidence",
                    )
                )
    state = journal.get_memory_workspace_state(workspace_id)
    counts["learning_memory_workspace_state"] = 1 if state is not None else 0
    if active_heads and state is None:
        issues.append(
            issue_factory(
                "learning_memory_revision",
                DoctorSeverity.ERROR,
                "active Project Knowledge has no Memory workspace revision",
            )
        )


__all__ = ["inspect_knowledge", "inspect_promotions"]
