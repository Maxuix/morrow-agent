"""Persistence port for immutable selections and rebuildable search terms."""

from __future__ import annotations

from typing import Protocol

from morrow.core.journal import TransactionalJournalPort
from morrow.core.memory_selection import MemorySearchTerm, MemorySelection


class MemorySelectionJournalPort(TransactionalJournalPort, Protocol):
    """Narrow v12 store surface; selection authority stays in durable rows."""

    def put_memory_selection(
        self, workspace_id: str, selection: MemorySelection
    ) -> MemorySelection: ...

    def get_memory_selection(
        self, workspace_id: str, selection_id: str
    ) -> MemorySelection | None: ...

    def list_memory_selections(
        self, workspace_id: str, *, limit: int = 100
    ) -> tuple[MemorySelection, ...]: ...

    def replace_memory_search_terms(
        self,
        workspace_id: str,
        knowledge_revision_id: str,
        terms: tuple[MemorySearchTerm, ...],
    ) -> tuple[MemorySearchTerm, ...]: ...

    def list_memory_search_terms(
        self,
        workspace_id: str,
        *,
        knowledge_revision_id: str | None = None,
        token: str | None = None,
        limit: int = 500,
    ) -> tuple[MemorySearchTerm, ...]: ...


__all__ = ["MemorySelectionJournalPort"]
