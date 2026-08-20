"""Transactional Project Knowledge term projection and bounded retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.learning_memory import (
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.memory_selection import MemorySearchTerm, MemorySearchWeightBand
from morrow.core.memory_tokens import (
    tokenize_memory_query,
    tokenize_memory_record,
)
from morrow.core.store import StorageError, StorageErrorCode

MEMORY_TERM_REVISION_PAGE = 500
MEMORY_TERM_CANDIDATE_LIMIT = 256
MEMORY_TERM_MATCH_PAGE = 500

_BAND_SCORE = {
    MemorySearchWeightBand.HIGH: 3,
    MemorySearchWeightBand.MEDIUM: 2,
    MemorySearchWeightBand.LOW: 1,
}


@dataclass(frozen=True, slots=True)
class MemoryTermCandidate:
    """A bounded lexical hit; selector policy owns final inclusion and ranking."""

    knowledge_revision_id: str
    score: int
    matched_terms: tuple[MemorySearchTerm, ...]


def terms_for_project_knowledge_revision(
    revision: ProjectKnowledgeRevision,
    head: ProjectKnowledgeHead,
) -> tuple[MemorySearchTerm, ...]:
    """Build the rebuildable term rows for one immutable revision."""

    if revision.workspace_id != head.workspace_id or revision.knowledge_id != head.knowledge_id:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "memory term source is inconsistent")
    tokens = tokenize_memory_record(
        semantic_key=head.semantic_key,
        category=head.category.value,
        statement=revision.statement,
    )
    return tuple(
        MemorySearchTerm(
            workspace_id=revision.workspace_id,
            knowledge_revision_id=revision.knowledge_revision_id,
            token_kind=token.token_kind,
            token=token.token,
            weight_band=token.weight_band,
        )
        for token in tokens
    )


def refresh_project_knowledge_terms(
    txn,
    workspace_id: str,
    head: ProjectKnowledgeHead,
    *,
    previous_revision_id: str | None = None,
) -> tuple[MemorySearchTerm, ...]:
    """Replace the current head's projection inside the caller's transaction."""

    if head.workspace_id != workspace_id:
        raise StorageError(
            StorageErrorCode.UNAVAILABLE, "memory term head is outside the workspace"
        )
    if previous_revision_id is not None and previous_revision_id != head.current_revision_id:
        txn.replace_memory_search_terms(workspace_id, previous_revision_id, ())
    if head.current_revision_id is None:
        return ()
    revision = txn.get_project_knowledge_revision(workspace_id, head.current_revision_id)
    if revision is None:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "memory term revision is missing")
    if revision.knowledge_id != head.knowledge_id:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "memory term revision is inconsistent")
    terms: tuple[MemorySearchTerm, ...] = ()
    if head.status is ProjectKnowledgeStatus.ACTIVE:
        terms = terms_for_project_knowledge_revision(revision, head)
    return txn.replace_memory_search_terms(
        workspace_id,
        revision.knowledge_revision_id,
        terms,
    )


def rebuild_project_knowledge_terms(txn, workspace_id: str, *, limit: int = 500) -> int:
    """Rebuild all bounded head projections and return the number of heads visited."""

    if isinstance(limit, bool) or not 1 <= limit <= MEMORY_TERM_REVISION_PAGE:
        raise ValueError("memory term rebuild limit is invalid")
    heads = txn.list_project_knowledge_heads(
        workspace_id,
        include_deleted=True,
        limit=limit,
    )
    for head in heads:
        refresh_project_knowledge_terms(txn, workspace_id, head)
    return len(heads)


def retrieve_memory_term_candidates(
    txn,
    workspace_id: str,
    query: str,
    *,
    limit: int = MEMORY_TERM_CANDIDATE_LIMIT,
) -> tuple[MemoryTermCandidate, ...]:
    """Return bounded lexical hits; active/current/safety filters remain selector policy."""

    if isinstance(limit, bool) or not 1 <= limit <= MEMORY_TERM_CANDIDATE_LIMIT:
        raise ValueError("memory term candidate limit is invalid")
    query_tokens = tokenize_memory_query(query)
    matched: dict[str, dict[tuple[object, str], MemorySearchTerm]] = {}
    scores: dict[str, int] = {}
    for token in query_tokens:
        for term in txn.list_memory_search_terms(
            workspace_id,
            token=token.token,
            limit=MEMORY_TERM_MATCH_PAGE,
        ):
            if term.token_kind is not token.token_kind:
                continue
            key = (term.token_kind, term.token)
            revision_terms = matched.setdefault(term.knowledge_revision_id, {})
            if key in revision_terms:
                continue
            revision_terms[key] = term
            scores[term.knowledge_revision_id] = scores.get(term.knowledge_revision_id, 0) + (
                _BAND_SCORE[token.weight_band] * _BAND_SCORE[term.weight_band]
            )
    candidates = (
        MemoryTermCandidate(
            knowledge_revision_id=revision_id,
            score=scores[revision_id],
            matched_terms=tuple(
                sorted(
                    revision_terms.values(),
                    key=lambda term: (term.token_kind.value, term.token),
                )
            ),
        )
        for revision_id, revision_terms in matched.items()
    )
    return tuple(
        sorted(candidates, key=lambda item: (-item.score, item.knowledge_revision_id))[:limit]
    )


__all__ = [
    "MEMORY_TERM_CANDIDATE_LIMIT",
    "MemoryTermCandidate",
    "rebuild_project_knowledge_terms",
    "refresh_project_knowledge_terms",
    "retrieve_memory_term_candidates",
    "terms_for_project_knowledge_revision",
]
