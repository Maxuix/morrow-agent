"""The only Candidate→Active dispatcher for Stage 5 Learning."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from morrow.application.api_context import ApplicationCommandContext
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import (
    LEARNING_MAX_REFERENCE_IDS,
    LearningCandidate,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningResolutionActor,
    LearningScope,
    LearningSensitivity,
)
from morrow.core.learning_commands import (
    AcceptLearningCandidateCommand,
    EditAndAcceptLearningCandidateCommand,
)
from morrow.core.learning_memory import (
    LearningCandidateDecision,
    LearningCandidateDecisionKind,
    LearningConflictResolution,
    ProjectKnowledgeEvidenceLink,
    ProjectKnowledgeHead,
    ProjectKnowledgeRevision,
    ProjectKnowledgeStatus,
)
from morrow.core.learning_payloads import CandidatePayload, ProjectKnowledgeCandidatePayload
from morrow.core.models import ProtocolModel


class LearningPromotionResult(ProtocolModel):
    candidate: LearningCandidate
    decision: LearningCandidateDecision
    outcome: Literal["activated", "confirmed", "superseded", "enabled", "candidate_only"]
    knowledge_id: str | None = None
    knowledge_revision_id: str | None = None
    revision: int | None = None
    memory_revision: int | None = None


def _now(context: ApplicationCommandContext) -> datetime:
    value = context.clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LearningPromotionService:
    """Promote only governed Project Knowledge or acknowledge future candidates."""

    def __init__(self, context: ApplicationCommandContext) -> None:
        self.context = context

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    def accept_candidate(
        self, command: AcceptLearningCandidateCommand
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        return self._accept(command, edit=False)

    def edit_and_accept_candidate(
        self, command: EditAndAcceptLearningCandidateCommand
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        return self._accept(command, edit=True)

    def _accept(self, command, *, edit: bool):
        self._assert_workspace(command.workspace_id)
        operation = "learning_candidate_edit_and_accept" if edit else "learning_candidate_accept"
        payload = {
            "candidate_id": command.candidate_id,
            "expected_row_version": command.expected_row_version,
            "scope": command.scope,
            "conflict_resolution": command.conflict_resolution.value,
        }
        if edit:
            payload["final_payload"] = command.final_payload.model_dump(mode="json")
        command_id, digest, replay = self.context._prepare(operation, payload, command.command_id)
        if replay is not None:
            return ApplicationCommandResult(self._load_result(replay.result_id), replay)
        if self._expire_due_candidate(command.candidate_id, origin_command_id=command_id):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Learning Candidate has expired")

        def work(txn):
            existing = self.context._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(self._load_result(existing.result_id), existing)
            candidate = txn.get_learning_candidate(self.workspace_id, command.candidate_id)
            if candidate is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Learning Candidate is missing"
                )
            if candidate.row_version != command.expected_row_version:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Learning Candidate row is stale"
                )
            if candidate.status is not LearningCandidateStatus.PROPOSED:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "only a proposed Learning Candidate can be accepted",
                )
            stamp = _now(self.context)
            if candidate.expires_at <= stamp:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Learning Candidate has expired"
                )
            if candidate.sensitivity is LearningSensitivity.PROHIBITED:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "prohibited Learning Candidate cannot be accepted"
                )
            evidence = txn.list_learning_candidate_evidence(
                self.workspace_id, candidate.candidate_id
            )[:LEARNING_MAX_REFERENCE_IDS]
            if (
                len(evidence) != len(candidate.evidence_ids)
                or {item.evidence_id for item in evidence} != set(candidate.evidence_ids)
                or any(
                    item.safety_rejection_code is not None or item.workspace_id != self.workspace_id
                    for item in evidence
                )
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "Learning Candidate evidence is not eligible for promotion",
                )
            final_payload = command.final_payload if edit else candidate.proposed_payload
            if final_payload.candidate_type is not candidate.candidate_type:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "edited Learning Candidate payload type does not match candidate",
                )
            if (
                not edit
                and isinstance(final_payload, ProjectKnowledgeCandidatePayload)
                and final_payload.semantic_key != candidate.semantic_key
            ):
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Project Knowledge candidate semantic key does not match payload",
                )
            scope = self._scope(command.scope or candidate.proposed_scope.value)
            self._validate_scope(candidate.candidate_type, scope)
            semantic_key = self._semantic_key(candidate, final_payload)
            self._assert_not_suppressed(txn, candidate, semantic_key, stamp)
            decision_kind = (
                LearningCandidateDecisionKind.EDIT_AND_ACCEPT
                if edit
                else LearningCandidateDecisionKind.ACCEPT
            )
            effective_resolution = self._effective_conflict_resolution(
                txn,
                candidate=candidate,
                payload=final_payload,
                semantic_key=semantic_key,
                requested=command.conflict_resolution,
            )
            decision = self._decision(
                command_id=command_id,
                candidate=candidate,
                kind=decision_kind,
                scope=scope,
                final_payload=final_payload if edit else None,
                conflict_resolution=effective_resolution,
                created_at=stamp,
            )
            txn.put_learning_candidate_decision(self.workspace_id, decision)
            if candidate.candidate_type in {
                LearningCandidateType.PREFERENCE,
                LearningCandidateType.PROFILE,
            }:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE,
                    "Preference/Profile promotion is reserved for Subplan 52",
                )
            if candidate.candidate_type is LearningCandidateType.PROJECT_KNOWLEDGE:
                knowledge = self._promote_project_knowledge(
                    txn,
                    candidate=candidate,
                    decision=decision,
                    payload=final_payload,
                    semantic_key=semantic_key,
                    scope=scope,
                    stamp=stamp,
                )
            elif candidate.candidate_type in {
                LearningCandidateType.SKILL_CANDIDATE,
                LearningCandidateType.WORKFLOW_FEEDBACK,
                LearningCandidateType.ORCHESTRATION_POLICY_CANDIDATE,
            }:
                knowledge = self._acknowledge_candidate_only(
                    txn,
                    candidate=candidate,
                    decision=decision,
                    stamp=stamp,
                )
            else:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Learning Candidate type is unavailable"
                )
            updated = txn.save_learning_candidate(
                self.workspace_id,
                candidate.model_copy(
                    update={
                        "status": (
                            LearningCandidateStatus.EDITED_AND_ACCEPTED
                            if edit
                            else LearningCandidateStatus.ACCEPTED
                        ),
                        "row_version": candidate.row_version + 1,
                        "resolved_at": stamp,
                        "resolved_by": LearningResolutionActor.USER,
                    }
                ),
                expected_row_version=candidate.row_version,
            )
            result = LearningPromotionResult(
                candidate=updated,
                decision=decision,
                outcome=knowledge[0],
                knowledge_id=knowledge[1],
                knowledge_revision_id=knowledge[2],
                revision=knowledge[3],
                memory_revision=knowledge[4],
            )
            event = self.context._event(
                txn,
                event_type=(
                    "learning.candidate_edited_and_accepted"
                    if edit
                    else "learning.candidate_accepted"
                ),
                aggregate_kind="learning_candidate",
                aggregate_id=updated.candidate_id,
                payload={
                    "candidate_type": updated.candidate_type.value,
                    "status": updated.status.value,
                    "row_version": updated.row_version,
                    "decision_kind": decision.kind.value,
                    "outcome": result.outcome,
                },
            )
            if knowledge[5] is not None:
                event = self.context._event(
                    txn,
                    event_type=knowledge[5],
                    aggregate_kind="project_knowledge",
                    aggregate_id=knowledge[1] or updated.candidate_id,
                    payload={
                        "category": (
                            final_payload.category.value
                            if isinstance(final_payload, ProjectKnowledgeCandidatePayload)
                            else updated.candidate_type.value
                        ),
                        "knowledge_id": knowledge[1],
                        "revision": knowledge[3],
                        "row_version": updated.row_version,
                        "memory_revision": knowledge[4],
                        "semantic_key_digest": sha256_digest(semantic_key),
                    },
                )
            receipt = self.context._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=None,
                result_kind="learning_promotion",
                result_id=self._result_ref(result),
                row_version=updated.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(result, receipt)

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _expire_due_candidate(self, candidate_id: str, *, origin_command_id: str) -> bool:
        """Commit lazy expiry before a promotion attempt can inspect a due candidate."""

        def work(txn):
            candidate = txn.get_learning_candidate(self.workspace_id, candidate_id)
            if candidate is None or candidate.status is not LearningCandidateStatus.PROPOSED:
                return False
            stamp = _now(self.context)
            if candidate.expires_at > stamp:
                return False
            decision_command_id = (
                "cmd_"
                + sha256_digest(
                    canonical_json_bytes(["lazy-expiry", origin_command_id, candidate_id])
                )[:48]
            )
            decision = LearningCandidateDecision(
                decision_id=self.context.id_source.new_id("lcd"),
                workspace_id=self.workspace_id,
                candidate_id=candidate_id,
                kind=LearningCandidateDecisionKind.EXPIRE,
                actor=LearningResolutionActor.POLICY,
                original_proposal_digest=candidate.fingerprint,
                scope=candidate.proposed_scope,
                command_id=decision_command_id,
                created_at=stamp,
            )
            txn.put_learning_candidate_decision(self.workspace_id, decision)
            updated = txn.save_learning_candidate(
                self.workspace_id,
                candidate.model_copy(
                    update={
                        "status": LearningCandidateStatus.EXPIRED,
                        "row_version": candidate.row_version + 1,
                        "resolved_at": stamp,
                        "resolved_by": LearningResolutionActor.POLICY,
                    }
                ),
                expected_row_version=candidate.row_version,
            )
            self.context._event(
                txn,
                event_type="learning.candidate_expired",
                aggregate_kind="learning_candidate",
                aggregate_id=updated.candidate_id,
                payload={
                    "candidate_type": updated.candidate_type.value,
                    "status": updated.status.value,
                    "row_version": updated.row_version,
                    "reason_code": "expired_before_promotion",
                },
            )
            return True

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _promote_project_knowledge(
        self,
        txn,
        *,
        candidate: LearningCandidate,
        decision: LearningCandidateDecision,
        payload: CandidatePayload,
        semantic_key: str,
        scope: LearningScope,
        stamp: datetime,
    ) -> tuple[str, str | None, str | None, int | None, int | None, str | None]:
        if not isinstance(payload, ProjectKnowledgeCandidatePayload):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Project Knowledge payload is invalid"
            )
        head = txn.get_project_knowledge_head_by_key(self.workspace_id, semantic_key)
        current = None
        if head is not None:
            if head.category is not payload.category:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "knowledge category conflicts with current head"
                )
            if head.current_revision_id is not None:
                current = txn.get_project_knowledge_revision(
                    self.workspace_id, head.current_revision_id
                )
                if current is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY, "knowledge current revision is missing"
                    )
        if head is None:
            head = ProjectKnowledgeHead(
                knowledge_id=self.context.id_source.new_id("knw"),
                workspace_id=self.workspace_id,
                semantic_key=semantic_key,
                category=payload.category,
                status=ProjectKnowledgeStatus.ACTIVE,
                created_at=stamp,
                updated_at=stamp,
            )
            txn.put_project_knowledge_head(self.workspace_id, head)
            revision = self._new_revision(
                candidate=candidate,
                decision=decision,
                head=head,
                payload=payload,
                revision_number=1,
                supersedes=None,
                stamp=stamp,
            )
            txn.put_project_knowledge_revision(self.workspace_id, revision)
            head = txn.save_project_knowledge_head(
                self.workspace_id,
                head.model_copy(
                    update={"current_revision_id": revision.knowledge_revision_id, "row_version": 2}
                ),
                expected_row_version=1,
            )
            outcome: Literal[
                "activated", "confirmed", "superseded", "enabled", "candidate_only"
            ] = "activated"
            memory_event = "memory.record_activated"
        else:
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "knowledge head has no current revision"
                )
            if head.status is ProjectKnowledgeStatus.DELETED:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "deleted knowledge requires a dedicated resolution",
                )
            if (
                head.status is ProjectKnowledgeStatus.ACTIVE
                and current.statement == payload.statement
            ):
                revision = txn.confirm_project_knowledge_revision(
                    self.workspace_id, current.knowledge_revision_id, confirmed_at=stamp
                )
                outcome = "confirmed"
                memory_event = "memory.record_confirmed"
                head = txn.save_project_knowledge_head(
                    self.workspace_id,
                    head.model_copy(
                        update={"row_version": head.row_version + 1, "updated_at": stamp}
                    ),
                    expected_row_version=head.row_version,
                )
            else:
                resolution = decision.conflict_resolution
                previous_status = head.status
                if head.status is ProjectKnowledgeStatus.ACTIVE:
                    allowed = resolution in {
                        LearningConflictResolution.REPLACE,
                        LearningConflictResolution.MERGE,
                    }
                elif head.status is ProjectKnowledgeStatus.DISABLED:
                    allowed = resolution is LearningConflictResolution.RE_ENABLE
                elif head.status is ProjectKnowledgeStatus.DISPUTED:
                    allowed = resolution is LearningConflictResolution.RESOLVE_DISPUTE
                else:
                    allowed = False
                if not allowed:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "knowledge conflict requires explicit resolution",
                    )
                revision = self._new_revision(
                    candidate=candidate,
                    decision=decision,
                    head=head,
                    payload=payload,
                    revision_number=current.revision + 1,
                    supersedes=current.knowledge_revision_id,
                    stamp=stamp,
                )
                txn.put_project_knowledge_revision(self.workspace_id, revision)
                next_status = ProjectKnowledgeStatus.ACTIVE
                head = txn.save_project_knowledge_head(
                    self.workspace_id,
                    head.model_copy(
                        update={
                            "status": next_status,
                            "current_revision_id": revision.knowledge_revision_id,
                            "row_version": head.row_version + 1,
                            "updated_at": stamp,
                        }
                    ),
                    expected_row_version=head.row_version,
                )
                outcome = (
                    "enabled"
                    if previous_status is ProjectKnowledgeStatus.DISABLED
                    else "superseded"
                )
                memory_event = (
                    "memory.record_enabled" if outcome == "enabled" else "memory.record_superseded"
                )
        for evidence in txn.list_learning_candidate_evidence(
            self.workspace_id, candidate.candidate_id
        )[:LEARNING_MAX_REFERENCE_IDS]:
            txn.put_project_knowledge_evidence(
                self.workspace_id,
                self._evidence_link(revision.knowledge_revision_id, evidence.evidence_id),
            )
        memory_state = txn.ensure_memory_workspace_state(self.workspace_id)
        next_memory = memory_state.model_copy(
            update={
                "memory_revision": memory_state.memory_revision + 1,
                "row_version": memory_state.row_version + 1,
                "updated_at": stamp,
            }
        )
        saved_memory = txn.save_memory_workspace_state(
            self.workspace_id,
            next_memory,
            expected_row_version=memory_state.row_version,
        )
        return (
            outcome,
            head.knowledge_id,
            revision.knowledge_revision_id,
            revision.revision,
            saved_memory.memory_revision,
            memory_event,
        )

    def _acknowledge_candidate_only(
        self,
        txn,
        *,
        candidate: LearningCandidate,
        decision: LearningCandidateDecision,
        stamp: datetime,
    ) -> tuple[str, None, None, None, None, None]:
        return ("candidate_only", None, None, None, None, None)

    def _new_revision(
        self,
        *,
        candidate: LearningCandidate,
        decision: LearningCandidateDecision,
        head: ProjectKnowledgeHead,
        payload: ProjectKnowledgeCandidatePayload,
        revision_number: int,
        supersedes: str | None,
        stamp: datetime,
    ) -> ProjectKnowledgeRevision:
        return ProjectKnowledgeRevision(
            knowledge_revision_id=self.context.id_source.new_id("krv"),
            knowledge_id=head.knowledge_id,
            workspace_id=self.workspace_id,
            revision=revision_number,
            statement=payload.statement,
            statement_digest=ProjectKnowledgeRevision.digest_for(payload.statement),
            source_candidate_id=candidate.candidate_id,
            source_decision_id=decision.decision_id,
            supersedes_revision_id=supersedes,
            sensitivity=candidate.sensitivity,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            created_at=stamp,
            last_confirmed_at=stamp,
        )

    def _decision(
        self,
        *,
        command_id: str,
        candidate: LearningCandidate,
        kind: LearningCandidateDecisionKind,
        scope: LearningScope,
        final_payload: CandidatePayload | None,
        conflict_resolution: LearningConflictResolution,
        created_at: datetime,
    ) -> LearningCandidateDecision:
        final_json = None
        final_bytes = None
        if final_payload is not None:
            encoded = canonical_json_bytes(final_payload.model_dump(mode="json"))
            final_json = encoded.decode("utf-8")
            final_bytes = len(encoded)
        return LearningCandidateDecision(
            decision_id=self.context.id_source.new_id("lcd"),
            workspace_id=self.workspace_id,
            candidate_id=candidate.candidate_id,
            kind=kind,
            actor=LearningResolutionActor.USER,
            original_proposal_digest=candidate.fingerprint,
            final_proposal_json=final_json,
            final_proposal_bytes=final_bytes,
            scope=scope,
            conflict_resolution=conflict_resolution,
            command_id=command_id,
            created_at=created_at,
        )

    def _evidence_link(self, revision_id: str, evidence_id: str):
        return ProjectKnowledgeEvidenceLink(
            workspace_id=self.workspace_id,
            knowledge_revision_id=revision_id,
            evidence_id=evidence_id,
        )

    def _effective_conflict_resolution(
        self,
        txn,
        *,
        candidate: LearningCandidate,
        payload: CandidatePayload,
        semantic_key: str,
        requested: LearningConflictResolution,
    ) -> LearningConflictResolution:
        if not isinstance(payload, ProjectKnowledgeCandidatePayload):
            return requested
        head = txn.get_project_knowledge_head_by_key(self.workspace_id, semantic_key)
        if head is None or head.current_revision_id is None:
            return requested
        current = txn.get_project_knowledge_revision(self.workspace_id, head.current_revision_id)
        if current is not None and head.status is ProjectKnowledgeStatus.ACTIVE:
            if current.statement == payload.statement:
                return LearningConflictResolution.CONFIRM
        return requested

    def _assert_not_suppressed(self, txn, candidate, semantic_key: str, stamp: datetime) -> None:
        suppressions = txn.list_learning_suppressions(
            self.workspace_id,
            candidate_type=candidate.candidate_type.value,
            scope=candidate.proposed_scope,
            semantic_key=semantic_key,
            limit=LEARNING_MAX_REFERENCE_IDS,
        ) + txn.list_learning_suppressions(
            self.workspace_id,
            candidate_type=candidate.candidate_type.value,
            scope=candidate.proposed_scope,
            fingerprint=candidate.fingerprint,
            limit=LEARNING_MAX_REFERENCE_IDS,
        )
        if any(
            item.status.value == "active" and (item.expires_at is None or item.expires_at > stamp)
            for item in suppressions
        ):
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Learning Candidate is suppressed"
            )

    @staticmethod
    def _semantic_key(candidate: LearningCandidate, payload: CandidatePayload) -> str:
        return (
            payload.semantic_key
            if isinstance(payload, ProjectKnowledgeCandidatePayload)
            else candidate.semantic_key
        )

    @staticmethod
    def _scope(value: str) -> LearningScope:
        try:
            return LearningScope(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Learning scope is invalid"
            ) from exc

    @staticmethod
    def _validate_scope(candidate_type: LearningCandidateType, scope: LearningScope) -> None:
        if candidate_type is LearningCandidateType.PREFERENCE:
            allowed = {LearningScope.GLOBAL, LearningScope.WORKSPACE}
        else:
            allowed = {LearningScope.WORKSPACE}
        if scope not in allowed:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Learning scope is invalid for candidate type"
            )

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE, "Learning command is outside the workspace"
            )

    @staticmethod
    def _result_ref(result: LearningPromotionResult) -> str:
        return canonical_json_bytes(
            {
                "decision_id": result.decision.decision_id,
                "knowledge_id": result.knowledge_id,
                "knowledge_revision_id": result.knowledge_revision_id,
                "revision": result.revision,
                "memory_revision": result.memory_revision,
                "outcome": result.outcome,
            }
        ).decode("utf-8")

    def _load_result(self, result_id: str | None) -> LearningPromotionResult:
        if not result_id:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning promotion result is missing"
            )
        try:
            reference = json.loads(result_id)
            if not isinstance(reference, dict):
                raise ValueError
            decision_id = reference["decision_id"]
            decision = self.context._query(
                lambda: self.context.journal.get_learning_candidate_decision(
                    self.workspace_id, decision_id
                )
            )
            if decision is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "Learning promotion decision is missing"
                )
            candidate = self.context._query(
                lambda: self.context.journal.get_learning_candidate(
                    self.workspace_id, decision.candidate_id
                )
            )
            if candidate is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "Learning promotion candidate is missing"
                )
            return LearningPromotionResult(
                candidate=candidate,
                decision=decision,
                outcome=reference["outcome"],
                knowledge_id=reference.get("knowledge_id"),
                knowledge_revision_id=reference.get("knowledge_revision_id"),
                revision=reference.get("revision"),
                memory_revision=reference.get("memory_revision"),
            )
        except ApplicationError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning promotion result is invalid"
            ) from exc


__all__ = ["LearningPromotionResult", "LearningPromotionService"]
