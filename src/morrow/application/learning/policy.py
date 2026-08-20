"""Workspace LearningPolicy query and command service."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.application.api_context import ApplicationCommandContext
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.learning import LearningMode, LearningPolicy
from morrow.core.models import ProtocolModel


class LearningPolicyStatus(ProtocolModel):
    """Effective policy plus whether it is durably configured."""

    policy: LearningPolicy
    persisted: bool


def _now(context: ApplicationCommandContext) -> datetime:
    value = context.clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class LearningPolicyService:
    """Own policy changes without exposing storage or event plumbing to UI code."""

    def __init__(self, context: ApplicationCommandContext) -> None:
        self.context = context

    def get_status(self) -> LearningPolicyStatus:
        policy = self.context._query(
            lambda: self.context.journal.get_learning_policy(self.context.workspace_id)
        )
        if policy is None:
            return LearningPolicyStatus(
                policy=LearningPolicy.default_for(
                    self.context.workspace_id, now=_now(self.context)
                ),
                persisted=False,
            )
        return LearningPolicyStatus(policy=policy, persisted=True)

    def set_mode(
        self,
        mode: LearningMode | str,
        *,
        expected_row_version: int | None = None,
        command_id: str | None = None,
    ) -> ApplicationCommandResult[LearningPolicyStatus]:
        try:
            selected = mode if isinstance(mode, LearningMode) else LearningMode(mode)
        except (TypeError, ValueError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "learning mode is invalid"
            ) from exc
        if selected is LearningMode.EXPLICIT_AUTO:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "explicit_auto is reserved and unavailable in this Stage 5 release",
            )
        if expected_row_version is not None and (
            isinstance(expected_row_version, bool)
            or not isinstance(expected_row_version, int)
            or expected_row_version < 0
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "learning policy row version is invalid"
            )

        operation = "learning_policy_set_mode"
        payload = {
            "mode": selected.value,
            "expected_row_version": expected_row_version,
        }
        command_id, digest, replay = self.context._prepare(operation, payload, command_id)
        if replay is not None:
            current_policy = self.context._query(
                lambda: self.context.journal.get_learning_policy(self.context.workspace_id)
            )
            if current_policy is None:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "learning policy command result is missing",
                )
            return ApplicationCommandResult(
                LearningPolicyStatus(policy=current_policy, persisted=True),
                replay,
            )

        def work(txn):
            existing = self.context._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                current = txn.get_learning_policy(self.context.workspace_id)
                if current is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        "learning policy command result is missing",
                    )
                return ApplicationCommandResult(
                    LearningPolicyStatus(policy=current, persisted=True),
                    existing,
                )

            current = txn.get_learning_policy(self.context.workspace_id)
            if current is None:
                if expected_row_version not in {None, 0, 1}:
                    raise ApplicationError(
                        ApplicationErrorCode.STALE, "learning policy row is stale"
                    )
                timestamp = _now(self.context)
                next_policy = LearningPolicy(
                    workspace_id=self.context.workspace_id,
                    mode=selected,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                expected = None
            else:
                if expected_row_version != current.row_version:
                    raise ApplicationError(
                        ApplicationErrorCode.STALE, "learning policy row is stale"
                    )
                next_policy = LearningPolicy(
                    workspace_id=current.workspace_id,
                    mode=selected,
                    candidate_ttl_days=current.candidate_ttl_days,
                    max_candidates_per_review=current.max_candidates_per_review,
                    max_evidence_per_review=current.max_evidence_per_review,
                    row_version=current.row_version + 1,
                    created_at=current.created_at,
                    updated_at=_now(self.context),
                )
                expected = current.row_version

            saved = txn.save_learning_policy(
                self.context.workspace_id,
                next_policy,
                expected_row_version=expected,
            )
            event = self.context._event(
                txn,
                event_type="learning.policy_changed",
                aggregate_kind="learning_policy",
                aggregate_id=self.context.workspace_id,
                payload={
                    "mode": saved.mode.value,
                    "candidate_ttl_days": saved.candidate_ttl_days,
                    "max_candidates_per_review": saved.max_candidates_per_review,
                    "max_evidence_per_review": saved.max_evidence_per_review,
                    "row_version": saved.row_version,
                },
            )
            receipt = self.context._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=None,
                result_kind="learning_policy",
                result_id=self.context.workspace_id,
                row_version=saved.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(
                LearningPolicyStatus(policy=saved, persisted=True),
                receipt,
            )

        return self.context._translate(lambda: self.context.journal.transact(work))
