"""Validation and conversion policy for configuration learning Candidates."""

from __future__ import annotations

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.configuration_promotion import (
    ConfigurationActivationOperation,
    PromotionOperation,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.learning import (
    LEARNING_MAX_REFERENCE_IDS,
    LearningCandidate,
    LearningCandidateOperation,
    LearningCandidateStatus,
    LearningCandidateType,
    LearningScope,
    LearningSensitivity,
    is_positive_explicit_user_evidence,
)
from morrow.core.learning_payloads import (
    CandidatePayload,
    ProfileCandidatePayload,
)
from morrow.services.profile_configuration import (
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    ConfigurationReadOnlyError,
    ConfigurationStateError,
    ConfigurationValidationError,
)

from .configuration_promotion_support import promotion_now


class ConfigurationPromotionPolicyMixin:
    """Keep promotion safety rules independent from Saga orchestration."""

    def _valid_evidence(self, txn, candidate: LearningCandidate):
        evidence = txn.list_learning_candidate_evidence(self.workspace_id, candidate.candidate_id)[
            :LEARNING_MAX_REFERENCE_IDS
        ]
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
                "Learning Candidate 证据已不可用于配置变更",
            )
        return evidence

    @staticmethod
    def _validate_explicit_evidence(candidate, evidence) -> None:
        if candidate.candidate_type in {
            LearningCandidateType.PROFILE,
        } and not any(is_positive_explicit_user_evidence(item) for item in evidence):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Preference/Profile 变更需要明确的用户证据",
            )

    def _validate_candidate_in_txn(
        self,
        txn,
        candidate: LearningCandidate,
        *,
        final_payload: CandidatePayload,
        prepared,
        edit: bool,
    ) -> None:
        if candidate.status is not LearningCandidateStatus.PROPOSED:
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "只能接受 proposed Candidate")
        if candidate.expires_at <= promotion_now(self.context):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "Learning Candidate 已过期")
        if candidate.sensitivity is LearningSensitivity.PROHIBITED:
            raise ApplicationError(ApplicationErrorCode.INVALID, "禁止的 Candidate 不能接受")
        evidence = self._valid_evidence(txn, candidate)
        self._validate_explicit_evidence(candidate, evidence)
        scope = LearningScope(prepared.command.scope)
        semantic_key = candidate.semantic_key
        fingerprint = LearningCandidate.fingerprint_for(
            candidate_type=candidate.candidate_type,
            scope=scope,
            semantic_key=semantic_key,
            operation=candidate.operation,
            proposed_payload=final_payload,
        )
        if not edit and scope is candidate.proposed_scope and fingerprint != candidate.fingerprint:
            raise ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "Candidate 提议摘要不匹配")
        duplicate = txn.list_learning_candidates(
            self.workspace_id, fingerprint=fingerprint, limit=LEARNING_MAX_REFERENCE_IDS
        )
        if any(
            item.candidate_id != candidate.candidate_id
            and item.status
            in {
                LearningCandidateStatus.PROPOSED,
                LearningCandidateStatus.PROMOTING,
                LearningCandidateStatus.ACCEPTED,
                LearningCandidateStatus.EDITED_AND_ACCEPTED,
            }
            for item in duplicate
        ):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "相同配置 Candidate 已存在")
        suppressions = txn.list_learning_suppressions(
            self.workspace_id,
            candidate_type=candidate.candidate_type.value,
            scope=scope,
            semantic_key=semantic_key,
            limit=LEARNING_MAX_REFERENCE_IDS,
        ) + txn.list_learning_suppressions(
            self.workspace_id,
            candidate_type=candidate.candidate_type.value,
            scope=scope,
            fingerprint=fingerprint,
            limit=LEARNING_MAX_REFERENCE_IDS,
        )
        now = promotion_now(self.context)
        if any(
            item.status.value == "active" and (item.expires_at is None or item.expires_at > now)
            for item in suppressions
        ):
            raise ApplicationError(ApplicationErrorCode.CONFLICT, "该配置 Candidate 已被抑制")

    def _validate_candidate_shape(
        self,
        candidate: LearningCandidate,
        payload: CandidatePayload,
        *,
        scope: str | None,
        edit: bool,
    ) -> None:
        del edit
        if candidate.candidate_type is not LearningCandidateType.PROFILE:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Candidate 不是配置类型")
        if payload.candidate_type is not candidate.candidate_type:
            raise ApplicationError(ApplicationErrorCode.INVALID, "编辑后的 Candidate 类型不匹配")
        if scope not in {
            None,
            LearningScope.WORKSPACE.value,
        }:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Profile 只能写入 workspace")
        if isinstance(payload, ProfileCandidatePayload):
            if payload.path in {"goals", "tech_stack", "constraints", "conventions"}:
                if len(payload.value) != 1 or candidate.operation not in {
                    LearningCandidateOperation.APPEND,
                    LearningCandidateOperation.REMOVE,
                }:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID, "Profile 列表只能 append/remove 一项"
                    )
            elif candidate.operation not in {
                LearningCandidateOperation.SET,
                LearningCandidateOperation.REPLACE,
                LearningCandidateOperation.REMOVE,
            }:
                raise ApplicationError(ApplicationErrorCode.INVALID, "Profile 标量操作无效")

    def _command_for_candidate(
        self,
        candidate: LearningCandidate,
        payload: CandidatePayload,
        scope: LearningScope,
    ):
        if not isinstance(payload, ProfileCandidatePayload):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Candidate payload 不是配置 payload"
            )
        operation = candidate.operation
        if payload.path in {
            "instructions",
            "goals",
            "tech_stack",
            "constraints",
            "conventions",
        }:
            mapped = {
                LearningCandidateOperation.APPEND: "append",
                LearningCandidateOperation.REMOVE: "remove",
            }.get(operation)
            value = payload.value[0]
        elif operation in {LearningCandidateOperation.SET, LearningCandidateOperation.REPLACE}:
            mapped = "set"
            value = payload.value
        elif operation is LearningCandidateOperation.REMOVE:
            mapped = "unset"
            value = None
        else:
            mapped = None
            value = None
        if mapped is None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Candidate operation 不能转换为配置操作"
            )
        return self.configuration_command_type(
            scope=scope.value,
            target="profile",
            operation=mapped,
            path=payload.path,
            **({} if mapped == "unset" else {"value": value}),
        )

    @staticmethod
    def _scope(value: str | LearningScope) -> LearningScope:
        try:
            selected = value if isinstance(value, LearningScope) else LearningScope(value)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(ApplicationErrorCode.INVALID, "Learning scope 无效") from exc
        if selected in {LearningScope.SESSION, LearningScope.TASK}:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "配置 Candidate 不能写入 session/task"
            )
        return selected

    @staticmethod
    def _activation_operation(candidate: LearningCandidate) -> ConfigurationActivationOperation:
        if candidate.operation is LearningCandidateOperation.REPLACE:
            return ConfigurationActivationOperation.REPLACE
        return ConfigurationActivationOperation(candidate.operation.value)

    @staticmethod
    def _value_digest(command) -> str:
        value = {"operation": command.operation, "value": command.value}
        return sha256_digest(canonical_json_bytes(value))

    def _is_edit_operation(
        self,
        operation: PromotionOperation,
        candidate: LearningCandidate,
        prepared,
    ) -> bool:
        """Infer edit provenance from the bounded command, excluding an explicit scope change."""

        try:
            original = self._command_for_candidate(
                candidate,
                candidate.proposed_payload,
                operation.scope,
            )
        except ApplicationError:
            return True
        return any(
            getattr(original, field) != getattr(prepared.command, field)
            for field in ("target", "operation", "path", "value")
        )

    @staticmethod
    def _payload_for_decision(candidate: LearningCandidate, prepared):
        payload = candidate.proposed_payload.model_dump(mode="json")
        payload["path"] = prepared.command.path
        if prepared.command.operation in {"append", "remove"}:
            payload["value"] = (prepared.command.value,)
        else:
            payload["value"] = prepared.command.value
        return payload

    @staticmethod
    def _configuration_error(exc: Exception) -> ApplicationError:
        if isinstance(exc, ConfigurationConflictError):
            return ApplicationError(ApplicationErrorCode.CONFLICT, "配置版本或内容已变化")
        if isinstance(exc, ConfigurationNotFoundError):
            return ApplicationError(ApplicationErrorCode.NOT_FOUND, "配置目标不存在")
        if isinstance(exc, ConfigurationValidationError):
            return ApplicationError(ApplicationErrorCode.INVALID, "配置操作无效")
        if isinstance(exc, ConfigurationReadOnlyError):
            return ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置当前不可安全写入")
        if isinstance(exc, ConfigurationStateError):
            return ApplicationError(ApplicationErrorCode.NEEDS_RECOVERY, "配置状态需要前台恢复")
        return ApplicationError(ApplicationErrorCode.UNAVAILABLE, "配置 promotion 失败")

    def _assert_available(self) -> None:
        if self.config_service is None:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "配置 promotion 服务尚未就绪")

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE, "Learning command 超出 workspace"
            )
