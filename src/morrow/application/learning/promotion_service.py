"""Cross-store Saga for promoting Preference/Profile Candidates into YAML."""

from __future__ import annotations

from morrow.application.api_context import ApplicationCommandContext
from morrow.application.configuration import ConfigurationCommand, PreparedConfigurationChange
from morrow.application.learning.configuration_finalize import ConfigurationPromotionFinalizeMixin
from morrow.application.learning.configuration_policy import ConfigurationPromotionPolicyMixin
from morrow.application.learning.configuration_promotion_support import promotion_now
from morrow.application.learning.configuration_recovery import ConfigurationPromotionRecoveryMixin
from morrow.application.learning.configuration_undo import ConfigurationPromotionUndoMixin
from morrow.application.learning.results import LearningPromotionResult
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.configuration_promotion import (
    PromotionFailureCode,
    PromotionOperation,
    PromotionOperationState,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import (
    LearningCandidate,
    LearningCandidateStatus,
    LearningScope,
)
from morrow.core.learning_commands import (
    AcceptLearningCandidateCommand,
    EditAndAcceptLearningCandidateCommand,
)
from morrow.core.learning_payloads import CandidatePayload
from morrow.services.preferences import (
    ConfigPatchService,
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    ConfigurationReadOnlyError,
    ConfigurationStateError,
    ConfigurationValidationError,
)


class ConfigurationPromotionService(
    ConfigurationPromotionUndoMixin,
    ConfigurationPromotionRecoveryMixin,
    ConfigurationPromotionFinalizeMixin,
    ConfigurationPromotionPolicyMixin,
):
    """Own the SQLite A/YAML/SQLite B boundaries for one config Candidate."""

    configuration_command_type = ConfigurationCommand
    prepared_change_type = PreparedConfigurationChange

    def __init__(
        self,
        context: ApplicationCommandContext,
        config_service: ConfigPatchService | None,
    ) -> None:
        self.context = context
        self.config_service = config_service

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    def accept_candidate(
        self,
        command: AcceptLearningCandidateCommand | EditAndAcceptLearningCandidateCommand,
        *,
        edit: bool,
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        self._assert_available()
        self._assert_workspace(command.workspace_id)
        operation_name = (
            "learning_configuration_edit_and_accept" if edit else "learning_configuration_accept"
        )
        payload: dict[str, object] = {
            "candidate_id": command.candidate_id,
            "expected_row_version": command.expected_row_version,
            "scope": command.scope,
            "conflict_resolution": command.conflict_resolution.value,
        }
        if edit:
            payload["final_payload"] = command.final_payload.model_dump(mode="json")
        command_id, request_digest, replay = self.context._prepare(
            operation_name, payload, command.command_id
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
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "promotion command ID was reused with a different request",
                )
            return self._resume(existing, request_digest=request_digest)

        candidate = self.context._query(
            lambda: self.context.journal.get_learning_candidate(
                self.workspace_id, command.candidate_id
            )
        )
        if candidate is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Learning Candidate is missing")
        final_payload = command.final_payload if edit else candidate.proposed_payload
        prepared = self._prepare_candidate(
            candidate,
            final_payload=final_payload,
            scope=command.scope,
            edit=edit,
        )
        operation = self._prepare_sqlite(
            command=command,
            candidate=candidate,
            prepared=prepared,
            request_digest=request_digest,
            command_id=command_id,
            final_payload=final_payload,
            edit=edit,
        )
        return self._resume(operation, request_digest=request_digest)

    def preview_candidate(
        self,
        candidate: LearningCandidate,
        *,
        final_payload: CandidatePayload,
        scope: str | None,
        edit: bool,
    ) -> PreparedConfigurationChange:
        """Prepare the complete final YAML preview without writing either store."""

        self._assert_available()
        return self._prepare_candidate(
            candidate,
            final_payload=final_payload,
            scope=scope,
            edit=edit,
        )

    def _prepare_candidate(
        self,
        candidate: LearningCandidate,
        *,
        final_payload: CandidatePayload,
        scope: str | None,
        edit: bool,
    ) -> PreparedConfigurationChange:
        self._validate_candidate_shape(candidate, final_payload, scope=scope, edit=edit)
        selected_scope = self._scope(scope or candidate.proposed_scope.value)
        command = self._command_for_candidate(candidate, final_payload, selected_scope)
        try:
            prepared = self.config_service.prepare(command)  # type: ignore[union-attr]
        except (
            ConfigurationValidationError,
            ConfigurationNotFoundError,
            ConfigurationReadOnlyError,
            ConfigurationStateError,
            ConfigurationConflictError,
        ) as exc:
            raise self._configuration_error(exc) from exc
        if not prepared.changed:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "配置当前已经是目标状态")
        if prepared.inverse_command is None:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "配置变更缺少可安全撤销的单字段逆操作",
            )
        return prepared

    def _prepare_sqlite(
        self,
        *,
        command,
        candidate: LearningCandidate,
        prepared: PreparedConfigurationChange,
        request_digest: str,
        command_id: str,
        final_payload: CandidatePayload,
        edit: bool,
    ) -> PromotionOperation:
        stamp = promotion_now(self.context)
        encoded = canonical_json_bytes(prepared.model_dump(mode="json")).decode("utf-8")
        operation = PromotionOperation(
            operation_id=self.context.id_source.new_id("pop"),
            command_id=command_id,
            request_digest=request_digest,
            workspace_id=self.workspace_id,
            candidate_id=candidate.candidate_id,
            candidate_row_version=candidate.row_version,
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
            existing = txn.get_promotion_operation_by_command(self.workspace_id, command_id)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "promotion command ID was reused with a different request",
                    )
                return existing
            current = txn.get_learning_candidate(self.workspace_id, candidate.candidate_id)
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Learning Candidate is missing"
                )
            if current.row_version != candidate.row_version:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Learning Candidate row is stale"
                )
            if current.row_version != command.expected_row_version:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Learning Candidate command row is stale"
                )
            self._validate_candidate_in_txn(
                txn,
                current,
                final_payload=final_payload,
                prepared=prepared,
                edit=edit,
            )
            in_progress = tuple(
                item
                for item in txn.list_promotion_operations(self.workspace_id, limit=500)
                if item.candidate_id == current.candidate_id
                and item.state is PromotionOperationState.PREPARED
                and item.operation_id != operation.operation_id
            )
            if in_progress:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "该 Candidate 已有进行中的配置变更",
                )
            promoting = current.model_copy(
                update={
                    "status": LearningCandidateStatus.PROMOTING,
                    "row_version": current.row_version + 1,
                }
            )
            txn.save_learning_candidate(
                self.workspace_id,
                promoting,
                expected_row_version=current.row_version,
            )
            return txn.put_promotion_operation(self.workspace_id, operation)

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _resume(
        self,
        operation: PromotionOperation,
        *,
        request_digest: str,
    ) -> ApplicationCommandResult[LearningPromotionResult]:
        if operation.request_digest != request_digest:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "配置变更请求摘要不匹配")
        if operation.state is PromotionOperationState.FINALIZED:
            return ApplicationCommandResult(self._load_result_for_operation(operation), None)
        if operation.state is not PromotionOperationState.PREPARED:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "配置变更需要前台恢复处理",
            )
        prepared = self._decode_prepared(operation)
        try:
            revision, _presence, digest = self.config_service.current_state(  # type: ignore[union-attr]
                prepared.command
            )
        except Exception as exc:
            self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
            raise self._configuration_error(exc) from exc
        if revision == prepared.expected_applied_revision and digest == prepared.after_digest:
            applied_revision = self._sync_after_state(operation, prepared)
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
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "配置在预览后发生变化，未覆盖当前值；请重新预览",
            )
        if applied_revision is None:
            self._mark_needs_resolution(operation, PromotionFailureCode.NEEDS_RECOVERY)
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY,
                "配置写入版本缺失，已暂停恢复",
            )
        return self._finalize(operation, prepared, applied_revision=applied_revision)


__all__ = ["ConfigurationPromotionService"]
