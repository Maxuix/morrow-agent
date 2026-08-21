"""Read-only Candidate and suppression invariants for the Learning doctor."""

from __future__ import annotations

from collections.abc import Callable

from morrow.core.doctor import DoctorIssue, DoctorSeverity
from morrow.core.learning import LearningCandidateStatus
from morrow.core.learning_memory import LearningCandidateDecisionKind


def inspect_candidates(
    journal,
    workspace_id,
    candidates,
    candidates_by_id,
    decisions_by_candidate,
    decisions_by_id,
    reviews_by_id,
    counts,
    issues: list[DoctorIssue],
    issue_factory: Callable[..., DoctorIssue],
) -> None:
    for decision in decisions_by_id.values():
        candidate = candidates_by_id.get(decision.candidate_id)
        if candidate is None or decision.workspace_id != workspace_id:
            issues.append(
                issue_factory(
                    "learning_decision_candidate",
                    DoctorSeverity.ERROR,
                    f"Learning Candidate decision {decision.decision_id} points to a missing "
                    "or foreign Candidate",
                )
            )
            continue
        if decision.original_proposal_digest != candidate.fingerprint:
            issues.append(
                issue_factory(
                    "learning_decision_digest",
                    DoctorSeverity.ERROR,
                    f"Learning Candidate decision {decision.decision_id} does not match the "
                    f"original proposal for Candidate {candidate.candidate_id}",
                )
            )
        decisions_by_candidate[candidate.candidate_id].append(decision)

    required_decisions = {
        LearningCandidateStatus.ACCEPTED: {
            LearningCandidateDecisionKind.ACCEPT,
            LearningCandidateDecisionKind.EDIT_AND_ACCEPT,
        },
        LearningCandidateStatus.EDITED_AND_ACCEPTED: {
            LearningCandidateDecisionKind.EDIT_AND_ACCEPT,
        },
        LearningCandidateStatus.REJECTED: {
            LearningCandidateDecisionKind.REJECT,
            LearningCandidateDecisionKind.REJECT_AND_SUPPRESS,
        },
        LearningCandidateStatus.EXPIRED: {LearningCandidateDecisionKind.EXPIRE},
        LearningCandidateStatus.SUPERSEDED: {LearningCandidateDecisionKind.SUPERSEDE},
    }
    for candidate in candidates:
        review = reviews_by_id.get(candidate.origin_review_id)
        if review is None or candidate.workspace_id != workspace_id:
            issues.append(
                issue_factory(
                    "learning_candidate_review",
                    DoctorSeverity.ERROR,
                    f"Learning Candidate {candidate.candidate_id} points to a missing or foreign "
                    "Review",
                )
            )
        linked = journal.list_learning_candidate_evidence(workspace_id, candidate.candidate_id)
        counts["learning_candidate_evidence_links"] += len(linked)
        if {item.evidence_id for item in linked} != set(candidate.evidence_ids):
            issues.append(
                issue_factory(
                    "learning_candidate_evidence",
                    DoctorSeverity.ERROR,
                    f"Learning Candidate {candidate.candidate_id} Evidence links do not match "
                    "the Candidate",
                )
            )
        if any(item.workspace_id != workspace_id for item in linked):
            issues.append(
                issue_factory(
                    "learning_candidate_workspace",
                    DoctorSeverity.ERROR,
                    f"Learning Candidate {candidate.candidate_id} links Evidence from another "
                    "workspace",
                )
            )
        for reference_id in (*candidate.conflict_refs, candidate.duplicate_of_id):
            if reference_id is not None and reference_id not in candidates_by_id:
                issues.append(
                    issue_factory(
                        "learning_candidate_reference",
                        DoctorSeverity.ERROR,
                        f"Learning Candidate {candidate.candidate_id} references missing "
                        f"Candidate {reference_id}",
                    )
                )
        decisions_for_candidate = decisions_by_candidate.get(candidate.candidate_id, ())
        expected = required_decisions.get(candidate.status)
        if expected is not None and not any(
            item.kind in expected for item in decisions_for_candidate
        ):
            issues.append(
                issue_factory(
                    "learning_candidate_decision_status",
                    DoctorSeverity.ERROR,
                    f"Learning Candidate {candidate.candidate_id} status has no matching decision",
                )
            )
        if candidate.status is LearningCandidateStatus.PROPOSED and decisions_for_candidate:
            issues.append(
                issue_factory(
                    "learning_candidate_decision_status",
                    DoctorSeverity.WARNING,
                    f"proposed Learning Candidate {candidate.candidate_id} already has a terminal "
                    "decision",
                )
            )


def inspect_suppressions(
    workspace_id,
    suppressions,
    candidates_by_id,
    issues: list[DoctorIssue],
    issue_factory: Callable[..., DoctorIssue],
) -> None:
    for suppression in suppressions:
        if suppression.source_candidate_id is None:
            continue
        candidate = candidates_by_id.get(suppression.source_candidate_id)
        if (
            candidate is None
            or candidate.workspace_id != workspace_id
            or candidate.candidate_type is not suppression.candidate_type
            or candidate.proposed_scope is not suppression.scope
            or (
                suppression.semantic_key is not None
                and candidate.semantic_key != suppression.semantic_key
            )
            or (
                suppression.fingerprint is not None
                and candidate.fingerprint != suppression.fingerprint
            )
        ):
            issues.append(
                issue_factory(
                    "learning_suppression_target",
                    DoctorSeverity.ERROR,
                    f"Learning suppression {suppression.suppression_id} target Candidate "
                    f"{suppression.source_candidate_id} is missing or inconsistent",
                )
            )


__all__ = ["inspect_candidates", "inspect_suppressions"]
