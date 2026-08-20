"""Deterministic, bounded Project Knowledge selection for foreground runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import LearningSensitivity, ProjectKnowledgeCategory
from morrow.core.learning_memory import (
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.memory_rendering import (
    project_knowledge_content_digest,
    render_project_knowledge,
)
from morrow.core.memory_selection import (
    MemoryQuery,
    MemorySelection,
    MemorySelectionItem,
    MemorySelectionReasonCode,
)
from morrow.core.ports import IdSource
from morrow.core.store import StorageError, StorageErrorCode

from .memory_terms import MemoryTermCandidate, retrieve_memory_term_candidates

MEMORY_SELECTOR_HEAD_LIMIT = 500
MEMORY_SELECTOR_RECENCY_WINDOW = timedelta(days=30)

_REASON_ORDER = {
    MemorySelectionReasonCode.EXPLICIT_KEY: 0,
    MemorySelectionReasonCode.CATEGORY: 1,
    MemorySelectionReasonCode.IDENTIFIER_OVERLAP: 2,
    MemorySelectionReasonCode.COMMAND_PATH_OVERLAP: 3,
    MemorySelectionReasonCode.LEXICAL_OVERLAP: 4,
    MemorySelectionReasonCode.RECENT_CONFIRMATION: 5,
    MemorySelectionReasonCode.CATEGORY_DIVERSITY: 6,
}


@dataclass(frozen=True, slots=True)
class _SelectionCandidate:
    head: ProjectKnowledgeHead
    revision: ProjectKnowledgeRevision
    explicit: bool
    category_match: bool
    lexical: MemoryTermCandidate | None
    recent: bool
    reasons: frozenset[MemorySelectionReasonCode] = field(default_factory=frozenset)

    @property
    def lexical_score(self) -> int:
        return self.lexical.score if self.lexical is not None else 0

    @property
    def identifier_overlap(self) -> int:
        if self.lexical is None:
            return 0
        return sum(term.token_kind.value == "identifier" for term in self.lexical.matched_terms)

    @property
    def path_overlap(self) -> int:
        if self.lexical is None:
            return 0
        return sum(term.token_kind.value == "path" for term in self.lexical.matched_terms)

    @property
    def lexical_overlap(self) -> int:
        if self.lexical is None:
            return 0
        return sum(
            term.token_kind.value in {"word", "cjk_bigram", "number"}
            for term in self.lexical.matched_terms
        )

    @property
    def sort_key(self) -> tuple[object, ...]:
        return (
            -int(self.explicit),
            -int(self.category_match),
            -self.lexical_score,
            -self.identifier_overlap,
            -self.path_overlap,
            -self.lexical_overlap,
            -int(self.recent),
            self.head.semantic_key,
            -self.revision.revision,
            self.revision.knowledge_revision_id,
            self.head.knowledge_id,
        )


class MemorySelector:
    """Build a persisted-ready selection without mutating memory or external state."""

    def __init__(
        self,
        *,
        id_source: IdSource,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.id_source = id_source
        self.clock = clock or (lambda: datetime.now(UTC))

    def select(
        self,
        txn,
        query: MemoryQuery,
        *,
        selection_id: str | None = None,
        now: datetime | None = None,
    ) -> MemorySelection:
        stamp = self._now(now)
        selection_id = selection_id or self.id_source.new_id("msel")
        source_memory_revision = self._memory_revision(txn, query.workspace_id)
        lexical = {
            item.knowledge_revision_id: item
            for item in retrieve_memory_term_candidates(txn, query.workspace_id, query.task_goal)
        }
        candidates = self._candidates(txn, query, lexical, stamp)
        ranked = tuple(sorted(candidates, key=lambda item: item.sort_key))
        selected, omitted = self._select_with_budgets(query, ranked)
        items = tuple(
            self._selection_item(selection_id, query.workspace_id, ordinal, candidate)
            for ordinal, candidate in enumerate(selected, start=1)
        )
        query_digest = sha256_digest(canonical_json_bytes(query.model_dump(mode="json")))
        selection_digest = sha256_digest(
            canonical_json_bytes(
                {
                    "selection_id": selection_id,
                    "workspace_id": query.workspace_id,
                    "query_digest": query_digest,
                    "source_memory_revision": source_memory_revision,
                    "items": [item.model_dump(mode="json") for item in items],
                    "omitted_count": omitted,
                    "rendered_chars": sum(item.estimated_chars for item in items),
                }
            )
        )
        return MemorySelection(
            selection_id=selection_id,
            workspace_id=query.workspace_id,
            query_digest=query_digest,
            source_memory_revision=source_memory_revision,
            selected_items=items,
            item_count=len(items),
            omitted_count=omitted,
            rendered_chars=sum(item.estimated_chars for item in items),
            selection_digest=selection_digest,
            created_at=stamp,
        )

    def _candidates(
        self,
        txn,
        query: MemoryQuery,
        lexical: dict[str, MemoryTermCandidate],
        now: datetime,
    ) -> tuple[_SelectionCandidate, ...]:
        explicit_keys = set(query.explicit_semantic_keys)
        requested_categories = set(query.requested_categories)
        heads = txn.list_project_knowledge_heads(
            query.workspace_id,
            status=ProjectKnowledgeStatus.ACTIVE,
            limit=MEMORY_SELECTOR_HEAD_LIMIT,
        )
        known_head_ids = {head.knowledge_id for head in heads}
        extra_heads = []
        for semantic_key in query.explicit_semantic_keys:
            head = txn.get_project_knowledge_head_by_key(query.workspace_id, semantic_key)
            if (
                head is not None
                and head.status is ProjectKnowledgeStatus.ACTIVE
                and head.knowledge_id not in known_head_ids
            ):
                extra_heads.append(head)
        heads = tuple(heads) + tuple(extra_heads)
        candidates: list[_SelectionCandidate] = []
        for head in heads:
            revision = self._current_revision(txn, query.workspace_id, head)
            if revision is None or not self._eligible_revision(revision, now):
                continue
            lexical_hit = lexical.get(revision.knowledge_revision_id)
            explicit = head.semantic_key in explicit_keys
            category_match = head.category in requested_categories
            if not explicit and not category_match and lexical_hit is None:
                continue
            reasons = set()
            if explicit:
                reasons.add(MemorySelectionReasonCode.EXPLICIT_KEY)
            if category_match:
                reasons.add(MemorySelectionReasonCode.CATEGORY)
            if lexical_hit is not None:
                kinds = {term.token_kind.value for term in lexical_hit.matched_terms}
                if "identifier" in kinds:
                    reasons.add(MemorySelectionReasonCode.IDENTIFIER_OVERLAP)
                if "path" in kinds:
                    reasons.add(MemorySelectionReasonCode.COMMAND_PATH_OVERLAP)
                if kinds & {"word", "cjk_bigram", "number"}:
                    reasons.add(MemorySelectionReasonCode.LEXICAL_OVERLAP)
            recent = self._recent(revision.last_confirmed_at, now)
            if recent:
                reasons.add(MemorySelectionReasonCode.RECENT_CONFIRMATION)
            candidates.append(
                _SelectionCandidate(
                    head=head,
                    revision=revision,
                    explicit=explicit,
                    category_match=category_match,
                    lexical=lexical_hit,
                    recent=recent,
                    reasons=frozenset(reasons),
                )
            )
        return tuple(candidates)

    def _select_with_budgets(
        self,
        query: MemoryQuery,
        ranked: tuple[_SelectionCandidate, ...],
    ) -> tuple[tuple[_SelectionCandidate, ...], int]:
        if query.max_items == 0 or query.max_rendered_chars == 0:
            return (), len(ranked)
        selected: list[_SelectionCandidate] = []
        selected_categories: set[ProjectKnowledgeCategory] = set()
        explicit = tuple(candidate for candidate in ranked if candidate.explicit)
        regular = tuple(candidate for candidate in ranked if not candidate.explicit)
        for candidate in explicit:
            if self._fits(query, selected, candidate):
                selected.append(candidate)
                selected_categories.add(candidate.head.category)
        remaining = list(regular)
        while remaining and len(selected) < query.max_items:
            index = next(
                (
                    index
                    for index, candidate in enumerate(remaining)
                    if candidate.head.category not in selected_categories
                    and self._fits(query, selected, candidate)
                ),
                None,
            )
            if index is None:
                index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining)
                        if self._fits(query, selected, candidate)
                    ),
                    None,
                )
            if index is None:
                break
            candidate = remaining.pop(index)
            if candidate.head.category not in selected_categories:
                candidate = candidate.__class__(
                    head=candidate.head,
                    revision=candidate.revision,
                    explicit=candidate.explicit,
                    category_match=candidate.category_match,
                    lexical=candidate.lexical,
                    recent=candidate.recent,
                    reasons=candidate.reasons | {MemorySelectionReasonCode.CATEGORY_DIVERSITY},
                )
            selected.append(candidate)
            selected_categories.add(candidate.head.category)
        return tuple(selected), len(ranked) - len(selected)

    @staticmethod
    def _fits(
        query: MemoryQuery,
        selected: list[_SelectionCandidate],
        candidate: _SelectionCandidate,
    ) -> bool:
        if len(selected) >= query.max_items:
            return False
        rendered = render_project_knowledge(candidate.head, candidate.revision)
        return (
            sum(len(render_project_knowledge(item.head, item.revision)) for item in selected)
            + len(rendered)
            <= query.max_rendered_chars
        )

    @staticmethod
    def _selection_item(
        selection_id: str,
        workspace_id: str,
        ordinal: int,
        candidate: _SelectionCandidate,
    ) -> MemorySelectionItem:
        rendered = render_project_knowledge(candidate.head, candidate.revision)
        return MemorySelectionItem(
            selection_id=selection_id,
            workspace_id=workspace_id,
            ordinal=ordinal,
            record_id=candidate.head.knowledge_id,
            record_revision_id=candidate.revision.knowledge_revision_id,
            revision=candidate.revision.revision,
            reason_codes=tuple(sorted(candidate.reasons, key=lambda code: _REASON_ORDER[code])),
            estimated_chars=len(rendered),
            rendered_content_digest=project_knowledge_content_digest(
                candidate.head, candidate.revision
            ),
        )

    @staticmethod
    def _memory_revision(txn, workspace_id: str) -> int:
        state = txn.get_memory_workspace_state(workspace_id)
        return 0 if state is None else state.memory_revision

    @staticmethod
    def _current_revision(
        txn, workspace_id: str, head: ProjectKnowledgeHead
    ) -> ProjectKnowledgeRevision | None:
        if head.current_revision_id is None:
            return None
        revision = txn.get_project_knowledge_revision(workspace_id, head.current_revision_id)
        if revision is None:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "memory selection revision is missing"
            )
        if revision.workspace_id != workspace_id or revision.knowledge_id != head.knowledge_id:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "memory selection revision is inconsistent"
            )
        return revision

    @staticmethod
    def _eligible_revision(
        revision: ProjectKnowledgeRevision,
        now: datetime,
    ) -> bool:
        if revision.sensitivity is LearningSensitivity.PROHIBITED:
            return False
        if revision.valid_from is not None and now < revision.valid_from:
            return False
        if revision.valid_until is not None and now >= revision.valid_until:
            return False
        return True

    @staticmethod
    def _recent(value: datetime, now: datetime) -> bool:
        return value <= now and now - value <= MEMORY_SELECTOR_RECENCY_WINDOW

    def _now(self, value: datetime | None) -> datetime:
        stamp = value or self.clock()
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC)


__all__ = ["MEMORY_SELECTOR_HEAD_LIMIT", "MemorySelector"]
