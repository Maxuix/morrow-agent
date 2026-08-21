"""Read-only Review and Evidence invariants for the Learning doctor."""

from __future__ import annotations

from collections import defaultdict

from morrow.core.doctor import DoctorIssue, DoctorSeverity
from morrow.core.learning import LearningReviewStatus


def inspect_reviews(
    journal,
    workspace_id,
    reviews,
    reviews_by_id,
    issues: list[DoctorIssue],
    issue_factory,
) -> None:
    versions: dict[str, list[int]] = defaultdict(list)
    now = journal.now()
    for review in reviews:
        versions[review.task_outcome_id].append(review.review_version)
        task = journal.get_task_run(workspace_id, review.task_run_id)
        outcome = journal.get_task_outcome(workspace_id, review.task_outcome_id)
        if (
            task is None
            or outcome is None
            or task.workspace_id != workspace_id
            or outcome.workspace_id != workspace_id
            or task.task_run_id != review.task_run_id
            or outcome.task_run_id != review.task_run_id
        ):
            issues.append(
                issue_factory(
                    "learning_review_subject",
                    DoctorSeverity.ERROR,
                    "Learning Review does not point to the same-workspace TaskRun and Outcome",
                )
            )
        if review.status is LearningReviewStatus.RUNNING:
            if review.lease_id is None or review.lease_expires_at is None:
                issues.append(
                    issue_factory(
                        "learning_review_lease",
                        DoctorSeverity.ERROR,
                        "running Learning Review has no lease",
                    )
                )
            elif review.lease_expires_at <= now:
                issues.append(
                    issue_factory(
                        "learning_review_lease_expired",
                        DoctorSeverity.WARNING,
                        "running Learning Review lease has expired and needs foreground recovery",
                    )
                )
        elif review.lease_id is not None or review.lease_expires_at is not None:
            issues.append(
                issue_factory(
                    "learning_review_lease",
                    DoctorSeverity.ERROR,
                    "non-running Learning Review still owns a lease",
                )
            )
        if review.status in {LearningReviewStatus.COMPLETED, LearningReviewStatus.FAILED} and (
            review.completed_at is None
        ):
            issues.append(
                issue_factory(
                    "learning_review_completion",
                    DoctorSeverity.ERROR,
                    "terminal Learning Review has no completion timestamp",
                )
            )
        if review.supersedes_review_id is not None:
            previous = reviews_by_id.get(review.supersedes_review_id)
            if (
                previous is None
                or previous.task_outcome_id != review.task_outcome_id
                or previous.review_version >= review.review_version
            ):
                issues.append(
                    issue_factory(
                        "learning_review_supersedes",
                        DoctorSeverity.ERROR,
                        "Learning Review supersedes an invalid review version",
                    )
                )
    for versions_for_outcome in versions.values():
        if len(versions_for_outcome) != len(set(versions_for_outcome)):
            issues.append(
                issue_factory(
                    "learning_review_version",
                    DoctorSeverity.ERROR,
                    "Learning Review versions are not unique for an Outcome",
                )
            )


def inspect_evidence(
    journal,
    workspace_id,
    reviews_by_id,
    evidence,
    counts,
    issues: list[DoctorIssue],
    issue_factory,
) -> None:
    for item in evidence:
        review = reviews_by_id.get(item.origin_review_id)
        if (
            review is None
            or item.workspace_id != workspace_id
            or item.task_run_id != review.task_run_id
        ):
            issues.append(
                issue_factory(
                    "learning_evidence_link",
                    DoctorSeverity.ERROR,
                    "Learning Evidence points outside its Review workspace or TaskRun",
                )
            )
    for review_id, review in reviews_by_id.items():
        linked = journal.list_learning_review_evidence(workspace_id, review_id)
        counts["learning_review_evidence_links"] += len(linked)
        linked_ids = [item.evidence_id for item in linked]
        if len(linked_ids) != len(set(linked_ids)):
            issues.append(
                issue_factory(
                    "learning_review_evidence_duplicate",
                    DoctorSeverity.ERROR,
                    "Learning Review contains duplicate Evidence links",
                )
            )
        if any(item.origin_review_id != review.review_id for item in linked):
            issues.append(
                issue_factory(
                    "learning_review_evidence_owner",
                    DoctorSeverity.ERROR,
                    "Learning Review links Evidence owned by another Review",
                )
            )


__all__ = ["inspect_evidence", "inspect_reviews"]
