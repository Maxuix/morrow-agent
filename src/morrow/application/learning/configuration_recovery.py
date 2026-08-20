"""Recovery and durable-result helpers for configuration promotion."""

from __future__ import annotations

import json

from morrow.application.learning.results import LearningPromotionResult
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.configuration_promotion import (
    ConfigurationActivationStatus,
    PromotionFailureCode,
    PromotionOperation,
    PromotionOperationState,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import LearningCandidateStatus

from .configuration_promotion_support import promotion_now


class ConfigurationPromotionRecoveryMixin:
    """Foreground recovery and idempotent result reconstruction."""

    def list_operations(self, *, state: PromotionOperationState | None = None):
        return self.context._query(
            lambda: self.context.journal.list_promotion_operations(
                self.workspace_id, state=state, limit=500
            )
        )

    def list_unresolved_operations(self):
        return tuple(
            operation
            for operation in self.list_operations()
            if operation.state
            in {
                PromotionOperationState.PREPARED,
                PromotionOperationState.NEEDS_RESOLUTION,
            }
        )

    def get_operation(self, operation_id: str) -> PromotionOperation | None:
        return self.context._query(
            lambda: self.context.journal.get_promotion_operation(self.workspace_id, operation_id)
        )

    def recover_operation(
        self,
        operation_id: str,
        *,
        action: str,
    ) -> PromotionOperation | ApplicationCommandResult[LearningPromotionResult]:
        """Perform one explicit, non-destructive recovery action in the foreground."""

        operation = self.get_operation(operation_id)
        if operation is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 Saga 不存在")
        if operation.state is PromotionOperationState.FINALIZED:
            return ApplicationCommandResult(self._load_result_for_operation(operation), None)
        if action not in {"retry", "finalize", "cancel", "abort"}:
            raise ApplicationError(ApplicationErrorCode.INVALID, "配置恢复动作无效")
        prepared = self._decode_prepared(operation)
        try:
            revision, _presence, digest = self.config_service.current_state(  # type: ignore[union-attr]
                prepared.command
            )
        except Exception as exc:
            if action != "abort":
                self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
                raise self._configuration_error(exc) from exc
            revision = None
            digest = None
        before = revision == prepared.expected_revision and digest == prepared.before_digest
        after = revision == prepared.expected_applied_revision and digest == prepared.after_digest
        undo = self._is_undo_operation(operation, prepared)
        if action == "finalize":
            if not after:
                if not before:
                    self._mark_needs_resolution(operation, PromotionFailureCode.CONFLICT)
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "当前 YAML 不匹配可安全 finalize 的 after 状态",
                )
            applied_revision = self._sync_after_state(operation, prepared)
            if undo:
                return self._finalize_undo(operation, prepared, applied_revision=applied_revision)
            return self._finalize(operation, prepared, applied_revision=applied_revision)
        if action == "retry":
            if not before:
                if not after:
                    self._mark_needs_resolution(operation, PromotionFailureCode.CONFLICT)
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "当前 YAML 不匹配可安全 retry 的 before 状态",
                )
            if operation.state is PromotionOperationState.NEEDS_RESOLUTION:
                operation = self._reopen_operation(operation)
            if undo:
                return self._resume_undo(operation, request_digest=operation.request_digest)
            return self._resume(operation, request_digest=operation.request_digest)
        if after and action in {"cancel", "abort"}:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "YAML 已处于 after 状态，只能 finalize，不能取消或中止",
            )
        if action == "cancel" and not before:
            if not after:
                self._mark_needs_resolution(operation, PromotionFailureCode.CONFLICT)
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "只有 YAML 仍处于 before 状态时才能取消",
            )
        return self._abort_operation(
            operation, failure_code=None if before else PromotionFailureCode.CONFLICT
        )

    def _sync_after_state(self, operation: PromotionOperation, prepared) -> int:
        """Re-validate an after-state and refresh any attached Session projection."""

        try:
            result = self.config_service.apply_prepared(  # type: ignore[union-attr]
                prepared,
                operation_id=operation.operation_id,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
            raise self._configuration_error(exc) from exc
        if result.revision is None:
            self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "配置写入版本缺失，已暂停恢复",
            )
        return result.revision

    def _is_undo_operation(self, operation: PromotionOperation, prepared) -> bool:
        candidate = self.context._query(
            lambda: self.context.journal.get_learning_candidate(
                self.workspace_id, operation.candidate_id
            )
        )
        if candidate is None or candidate.status is LearningCandidateStatus.PROMOTING:
            return False
        activations = self.context._query(
            lambda: self.context.journal.list_configuration_activations(
                self.workspace_id,
                target=operation.target,
                path=operation.path,
                status=ConfigurationActivationStatus.ACTIVE,
                limit=500,
            )
        )
        return any(
            item.candidate_id == operation.candidate_id
            and item.scope is operation.scope
            and item.applied_revision == prepared.expected_revision
            and item.after_digest == prepared.before_digest
            and item.reverses_activation_id is None
            for item in activations
        )

    def _reopen_operation(self, operation: PromotionOperation) -> PromotionOperation:
        stamp = promotion_now(self.context)

        def work(txn):
            current = txn.get_promotion_operation(self.workspace_id, operation.operation_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 Saga 不存在")
            if current.state is PromotionOperationState.PREPARED:
                return current
            return txn.save_promotion_operation(
                self.workspace_id,
                current.model_copy(
                    update={
                        "state": PromotionOperationState.PREPARED,
                        "failure_code": None,
                        "row_version": current.row_version + 1,
                        "updated_at": stamp,
                    }
                ),
                expected_row_version=current.row_version,
            )

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _abort_operation(
        self,
        operation: PromotionOperation,
        *,
        failure_code: PromotionFailureCode | None,
    ) -> PromotionOperation:
        stamp = promotion_now(self.context)

        def work(txn):
            current = txn.get_promotion_operation(self.workspace_id, operation.operation_id)
            if current is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 Saga 不存在")
            if current.state not in {
                PromotionOperationState.PREPARED,
                PromotionOperationState.NEEDS_RESOLUTION,
            }:
                return current
            candidate = txn.get_learning_candidate(self.workspace_id, current.candidate_id)
            if candidate is not None and candidate.status is LearningCandidateStatus.PROMOTING:
                txn.save_learning_candidate(
                    self.workspace_id,
                    candidate.model_copy(
                        update={
                            "status": LearningCandidateStatus.PROPOSED,
                            "row_version": candidate.row_version + 1,
                            "resolved_at": None,
                            "resolved_by": None,
                        }
                    ),
                    expected_row_version=candidate.row_version,
                )
            return txn.save_promotion_operation(
                self.workspace_id,
                current.model_copy(
                    update={
                        "state": PromotionOperationState.ABORTED,
                        "failure_code": failure_code,
                        "row_version": current.row_version + 1,
                        "updated_at": stamp,
                    }
                ),
                expected_row_version=current.row_version,
            )

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _mark_after_apply_failure(self, operation, prepared) -> None:
        try:
            revision, _presence, digest = self.config_service.current_state(  # type: ignore[union-attr]
                prepared.command
            )
        except Exception:
            self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
            return
        if revision == prepared.expected_revision and digest == prepared.before_digest:
            return
        if revision == prepared.expected_applied_revision and digest == prepared.after_digest:
            return
        self._mark_needs_resolution(operation, PromotionFailureCode.CONFLICT)

    def _mark_needs_resolution(
        self,
        operation: PromotionOperation,
        failure_code: PromotionFailureCode,
    ) -> None:
        stamp = promotion_now(self.context)

        def work(txn):
            current = txn.get_promotion_operation(self.workspace_id, operation.operation_id)
            if current is None or current.state is not PromotionOperationState.PREPARED:
                return
            self._save_needs_resolution_in_txn(txn, current, failure_code, stamp)

        self.context._translate(lambda: self.context.journal.transact(work))

    @staticmethod
    def _save_needs_resolution_in_txn(
        txn,
        operation: PromotionOperation,
        failure_code: PromotionFailureCode,
        stamp,
    ) -> None:
        txn.save_promotion_operation(
            operation.workspace_id,
            operation.model_copy(
                update={
                    "state": PromotionOperationState.NEEDS_RESOLUTION,
                    "failure_code": failure_code,
                    "row_version": operation.row_version + 1,
                    "updated_at": stamp,
                }
            ),
            expected_row_version=operation.row_version,
        )

    @staticmethod
    def _superseded_activation(txn, operation: PromotionOperation, prepared):
        activations = txn.list_configuration_activations(
            operation.workspace_id,
            target=operation.target,
            path=operation.path,
            status=ConfigurationActivationStatus.ACTIVE,
            limit=500,
        )
        matches = [
            item
            for item in activations
            if item.scope is operation.scope
            and item.applied_revision == prepared.expected_revision
            and item.after_digest == prepared.before_digest
        ]
        return max(matches, key=lambda item: item.applied_revision) if matches else None

    def _decode_prepared(self, operation: PromotionOperation):
        try:
            payload = json.loads(operation.prepared_change_json)
            prepared = self.prepared_change_type.model_validate(payload, strict=False)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Prepared 配置记录无效"
            ) from exc
        if sha256_digest(operation.prepared_change_json) != operation.prepared_change_digest:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "Prepared 配置摘要无效")
        return prepared

    def _load_result(self, result_id: str | None) -> LearningPromotionResult:
        if not result_id:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置 promotion 结果缺失")
        try:
            reference = json.loads(result_id)
            decision_id = reference["decision_id"]
            decision = self.context._query(
                lambda: self.context.journal.get_learning_candidate_decision(
                    self.workspace_id, decision_id
                )
            )
            if decision is None:
                raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置 decision 缺失")
            candidate = self.context._query(
                lambda: self.context.journal.get_learning_candidate(
                    self.workspace_id, decision.candidate_id
                )
            )
            if candidate is None:
                raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置 Candidate 缺失")
            return LearningPromotionResult(
                candidate=candidate,
                decision=decision,
                outcome=reference["outcome"],
                target=reference.get("target"),
                scope=reference.get("scope"),
                path=reference.get("path"),
                revision=reference.get("revision"),
                activation_id=reference.get("activation_id"),
            )
        except ApplicationError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "配置 promotion 结果无效"
            ) from exc

    def _load_result_for_operation(self, operation: PromotionOperation, txn=None):
        getter = txn or self.context.journal
        activation_items = getter.list_configuration_activations(
            self.workspace_id, target=operation.target, path=operation.path, limit=500
        )
        activation = next(
            (
                item
                for item in reversed(activation_items)
                if item.operation_id == operation.operation_id
            ),
            None,
        )
        if activation is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置 activation 缺失")
        decision = getter.get_learning_candidate_decision(self.workspace_id, activation.decision_id)
        candidate = getter.get_learning_candidate(self.workspace_id, activation.candidate_id)
        if decision is None or candidate is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置 promotion 记录不完整")
        return LearningPromotionResult(
            candidate=candidate,
            decision=decision,
            outcome="reversed" if activation.reverses_activation_id else "activated",
            target=activation.target,
            scope=activation.scope,
            path=activation.path,
            revision=activation.applied_revision,
            activation_id=activation.activation_id,
        )

    @staticmethod
    def _result_ref(result: LearningPromotionResult) -> str:
        return canonical_json_bytes(
            {
                "decision_id": result.decision.decision_id,
                "outcome": result.outcome,
                "target": result.target,
                "scope": result.scope.value if result.scope else None,
                "path": result.path,
                "revision": result.revision,
                "activation_id": result.activation_id,
            }
        ).decode("utf-8")


__all__ = ["ConfigurationPromotionRecoveryMixin"]
