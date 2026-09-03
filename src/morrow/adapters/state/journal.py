"""SQLite adapter for Session lifecycle and the conversation journal."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from morrow.adapters.state.agent_definition_journal import SqliteAgentDefinitionJournal
from morrow.adapters.state.application_journal import SqliteApplicationJournal
from morrow.adapters.state.artifact_journal import SqliteArtifactJournal
from morrow.adapters.state.configuration_promotion_journal import (
    SqliteConfigurationPromotionJournal,
)
from morrow.adapters.state.context_journal import SqliteContextJournal
from morrow.adapters.state.conversation_journal import SqliteConversationJournal
from morrow.adapters.state.learning_journal import SqliteLearningJournal
from morrow.adapters.state.learning_memory_journal import SqliteLearningMemoryJournal
from morrow.adapters.state.mcp_journal import SqliteMcpJournal
from morrow.adapters.state.memory_selection_journal import SqliteMemorySelectionJournal
from morrow.adapters.state.observability_journal import SqliteObservabilityJournal
from morrow.adapters.state.operational import OperationalStoreSession, SqliteExecutor
from morrow.adapters.state.permission_journal import SqliteRunPermissionJournal
from morrow.adapters.state.preference_journal import SqlitePreferenceJournal
from morrow.adapters.state.recovery_journal import SqliteRecoveryJournal
from morrow.adapters.state.runtime_control_journal import SqliteRuntimeControlJournal
from morrow.adapters.state.skill_journal import SqliteSkillJournal
from morrow.adapters.state.task_journal import SqliteTaskJournal
from morrow.adapters.state.tool_journal import SqliteToolJournal
from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.adapters.state.workflow_journal import SqliteWorkflowJournal
from morrow.adapters.state.workflow_ownership import require_user_task
from morrow.core.application import (
    ApplicationCommandReceipt,
    ApplicationEvent,
)
from morrow.core.artifacts import (
    ArtifactMetadata,
    ArtifactState,
)
from morrow.core.configuration_promotion import (
    ConfigurationActivation,
    ConfigurationActivationStatus,
    PromotionOperation,
    PromotionOperationState,
)
from morrow.core.context import (
    ContextCheckpoint,
    SessionLineage,
)
from morrow.core.domain import (
    ArtifactReference,
    DurableAgentRun,
    DurableConversationRecord,
    DurableSession,
    DurableTaskRun,
    DurableTaskRunTransition,
    DurableTurn,
    SessionHealth,
    SessionLifecycle,
    TaskCommandReceipt,
    TaskOutcome,
    TaskRunStatus,
    TurnSubmitReceipt,
    session_can_start_work,
)
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
)
from morrow.core.learning import (
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningEvidence,
    LearningPolicy,
    LearningReview,
    LearningReviewStatus,
    LearningScope,
    LearningSuppression,
)
from morrow.core.learning_memory import (
    LearningCandidateDecision,
    MemoryWorkspaceState,
    ProjectKnowledgeCategory,
    ProjectKnowledgeEvidenceLink,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.mcp import (
    McpCatalogSnapshot,
    McpLaunchSnapshot,
    McpResultArtifactLink,
    McpServerDefinition,
    McpToolSnapshot,
)
from morrow.core.memory_selection import MemorySearchTerm, MemorySelection
from morrow.core.permissions import (
    CapabilityGrant,
    PermissionSnapshot,
)
from morrow.core.preference_persistence_models import (
    PreferenceWriteBatch,
    PreferenceWriteBatchStatus,
)
from morrow.core.recovery import RecoveryReceipt, RecoveryReport
from morrow.core.runtime_control import (
    RuntimeControlEntry,
    RuntimeControlKind,
    RuntimeControlStatus,
)
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.ids import RandomIdSource

_SESSION_COLUMNS = (
    "session_id, workspace_id, lifecycle, health, current_task_run_id, "
    "conversation_position, parent_session_id, parent_cut_record_id, parent_cut_position, "
    "parent_checkpoint_id, fork_reason, created_at_unix, updated_at_unix"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class SqliteOperationalJournal:
    """One SQLite adapter exposing the narrow lifecycle and journal ports."""

    def __init__(
        self,
        session: OperationalStoreSession,
        *,
        clock: Callable[[], datetime] | None = None,
        id_source=None,
    ) -> None:
        self._backend = SqliteJournalBackend(session, clock=clock)
        self._recovery_journal = SqliteRecoveryJournal(
            self._backend,
            session_exists=lambda workspace_id, session_id: (
                self.get_session(workspace_id, session_id) is not None
            ),
        )
        self._application_journal = SqliteApplicationJournal(
            self._backend,
            session_exists=lambda workspace_id, session_id: (
                self.get_session(workspace_id, session_id) is not None
            ),
        )
        self._artifact_journal = SqliteArtifactJournal(
            self._backend,
            session_exists=lambda workspace_id, session_id: (
                self.get_session(workspace_id, session_id) is not None
            ),
            task_belongs_to_session=self._task_belongs_to_session,
        )
        self._task_journal = SqliteTaskJournal(
            self._backend,
            get_session=self.get_session,
            turn_belongs_to_task=self._turn_belongs_to_task,
            validate_artifact_refs=self._validate_artifact_refs,
            replace_artifact_refs=self._replace_artifact_references,
        )
        self._conversation_journal = SqliteConversationJournal(
            self._backend,
            get_session=self.get_session,
            get_task=self.get_task_run,
            session_mutation_time=self._session_mutation_time,
        )
        self._permission_journal = SqliteRunPermissionJournal(
            self._backend,
            get_turn=self.get_turn,
            get_task=self.get_task_run,
        )
        self._context_journal = SqliteContextJournal(
            self._backend,
            get_session=self.get_session,
            get_task=self.get_task_run,
            get_agent_run=self.get_agent_run,
            load_effective_records=self.load_effective_records,
            validate_artifact_refs=self._validate_artifact_refs,
        )
        self._tool_journal = SqliteToolJournal(
            self._backend,
            get_agent_run=self.get_agent_run,
            get_task=self.get_task_run,
            get_permission_snapshot=self.get_permission_snapshot,
            get_capability_grant=self.get_capability_grant,
            validate_artifact_refs=self._validate_artifact_refs,
            replace_artifact_refs=self._replace_artifact_references,
        )
        self._observability_journal = SqliteObservabilityJournal(
            self._backend,
            id_source=id_source or RandomIdSource(),
            get_agent_run=self.get_agent_run,
            list_tool_executions=lambda workspace_id, agent_run_id: self.list_executions(
                workspace_id, agent_run_id=agent_run_id
            ),
        )

        self._learning_journal = SqliteLearningJournal(self._backend)
        self._learning_memory_journal = SqliteLearningMemoryJournal(self._backend)
        self._configuration_promotion_journal = SqliteConfigurationPromotionJournal(self._backend)
        self._memory_selection_journal = SqliteMemorySelectionJournal(self._backend)
        self._preference_journal = SqlitePreferenceJournal(self._backend)
        self._skill_journal = SqliteSkillJournal(self._backend)
        self._mcp_journal = SqliteMcpJournal(self._backend)
        self.agent_definitions = SqliteAgentDefinitionJournal(self._backend)
        self.workflows = SqliteWorkflowJournal(
            self._backend,
            get_task=self.get_task_run,
            get_artifact=self.get_artifact,
            get_agent_version=self.agent_definitions.get_version,
            get_agent_run=self.get_agent_run,
        )
        self._runtime_control_journal = SqliteRuntimeControlJournal(self._backend)

    def now(self) -> datetime:
        return self._backend.now()

    def supports_writes(self) -> bool:
        return self._backend.supports_writes()

    def schema_version(self) -> int:
        return self._backend.schema_version()

    def transaction_is_active(self) -> bool:
        return self._backend.transaction.active

    def enqueue_runtime_control(
        self,
        workspace_id: str,
        *,
        session_id: str,
        kind: RuntimeControlKind,
        client_message_id: str,
        text: str,
        created_at: datetime,
    ) -> RuntimeControlEntry:
        return self._runtime_control_journal.enqueue(
            workspace_id,
            session_id=session_id,
            kind=kind,
            client_message_id=client_message_id,
            text=text,
            created_at=created_at,
        )

    def get_runtime_control(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> RuntimeControlEntry | None:
        return self._runtime_control_journal.get(workspace_id, session_id, client_message_id)

    def peek_runtime_control(
        self,
        workspace_id: str,
        session_id: str,
        *,
        kind: RuntimeControlKind,
    ) -> RuntimeControlEntry | None:
        return self._runtime_control_journal.peek(workspace_id, session_id, kind=kind)

    def list_runtime_controls(
        self,
        workspace_id: str,
        session_id: str,
        *,
        status: RuntimeControlStatus | None = None,
    ) -> tuple[RuntimeControlEntry, ...]:
        return self._runtime_control_journal.list_entries(workspace_id, session_id, status=status)

    def consume_runtime_control(
        self,
        workspace_id: str,
        session_id: str,
        client_message_id: str,
        *,
        consumed_at: datetime,
    ) -> RuntimeControlEntry:
        return self._runtime_control_journal.consume(
            workspace_id,
            session_id,
            client_message_id,
            consumed_at=consumed_at,
        )

    @property
    def preference_journal(self) -> SqlitePreferenceJournal:
        """Expose the bounded v13 Preference repository to application services."""

        return self._preference_journal

    def put_preference_write_batch(
        self, workspace_id: str, batch: PreferenceWriteBatch
    ) -> PreferenceWriteBatch:
        return self._preference_journal.put_preference_write_batch(workspace_id, batch)

    def get_preference_write_batch(
        self, workspace_id: str, batch_id: str
    ) -> PreferenceWriteBatch | None:
        return self._preference_journal.get_preference_write_batch(workspace_id, batch_id)

    def get_preference_write_batch_by_command(
        self, workspace_id: str, command_id: str
    ) -> PreferenceWriteBatch | None:
        return self._preference_journal.get_preference_write_batch_by_command(
            workspace_id, command_id
        )

    def save_preference_write_batch(
        self,
        workspace_id: str,
        batch: PreferenceWriteBatch,
        *,
        expected_row_version: int,
    ) -> PreferenceWriteBatch:
        return self._preference_journal.save_preference_write_batch(
            workspace_id, batch, expected_row_version=expected_row_version
        )

    def list_preference_write_batches(
        self,
        workspace_id: str,
        *,
        status: PreferenceWriteBatchStatus | None = None,
        limit: int = 100,
    ) -> tuple[PreferenceWriteBatch, ...]:
        return self._preference_journal.list_preference_write_batches(
            workspace_id, status=status, limit=limit
        )

    def put_preference_review_job(self, workspace_id: str, job):
        return self._preference_journal.put_preference_review_job(workspace_id, job)

    def get_preference_review_job(self, workspace_id: str, job_id: str):
        return self._preference_journal.get_preference_review_job(workspace_id, job_id)

    def get_preference_review_job_for_turn(
        self, workspace_id: str, turn_id: str, *, review_version: int = 1
    ):
        return self._preference_journal.get_preference_review_job_for_turn(
            workspace_id, turn_id, review_version=review_version
        )

    def list_preference_review_jobs(self, workspace_id: str, *, status=None, limit: int = 100):
        return self._preference_journal.list_preference_review_jobs(
            workspace_id, status=status, limit=limit
        )

    def list_claimable_preference_review_jobs(self, workspace_id: str, *, limit: int = 100):
        return self._preference_journal.list_claimable_preference_review_jobs(
            workspace_id, limit=limit
        )

    def save_preference_review_job(self, workspace_id: str, job, *, expected_row_version: int):
        return self._preference_journal.save_preference_review_job(
            workspace_id, job, expected_row_version=expected_row_version
        )

    def claim_preference_review_job(
        self,
        workspace_id: str,
        job_id: str,
        *,
        expected_row_version: int,
        lease_id: str,
        lease_expires_at,
        started_at,
    ):
        return self._preference_journal.claim_preference_review_job(
            workspace_id,
            job_id,
            expected_row_version=expected_row_version,
            lease_id=lease_id,
            lease_expires_at=lease_expires_at,
            started_at=started_at,
        )

    def count_preference_review_jobs(self, workspace_id: str, *, status=None):
        return self._preference_journal.count_preference_review_jobs(workspace_id, status=status)

    def put_preference_evidence(self, workspace_id: str, evidence):
        return self._preference_journal.put_preference_evidence(workspace_id, evidence)

    def get_preference_evidence(self, workspace_id: str, evidence_id: str):
        return self._preference_journal.get_preference_evidence(workspace_id, evidence_id)

    def get_preference_evidence_for_job(self, workspace_id: str, job_id: str):
        return self._preference_journal.get_preference_evidence_for_job(workspace_id, job_id)

    def list_preference_evidence(
        self, workspace_id: str, *, job_id: str | None = None, limit: int = 100
    ):
        return self._preference_journal.list_preference_evidence(
            workspace_id, job_id=job_id, limit=limit
        )

    def put_preference_job_with_evidence(self, workspace_id: str, job, evidence):
        return self._preference_journal.put_preference_job_with_evidence(
            workspace_id, job, evidence
        )

    def put_preference_proposal(self, workspace_id: str, proposal):
        return self._preference_journal.put_preference_proposal(workspace_id, proposal)

    def get_preference_proposal(self, workspace_id: str, proposal_id: str):
        return self._preference_journal.get_preference_proposal(workspace_id, proposal_id)

    def list_preference_proposals(
        self, workspace_id: str, *, status=None, job_id=None, limit: int = 100
    ):
        return self._preference_journal.list_preference_proposals(
            workspace_id, status=status, job_id=job_id, limit=limit
        )

    def has_preference_proposal_fingerprint(
        self, workspace_id: str, fingerprint: str, *, status=None
    ):
        return self._preference_journal.has_preference_proposal_fingerprint(
            workspace_id, fingerprint, status=status
        )

    def finalize_preference_proposals(
        self, workspace_id: str, proposal_ids, *, command_id: str, operations, resolved_at
    ):
        return self._preference_journal.finalize_preference_proposals(
            workspace_id,
            proposal_ids,
            command_id=command_id,
            operations=operations,
            resolved_at=resolved_at,
        )

    def save_preference_proposal(self, workspace_id: str, proposal, *, expected_row_version: int):
        return self._preference_journal.save_preference_proposal(
            workspace_id, proposal, expected_row_version=expected_row_version
        )

    def transact[T](self, work: Callable[[SqliteOperationalJournal], T]) -> T:
        return self._backend.transact(lambda: work(self))

    def transact_once[T](self, work: Callable[[SqliteOperationalJournal], T]) -> T:
        """Run a non-replayable journal transaction for filesystem-coupled maintenance."""

        return self._backend.transact(lambda: work(self), replayable=False)

    def get_learning_policy(self, workspace_id: str) -> LearningPolicy | None:
        return self._learning_journal.get_learning_policy(workspace_id)

    def get_effective_learning_policy(self, workspace_id: str) -> LearningPolicy:
        return self._learning_journal.get_effective_learning_policy(workspace_id)

    def save_learning_policy(
        self,
        workspace_id: str,
        policy: LearningPolicy,
        *,
        expected_row_version: int | None,
    ) -> LearningPolicy:
        return self._learning_journal.save_learning_policy(
            workspace_id, policy, expected_row_version=expected_row_version
        )

    def put_learning_review(self, workspace_id: str, review: LearningReview) -> LearningReview:
        return self._learning_journal.put_learning_review(workspace_id, review)

    def get_learning_review(self, workspace_id: str, review_id: str) -> LearningReview | None:
        return self._learning_journal.get_learning_review(workspace_id, review_id)

    def list_learning_reviews(
        self,
        workspace_id: str,
        *,
        status: LearningReviewStatus | None = None,
        task_outcome_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningReview, ...]:
        return self._learning_journal.list_learning_reviews(
            workspace_id, status=status, task_outcome_id=task_outcome_id, limit=limit
        )

    def count_learning_reviews(
        self,
        workspace_id: str,
        *,
        status: LearningReviewStatus | None = None,
    ) -> int:
        return self._learning_journal.count_learning_reviews(workspace_id, status=status)

    def save_learning_review(
        self,
        workspace_id: str,
        review: LearningReview,
        *,
        expected_row_version: int,
    ) -> LearningReview:
        return self._learning_journal.save_learning_review(
            workspace_id, review, expected_row_version=expected_row_version
        )

    def claim_learning_review(
        self,
        workspace_id: str,
        review_id: str,
        *,
        expected_row_version: int,
        lease_id: str,
        lease_expires_at: datetime,
        started_at: datetime,
    ) -> LearningReview:
        return self._learning_journal.claim_learning_review(
            workspace_id,
            review_id,
            expected_row_version=expected_row_version,
            lease_id=lease_id,
            lease_expires_at=lease_expires_at,
            started_at=started_at,
        )

    def put_learning_evidence(
        self, workspace_id: str, evidence: LearningEvidence
    ) -> LearningEvidence:
        return self._learning_journal.put_learning_evidence(workspace_id, evidence)

    def get_learning_evidence(self, workspace_id: str, evidence_id: str) -> LearningEvidence | None:
        return self._learning_journal.get_learning_evidence(workspace_id, evidence_id)

    def list_learning_evidence(
        self,
        workspace_id: str,
        *,
        review_id: str | None = None,
        task_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningEvidence, ...]:
        return self._learning_journal.list_learning_evidence(
            workspace_id, review_id=review_id, task_run_id=task_run_id, limit=limit
        )

    def count_learning_evidence(
        self,
        workspace_id: str,
        *,
        review_id: str | None = None,
        task_run_id: str | None = None,
    ) -> int:
        return self._learning_journal.count_learning_evidence(
            workspace_id, review_id=review_id, task_run_id=task_run_id
        )

    def link_learning_review_evidence(
        self, workspace_id: str, review_id: str, evidence_id: str
    ) -> None:
        self._learning_journal.link_learning_review_evidence(workspace_id, review_id, evidence_id)

    def list_learning_review_evidence(
        self, workspace_id: str, review_id: str
    ) -> tuple[LearningEvidence, ...]:
        return self._learning_journal.list_learning_review_evidence(workspace_id, review_id)

    def put_learning_candidate(
        self, workspace_id: str, candidate: LearningCandidate
    ) -> LearningCandidate:
        return self._learning_journal.put_learning_candidate(workspace_id, candidate)

    def get_learning_candidate(
        self, workspace_id: str, candidate_id: str
    ) -> LearningCandidate | None:
        return self._learning_journal.get_learning_candidate(workspace_id, candidate_id)

    def list_learning_candidates(
        self,
        workspace_id: str,
        *,
        status: LearningCandidateStatus | None = None,
        candidate_type: LearningCandidateType | None = None,
        origin_review_id: str | None = None,
        fingerprint: str | None = None,
        semantic_key: str | None = None,
        expires_before: datetime | None = None,
        limit: int = 100,
    ) -> tuple[LearningCandidate, ...]:
        return self._learning_journal.list_learning_candidates(
            workspace_id,
            status=status,
            candidate_type=candidate_type,
            origin_review_id=origin_review_id,
            fingerprint=fingerprint,
            semantic_key=semantic_key,
            expires_before=expires_before,
            limit=limit,
        )

    def count_learning_candidates(
        self,
        workspace_id: str,
        *,
        status: LearningCandidateStatus | None = None,
        candidate_type: LearningCandidateType | None = None,
        origin_review_id: str | None = None,
    ) -> int:
        return self._learning_journal.count_learning_candidates(
            workspace_id,
            status=status,
            candidate_type=candidate_type,
            origin_review_id=origin_review_id,
        )

    def save_learning_candidate(
        self,
        workspace_id: str,
        candidate: LearningCandidate,
        *,
        expected_row_version: int,
    ) -> LearningCandidate:
        return self._learning_journal.save_learning_candidate(
            workspace_id, candidate, expected_row_version=expected_row_version
        )

    def link_learning_candidate_evidence(
        self,
        workspace_id: str,
        candidate_id: str,
        evidence_id: str,
        *,
        expected_row_version: int,
    ) -> LearningCandidate:
        return self._learning_journal.link_learning_candidate_evidence(
            workspace_id,
            candidate_id,
            evidence_id,
            expected_row_version=expected_row_version,
        )

    def list_learning_candidate_evidence(
        self, workspace_id: str, candidate_id: str
    ) -> tuple[LearningEvidence, ...]:
        return self._learning_journal.list_learning_candidate_evidence(workspace_id, candidate_id)

    def put_learning_suppression(
        self, workspace_id: str, suppression: LearningSuppression
    ) -> LearningSuppression:
        return self._learning_journal.put_learning_suppression(workspace_id, suppression)

    def get_learning_suppression(
        self, workspace_id: str, suppression_id: str
    ) -> LearningSuppression | None:
        return self._learning_journal.get_learning_suppression(workspace_id, suppression_id)

    def list_learning_suppressions(
        self,
        workspace_id: str,
        *,
        candidate_type: str | None = None,
        scope: LearningScope | None = None,
        semantic_key: str | None = None,
        fingerprint: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningSuppression, ...]:
        return self._learning_journal.list_learning_suppressions(
            workspace_id,
            candidate_type=candidate_type,
            scope=scope,
            semantic_key=semantic_key,
            fingerprint=fingerprint,
            limit=limit,
        )

    def save_learning_suppression(
        self,
        workspace_id: str,
        suppression: LearningSuppression,
        *,
        expected_row_version: int,
    ) -> LearningSuppression:
        return self._learning_journal.save_learning_suppression(
            workspace_id, suppression, expected_row_version=expected_row_version
        )

    def put_learning_candidate_decision(
        self, workspace_id: str, decision: LearningCandidateDecision
    ) -> LearningCandidateDecision:
        return self._learning_memory_journal.put_learning_candidate_decision(workspace_id, decision)

    def get_learning_candidate_decision(
        self, workspace_id: str, decision_id: str
    ) -> LearningCandidateDecision | None:
        return self._learning_memory_journal.get_learning_candidate_decision(
            workspace_id, decision_id
        )

    def list_learning_candidate_decisions(
        self,
        workspace_id: str,
        *,
        candidate_id: str | None = None,
        limit: int = 100,
    ) -> tuple[LearningCandidateDecision, ...]:
        return self._learning_memory_journal.list_learning_candidate_decisions(
            workspace_id, candidate_id=candidate_id, limit=limit
        )

    def get_project_knowledge_head(
        self, workspace_id: str, knowledge_id: str
    ) -> ProjectKnowledgeHead | None:
        return self._learning_memory_journal.get_project_knowledge_head(workspace_id, knowledge_id)

    def get_project_knowledge_head_by_key(
        self, workspace_id: str, semantic_key: str
    ) -> ProjectKnowledgeHead | None:
        return self._learning_memory_journal.get_project_knowledge_head_by_key(
            workspace_id, semantic_key
        )

    def list_project_knowledge_heads(
        self,
        workspace_id: str,
        *,
        status: ProjectKnowledgeStatus | None = None,
        category: ProjectKnowledgeCategory | None = None,
        include_deleted: bool = False,
        limit: int = 100,
    ) -> tuple[ProjectKnowledgeHead, ...]:
        return self._learning_memory_journal.list_project_knowledge_heads(
            workspace_id,
            status=status,
            category=category,
            include_deleted=include_deleted,
            limit=limit,
        )

    def put_project_knowledge_head(
        self, workspace_id: str, head: ProjectKnowledgeHead
    ) -> ProjectKnowledgeHead:
        return self._learning_memory_journal.put_project_knowledge_head(workspace_id, head)

    def save_project_knowledge_head(
        self,
        workspace_id: str,
        head: ProjectKnowledgeHead,
        *,
        expected_row_version: int,
    ) -> ProjectKnowledgeHead:
        return self._learning_memory_journal.save_project_knowledge_head(
            workspace_id, head, expected_row_version=expected_row_version
        )

    def get_project_knowledge_revision(
        self, workspace_id: str, revision_id: str
    ) -> ProjectKnowledgeRevision | None:
        return self._learning_memory_journal.get_project_knowledge_revision(
            workspace_id, revision_id
        )

    def list_project_knowledge_revisions(
        self,
        workspace_id: str,
        knowledge_id: str,
        *,
        limit: int = 100,
    ) -> tuple[ProjectKnowledgeRevision, ...]:
        return self._learning_memory_journal.list_project_knowledge_revisions(
            workspace_id, knowledge_id, limit=limit
        )

    def put_project_knowledge_revision(
        self, workspace_id: str, revision: ProjectKnowledgeRevision
    ) -> ProjectKnowledgeRevision:
        return self._learning_memory_journal.put_project_knowledge_revision(workspace_id, revision)

    def confirm_project_knowledge_revision(
        self,
        workspace_id: str,
        revision_id: str,
        *,
        confirmed_at: datetime,
    ) -> ProjectKnowledgeRevision:
        return self._learning_memory_journal.confirm_project_knowledge_revision(
            workspace_id, revision_id, confirmed_at=confirmed_at
        )

    def put_project_knowledge_evidence(
        self, workspace_id: str, link: ProjectKnowledgeEvidenceLink
    ) -> ProjectKnowledgeEvidenceLink:
        return self._learning_memory_journal.put_project_knowledge_evidence(workspace_id, link)

    def list_project_knowledge_evidence(
        self, workspace_id: str, revision_id: str
    ) -> tuple[ProjectKnowledgeEvidenceLink, ...]:
        return self._learning_memory_journal.list_project_knowledge_evidence(
            workspace_id, revision_id
        )

    def get_memory_workspace_state(self, workspace_id: str) -> MemoryWorkspaceState | None:
        return self._learning_memory_journal.get_memory_workspace_state(workspace_id)

    def ensure_memory_workspace_state(self, workspace_id: str) -> MemoryWorkspaceState:
        return self._learning_memory_journal.ensure_memory_workspace_state(workspace_id)

    def save_memory_workspace_state(
        self,
        workspace_id: str,
        state: MemoryWorkspaceState,
        *,
        expected_row_version: int,
    ) -> MemoryWorkspaceState:
        return self._learning_memory_journal.save_memory_workspace_state(
            workspace_id, state, expected_row_version=expected_row_version
        )

    def put_skill_definition(self, definition, *, updated_at) -> None:
        self._skill_journal.put_definition(definition, updated_at=updated_at)

    def put_skill_version(self, version) -> None:
        self._skill_journal.put_version(version)

    def record_skill_operation(self, **kwargs) -> None:
        self._skill_journal.record_operation(**kwargs)

    def get_skill_operation(self, operation_id: str):
        return self._skill_journal.get_operation(operation_id)

    def get_skill_definition(self, scope_id, skill_id):
        return self._skill_journal.get_definition(scope_id, skill_id)

    def list_skill_definitions(self):
        return self._skill_journal.list_definitions()

    def list_skill_versions(self, *, workspace_id=None):
        return self._skill_journal.list_versions(workspace_id=workspace_id)

    def skill_version_references(self, version_id):
        return self._skill_journal.skill_version_references(version_id)

    def delete_skill_version(self, version_id):
        self._skill_journal.delete_version(version_id)

    def put_skill_selection(self, workspace_id, selection):
        return self._skill_journal.put_selection(selection)

    def get_skill_selection(self, workspace_id, selection_id):
        return self._skill_journal.get_selection(workspace_id, selection_id)

    def list_skill_selections(self, workspace_id, agent_run_id):
        return self._skill_journal.list_selections(workspace_id, agent_run_id)

    def put_skill_context(self, workspace_id, context):
        return self._skill_journal.put_context(context)

    def get_skill_context(self, workspace_id, context_id):
        return self._skill_journal.get_context(workspace_id, context_id)

    def list_skill_contexts(self, workspace_id, agent_run_id):
        return self._skill_journal.list_contexts(workspace_id, agent_run_id)

    def put_skill_draft(self, workspace_id, draft):
        if draft.workspace_id != workspace_id:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft is outside the workspace")
        return self._skill_journal.put_draft(draft)

    def get_skill_draft(self, workspace_id, draft_id):
        return self._skill_journal.get_draft(workspace_id, draft_id)

    def get_skill_draft_by_candidate(self, workspace_id, candidate_id, *, latest=True):
        return self._skill_journal.get_draft_by_candidate(workspace_id, candidate_id, latest=latest)

    def list_skill_drafts(self, workspace_id, *, candidate_id=None, status=None, limit=100):
        return self._skill_journal.list_drafts(
            workspace_id, candidate_id=candidate_id, status=status, limit=limit
        )

    def save_skill_draft(self, workspace_id, draft, *, expected_row_version):
        if draft.workspace_id != workspace_id:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill Draft is outside the workspace")
        return self._skill_journal.save_draft(draft, expected_row_version=expected_row_version)

    def put_skill_draft_validation(self, workspace_id, report):
        draft = self.get_skill_draft(workspace_id, report.draft_id)
        if draft is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "Skill Draft is missing")
        return self._skill_journal.put_validation(report)

    def get_skill_draft_validation(self, workspace_id, draft_id, validation_id):
        draft = self.get_skill_draft(workspace_id, draft_id)
        if draft is None:
            return None
        return self._skill_journal.get_validation(draft_id, validation_id)

    def list_skill_draft_validations(self, workspace_id, draft_id, *, limit=32):
        draft = self.get_skill_draft(workspace_id, draft_id)
        if draft is None:
            return ()
        return self._skill_journal.list_validations(draft_id, limit=limit)

    def put_skill_usage(self, workspace_id, usage):
        if usage.workspace_id != workspace_id:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "Skill usage is outside the workspace")
        return self._skill_journal.put_usage(usage)

    def get_skill_usage(self, workspace_id, usage_id):
        return self._skill_journal.get_usage(workspace_id, usage_id)

    def list_skill_usages(
        self, workspace_id, *, skill_id=None, version_id=None, agent_run_id=None, limit=100
    ):
        return self._skill_journal.list_usages(
            workspace_id,
            skill_id=skill_id,
            version_id=version_id,
            agent_run_id=agent_run_id,
            limit=limit,
        )

    def put_mcp_server(
        self, definition: McpServerDefinition, *, catalog: McpCatalogSnapshot | None = None
    ) -> McpServerDefinition:
        return self._mcp_journal.put_server(definition, catalog=catalog)

    def get_mcp_server(
        self, scope: str, server_id: str, *, scope_id: str | None = None
    ) -> McpServerDefinition | None:
        return self._mcp_journal.get_server(scope, server_id, scope_id=scope_id)

    def list_mcp_servers(
        self, scope: str, *, scope_id: str | None = None
    ) -> tuple[McpServerDefinition, ...]:
        return self._mcp_journal.list_servers(scope, scope_id=scope_id)

    def put_mcp_catalog(
        self, definition: McpServerDefinition, snapshot: McpCatalogSnapshot
    ) -> McpCatalogSnapshot:
        return self._mcp_journal.put_catalog(definition, snapshot)

    def get_mcp_catalog(
        self,
        scope: str,
        server_id: str,
        *,
        scope_id: str | None = None,
        revision: int | None = None,
    ) -> McpCatalogSnapshot | None:
        return self._mcp_journal.get_catalog(scope, server_id, scope_id=scope_id, revision=revision)

    def put_mcp_launch_snapshot(self, snapshot: McpLaunchSnapshot) -> McpLaunchSnapshot:
        return self._mcp_journal.put_launch_snapshot(snapshot)

    def list_mcp_launch_snapshots(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[McpLaunchSnapshot, ...]:
        return self._mcp_journal.list_launch_snapshots(workspace_id, agent_run_id)

    def put_mcp_tool_snapshot(
        self, workspace_id: str, snapshot: McpToolSnapshot
    ) -> McpToolSnapshot:
        return self._mcp_journal.put_tool_snapshot(snapshot, workspace_id=workspace_id)

    def list_mcp_tool_snapshots(
        self, workspace_id: str, agent_run_id: str
    ) -> tuple[McpToolSnapshot, ...]:
        return self._mcp_journal.list_tool_snapshots(workspace_id, agent_run_id)

    def put_mcp_result_artifact_link(self, link: McpResultArtifactLink) -> McpResultArtifactLink:
        return self._mcp_journal.put_result_artifact_link(link)

    def list_mcp_result_artifact_links(
        self, workspace_id: str, tool_execution_id: str
    ) -> tuple[McpResultArtifactLink, ...]:
        return self._mcp_journal.list_result_artifact_links(workspace_id, tool_execution_id)

    def put_memory_selection(
        self, workspace_id: str, selection: MemorySelection
    ) -> MemorySelection:
        return self._memory_selection_journal.put_memory_selection(workspace_id, selection)

    def get_memory_selection(self, workspace_id: str, selection_id: str) -> MemorySelection | None:
        return self._memory_selection_journal.get_memory_selection(workspace_id, selection_id)

    def list_memory_selections(
        self, workspace_id: str, *, limit: int = 100
    ) -> tuple[MemorySelection, ...]:
        return self._memory_selection_journal.list_memory_selections(workspace_id, limit=limit)

    def replace_memory_search_terms(
        self,
        workspace_id: str,
        knowledge_revision_id: str,
        terms: tuple[MemorySearchTerm, ...],
    ) -> tuple[MemorySearchTerm, ...]:
        return self._memory_selection_journal.replace_memory_search_terms(
            workspace_id, knowledge_revision_id, terms
        )

    def list_memory_search_terms(
        self,
        workspace_id: str,
        *,
        knowledge_revision_id: str | None = None,
        token: str | None = None,
        limit: int = 500,
    ) -> tuple[MemorySearchTerm, ...]:
        return self._memory_selection_journal.list_memory_search_terms(
            workspace_id,
            knowledge_revision_id=knowledge_revision_id,
            token=token,
            limit=limit,
        )

    def create_session(
        self, session: DurableSession, *, task: DurableTaskRun | None = None
    ) -> DurableSession:
        if (
            session.lifecycle is not SessionLifecycle.ACTIVE
            and session.current_task_run_id is not None
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "inactive session cannot retain a current task",
            )
        if task is not None:
            require_user_task(self._backend, task)
            if not session_can_start_work(session.lifecycle, session.health):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "only an active healthy session can start a task",
                )
            if task.status is not TaskRunStatus.OPEN:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "new operational task must start open"
                )
            if task.session_id != session.session_id or task.workspace_id != session.workspace_id:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational task does not belong to the session"
                )
            if session.current_task_run_id not in {None, task.task_run_id}:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session task pointer is inconsistent"
                )
            session = session.model_copy(update={"current_task_run_id": task.task_run_id})

        def work(journal: SqliteOperationalJournal) -> DurableSession:
            journal._validate_session_lineage(session)
            journal._insert_session(session)
            if task is not None:
                journal._insert_task(task)
            loaded = journal.get_session(session.workspace_id, session.session_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session could not be read"
                )
            return loaded

        return self.transact(work)

    def get_session(self, workspace_id: str, session_id: str) -> DurableSession | None:
        row = self._read_one(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE session_id = ? AND workspace_id = ?",
            (session_id, workspace_id),
        )
        if row is None:
            return None
        return _session_from_row(row)

    def list_sessions(self, workspace_id: str) -> tuple[DurableSession, ...]:
        rows = self._read_all(
            f"SELECT {_SESSION_COLUMNS} FROM sessions "
            "WHERE workspace_id = ? AND lifecycle != 'deleted' "
            "ORDER BY created_at_unix ASC, session_id ASC",
            (workspace_id,),
        )
        return tuple(_session_from_row(row) for row in rows)

    def list_workspace_ids(self) -> tuple[str, ...]:
        definitions = (
            "UNION SELECT workspace_id FROM agent_definition_versions "
            if self.schema_version() >= 23
            else ""
        )
        rows = self._read_all(
            "SELECT workspace_id FROM sessions "
            "UNION SELECT workspace_id FROM artifacts "
            "UNION SELECT workspace_id FROM artifact_references "
            "UNION SELECT workspace_id FROM checkpoint_artifact_references "
            "UNION SELECT workspace_id FROM application_events "
            + definitions
            + "ORDER BY workspace_id"
        )
        return tuple(str(row[0]) for row in rows)

    def has_global_artifact_authority(self, artifact_id: str) -> bool:
        """Check root-wide metadata/reference authority for one cleanup candidate."""

        if self.schema_version() >= 24 and self._read_one(
            "SELECT 1 FROM workflow_artifact_bindings WHERE artifact_id=? LIMIT 1", (artifact_id,)
        ):
            return True
        if self.schema_version() >= 26 and self._read_one(
            "SELECT 1 FROM workflow_run_artifact_imports WHERE artifact_id=? LIMIT 1",
            (artifact_id,),
        ):
            return True

        row = self._read_one(
            """
            SELECT EXISTS (
                SELECT artifact_id FROM artifacts WHERE artifact_id = ?
                UNION ALL
                SELECT artifact_id FROM artifact_references WHERE artifact_id = ?
                UNION ALL
                SELECT artifact_id FROM checkpoint_artifact_references WHERE artifact_id = ?
            )
            """,
            (artifact_id, artifact_id, artifact_id),
        )
        return bool(row and row[0])

    def get_session_lineage(self, workspace_id: str, session_id: str) -> SessionLineage | None:
        session = self.get_session(workspace_id, session_id)
        if session is None or session.parent_session_id is None:
            return None
        return SessionLineage(
            workspace_id=workspace_id,
            child_session_id=session.session_id,
            parent_session_id=session.parent_session_id,
            cut_record_id=session.parent_cut_record_id,
            cut_position=session.parent_cut_position,
            checkpoint_id=session.parent_checkpoint_id,
            reason=session.fork_reason,
            created_at=session.created_at,
        )

    def get_lineage(self, workspace_id: str, session_id: str) -> SessionLineage | None:
        return self.get_session_lineage(workspace_id, session_id)

    def save_session(self, workspace_id: str, session: DurableSession) -> DurableSession:
        if session.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational session is outside the workspace"
            )

        def work(journal: SqliteOperationalJournal) -> DurableSession:
            existing = journal.get_session(workspace_id, session.session_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational session is missing")
            if _session_lineage_fields(existing) != _session_lineage_fields(session):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational Session lineage is immutable"
                )
            if session.current_task_run_id is not None:
                task = journal.get_task_run(workspace_id, session.current_task_run_id)
                if task is None or task.session_id != session.session_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational session task pointer is inconsistent",
                    )
                if task.status.is_terminal:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational session cannot point at a terminal task",
                    )
            if (
                session.lifecycle is not SessionLifecycle.ACTIVE
                and session.current_task_run_id is not None
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "inactive session cannot retain a current task",
                )
            visible_changed = (
                existing.lifecycle != session.lifecycle
                or existing.health != session.health
                or existing.current_task_run_id != session.current_task_run_id
                or existing.conversation_position != session.conversation_position
            )
            updated_at = (
                journal._session_mutation_time(existing, requested=session.updated_at)
                if visible_changed
                else existing.updated_at
            )
            journal._executor_or_raise().execute(
                """
                UPDATE sessions
                SET lifecycle = ?, health = ?, current_task_run_id = ?,
                    conversation_position = ?, updated_at_unix = ?
                WHERE session_id = ? AND workspace_id = ?
                """,
                (
                    session.lifecycle.value,
                    session.health.value,
                    session.current_task_run_id,
                    session.conversation_position,
                    _unix(updated_at),
                    session.session_id,
                    workspace_id,
                ),
            )
            loaded = journal.get_session(workspace_id, session.session_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational session could not be read"
                )
            return loaded

        return self.transact(work)

    def get_task_run(self, workspace_id: str, task_run_id: str) -> DurableTaskRun | None:
        return self._task_journal.get(workspace_id, task_run_id)

    def has_open_turn_submission(self, workspace_id: str, session_id: str) -> bool:
        """Durable open-Turn fact: an accepted submission receipt was never closed."""

        del workspace_id
        return (
            self._read_one(
                "SELECT 1 FROM turn_submit_receipts "
                "WHERE session_id=? AND disposition='accepted_open' LIMIT 1",
                (session_id,),
            )
            is not None
        )

    def has_nonterminal_agent_run(self, workspace_id: str, session_id: str) -> bool:
        """Durable in-flight AgentRun fact: no terminal metrics row exists yet."""

        del workspace_id
        return (
            self._read_one(
                "SELECT 1 FROM agent_runs r WHERE r.session_id=? AND NOT EXISTS "
                "(SELECT 1 FROM agent_run_terminal_metrics m WHERE m.agent_run_id=r.agent_run_id) "
                "LIMIT 1",
                (session_id,),
            )
            is not None
        )

    def count_workflow_agent_requests(self, workspace_id: str, workflow_run_id: str) -> int:
        """Durable purpose=agent admissions across every NodeRun of one WorkflowRun."""

        del workspace_id
        row = self._read_one(
            "SELECT COUNT(*) FROM agent_run_model_requests r "
            "JOIN workflow_agent_run_refs w ON r.agent_run_id = w.agent_run_id "
            "JOIN workflow_node_runs n ON w.node_run_id = n.node_run_id "
            "WHERE n.workflow_run_id=? AND r.purpose='agent'",
            (workflow_run_id,),
        )
        return int(row[0]) if row else 0

    def count_lineage_agent_requests(
        self, workspace_id: str, lineage_budget_root_run_id: str
    ) -> int:
        """Durable purpose=agent admissions across one whole continuation lineage.

        Stage 8 continuation children share their root's budget; an initial run
        is its own root, so this equals the single-run count until then.
        """

        del workspace_id
        row = self._read_one(
            "SELECT COUNT(*) FROM agent_run_model_requests r "
            "JOIN workflow_agent_run_refs w ON r.agent_run_id = w.agent_run_id "
            "JOIN workflow_node_runs n ON w.node_run_id = n.node_run_id "
            "JOIN workflow_runs wr ON n.workflow_run_id = wr.workflow_run_id "
            "WHERE wr.lineage_budget_root_run_id=? AND r.purpose='agent'",
            (lineage_budget_root_run_id,),
        )
        return int(row[0]) if row else 0

    def require_user_task(self, workspace_id, task_run_id):
        task = self.get_task_run(workspace_id, task_run_id)
        if task is not None:
            require_user_task(self._backend, task)

    def create_workflow_leaf(self, workspace_id, node_run_id, session, task):
        """Internal lifecycle seam; not exposed through ordinary Task commands."""

        def work(_):
            node = self.workflows.get_node(workspace_id, node_run_id)
            if node is None or node.status.value != "queued":
                raise ValueError("Workflow leaf requires a queued NodeRun")
            run = self.workflows.get_run(workspace_id, node.workflow_run_id)
            if run is None or run.status.terminal:
                raise ValueError("terminal Workflow cannot create leaves")
            if (
                task.purpose.value != "workflow_node"
                or task.session_id != session.session_id
                or session.workspace_id != workspace_id
            ):
                raise ValueError("Workflow leaf Session/Task scope mismatch")
            if session.parent_session_id is not None or session.current_task_run_id is not None:
                raise ValueError("Workflow leaf requires a fresh standalone Session")
            self.create_session(session)
            result = self._task_journal._create(workspace_id, task, make_current=True)
            self._backend.executor().execute(
                "INSERT INTO workflow_leaf_ownership VALUES(?,?,?)",
                (
                    node_run_id,
                    session.session_id,
                    task.task_run_id,
                ),
            )
            return result

        return self.transact(work)

    def create_workflow_turn(self, workspace_id, node_run_id, turn):
        node = self.workflows.get_node(workspace_id, node_run_id)
        run = (
            self.workflows.get_run(workspace_id, node.workflow_run_id) if node is not None else None
        )
        task = self.get_task_run(workspace_id, turn.task_run_id)
        if node is None or node.status.value != "queued" or run is None or run.status.terminal:
            raise ValueError("Workflow Turn requires an active Workflow and queued internal Task")
        revision = self.workflows.get_revision(workspace_id, run.workflow_revision_id)
        definition = next(n for n in revision.nodes if n.node_id == node.node_id)
        direct = definition.conversation_scope == "invoking_session"
        if direct:
            session = self.get_session(workspace_id, turn.session_id)
            if (
                task is None
                or task.purpose.value != "user"
                or task.task_run_id != run.root_task_run_id
                or task.row_version != run.invoking_root_row_version
                or session is None
                or session.current_task_run_id != task.task_run_id
                or turn.client_message_id != run.invoking_client_message_id
            ):
                raise ValueError("Workflow Direct Turn does not match its frozen root binding")
            return self._conversation_journal._create_turn(workspace_id, turn)
        if task is None or task.purpose.value != "workflow_node":
            raise ValueError("Workflow Turn requires an active Workflow and queued internal Task")
        owner = self._backend.read_one(
            "SELECT session_id, task_run_id FROM workflow_leaf_ownership WHERE node_run_id=?",
            (node_run_id,),
        )
        if owner != (turn.session_id, turn.task_run_id):
            raise ValueError("Workflow Turn does not match its owned leaf")
        return self._conversation_journal._create_turn(workspace_id, turn)

    def transition_workflow_task(self, workspace_id, workflow_run_id, task_run_id, **kwargs):
        run = self.workflows.get_run(workspace_id, workflow_run_id)
        owned_leaf = self._backend.read_one(
            """
            SELECT 1
            FROM workflow_leaf_ownership AS ownership
            JOIN workflow_node_runs AS node USING(node_run_id)
            WHERE node.workspace_id=? AND node.workflow_run_id=? AND ownership.task_run_id=?
            """,
            (workspace_id, workflow_run_id, task_run_id),
        )
        is_owned_leaf = owned_leaf is not None
        is_owned_task = run is not None and (task_run_id == run.root_task_run_id or is_owned_leaf)
        if not is_owned_task:
            raise ValueError("Task is not owned by this Workflow")
        if run.status.terminal and (not is_owned_leaf or not kwargs["target"].is_terminal):
            raise ValueError("terminal Workflow only permits draining owned leaf Tasks")
        return self._task_journal._transition(workspace_id, task_run_id, **kwargs)

    def _task_belongs_to_session(
        self, workspace_id: str, task_run_id: str, session_id: str
    ) -> bool:
        task = self.get_task_run(workspace_id, task_run_id)
        return task is not None and task.session_id == session_id

    def list_task_runs(self, workspace_id: str, session_id: str) -> tuple[DurableTaskRun, ...]:
        return self._task_journal.list(workspace_id, session_id)

    def create_task_run(
        self, workspace_id: str, task: DurableTaskRun, *, make_current: bool = False
    ) -> DurableTaskRun:
        return self._task_journal.create(workspace_id, task, make_current=make_current)

    def transition_task_run(
        self,
        workspace_id: str,
        task_run_id: str,
        *,
        target: TaskRunStatus,
        transition: DurableTaskRunTransition,
        expected_row_version: int,
    ) -> DurableTaskRun:
        """Apply one optimistic, audited TaskRun transition."""

        return self._task_journal.transition(
            workspace_id,
            task_run_id,
            target=target,
            transition=transition,
            expected_row_version=expected_row_version,
        )

    def list_task_transitions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableTaskRunTransition, ...]:
        return self._task_journal.list_transitions(workspace_id, task_run_id)

    def put_task_outcome(self, workspace_id: str, outcome: TaskOutcome) -> TaskOutcome:
        return self._task_journal.put_outcome(workspace_id, outcome)

    def get_task_outcome(self, workspace_id: str, outcome_id: str) -> TaskOutcome | None:
        return self._task_journal.get_outcome(workspace_id, outcome_id)

    def list_task_outcomes(self, workspace_id: str, task_run_id: str) -> tuple[TaskOutcome, ...]:
        return self._task_journal.list_outcomes(workspace_id, task_run_id)

    def reserve_artifact(self, workspace_id: str, metadata: ArtifactMetadata) -> ArtifactMetadata:
        """Reserve metadata before any managed file is published."""

        return self._artifact_journal.reserve(workspace_id, metadata)

    def get_artifact(self, workspace_id: str, artifact_id: str) -> ArtifactMetadata | None:
        return self._artifact_journal.get(workspace_id, artifact_id)

    def list_artifacts(
        self,
        workspace_id: str,
        *,
        session_id: str | None = None,
        task_run_id: str | None = None,
    ) -> tuple[ArtifactMetadata, ...]:
        return self._artifact_journal.list(
            workspace_id, session_id=session_id, task_run_id=task_run_id
        )

    def save_artifact(
        self,
        workspace_id: str,
        metadata: ArtifactMetadata,
        *,
        expected_row_version: int,
    ) -> ArtifactMetadata:
        return self._artifact_journal.save(
            workspace_id, metadata, expected_row_version=expected_row_version
        )

    def artifact_bytes_for_task(self, workspace_id: str, task_run_id: str) -> int:
        return self._artifact_journal.bytes_for_task(workspace_id, task_run_id)

    def list_artifact_references(
        self, workspace_id: str, artifact_id: str | None = None
    ) -> tuple[tuple[str, str, str, str], ...]:
        return self._artifact_journal.list_references(workspace_id, artifact_id)

    def _validate_artifact_scope(self, workspace_id: str, metadata: ArtifactMetadata) -> None:
        self._artifact_journal.validate_scope(workspace_id, metadata)

    def _replace_artifact_references(
        self,
        workspace_id: str,
        *,
        owner_kind: str,
        owner_id: str,
        references: tuple[ArtifactReference, ...],
        created_at: datetime,
    ) -> None:
        self._artifact_journal.replace_references(
            workspace_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            references=references,
            created_at=created_at,
        )

    def get_task_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> TaskCommandReceipt | None:
        return self._task_journal.get_command_receipt(workspace_id, command_id)

    def put_task_command_receipt(
        self, workspace_id: str, receipt: TaskCommandReceipt
    ) -> TaskCommandReceipt:
        return self._task_journal.put_command_receipt(workspace_id, receipt)

    def get_application_event(self, workspace_id: str, event_id: str) -> ApplicationEvent | None:
        return self._application_journal.get_event(workspace_id, event_id)

    def list_application_events(
        self, workspace_id: str, *, after_cursor: int = 0, limit: int = 100
    ) -> tuple[ApplicationEvent, ...]:
        return self._application_journal.list_events(
            workspace_id, after_cursor=after_cursor, limit=limit
        )

    def put_application_event(self, workspace_id: str, event: ApplicationEvent) -> ApplicationEvent:
        return self._application_journal.put_event(workspace_id, event)

    def latest_application_event_cursor(self, workspace_id: str) -> int:
        return self._application_journal.latest_cursor(workspace_id)

    def put_application_event_in_txn(
        self, workspace_id: str, event: ApplicationEvent
    ) -> ApplicationEvent:
        return self._application_journal.put_event_in_txn(workspace_id, event)

    def get_application_command_receipt(
        self, workspace_id: str, command_id: str
    ) -> ApplicationCommandReceipt | None:
        return self._application_journal.get_receipt(workspace_id, command_id)

    def put_application_command_receipt(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt:
        return self._application_journal.put_receipt(workspace_id, receipt)

    def put_application_command_receipt_in_txn(
        self, workspace_id: str, receipt: ApplicationCommandReceipt
    ) -> ApplicationCommandReceipt:
        return self._application_journal.put_receipt_in_txn(workspace_id, receipt)

    def get_promotion_operation(
        self, workspace_id: str, operation_id: str
    ) -> PromotionOperation | None:
        return self._configuration_promotion_journal.get_promotion_operation(
            workspace_id, operation_id
        )

    def get_promotion_operation_by_command(
        self, workspace_id: str, command_id: str
    ) -> PromotionOperation | None:
        return self._configuration_promotion_journal.get_promotion_operation_by_command(
            workspace_id, command_id
        )

    def list_promotion_operations(
        self,
        workspace_id: str,
        *,
        state: PromotionOperationState | None = None,
        limit: int = 100,
    ) -> tuple[PromotionOperation, ...]:
        return self._configuration_promotion_journal.list_promotion_operations(
            workspace_id, state=state, limit=limit
        )

    def put_promotion_operation(
        self, workspace_id: str, operation: PromotionOperation
    ) -> PromotionOperation:
        return self._configuration_promotion_journal.put_promotion_operation(
            workspace_id, operation
        )

    def save_promotion_operation(
        self,
        workspace_id: str,
        operation: PromotionOperation,
        *,
        expected_row_version: int,
    ) -> PromotionOperation:
        return self._configuration_promotion_journal.save_promotion_operation(
            workspace_id, operation, expected_row_version=expected_row_version
        )

    def get_configuration_activation(
        self, workspace_id: str, activation_id: str
    ) -> ConfigurationActivation | None:
        return self._configuration_promotion_journal.get_configuration_activation(
            workspace_id, activation_id
        )

    def list_configuration_activations(
        self,
        workspace_id: str,
        *,
        target: str | None = None,
        path: str | None = None,
        status: ConfigurationActivationStatus | None = None,
        limit: int = 100,
    ) -> tuple[ConfigurationActivation, ...]:
        return self._configuration_promotion_journal.list_configuration_activations(
            workspace_id, target=target, path=path, status=status, limit=limit
        )

    def put_configuration_activation(
        self, workspace_id: str, activation: ConfigurationActivation
    ) -> ConfigurationActivation:
        return self._configuration_promotion_journal.put_configuration_activation(
            workspace_id, activation
        )

    def save_configuration_activation(
        self, workspace_id: str, activation: ConfigurationActivation
    ) -> ConfigurationActivation:
        return self._configuration_promotion_journal.save_configuration_activation(
            workspace_id, activation
        )

    def create_turn(self, workspace_id: str, turn: DurableTurn) -> DurableTurn:
        return self._conversation_journal.create_turn(workspace_id, turn)

    def get_turn(self, workspace_id: str, turn_id: str) -> DurableTurn | None:
        return self._conversation_journal.get_turn(workspace_id, turn_id)

    def _turn_belongs_to_task(self, workspace_id: str, turn_id: str, task_run_id: str) -> bool:
        turn = self.get_turn(workspace_id, turn_id)
        return turn is not None and turn.task_run_id == task_run_id

    def list_task_turns(self, workspace_id: str, task_run_id: str) -> tuple[DurableTurn, ...]:
        return self._conversation_journal.list_task_turns(workspace_id, task_run_id)

    def list_session_turns(self, workspace_id: str, session_id: str) -> tuple[DurableTurn, ...]:
        return self._conversation_journal.list_session_turns(workspace_id, session_id)

    def create_agent_run(self, workspace_id: str, run: DurableAgentRun) -> DurableAgentRun:
        return self._permission_journal.create_agent_run(workspace_id, run)

    def create_agent_run_with_permission_snapshot(
        self,
        workspace_id: str,
        run: DurableAgentRun,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun:
        return self._permission_journal.create_agent_run_with_permission_snapshot(
            workspace_id, run, permission_snapshot
        )

    def freeze_agent_run_permission_snapshot(
        self,
        workspace_id: str,
        agent_run_id: str,
        permission_snapshot: PermissionSnapshot,
    ) -> DurableAgentRun:
        return self._permission_journal.freeze_agent_run_permission_snapshot(
            workspace_id, agent_run_id, permission_snapshot
        )

    def get_agent_run(self, workspace_id: str, agent_run_id: str) -> DurableAgentRun | None:
        return self._permission_journal.get_agent_run(workspace_id, agent_run_id)

    def list_session_agent_runs(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableAgentRun, ...]:
        return self._permission_journal.list_session_agent_runs(workspace_id, session_id)

    def admit_model_request(self, workspace_id: str, **kwargs):
        return self._observability_journal.admit_model_request(workspace_id, **kwargs)

    def settle_model_request(self, workspace_id: str, model_request_id: str, **kwargs):
        return self._observability_journal.settle_model_request(
            workspace_id, model_request_id, **kwargs
        )

    def finalize_agent_run(self, workspace_id: str, **kwargs):
        return self._observability_journal.finalize_agent_run(workspace_id, **kwargs)

    def get_model_request(self, workspace_id: str, model_request_id: str):
        return self._observability_journal.get_model_request(workspace_id, model_request_id)

    def list_model_requests(self, workspace_id: str, agent_run_id: str):
        return self._observability_journal.list_model_requests(workspace_id, agent_run_id)

    def get_agent_run_terminal_metrics(self, workspace_id: str, agent_run_id: str):
        return self._observability_journal.get_terminal_metrics(workspace_id, agent_run_id)

    def get_agent_run_observation(self, workspace_id: str, agent_run_id: str):
        return self._observability_journal.get_agent_run_observation(workspace_id, agent_run_id)

    def get_agent_run_retry_progress(self, workspace_id: str, agent_run_id: str):
        return self._observability_journal.get_retry_progress(workspace_id, agent_run_id)

    def record_agent_run_retry_progress(self, workspace_id: str, **kwargs):
        return self._observability_journal.record_retry_progress(workspace_id, **kwargs)

    def get_permission_snapshot(
        self, workspace_id: str, permission_snapshot_id: str
    ) -> PermissionSnapshot | None:
        return self._permission_journal.get_permission_snapshot(
            workspace_id, permission_snapshot_id
        )

    def get_permission_snapshot_for_run(
        self, workspace_id: str, agent_run_id: str
    ) -> PermissionSnapshot | None:
        return self._permission_journal.get_permission_snapshot_for_run(workspace_id, agent_run_id)

    def list_permission_snapshots(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[PermissionSnapshot, ...]:
        return self._permission_journal.list_permission_snapshots(
            workspace_id, agent_run_id=agent_run_id
        )

    def put_permission_snapshot(
        self, workspace_id: str, permission_snapshot: PermissionSnapshot
    ) -> PermissionSnapshot:
        return self._permission_journal.put_permission_snapshot(workspace_id, permission_snapshot)

    def link_agent_run_permission_snapshot(
        self, workspace_id: str, agent_run_id: str, permission_snapshot_id: str
    ) -> DurableAgentRun:
        return self._permission_journal.link_agent_run_permission_snapshot(
            workspace_id, agent_run_id, permission_snapshot_id
        )

    def append_records(
        self, workspace_id: str, records: Sequence[DurableConversationRecord]
    ) -> DurableSession:
        return self._conversation_journal.append_records(workspace_id, records)

    def load_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        return self._conversation_journal.load_records(workspace_id, session_id)

    def load_effective_records(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableConversationRecord, ...]:
        return self._conversation_journal.load_effective_records(workspace_id, session_id)

    def put_context_checkpoint(
        self, workspace_id: str, checkpoint: ContextCheckpoint
    ) -> ContextCheckpoint:
        return self._context_journal.put(workspace_id, checkpoint)

    def get_context_checkpoint(
        self, workspace_id: str, checkpoint_id: str
    ) -> ContextCheckpoint | None:
        return self._context_journal.get(workspace_id, checkpoint_id)

    def list_context_checkpoints(
        self, workspace_id: str, session_id: str, *, task_run_id: str | None = None
    ) -> tuple[ContextCheckpoint, ...]:
        return self._context_journal.list(workspace_id, session_id, task_run_id=task_run_id)

    def _validate_artifact_refs(
        self,
        workspace_id: str,
        references: tuple[ArtifactReference, ...],
        *,
        session_id: str,
        task_run_id: str | None,
        require_available: bool = True,
    ) -> None:
        for reference in references:
            artifact = self.get_artifact(workspace_id, reference.artifact_id)
            if artifact is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational artifact is missing")
            if artifact.session_id not in {None, session_id} or artifact.task_run_id not in {
                None,
                task_run_id,
            }:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational artifact reference scope is invalid"
                )
            if require_available and artifact.state is not ArtifactState.AVAILABLE:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "only available artifacts may be referenced"
                )

    def get_receipt(
        self, workspace_id: str, session_id: str, client_message_id: str
    ) -> TurnSubmitReceipt | None:
        return self._conversation_journal.get_receipt(workspace_id, session_id, client_message_id)

    def put_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        return self._conversation_journal.put_receipt(workspace_id, receipt)

    def update_receipt(self, workspace_id: str, receipt: TurnSubmitReceipt) -> TurnSubmitReceipt:
        return self._conversation_journal.update_receipt(workspace_id, receipt)

    def put_execution(
        self, workspace_id: str, execution: DurableToolExecution
    ) -> DurableToolExecution:
        return self._tool_journal.put_execution(workspace_id, execution)

    def get_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableToolExecution | None:
        return self._tool_journal.get_execution(workspace_id, tool_execution_id)

    def list_executions(
        self, workspace_id: str, *, agent_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_executions(workspace_id, agent_run_id=agent_run_id)

    def list_executions_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_executions_for_grant(workspace_id, grant_id)

    def list_session_executions(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_session_executions(workspace_id, session_id)

    def list_task_executions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        return self._tool_journal.list_task_executions(workspace_id, task_run_id)

    def save_execution(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        return self._tool_journal.save_execution(
            workspace_id, execution, expected_row_version=expected_row_version
        )

    def request_execution_cancellation_in_txn(
        self,
        workspace_id: str,
        tool_execution_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> DurableToolExecution | None:
        return self._tool_journal.request_cancellation_in_txn(
            workspace_id, tool_execution_id, now=now, reason=reason
        )

    def _save_execution_in_txn(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        return self._tool_journal.save_execution_in_txn(
            workspace_id, execution, expected_row_version=expected_row_version
        )

    def put_approval(self, workspace_id: str, approval: DurableApproval) -> DurableApproval:
        return self._tool_journal.put_approval(workspace_id, approval)

    def get_approval(self, workspace_id: str, approval_id: str) -> DurableApproval | None:
        return self._tool_journal.get_approval(workspace_id, approval_id)

    def get_approval_for_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableApproval | None:
        return self._tool_journal.get_approval_for_execution(workspace_id, tool_execution_id)

    def save_approval(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        return self._tool_journal.save_approval(
            workspace_id, approval, expected_row_version=expected_row_version
        )

    def list_approvals_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableApproval, ...]:
        return self._tool_journal.list_approvals_for_grant(workspace_id, grant_id)

    def revoke_approval_in_txn(
        self,
        workspace_id: str,
        approval_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> DurableApproval | None:
        return self._tool_journal.revoke_approval_in_txn(
            workspace_id, approval_id, now=now, reason=reason
        )

    def _save_approval_in_txn(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        return self._tool_journal.save_approval_in_txn(
            workspace_id, approval, expected_row_version=expected_row_version
        )

    def put_capability_grant(self, workspace_id: str, grant: CapabilityGrant) -> CapabilityGrant:
        return self._permission_journal.put_capability_grant(workspace_id, grant)

    def get_capability_grant(self, workspace_id: str, grant_id: str) -> CapabilityGrant | None:
        return self._permission_journal.get_capability_grant(workspace_id, grant_id)

    def list_capability_grants(
        self, workspace_id: str, *, agent_run_id: str | None = None
    ) -> tuple[CapabilityGrant, ...]:
        return self._permission_journal.list_capability_grants(
            workspace_id, agent_run_id=agent_run_id
        )

    def save_capability_grant(
        self,
        workspace_id: str,
        grant: CapabilityGrant,
        *,
        expected_row_version: int,
    ) -> CapabilityGrant:
        return self._permission_journal.save_capability_grant(
            workspace_id, grant, expected_row_version=expected_row_version
        )

    def put_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        return self._recovery_journal.put_report(workspace_id, report)

    def get_report(self, workspace_id: str, report_id: str) -> RecoveryReport | None:
        return self._recovery_journal.get_report(workspace_id, report_id)

    def get_open_report(self, workspace_id: str, session_id: str) -> RecoveryReport | None:
        return self._recovery_journal.get_open_report(workspace_id, session_id)

    def list_recovery_reports(
        self, workspace_id: str, session_id: str
    ) -> tuple[RecoveryReport, ...]:
        return self._recovery_journal.list_recovery_reports(workspace_id, session_id)

    def save_report(self, workspace_id: str, report: RecoveryReport) -> RecoveryReport:
        return self._recovery_journal.save_report(workspace_id, report)

    def get_recovery_receipt(
        self, workspace_id: str, session_id: str, command_id: str
    ) -> RecoveryReceipt | None:
        return self._recovery_journal.get_recovery_receipt(workspace_id, session_id, command_id)

    def put_recovery_receipt(self, workspace_id: str, receipt: RecoveryReceipt) -> RecoveryReceipt:
        return self._recovery_journal.put_recovery_receipt(workspace_id, receipt)

    def _insert_execution(self, workspace_id: str, execution: DurableToolExecution) -> None:
        self._tool_journal.insert_execution(workspace_id, execution)

    def _insert_session(self, session: DurableSession) -> None:
        self._executor_or_raise().execute(
            f"INSERT INTO sessions({_SESSION_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.workspace_id,
                session.lifecycle.value,
                session.health.value,
                session.current_task_run_id,
                session.conversation_position,
                session.parent_session_id,
                session.parent_cut_record_id,
                session.parent_cut_position,
                session.parent_checkpoint_id,
                session.fork_reason,
                _unix(session.created_at),
                _unix(session.updated_at),
            ),
        )

    def _validate_session_lineage(self, session: DurableSession) -> None:
        if session.parent_session_id is None:
            return
        parent = self.get_session(session.workspace_id, session.parent_session_id)
        if parent is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "fork parent Session is missing")
        if parent.lifecycle is SessionLifecycle.DELETED:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "deleted Session cannot be forked")
        if session.current_task_run_id is not None:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "forked Session cannot have a TaskRun")
        parent_records = self.load_effective_records(session.workspace_id, parent.session_id)
        cut = parent_records[: session.parent_cut_position]
        if len(cut) != session.parent_cut_position:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "fork cut is outside the parent history"
            )
        cut_record = cut[-1]
        if cut_record.record_id != session.parent_cut_record_id or cut_record.kind != "terminal":
            raise StorageError(StorageErrorCode.UNAVAILABLE, "fork cut must end at a closed Turn")
        if session.parent_checkpoint_id is not None:
            checkpoint = self.get_context_checkpoint(
                session.workspace_id, session.parent_checkpoint_id
            )
            if checkpoint is None or checkpoint.session_id != parent.session_id:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "fork checkpoint is invalid")
            if (
                checkpoint.source_end_position != session.parent_cut_position + 1
                or checkpoint.source_end_record_id != session.parent_cut_record_id
            ):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "fork checkpoint cut is invalid")

    def create_fork_session(
        self, session: DurableSession, *, lineage: SessionLineage
    ) -> DurableSession:
        """Create a child Session after validating the immutable lineage contract."""

        if (
            lineage.workspace_id != session.workspace_id
            or lineage.child_session_id != session.session_id
            or lineage.parent_session_id != session.parent_session_id
            or lineage.cut_record_id != session.parent_cut_record_id
            or lineage.cut_position != session.parent_cut_position
            or lineage.checkpoint_id != session.parent_checkpoint_id
        ):
            raise StorageError(StorageErrorCode.UNAVAILABLE, "fork Session lineage is inconsistent")
        if session.fork_reason != lineage.reason:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "fork Session reason is inconsistent")
        return self.create_session(session)

    def _insert_task(self, task: DurableTaskRun) -> None:
        self._task_journal.insert(task)

    def _executor_or_raise(self) -> SqliteExecutor:
        return self._backend.executor()

    def _session_mutation_time(
        self,
        session: DurableSession,
        *,
        requested: datetime | None = None,
    ) -> datetime:
        """Return one strictly monotonic Session token per outer transaction.

        Session timestamps are stored as whole Unix seconds. Advancing by at least one second keeps
        optimistic concurrency reliable even when multiple commands use the same injected clock
        value or occur within one wall-clock second. Nested journal calls in one transaction share
        the same token so a turn commit remains one atomic Session mutation.
        """

        return self._backend.session_mutation_time(
            session,
            requested=requested,
            load_current=lambda: self.get_session(session.workspace_id, session.session_id),
        )

    def _read_one(self, sql: str, parameters: tuple[object, ...]) -> tuple[object, ...] | None:
        return self._backend.read_one(sql, parameters)

    def _read_all(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> tuple[tuple[object, ...], ...]:
        return self._backend.read_all(sql, parameters)


def _session_from_row(row: tuple[object, ...]) -> DurableSession:
    return DurableSession(
        session_id=str(row[0]),
        workspace_id=str(row[1]),
        lifecycle=SessionLifecycle(str(row[2])),
        health=SessionHealth(str(row[3])),
        current_task_run_id=str(row[4]) if row[4] is not None else None,
        conversation_position=int(row[5]),
        parent_session_id=str(row[6]) if row[6] is not None else None,
        parent_cut_record_id=str(row[7]) if row[7] is not None else None,
        parent_cut_position=int(row[8]) if row[8] is not None else None,
        parent_checkpoint_id=str(row[9]) if row[9] is not None else None,
        fork_reason=str(row[10]) if row[10] is not None else None,
        created_at=_from_unix(row[11]),
        updated_at=_from_unix(row[12]),
    )


def _session_lineage_fields(session: DurableSession) -> tuple[object, ...]:
    return (
        session.parent_session_id,
        session.parent_cut_record_id,
        session.parent_cut_position,
        session.parent_checkpoint_id,
        session.fork_reason,
    )
