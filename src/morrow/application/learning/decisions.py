"""Replay-safe rejection, suppression, and foreground candidate expiry."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from morrow.application.api_context import ApplicationCommandContext
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import (
    LEARNING_CANDIDATE_ID_PREFIX,
    LEARNING_SUPPRESSION_ID_PREFIX,
    LearningCandidate,
    LearningCandidateStatus,
    LearningResolutionActor,
    LearningSuppression,
    LearningSuppressionStatus,
    validate_prefixed_id,
)
from morrow.core.learning_commands import (
    ExpireLearningCandidatesCommand,
    RejectLearningCandidateCommand,
)
from morrow.core.learning_memory import LearningCandidateDecision, LearningCandidateDecisionKind
from morrow.core.models import ProtocolModel


class LearningCandidateDecisionResult(ProtocolModel):
    candidate: LearningCandidate
    decision: LearningCandidateDecision
    suppression: LearningSuppression | None = None


class LearningExpiryResult(ProtocolModel):
    candidate_ids: tuple[str, ...] = ()

    @classmethod
    def from_ids(cls, candidate_ids: tuple[str, ...]) -> LearningExpiryResult:
        for candidate_id in candidate_ids:
            validate_prefixed_id(candidate_id, LEARNING_CANDIDATE_ID_PREFIX)
        return cls(candidate_ids=candidate_ids)


def _now(context: ApplicationCommandContext) -> datetime:
    value = context.clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LearningDecisionService:
    """Own Inbox decisions while leaving Candidate→Active promotion elsewhere."""

    def __init__(self, context: ApplicationCommandContext) -> None:
        self.context = context

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    def reject_candidate(
        self, command: RejectLearningCandidateCommand
    ) -> ApplicationCommandResult[LearningCandidateDecisionResult]:
        self._assert_workspace(command.workspace_id)
        operation = "learning_candidate_reject"
        payload = {
            "candidate_id": command.candidate_id,
            "expected_row_version": command.expected_row_version,
            "never_suggest": command.never_suggest,
            "reason": command.reason,
        }
        command_id, digest, replay = self.context._prepare(operation, payload, command.command_id)
        if replay is not None:
            return ApplicationCommandResult(self._load_rejection(replay.result_id), replay)

        def work(txn):
            existing = self.context._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(self._load_rejection(existing.result_id), existing)
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
                    "only a proposed Learning Candidate can be rejected",
                )
            stamp = _now(self.context)
            if candidate.expires_at <= stamp:
                decision = LearningCandidateDecision(
                    decision_id=self.context.id_source.new_id("lcd"),
                    workspace_id=self.workspace_id,
                    candidate_id=candidate.candidate_id,
                    kind=LearningCandidateDecisionKind.EXPIRE,
                    actor=LearningResolutionActor.POLICY,
                    original_proposal_digest=candidate.fingerprint,
                    scope=candidate.proposed_scope,
                    command_id=command_id,
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
                event = self.context._event(
                    txn,
                    event_type="learning.candidate_expired",
                    aggregate_kind="learning_candidate",
                    aggregate_id=updated.candidate_id,
                    payload={
                        "candidate_type": updated.candidate_type.value,
                        "status": updated.status.value,
                        "row_version": updated.row_version,
                        "reason_code": "expired_before_rejection",
                    },
                )
                value = LearningCandidateDecisionResult(
                    candidate=updated,
                    decision=decision,
                )
                receipt = self.context._receipt(
                    txn,
                    command_id=command_id,
                    operation=operation,
                    digest=digest,
                    session_id=None,
                    result_kind="learning_candidate_decision",
                    result_id=decision.decision_id,
                    row_version=updated.row_version,
                    event_cursor=event.cursor,
                )
                return ApplicationCommandResult(value, receipt)
            kind = (
                LearningCandidateDecisionKind.REJECT_AND_SUPPRESS
                if command.never_suggest
                else LearningCandidateDecisionKind.REJECT
            )
            decision = LearningCandidateDecision(
                decision_id=self.context.id_source.new_id("lcd"),
                workspace_id=self.workspace_id,
                candidate_id=candidate.candidate_id,
                kind=kind,
                actor=LearningResolutionActor.USER,
                original_proposal_digest=candidate.fingerprint,
                scope=candidate.proposed_scope,
                command_id=command_id,
                created_at=stamp,
            )
            txn.put_learning_candidate_decision(self.workspace_id, decision)
            updated = candidate.model_copy(
                update={
                    "status": LearningCandidateStatus.REJECTED,
                    "row_version": candidate.row_version + 1,
                    "resolved_at": stamp,
                    "resolved_by": LearningResolutionActor.USER,
                    "rejection_reason": command.reason,
                }
            )
            updated = txn.save_learning_candidate(
                self.workspace_id,
                updated,
                expected_row_version=candidate.row_version,
            )
            suppression = None
            if command.never_suggest:
                suppression = txn.put_learning_suppression(
                    self.workspace_id,
                    LearningSuppression(
                        suppression_id=self.context.id_source.new_id(
                            LEARNING_SUPPRESSION_ID_PREFIX
                        ),
                        workspace_id=self.workspace_id,
                        candidate_type=candidate.candidate_type,
                        scope=candidate.proposed_scope,
                        semantic_key=candidate.semantic_key,
                        fingerprint=candidate.fingerprint,
                        source_candidate_id=candidate.candidate_id,
                        reason="user_never_suggested",
                        status=LearningSuppressionStatus.ACTIVE,
                        created_at=stamp,
                        updated_at=stamp,
                    ),
                )
            event = self.context._event(
                txn,
                event_type="learning.candidate_rejected",
                aggregate_kind="learning_candidate",
                aggregate_id=updated.candidate_id,
                payload={
                    "candidate_type": updated.candidate_type.value,
                    "status": updated.status.value,
                    "row_version": updated.row_version,
                    "decision_kind": decision.kind.value,
                    "reason_code": "never_suggested" if command.never_suggest else "user_rejected",
                },
            )
            if suppression is not None:
                event = self.context._event(
                    txn,
                    event_type="learning.suppression_created",
                    aggregate_kind="learning_suppression",
                    aggregate_id=suppression.suppression_id,
                    payload={
                        "candidate_type": suppression.candidate_type.value,
                        "scope": suppression.scope.value,
                        "status": suppression.status.value,
                        "row_version": suppression.row_version,
                    },
                )
            value = LearningCandidateDecisionResult(
                candidate=updated,
                decision=decision,
                suppression=suppression,
            )
            receipt = self.context._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=None,
                result_kind="learning_candidate_decision",
                result_id=decision.decision_id,
                row_version=updated.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self.context._translate(lambda: self.context.journal.transact(work))

    def expire_candidates(
        self, command: ExpireLearningCandidatesCommand
    ) -> ApplicationCommandResult[LearningExpiryResult]:
        self._assert_workspace(command.workspace_id)
        operation = "learning_candidate_expire"
        payload = {
            "cutoff": command.cutoff.isoformat(),
            "limit": command.limit,
        }
        command_id, digest, replay = self.context._prepare(operation, payload, command.command_id)
        if replay is not None:
            return ApplicationCommandResult(self._load_expiry(replay.result_id), replay)

        def work(txn):
            existing = self.context._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(self._load_expiry(existing.result_id), existing)
            candidates = txn.list_learning_candidates(
                self.workspace_id,
                status=LearningCandidateStatus.PROPOSED,
                expires_before=command.cutoff,
                limit=command.limit,
            )
            stamp = _now(self.context)
            expired_ids: list[str] = []
            last_event = None
            for candidate in candidates:
                if candidate.expires_at > command.cutoff:
                    continue
                decision = LearningCandidateDecision(
                    decision_id=self.context.id_source.new_id("lcd"),
                    workspace_id=self.workspace_id,
                    candidate_id=candidate.candidate_id,
                    kind=LearningCandidateDecisionKind.EXPIRE,
                    actor=LearningResolutionActor.POLICY,
                    original_proposal_digest=candidate.fingerprint,
                    scope=candidate.proposed_scope,
                    command_id=self._derived_decision_command(command_id, candidate.candidate_id),
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
                expired_ids.append(updated.candidate_id)
                last_event = self.context._event(
                    txn,
                    event_type="learning.candidate_expired",
                    aggregate_kind="learning_candidate",
                    aggregate_id=updated.candidate_id,
                    payload={
                        "candidate_type": updated.candidate_type.value,
                        "status": updated.status.value,
                        "row_version": updated.row_version,
                        "reason_code": "expired_by_cutoff",
                    },
                )
            if last_event is None:
                last_event = self.context._event(
                    txn,
                    event_type="learning.candidate_expired",
                    aggregate_kind="learning_workspace",
                    aggregate_id=self.workspace_id,
                    payload={"count": 0, "reason_code": "no_due_candidates"},
                )
            value = LearningExpiryResult.from_ids(tuple(expired_ids))
            receipt = self.context._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=None,
                result_kind="learning_candidate_expiry",
                result_id=canonical_json_bytes(expired_ids).decode("utf-8"),
                event_cursor=last_event.cursor,
            )
            return ApplicationCommandResult(value, receipt)

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _load_rejection(self, decision_id: str | None) -> LearningCandidateDecisionResult:
        if not decision_id:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning decision result is missing"
            )
        decision = self.context._query(
            lambda: self.context.journal.get_learning_candidate_decision(
                self.workspace_id, decision_id
            )
        )
        if decision is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning decision is missing"
            )
        candidate = self.context._query(
            lambda: self.context.journal.get_learning_candidate(
                self.workspace_id, decision.candidate_id
            )
        )
        if candidate is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning Candidate result is missing"
            )
        suppression = None
        if decision.kind is LearningCandidateDecisionKind.REJECT_AND_SUPPRESS:
            suppressions = self.context._query(
                lambda: self.context.journal.list_learning_suppressions(
                    self.workspace_id,
                    candidate_type=candidate.candidate_type.value,
                    scope=candidate.proposed_scope,
                    fingerprint=candidate.fingerprint,
                    limit=16,
                )
            )
            suppression = next(
                (
                    item
                    for item in suppressions
                    if item.source_candidate_id == candidate.candidate_id
                ),
                None,
            )
        return LearningCandidateDecisionResult(
            candidate=candidate,
            decision=decision,
            suppression=suppression,
        )

    def _load_expiry(self, result_id: str | None) -> LearningExpiryResult:
        if not result_id:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning expiry result is missing"
            )
        try:
            values = json.loads(result_id)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError
            return LearningExpiryResult.from_ids(tuple(values))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Learning expiry result is invalid"
            ) from exc

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE, "Learning command is outside the workspace"
            )

    @staticmethod
    def _derived_decision_command(command_id: str, candidate_id: str) -> str:
        return "cmd_" + sha256_digest(canonical_json_bytes([command_id, candidate_id]))[:48]


__all__ = [
    "LearningCandidateDecisionResult",
    "LearningDecisionService",
    "LearningExpiryResult",
]
