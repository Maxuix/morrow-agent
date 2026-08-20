"""Replay-safe Project Knowledge lifecycle mutations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from morrow.application.api_context import ApplicationCommandContext
from morrow.core.application import ApplicationCommandResult, ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes
from morrow.core.learning import LearningSensitivity
from morrow.core.learning_commands import (
    DeleteProjectKnowledgeCommand,
    DisableProjectKnowledgeCommand,
    EnableProjectKnowledgeCommand,
    MarkProjectKnowledgeDisputedCommand,
)
from morrow.core.learning_memory import (
    ProjectKnowledgeHead,
    ProjectKnowledgeStatus,
)
from morrow.core.models import ProtocolModel

from .memory_terms import refresh_project_knowledge_terms


class KnowledgeLifecycleResult(ProtocolModel):
    head: ProjectKnowledgeHead
    operation: Literal["disabled", "enabled", "disputed", "deleted"]
    memory_revision: int


def _now(context: ApplicationCommandContext) -> datetime:
    value = context.clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MemoryLifecycleService:
    """Apply only explicit, reversible-or-tombstoning Knowledge state changes."""

    def __init__(self, context: ApplicationCommandContext) -> None:
        self.context = context

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    def disable_knowledge(
        self, command: DisableProjectKnowledgeCommand
    ) -> ApplicationCommandResult[KnowledgeLifecycleResult]:
        return self._mutate(
            command,
            operation="memory_knowledge_disable",
            target=ProjectKnowledgeStatus.DISABLED,
            allowed=(ProjectKnowledgeStatus.ACTIVE,),
            outcome="disabled",
            event_type="memory.record_disabled",
        )

    def enable_knowledge(
        self, command: EnableProjectKnowledgeCommand
    ) -> ApplicationCommandResult[KnowledgeLifecycleResult]:
        return self._mutate(
            command,
            operation="memory_knowledge_enable",
            target=ProjectKnowledgeStatus.ACTIVE,
            allowed=(ProjectKnowledgeStatus.DISABLED,),
            outcome="enabled",
            event_type="memory.record_enabled",
            validate_current=True,
        )

    def mark_disputed(
        self, command: MarkProjectKnowledgeDisputedCommand
    ) -> ApplicationCommandResult[KnowledgeLifecycleResult]:
        return self._mutate(
            command,
            operation="memory_knowledge_dispute",
            target=ProjectKnowledgeStatus.DISPUTED,
            allowed=(ProjectKnowledgeStatus.ACTIVE,),
            outcome="disputed",
            event_type="memory.record_disputed",
            validate_current=True,
        )

    def delete_knowledge(
        self, command: DeleteProjectKnowledgeCommand
    ) -> ApplicationCommandResult[KnowledgeLifecycleResult]:
        return self._mutate(
            command,
            operation="memory_knowledge_delete",
            target=ProjectKnowledgeStatus.DELETED,
            allowed=(
                ProjectKnowledgeStatus.ACTIVE,
                ProjectKnowledgeStatus.DISABLED,
                ProjectKnowledgeStatus.DISPUTED,
            ),
            outcome="deleted",
            event_type="memory.record_deleted",
            validate_current=True,
        )

    def _mutate(
        self,
        command,
        *,
        operation: str,
        target: ProjectKnowledgeStatus,
        allowed: tuple[ProjectKnowledgeStatus, ...],
        outcome: Literal["disabled", "enabled", "disputed", "deleted"],
        event_type: str,
        validate_current: bool = False,
    ) -> ApplicationCommandResult[KnowledgeLifecycleResult]:
        self._assert_workspace(command.workspace_id)
        payload = {
            "knowledge_id": command.knowledge_id,
            "expected_row_version": command.expected_row_version,
        }
        command_id, digest, replay = self.context._prepare(operation, payload, command.command_id)
        if replay is not None:
            return ApplicationCommandResult(self._load_result(replay.result_id), replay)

        def work(txn):
            existing = self.context._replay_in_txn(txn, command_id, digest)
            if existing is not None:
                return ApplicationCommandResult(self._load_result(existing.result_id), existing)
            head = txn.get_project_knowledge_head(self.workspace_id, command.knowledge_id)
            if head is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Project Knowledge is missing"
                )
            if head.row_version != command.expected_row_version:
                raise ApplicationError(ApplicationErrorCode.STALE, "Project Knowledge row is stale")
            if head.status not in allowed:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    f"Project Knowledge cannot transition from {head.status.value}",
                )
            current = None
            if validate_current or target is not ProjectKnowledgeStatus.DELETED:
                if head.current_revision_id is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        "Project Knowledge current revision is missing",
                    )
                current = txn.get_project_knowledge_revision(
                    self.workspace_id, head.current_revision_id
                )
                if current is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        "Project Knowledge current revision is missing",
                    )
                if current.knowledge_id != head.knowledge_id:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        "Project Knowledge current revision is inconsistent",
                    )
            if target is ProjectKnowledgeStatus.ACTIVE:
                if current is None or current.sensitivity is LearningSensitivity.PROHIBITED:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT,
                        "Project Knowledge current revision is not safe to enable",
                    )
            stamp = _now(self.context)
            saved_head = txn.save_project_knowledge_head(
                self.workspace_id,
                head.model_copy(
                    update={
                        "status": target,
                        "row_version": head.row_version + 1,
                        "updated_at": stamp,
                    }
                ),
                expected_row_version=head.row_version,
            )
            state = txn.ensure_memory_workspace_state(self.workspace_id)
            state_stamp = max(stamp, state.updated_at)
            saved_state = txn.save_memory_workspace_state(
                self.workspace_id,
                state.model_copy(
                    update={
                        "memory_revision": state.memory_revision + 1,
                        "row_version": state.row_version + 1,
                        "updated_at": state_stamp,
                    }
                ),
                expected_row_version=state.row_version,
            )
            refresh_project_knowledge_terms(txn, self.workspace_id, saved_head)
            event = self.context._event(
                txn,
                event_type=event_type,
                aggregate_kind="project_knowledge",
                aggregate_id=saved_head.knowledge_id,
                payload={
                    "category": saved_head.category.value,
                    "knowledge_id": saved_head.knowledge_id,
                    "status": saved_head.status.value,
                    "row_version": saved_head.row_version,
                    "memory_revision": saved_state.memory_revision,
                },
            )
            result = KnowledgeLifecycleResult(
                head=saved_head,
                operation=outcome,
                memory_revision=saved_state.memory_revision,
            )
            receipt = self.context._receipt(
                txn,
                command_id=command_id,
                operation=operation,
                digest=digest,
                session_id=None,
                result_kind="project_knowledge_lifecycle",
                result_id=self._result_ref(result),
                row_version=saved_head.row_version,
                event_cursor=event.cursor,
            )
            return ApplicationCommandResult(result, receipt)

        return self.context._translate(lambda: self.context.journal.transact(work))

    def _load_result(self, result_id: str | None) -> KnowledgeLifecycleResult:
        if not result_id:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Knowledge lifecycle result is missing"
            )
        try:
            reference = json.loads(result_id)
            if not isinstance(reference, dict):
                raise ValueError
            knowledge_id = reference["knowledge_id"]
            operation = reference["operation"]
            memory_revision = reference["memory_revision"]
            if "head" in reference:
                head = ProjectKnowledgeHead.model_validate(reference["head"])
            else:
                head = self.context._query(
                    lambda: self.context.journal.get_project_knowledge_head(
                        self.workspace_id, knowledge_id
                    )
                )
                if head is None:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY,
                        "Knowledge lifecycle head is missing",
                    )
            if head.workspace_id != self.workspace_id or head.knowledge_id != knowledge_id:
                raise ApplicationError(
                    ApplicationErrorCode.NEEDS_RECOVERY,
                    "Knowledge lifecycle result is outside the workspace",
                )
            return KnowledgeLifecycleResult(
                head=head,
                operation=operation,
                memory_revision=memory_revision,
            )
        except ApplicationError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Knowledge lifecycle result is invalid"
            ) from exc

    @staticmethod
    def _result_ref(result: KnowledgeLifecycleResult) -> str:
        return canonical_json_bytes(
            {
                "knowledge_id": result.head.knowledge_id,
                "operation": result.operation,
                "memory_revision": result.memory_revision,
                "head": result.head.model_dump(mode="json"),
            }
        ).decode("utf-8")

    def _assert_workspace(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ApplicationError(
                ApplicationErrorCode.CROSS_WORKSPACE,
                "Knowledge command is outside the workspace",
            )


__all__ = ["KnowledgeLifecycleResult", "MemoryLifecycleService"]
