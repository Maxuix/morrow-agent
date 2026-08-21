"""Persistence port for immutable selections and rebuildable search terms."""

from __future__ import annotations

from typing import Protocol

from morrow.core.journal import (
    SessionRestoreJournalPort,
    ToolExecutionJournalPort,
    TransactionalJournalPort,
    TurnLifecycleJournalPort,
)
from morrow.core.learning_memory_ports import LearningMemoryJournalPort
from morrow.core.learning_ports import LearningJournalPort
from morrow.core.memory_selection import MemorySearchTerm, MemorySelection
from morrow.core.ports import PreferencePersistencePort


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


class MemoryRunProjectionJournalPort(
    SessionRestoreJournalPort,
    LearningMemoryJournalPort,
    MemorySelectionJournalPort,
    Protocol,
):
    """Read surface for rebuilding one durable AgentRun context projection."""


class MemorySelectionAdmissionPort(
    TurnLifecycleJournalPort,
    MemoryRunProjectionJournalPort,
    LearningJournalPort,
    PreferencePersistencePort,
    ToolExecutionJournalPort,
    Protocol,
):
    """Composite read/write surface for one atomic foreground admission."""

    def list_memory_search_terms(
        self,
        workspace_id: str,
        *,
        knowledge_revision_id: str | None = None,
        token: str | None = None,
        limit: int = 500,
    ) -> tuple[MemorySearchTerm, ...]: ...


__all__ = [
    "MemoryRunProjectionJournalPort",
    "MemorySelectionAdmissionPort",
    "MemorySelectionJournalPort",
]
