"""Narrow v11 persistence contracts for Learning decisions and Active memory."""

from __future__ import annotations

from typing import Protocol

from morrow.core.journal import TransactionalJournalPort
from morrow.core.learning_memory import (
    LearningCandidateDecision,
    MemoryWorkspaceState,
    ProjectKnowledgeCategory,
    ProjectKnowledgeEvidenceLink,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)


class LearningMemoryJournalPort(TransactionalJournalPort, Protocol):
    """Persistence-only surface shared by Inbox and Project Knowledge services."""

    def put_learning_candidate_decision(
        self, workspace_id: str, decision: LearningCandidateDecision
    ) -> LearningCandidateDecision: ...

    def get_learning_candidate_decision(
        self, workspace_id: str, decision_id: str
    ) -> LearningCandidateDecision | None: ...

    def list_learning_candidate_decisions(
        self,
        workspace_id: str,
        *,
        candidate_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningCandidateDecision, ...]: ...

    def get_project_knowledge_head(
        self, workspace_id: str, knowledge_id: str
    ) -> ProjectKnowledgeHead | None: ...

    def get_project_knowledge_head_by_key(
        self, workspace_id: str, semantic_key: str
    ) -> ProjectKnowledgeHead | None: ...

    def list_project_knowledge_heads(
        self,
        workspace_id: str,
        *,
        status: ProjectKnowledgeStatus | None = None,
        category: ProjectKnowledgeCategory | None = None,
        limit: int = 100,
    ) -> tuple[ProjectKnowledgeHead, ...]: ...

    def put_project_knowledge_head(
        self, workspace_id: str, head: ProjectKnowledgeHead
    ) -> ProjectKnowledgeHead: ...

    def save_project_knowledge_head(
        self,
        workspace_id: str,
        head: ProjectKnowledgeHead,
        *,
        expected_row_version: int,
    ) -> ProjectKnowledgeHead: ...

    def get_project_knowledge_revision(
        self, workspace_id: str, revision_id: str
    ) -> ProjectKnowledgeRevision | None: ...

    def list_project_knowledge_revisions(
        self,
        workspace_id: str,
        knowledge_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ProjectKnowledgeRevision, ...]: ...

    def put_project_knowledge_revision(
        self, workspace_id: str, revision: ProjectKnowledgeRevision
    ) -> ProjectKnowledgeRevision: ...

    def put_project_knowledge_evidence(
        self, workspace_id: str, link: ProjectKnowledgeEvidenceLink
    ) -> ProjectKnowledgeEvidenceLink: ...

    def list_project_knowledge_evidence(
        self, workspace_id: str, revision_id: str
    ) -> tuple[ProjectKnowledgeEvidenceLink, ...]: ...

    def get_memory_workspace_state(self, workspace_id: str) -> MemoryWorkspaceState | None: ...

    def ensure_memory_workspace_state(self, workspace_id: str) -> MemoryWorkspaceState: ...

    def save_memory_workspace_state(
        self,
        workspace_id: str,
        state: MemoryWorkspaceState,
        *,
        expected_row_version: int,
    ) -> MemoryWorkspaceState: ...


__all__ = ["LearningMemoryJournalPort"]
