"""Undo Saga for activated configuration changes."""

from __future__ import annotations

import json

from morrow.application.learning.results import LearningPromotionResult
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.configuration_promotion import (
    ConfigurationActivation,
    ConfigurationActivationOperation,
    ConfigurationActivationStatus,
    PromotionFailureCode,
    PromotionOperation,
    PromotionOperationState,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import LearningResolutionActor, LearningScope
from morrow.core.learning_memory import LearningCandidateDecision, LearningCandidateDecisionKind

from .configuration_promotion_support import NeedsResolution, promotion_now


class ConfigurationPromotionUndoMixin:
    """Prepare and execute safe, provenance-preserving activation reversals."""

    def preview_undo(self, activation_id: str):
        activation = self.context._query(
            lambda: self.context.journal.get_configuration_activation(
                self.workspace_id, activation_id
            )
        )
        if activation is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 activation 不存在")
        if activation.status is not ConfigurationActivationStatus.ACTIVE:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "只有 active activation 可以撤销")
        inverse = self._inverse_from_activation(activation)
        try:
            revision, _presence, digest = self.config_service.current_state(  # type: ignore[union-attr]
                inverse
            )
        except Exception as exc:
            raise self._configuration_error(exc) from exc
        if revision != activation.applied_revision or digest != activation.after_digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "配置已在 activation 后变化，不能自动撤销",
            )
        try:
            prepared = self.config_service.prepare(inverse)  # type: ignore[union-attr]
        except Exception as exc:
            raise self._configuration_error(exc) from exc
        if (
            prepared.expected_revision != activation.applied_revision
            or prepared.before_digest != activation.after_digest
            or not prepared.changed
            or prepared.inverse_command is None
        ):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "activation 逆操作预览已失效")
        return prepared

    def undo_activation(
        self,
        activation_id: str,
        *,
        command_id: str,
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        self._assert_available()
        payload = {"activation_id": activation_id}
        command_id, request_digest, replay = self.context._prepare(
            "learning_configuration_undo", payload, command_id
        )
        if replay is not None:
            return ApplicationCommandResult(self._load_result(replay.result_id), replay)
        existing = self.context._query(
            lambda: self.context.journal.get_promotion_operation_by_command(
                self.workspace_id, command_id
            )
        )
        if existing is not None:
            if existing.request_digest != request_digest:
                raise ApplicationError(ApplicationErrorCode.CONFLICT, "undo command 摘要不匹配")
            return self._resume_undo(existing, request_digest=request_digest)
        activation = self.context._query(
            lambda: self.context.journal.get_configuration_activation(
                self.workspace_id, activation_id
            )
        )
        if activation is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置 activation 不存在")
        prepared = self.preview_undo(activation_id)
        operation = self._prepare_undo_sqlite(
            activation,
            prepared,
            command_id=command_id,
            request_digest=request_digest,
        )
        return self._resume_undo(operation, request_digest=request_digest)

    def _inverse_from_activation(self, activation: ConfigurationActivation):
        try:
            inverse = self.configuration_command_type.model_validate(
                json.loads(activation.inverse_command_json), strict=False
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "activation 逆命令无效"
            ) from exc
        encoded = canonical_json_bytes(inverse.model_dump(mode="json"))
        if sha256_digest(encoded) != activation.inverse_command_digest:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "activation 逆命令摘要无效")
        return inverse

    def _prepare_undo_sqlite(
        self,
        activation: ConfigurationActivation,
        prepared,
        *,
        command_id: str,
        request_digest: str,
    ) -> PromotionOperation:
        stamp = promotion_now(self.context)
        encoded = canonical_json_bytes(prepared.model_dump(mode="json")).decode("utf-8")
        operation = PromotionOperation(
            operation_id=self.context.id_source.new_id("pop"),
            command_id=command_id,
            request_digest=request_digest,
            workspace_id=self.workspace_id,
            candidate_id=activation.candidate_id,
            candidate_row_version=self._candidate_row_version(activation.candidate_id),
            target=prepared.command.target,
            scope=LearningScope(prepared.command.scope),
            path=prepared.command.path or "__document__",
            prepared_change_json=encoded,
            prepared_change_digest=sha256_digest(encoded),
            state=PromotionOperationState.PREPARED,
            before_revision=prepared.expected_revision,
            before_digest=prepared.before_digest,
            after_digest=prepared.after_digest,
            row_version=1,
            created_at=stamp,
            updated_at=stamp,
            prepared_at=stamp,
        )

        def work(txn):
            current_activation = txn.get_configuration_activation(
                self.workspace_id, activation.activation_id
            )
            if (
                current_activation is None
                or current_activation.status is not ConfigurationActivationStatus.ACTIVE
            ):
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "activation 已被其他恢复动作处理"
                )
            current_candidate = txn.get_learning_candidate(
                self.workspace_id, activation.candidate_id
            )
            if current_candidate is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY, "activation Candidate 缺失"
                )
            existing = txn.get_promotion_operation_by_command(self.workspace_id, command_id)
            if existing is not None:
                return existing
            return txn.put_promotion_operation(self.workspace_id, operation)

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _candidate_row_version(self, candidate_id: str) -> int:
        candidate = self.context._query(
            lambda: self.context.journal.get_learning_candidate(self.workspace_id, candidate_id)
        )
        if candidate is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "activation Candidate 缺失")
        return candidate.row_version

    def _resume_undo(
        self,
        operation: PromotionOperation,
        *,
        request_digest: str,
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        if operation.request_digest != request_digest:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "undo request 摘要不匹配")
        if operation.state is PromotionOperationState.FINALIZED:
            return ApplicationCommandResult(self._load_result_for_operation(operation), None)
        if operation.state is not PromotionOperationState.PREPARED:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "undo Saga 需要前台恢复")
        prepared = self._decode_prepared(operation)
        try:
            revision, _presence, digest = self.config_service.current_state(  # type: ignore[union-attr]
                prepared.command
            )
        except Exception as exc:
            self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
            raise self._configuration_error(exc) from exc
        if revision == prepared.expected_applied_revision and digest == prepared.after_digest:
            applied_revision = revision
        elif revision == prepared.expected_revision and digest == prepared.before_digest:
            try:
                result = self.config_service.apply_prepared(  # type: ignore[union-attr]
                    prepared,
                    operation_id=operation.operation_id,
                )
                applied_revision = result.revision
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self._mark_after_apply_failure(operation, prepared)
                raise self._configuration_error(exc) from exc
        else:
            self._mark_needs_resolution(operation, PromotionFailureCode.CONFLICT)
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "配置已变化，未覆盖当前值")
        if applied_revision is None:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "undo 应用版本缺失")
        return self._finalize_undo(operation, prepared, applied_revision=applied_revision)

    def _finalize_undo(
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
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "undo Saga 不存在")
            if current_operation.state is PromotionOperationState.FINALIZED:
                return ApplicationCommandResult(
                    self._load_result_for_operation(current_operation, txn=txn), None
                )
            if current_operation.state not in {
                PromotionOperationState.PREPARED,
                PromotionOperationState.NEEDS_RESOLUTION,
            }:
                raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "undo Saga 状态无效")
            current_revision, _presence, current_digest = self.config_service.current_state(  # type: ignore[union-attr]
                prepared.command
            )
            if current_revision != applied_revision or current_digest != prepared.after_digest:
                self._save_needs_resolution_in_txn(
                    txn, current_operation, PromotionFailureCode.CONFLICT, stamp
                )
                return NeedsResolution(
                    ApplicationError(ApplicationErrorCode.CONFLICT, "撤销前配置再次变化")
                )
            original = self._original_activation_for_undo(txn, operation, prepared)
            candidate = txn.get_learning_candidate(self.workspace_id, operation.candidate_id)
            if candidate is None:
                raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "undo Candidate 缺失")
            decision = LearningCandidateDecision(
                decision_id=self.context.id_source.new_id("lcd"),
                workspace_id=self.workspace_id,
                candidate_id=candidate.candidate_id,
                kind=LearningCandidateDecisionKind.SUPERSEDE,
                actor=LearningResolutionActor.USER,
                original_proposal_digest=candidate.fingerprint,
                scope=operation.scope,
                command_id=operation.command_id,
                created_at=stamp,
            )
            txn.put_learning_candidate_decision(self.workspace_id, decision)
            txn.save_configuration_activation(
                self.workspace_id,
                original.model_copy(
                    update={
                        "status": ConfigurationActivationStatus.REVERSED,
                        "updated_at": stamp,
                    }
                ),
            )
            inverse_json = canonical_json_bytes(
                prepared.inverse_command.model_dump(mode="json")
            ).decode("utf-8")
            new_activation = ConfigurationActivation(
                activation_id=self.context.id_source.new_id("act"),
                workspace_id=self.workspace_id,
                candidate_id=candidate.candidate_id,
                decision_id=decision.decision_id,
                operation_id=operation.operation_id,
                target=prepared.command.target,
                scope=operation.scope,
                path=prepared.command.path or "__document__",
                operation=self._activation_operation_for_command(prepared.command),
                applied_revision=applied_revision,
                before_digest=prepared.before_digest,
                after_digest=prepared.after_digest,
                value_digest=self._value_digest(prepared.command),
                inverse_command_json=inverse_json,
                inverse_command_digest=sha256_digest(inverse_json),
                reverses_activation_id=original.activation_id,
                created_at=stamp,
                updated_at=stamp,
            )
            txn.put_configuration_activation(self.workspace_id, new_activation)
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
            event = self.context._event(
                txn,
                event_type="configuration.reversed",
                aggregate_kind="configuration_activation",
                aggregate_id=new_activation.activation_id,
                payload={
                    "activation_id": new_activation.activation_id,
                    "reverses_activation_id": original.activation_id,
                    "target": new_activation.target,
                    "scope": new_activation.scope.value,
                    "path": new_activation.path,
                    "applied_revision": applied_revision,
                },
            )
            result = LearningPromotionResult(
                candidate=candidate,
                decision=decision,
                outcome="reversed",
                target=new_activation.target,
                scope=new_activation.scope,
                path=new_activation.path,
                revision=applied_revision,
                activation_id=new_activation.activation_id,
            )
            receipt = self.context._receipt(
                txn,
                command_id=operation.command_id,
                operation="learning_configuration_undo",
                digest=operation.request_digest,
                session_id=None,
                result_kind="learning_configuration_undo",
                result_id=self._result_ref(result),
                row_version=candidate.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(result, receipt)

        result = self.context._translate(lambda: self.context.journal.transact(work))
        if isinstance(result, NeedsResolution):
            raise result.error
        return result

    @staticmethod
    def _original_activation_for_undo(txn, operation, prepared):
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
        if not matches:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "原 activation 缺失")
        return max(matches, key=lambda item: item.applied_revision)

    @staticmethod
    def _activation_operation_for_command(
        command,
    ) -> ConfigurationActivationOperation:
        if command.operation == "set":
            return ConfigurationActivationOperation.SET
        if command.operation == "append":
            return ConfigurationActivationOperation.APPEND
        return ConfigurationActivationOperation.REMOVE


__all__ = ["ConfigurationPromotionUndoMixin"]
