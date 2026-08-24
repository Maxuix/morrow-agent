"""Approved direct-management tool for generic Preferences."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    ToolCallContext,
    ToolHandlerOutcome,
)
from morrow.core.execution import tool_declaration
from morrow.core.models import ToolEffect
from morrow.core.preference_models import PreferenceScope
from morrow.core.preference_operations import (
    reduce_preference_operations,
)
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tools import RegisteredTool, ToolErrorCode, ToolExecutionError, make_tool

PreferenceManagementOperation = Literal["add", "replace", "remove", "enable", "disable"]


class ManagePreferenceOperation(BaseModel):
    """One item in an approved same-scope Preference management batch."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation: PreferenceManagementOperation
    preference_id: str | None = None
    statement: str | None = None
    evidence_ids: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def valid_shape(self) -> ManagePreferenceOperation:
        if self.operation == "add":
            if self.preference_id is not None or self.statement is None:
                raise ValueError("add requires statement and no preference_id")
        elif self.operation == "replace":
            if self.preference_id is None or self.statement is None:
                raise ValueError("replace requires preference_id and statement")
        elif self.operation in {"remove", "enable", "disable"}:
            if self.preference_id is None or self.statement is not None:
                raise ValueError(f"{self.operation} requires preference_id and no statement")
            if self.evidence_ids:
                raise ValueError(f"{self.operation} does not accept evidence_ids")
        return self


class ManagePreferencesArguments(BaseModel):
    """Strict wire arguments for one bounded same-scope Preference batch."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope: Literal["workspace", "global"]
    operations: tuple[ManagePreferenceOperation, ...] = Field(min_length=1, max_length=8)
    expected_revision: int | None = Field(default=None, ge=0)


class PreferenceManagementService:
    """Thin application adapter that keeps the Writer as the mutation authority."""

    def __init__(self, writer, queries, session=None) -> None:
        self.writer = writer
        self.queries = queries
        self.session = session

    @staticmethod
    def _operations(arguments: ManagePreferencesArguments):
        scope = PreferenceScope(arguments.scope)
        regular = []
        lifecycle = []
        from morrow.core.preference_models import PreferenceLifecycleOperation, PreferenceOperation

        for item in arguments.operations:
            if item.operation in {"add", "replace", "remove"}:
                regular.append(
                    PreferenceOperation(
                        operation=item.operation,
                        scope=scope,
                        preference_id=item.preference_id,
                        statement=item.statement,
                        evidence_ids=item.evidence_ids,
                    )
                )
            else:
                lifecycle.append(
                    PreferenceLifecycleOperation(
                        operation=item.operation,
                        scope=scope,
                        preference_id=item.preference_id or "",
                    )
                )
        return tuple(regular), tuple(lifecycle)

    def preflight(self, arguments: ManagePreferencesArguments) -> tuple[str, ...]:
        document = self.queries.document(arguments.scope)
        expected = (
            document.revision
            if arguments.expected_revision is None
            else arguments.expected_revision
        )
        if expected != document.revision:
            raise ToolExecutionError(
                ToolErrorCode.CONFLICT, "Preference document revision is stale"
            )
        try:
            regular, lifecycle = self._operations(arguments)
            reduce_preference_operations(document, regular, lifecycle)
        except ValueError as exc:
            raise ToolExecutionError(ToolErrorCode.INVALID_ARGUMENTS, str(exc)) from exc
        return (
            f"Preference batch ({len(arguments.operations)} operations)",
            f"scope={arguments.scope}",
            f"revision={document.revision}->{document.revision + 1}",
        )

    def apply(
        self, arguments: ManagePreferencesArguments, *, command_id: str | None = None
    ) -> dict[str, Any]:
        command_id = command_id or self.writer.id_source.new_id("cmd")
        regular, lifecycle = self._operations(arguments)
        existing = self.writer.journal.get_preference_write_batch_by_command(
            self.writer.workspace_id, command_id
        )
        if existing is not None:
            expected = (
                existing.expected_document_revision
                if arguments.expected_revision is None
                else arguments.expected_revision
            )
            prepared = self.writer.prepare(
                arguments.scope,
                expected,
                command_id,
                regular,
                lifecycle_operations=lifecycle,
            )
            result = self.writer.apply(prepared)
            return self._result(result, arguments, replayed=True)

        self.preflight(arguments)
        document = self.queries.document(arguments.scope)
        revision = (
            document.revision
            if arguments.expected_revision is None
            else arguments.expected_revision
        )
        prepared = self.writer.prepare(
            arguments.scope,
            revision,
            command_id,
            regular,
            lifecycle_operations=lifecycle,
        )
        result = self.writer.apply(prepared)
        return self._result(result, arguments)

    @staticmethod
    def _result(result, arguments: ManagePreferencesArguments, *, replayed: bool = False):
        return {
            "status": "replayed" if replayed or result.replayed else "applied",
            "scope": arguments.scope,
            "operations": [item.operation for item in arguments.operations],
            "revision": result.document.revision,
            "preference_ids": list(
                dict.fromkeys(
                    [*result.batch.allocated_add_ids]
                    + [
                        item.preference_id
                        for item in arguments.operations
                        if item.preference_id is not None
                    ]
                )
            ),
        }

    def _sync_session(self, document) -> None:
        if self.session is None:
            return
        from morrow.adapters.state.preference_projection import preferences_from_entries

        projected = preferences_from_entries(document.entries)
        if document.scope == PreferenceScope.GLOBAL.value:
            self.session.generic_global_preferences = document
            self.session.global_preferences = projected
            self.session.global_preferences_revision = document.revision
        else:
            self.session.generic_workspace_preferences = document
            self.session.workspace_preferences = projected
            self.session.preferences_revision = document.revision

    def apply_with_session_sync(
        self, arguments: ManagePreferencesArguments, *, command_id: str | None = None
    ) -> dict[str, Any]:
        result = self.apply(arguments, command_id=command_id)
        self._sync_session(self.queries.document(arguments.scope))
        return result


PREFERENCE_MANAGEMENT_TOOL_DESCRIPTION = (
    "仅当用户明确要求新增、替换、删除、启用或禁用持久化 Preference 时调用；"
    "一次调用只能操作一个 global 或 workspace scope，最多八项；删除保留为 tombstone。"
)


def _tool_error(error: Exception) -> ToolExecutionError:
    if isinstance(error, ToolExecutionError):
        return error
    code = getattr(error, "code", None)
    if code == "conflict":
        return ToolExecutionError(ToolErrorCode.CONFLICT, "Preference 版本冲突")
    if code in {
        "invalid_scope",
        "invalid_status",
        "invalid_revision",
        "invalid_command",
        "invalid_request",
        "invalid_operation",
        "operation_count",
        "mixed_scope",
        "duplicate_target",
        "target_missing",
        "target_deleted",
        "already_in_state",
        "duplicate",
        "disabled_duplicate",
    }:
        return ToolExecutionError(ToolErrorCode.INVALID_ARGUMENTS, "Preference 操作无效")
    if code == "not_found":
        return ToolExecutionError(ToolErrorCode.NOT_FOUND, "Preference 不存在")
    return ToolExecutionError(ToolErrorCode.EXECUTION_FAILED, "Preference 操作失败")


def make_preference_management_tool(service: PreferenceManagementService) -> RegisteredTool:
    def preview(arguments: ManagePreferencesArguments) -> list[str]:
        try:
            return list(service.preflight(arguments))
        except Exception as exc:
            raise _tool_error(exc) from None

    async def handler(arguments: ManagePreferencesArguments) -> ToolHandlerOutcome:
        try:
            return ToolHandlerOutcome(payload=service.apply(arguments))
        except Exception as exc:
            raise _tool_error(exc) from None

    def intent(arguments: ManagePreferencesArguments, _: ToolCallContext) -> OperationIntent:
        try:
            preview_lines = service.preflight(arguments)
        except Exception as exc:
            raise _tool_error(exc) from None
        return OperationIntent(
            kind=OperationKind.CONFIGURATION_WRITE,
            effect=ToolEffect.PERSISTENT_WRITE,
            preview_summary=preview_lines,
        )

    return make_tool(
        name="manage_preferences",
        description=PREFERENCE_MANAGEMENT_TOOL_DESCRIPTION,
        arguments_model=ManagePreferencesArguments,
        handler=handler,
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.PERSISTENT_WRITE,
            approval=ToolApproval.REQUIRED,
        ),
        approval_preview=preview,
        intent_resolver=intent,
        recovery_declaration=tool_declaration("manage_preferences"),
    )


__all__ = [
    "ManagePreferenceOperation",
    "ManagePreferencesArguments",
    "PreferenceManagementService",
    "make_preference_management_tool",
]
