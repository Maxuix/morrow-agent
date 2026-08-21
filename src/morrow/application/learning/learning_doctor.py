"""Read-only orchestration for the v10/v11 Learning doctor checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from morrow.application.learning.learning_doctor_candidates import (
    inspect_candidates,
    inspect_suppressions,
)
from morrow.application.learning.learning_doctor_reviews import inspect_evidence, inspect_reviews
from morrow.application.learning.learning_doctor_state import inspect_knowledge, inspect_promotions
from morrow.core.doctor import DoctorIssue, DoctorSeverity
from morrow.core.store import StorageError

_LEARNING_SCHEMA_VERSION = 10
_PAGE_LIMIT = 500


def inspect_learning(
    journal,
    workspace_id: str,
    counts,
    issues: list[DoctorIssue],
    *,
    issue_factory: Callable[..., DoctorIssue],
) -> None:
    """Load bounded Learning rows and run each domain's read-only invariants."""

    if (
        getattr(journal, "schema_version", lambda: _LEARNING_SCHEMA_VERSION)()
        < _LEARNING_SCHEMA_VERSION
    ):
        return
    try:
        reviews = journal.list_learning_reviews(workspace_id, limit=_PAGE_LIMIT)
        evidence = journal.list_learning_evidence(workspace_id, limit=_PAGE_LIMIT)
        candidates = journal.list_learning_candidates(workspace_id, limit=_PAGE_LIMIT)
        suppressions = journal.list_learning_suppressions(workspace_id, limit=_PAGE_LIMIT)
        decisions = journal.list_learning_candidate_decisions(workspace_id, limit=_PAGE_LIMIT)
        operations = journal.list_promotion_operations(workspace_id, limit=_PAGE_LIMIT)
        activations = journal.list_configuration_activations(workspace_id, limit=_PAGE_LIMIT)
    except StorageError:
        issues.append(
            issue_factory(
                "learning_unavailable",
                DoctorSeverity.ERROR,
                "Learning state could not be read for diagnosis",
            )
        )
        return

    counts.update(
        {
            "learning_reviews": len(reviews),
            "learning_evidence": len(evidence),
            "learning_candidates": len(candidates),
            "learning_suppressions": len(suppressions),
            "learning_candidate_decisions": len(decisions),
            "learning_promotion_operations": len(operations),
            "learning_configuration_activations": len(activations),
        }
    )
    reviews_by_id = {item.review_id: item for item in reviews}
    candidates_by_id = {item.candidate_id: item for item in candidates}
    decisions_by_candidate: dict[str, list] = defaultdict(list)
    operations_by_id = {item.operation_id: item for item in operations}
    decisions_by_id = {item.decision_id: item for item in decisions}

    inspect_reviews(journal, workspace_id, reviews, reviews_by_id, issues, issue_factory)
    inspect_evidence(
        journal,
        workspace_id,
        reviews_by_id,
        evidence,
        counts,
        issues,
        issue_factory,
    )
    inspect_candidates(
        journal,
        workspace_id,
        candidates,
        candidates_by_id,
        decisions_by_candidate,
        decisions_by_id,
        reviews_by_id,
        counts,
        issues,
        issue_factory,
    )
    inspect_suppressions(workspace_id, suppressions, candidates_by_id, issues, issue_factory)
    inspect_promotions(
        workspace_id,
        operations,
        operations_by_id,
        candidates_by_id,
        decisions_by_id,
        activations,
        issues,
        issue_factory,
    )
    inspect_knowledge(
        journal,
        workspace_id,
        candidates_by_id,
        decisions_by_id,
        counts,
        issues,
        issue_factory,
    )


__all__ = ["inspect_learning"]
