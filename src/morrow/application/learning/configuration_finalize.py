"""SQLite finalization transaction for configuration promotion."""

from __future__ import annotations

from morrow.application.learning.configuration_promotion_support import (
    NeedsResolution,
    promotion_now,
)
from morrow.application.learning.results import LearningPromotionResult
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.configuration_promotion import (
    ConfigurationActivation,
    ConfigurationActivationStatus,
    PromotionFailureCode,
    PromotionOperation,
    PromotionOperationState,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import LearningCandidateStatus, LearningResolutionActor
from morrow.core.learning_memory import LearningCandidateDecision, LearningCandidateDecisionKind


class ConfigurationPromotionFinalizeMixin:
    """Commit candidate decision, activation, events, and receipt atomically."""

    def _finalize(
        self,
        operation: PromotionOperation,
        prepared,
        *,
        applied_revision: int,
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        stamp = promotion_now(self.context)

        def work(txn):
            current_operation = txn.get_promotion_operation(
                self.workspace_id, operation.operation_id
            )
            if current_operation is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 Saga 不存在")
            if current_operation.state is PromotionOperationState.FINALIZED:
                return ApplicationCommandResult(
                    self._load_result_for_operation(current_operation, txn=txn), None
                )
            if current_operation.state not in {
                PromotionOperationState.PREPARED,
                PromotionOperationState.NEEDS_RESOLUTION,
            }:
                raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置 Saga 需要恢复")
            candidate = txn.get_learning_candidate(self.workspace_id, operation.candidate_id)
            if candidate is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "Learning Candidate 丢失"
                )
            if candidate.status is not LearningCandidateStatus.PROMOTING:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Learning Candidate 状态已变化"
                )
            if candidate.row_version != operation.candidate_row_version + 1:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Learning Candidate 行版本已变化"
                )
            try:
                current_revision, _presence, current_digest = self.config_service.current_state(  # type: ignore[union-attr]
                    prepared.command
                )
            except Exception as exc:
                self._save_needs_resolution_in_txn(
                    txn, current_operation, PromotionFailureCode.NEEDS_RECOVERY, stamp
                )
                return NeedsResolution(self._configuration_error(exc))
            if current_revision != applied_revision or current_digest != prepared.after_digest:
                self._save_needs_resolution_in_txn(
                    txn, current_operation, PromotionFailureCode.CONFLICT, stamp
                )
                return NeedsResolution(
                    ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "配置在最终确认前再次变化，未声明为已接受",
                    )
                )
            evidence = self._valid_evidence(txn, candidate)
            self._validate_explicit_evidence(candidate, evidence)
            edited = self._is_edit_operation(operation, candidate, prepared)
            decision = LearningCandidateDecision(
                decision_id=self.context.id_source.new_id("lcd"),
                workspace_id=self.workspace_id,
                candidate_id=candidate.candidate_id,
                kind=(
                    LearningCandidateDecisionKind.EDIT_AND_ACCEPT
                    if edited
                    else LearningCandidateDecisionKind.ACCEPT
                ),
                actor=LearningResolutionActor.USER,
                original_proposal_digest=candidate.fingerprint,
                final_proposal_json=None,
                scope=operation.scope,
                command_id=operation.command_id,
                created_at=stamp,
            )
            if edited:
                payload = self._payload_for_decision(candidate, prepared)
                encoded_payload = canonical_json_bytes(payload)
                decision = decision.model_copy(
                    update={
                        "kind": LearningCandidateDecisionKind.EDIT_AND_ACCEPT,
                        "final_proposal_json": encoded_payload.decode("utf-8"),
                        "final_proposal_bytes": len(encoded_payload),
                    }
                )
            txn.put_learning_candidate_decision(self.workspace_id, decision)
            supersedes = self._superseded_activation(txn, operation, prepared)
            if supersedes is not None:
                txn.save_configuration_activation(
                    self.workspace_id,
                    supersedes.model_copy(
                        update={
                            "status": ConfigurationActivationStatus.SUPERSEDED,
                            "updated_at": stamp,
                        }
                    ),
                )
            inverse_json = canonical_json_bytes(
                prepared.inverse_command.model_dump(mode="json")
            ).decode("utf-8")
            activation = ConfigurationActivation(
                activation_id=self.context.id_source.new_id("act"),
                workspace_id=self.workspace_id,
                candidate_id=candidate.candidate_id,
                decision_id=decision.decision_id,
                operation_id=operation.operation_id,
                target=prepared.command.target,
                scope=operation.scope,
                path=prepared.command.path or "__document__",
                operation=self._activation_operation(candidate),
                applied_revision=applied_revision,
                before_digest=prepared.before_digest,
                after_digest=prepared.after_digest,
                value_digest=self._value_digest(prepared.command),
                inverse_command_json=inverse_json,
                inverse_command_digest=sha256_digest(
                    canonical_json_bytes(prepared.inverse_command.model_dump(mode="json"))
                ),
                supersedes_activation_id=supersedes.activation_id if supersedes else None,
                status=ConfigurationActivationStatus.ACTIVE,
                created_at=stamp,
                updated_at=stamp,
            )
            txn.put_configuration_activation(self.workspace_id, activation)
            updated = txn.save_learning_candidate(
                self.workspace_id,
                candidate.model_copy(
                    update={
                        "status": LearningCandidateStatus.EDITED_AND_ACCEPTED
                        if edited
                        else LearningCandidateStatus.ACCEPTED,
                        "row_version": candidate.row_version + 1,
                        "resolved_at": stamp,
                        "resolved_by": LearningResolutionActor.USER,
                    }
                ),
                expected_row_version=candidate.row_version,
            )
            event = self.context._event(
                txn,
                event_type="learning.candidate_accepted",
                aggregate_kind="learning_candidate",
                aggregate_id=updated.candidate_id,
                payload={
                    "candidate_type": updated.candidate_type.value,
                    "status": updated.status.value,
                    "row_version": updated.row_version,
                    "decision_kind": decision.kind.value,
                    "outcome": "activated",
                    "target": prepared.command.target,
                    "scope": operation.scope.value,
                    "path": prepared.command.path,
                    "activation_id": activation.activation_id,
                    "applied_revision": applied_revision,
                },
            )
            activation_event = self.context._event(
                txn,
                event_type="configuration.activated",
                aggregate_kind="configuration_activation",
                aggregate_id=activation.activation_id,
                payload={
                    "target": prepared.command.target,
                    "scope": operation.scope.value,
                    "path": prepared.command.path,
                    "activation_id": activation.activation_id,
                    "applied_revision": applied_revision,
                    "before_digest": prepared.before_digest,
                    "after_digest": prepared.after_digest,
                },
            )
            finalized = current_operation.model_copy(
                update={
                    "state": PromotionOperationState.FINALIZED,
                    "applied_revision": applied_revision,
                    "row_version": current_operation.row_version + 1,
                    "updated_at": stamp,
                    "finalized_at": stamp,
                    "failure_code": None,
                }
            )
            txn.save_promotion_operation(
                self.workspace_id,
                finalized,
                expected_row_version=current_operation.row_version,
            )
            result = LearningPromotionResult(
                candidate=updated,
                decision=decision,
                outcome="activated",
                target=prepared.command.target,
                scope=operation.scope,
                path=prepared.command.path,
                revision=applied_revision,
                activation_id=activation.activation_id,
            )
            receipt = self.context._receipt(
                txn,
                command_id=operation.command_id,
                operation=(
                    "learning_configuration_edit_and_accept"
                    if edited
                    else "learning_configuration_accept"
                ),
                digest=operation.request_digest,
                session_id=None,
                result_kind="learning_configuration_promotion",
                result_id=self._result_ref(result),
                row_version=updated.row_version,
                event_cursor=activation_event.cursor or event.cursor,
            )
            return ApplicationCommandResult(result, receipt)

        result = self.context._translate(lambda: self.context.journal.transact(work))
        if isinstance(result, NeedsResolution):
            raise result.error
        return result


__all__ = ["ConfigurationPromotionFinalizeMixin"]
