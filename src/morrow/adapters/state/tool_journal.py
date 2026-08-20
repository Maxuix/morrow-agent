"""SQLite persistence for durable tool executions and approvals."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import (
    ArtifactReference,
    DurableAgentRun,
    DurableTaskRun,
    canonical_json_bytes,
)
from morrow.core.execution import (
    ApprovalResolution,
    DurableApproval,
    DurableToolExecution,
    DurableToolFacts,
    EffectClass,
    HandlerResultEnvelope,
    PreparedIntent,
    ToolExecutionDisposition,
    ToolExecutionState,
    intent_hash,
    request_execution_cancellation,
    revoke_approval,
)
from morrow.core.permissions import (
    CapabilityGrant,
    IsolationLabel,
    PermissionEvidenceError,
    PermissionSnapshot,
    assert_grant_snapshot_matches,
)
from morrow.core.store import StorageError, StorageErrorCode

_EXECUTION_COLUMNS = (
    "tool_execution_id, workspace_id, session_id, task_run_id, turn_id, agent_run_id, "
    "assistant_record_id, call_id, ordinal, tool_name, state, disposition, row_version, "
    "retry_of_execution_id, approval_id, intent_json, intent_hash, schema_digest, "
    "permission_context_digest, result_envelope_json, facts_json, error_code, error_detail, "
    "created_at_unix, executing_at_unix, handler_completed_at_unix, closed_at_unix, "
    "artifact_refs_json, permission_snapshot_id, grant_id, isolation, "
    "cancel_requested_at_unix, cancel_request_reason"
)
_APPROVAL_COLUMNS = (
    "approval_id, tool_execution_id, intent_hash, tool_schema_digest, "
    "permission_context_digest, requested_scope, granted_scope, preview_json, preview_digest, "
    "row_version, created_at_unix, expires_at_unix, resolution, resolved_at_unix, "
    "consumed_at_unix, command_id, permission_snapshot_id, grant_id, isolation, "
    "revoked_at_unix, revocation_reason"
)
_APPROVAL_SELECT = (
    "a.approval_id, a.tool_execution_id, a.intent_hash, a.tool_schema_digest, "
    "a.permission_context_digest, a.requested_scope, a.granted_scope, a.preview_json, "
    "a.preview_digest, a.row_version, a.created_at_unix, a.expires_at_unix, a.resolution, "
    "a.resolved_at_unix, a.consumed_at_unix, a.command_id, a.permission_snapshot_id, "
    "a.grant_id, a.isolation, a.revoked_at_unix, a.revocation_reason"
)


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _optional_unix(value: datetime | None) -> int | None:
    return _unix(value) if value is not None else None


class SqliteToolJournal:
    """Bounded tool execution repository sharing one outer transaction backend."""

    def __init__(
        self,
        backend: SqliteJournalBackend,
        *,
        get_agent_run: Callable[[str, str], DurableAgentRun | None],
        get_task: Callable[[str, str], DurableTaskRun | None],
        get_permission_snapshot: Callable[[str, str], PermissionSnapshot | None],
        get_capability_grant: Callable[[str, str], CapabilityGrant | None],
        validate_artifact_refs: Callable[..., None],
        replace_artifact_refs: Callable[..., None],
    ) -> None:
        self.backend = backend
        self.get_agent_run = get_agent_run
        self.get_task = get_task
        self.get_permission_snapshot = get_permission_snapshot
        self.get_capability_grant = get_capability_grant
        self.validate_artifact_refs = validate_artifact_refs
        self.replace_artifact_refs = replace_artifact_refs

    def put_execution(
        self, workspace_id: str, execution: DurableToolExecution
    ) -> DurableToolExecution:
        def work() -> DurableToolExecution:
            self.insert_execution(workspace_id, execution)
            loaded = self.get_execution(workspace_id, execution.tool_execution_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational execution could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableToolExecution | None:
        row = self.backend.read_one(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE tool_execution_id = ? AND workspace_id = ?",
            (tool_execution_id, workspace_id),
        )
        return _execution_from_row(row) if row is not None else None

    def list_executions(
        self, workspace_id: str, *, agent_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self.backend.read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND agent_run_id = ? ORDER BY ordinal ASC",
            (workspace_id, agent_run_id),
        )
        return tuple(_execution_from_row(row) for row in rows)

    def list_executions_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self.backend.read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND grant_id = ? ORDER BY created_at_unix ASC, ordinal ASC",
            (workspace_id, grant_id),
        )
        return tuple(_execution_from_row(row) for row in rows)

    def list_session_executions(
        self, workspace_id: str, session_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self.backend.read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND session_id = ? "
            "ORDER BY created_at_unix ASC, ordinal ASC",
            (workspace_id, session_id),
        )
        return tuple(_execution_from_row(row) for row in rows)

    def list_task_executions(
        self, workspace_id: str, task_run_id: str
    ) -> tuple[DurableToolExecution, ...]:
        rows = self.backend.read_all(
            f"SELECT {_EXECUTION_COLUMNS} FROM tool_executions "
            "WHERE workspace_id = ? AND task_run_id = ? "
            "ORDER BY created_at_unix ASC, ordinal ASC, tool_execution_id ASC",
            (workspace_id, task_run_id),
        )
        return tuple(_execution_from_row(row) for row in rows)

    def save_execution(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        if execution.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution is outside the workspace"
            )
        return self.backend.transact(
            lambda: self.save_execution_in_txn(
                workspace_id, execution, expected_row_version=expected_row_version
            )
        )

    def request_cancellation_in_txn(
        self,
        workspace_id: str,
        tool_execution_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> DurableToolExecution | None:
        existing = self.get_execution(workspace_id, tool_execution_id)
        if existing is None or existing.state is not ToolExecutionState.EXECUTING:
            return existing
        requested = request_execution_cancellation(
            existing,
            expected_row_version=existing.row_version,
            now=now,
            reason=reason,
        )
        return self.save_execution_in_txn(
            workspace_id, requested, expected_row_version=existing.row_version
        )

    def save_execution_in_txn(
        self,
        workspace_id: str,
        execution: DurableToolExecution,
        *,
        expected_row_version: int,
    ) -> DurableToolExecution:
        if execution.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution is outside the workspace"
            )
        existing = self.get_execution(workspace_id, execution.tool_execution_id)
        if existing is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational execution is missing")
        if existing.row_version != expected_row_version:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution row version is stale"
            )
        if execution.row_version != expected_row_version + 1:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution row version is stale"
            )
        if (
            existing.permission_snapshot_id != execution.permission_snapshot_id
            or existing.grant_id != execution.grant_id
            or existing.isolation != execution.isolation
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational execution permission evidence is immutable",
            )
        self.validate_artifact_refs(
            workspace_id,
            execution.artifact_refs,
            session_id=execution.session_id,
            task_run_id=execution.task_run_id,
        )
        self.backend.executor().execute(
            """
            UPDATE tool_executions
            SET state = ?, disposition = ?, row_version = ?, approval_id = ?,
                result_envelope_json = ?, facts_json = ?, error_code = ?,
                error_detail = ?, executing_at_unix = ?,
                handler_completed_at_unix = ?, closed_at_unix = ?, artifact_refs_json = ?,
                cancel_requested_at_unix = ?, cancel_request_reason = ?
            WHERE tool_execution_id = ? AND workspace_id = ? AND row_version = ?
            """,
            (
                execution.state.value,
                execution.disposition.value,
                execution.row_version,
                execution.approval_id,
                _optional_json(execution.result_envelope),
                _optional_json(execution.facts),
                execution.error_code,
                execution.error_detail,
                _optional_unix(execution.executing_at),
                _optional_unix(execution.handler_completed_at),
                _optional_unix(execution.closed_at),
                _optional_json(execution.artifact_refs),
                _optional_unix(execution.cancel_requested_at),
                execution.cancel_request_reason,
                execution.tool_execution_id,
                workspace_id,
                expected_row_version,
            ),
        )
        self.replace_artifact_refs(
            workspace_id,
            owner_kind="tool_execution",
            owner_id=execution.tool_execution_id,
            references=execution.artifact_refs,
            created_at=execution.created_at,
        )
        loaded = self.get_execution(workspace_id, execution.tool_execution_id)
        if loaded is None or loaded.row_version != execution.row_version:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution row version is stale"
            )
        return loaded

    def put_approval(self, workspace_id: str, approval: DurableApproval) -> DurableApproval:
        def work() -> DurableApproval:
            execution = self.get_execution(workspace_id, approval.tool_execution_id)
            if execution is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "operational execution is missing")
            if (
                approval.intent_hash != intent_hash(execution.intent)
                or approval.tool_schema_digest != execution.intent.schema_digest
                or approval.permission_context_digest != execution.intent.permission_context_digest
                or approval.permission_snapshot_id != execution.permission_snapshot_id
                or approval.grant_id != execution.grant_id
                or approval.isolation != execution.isolation
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational approval does not match the execution",
                )
            elevated_intent = (
                execution.tool_name == "run_command"
                and execution.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
                and execution.intent.requires_approval
                and execution.grant_id is not None
            )
            if approval.grant_id is None and elevated_intent:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "elevated Host approval requires capability grant evidence",
                )
            if approval.grant_id is not None:
                snapshot = self.get_permission_snapshot(
                    workspace_id, approval.permission_snapshot_id or ""
                )
                grant = self.get_capability_grant(workspace_id, approval.grant_id)
                if snapshot is None or grant is None:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational approval permission evidence is missing",
                    )
                try:
                    assert_grant_snapshot_matches(
                        snapshot,
                        grant,
                        now=approval.created_at,
                        workspace_id=workspace_id,
                        task_run_id=execution.task_run_id,
                        agent_run_id=execution.agent_run_id,
                    )
                except PermissionEvidenceError as exc:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational approval capability grant is not active",
                    ) from exc
            self.backend.executor().execute(
                f"INSERT INTO approvals({_APPROVAL_COLUMNS}) "
                f"VALUES ({', '.join('?' for _ in range(21))})",
                (
                    approval.approval_id,
                    approval.tool_execution_id,
                    approval.intent_hash,
                    approval.tool_schema_digest,
                    approval.permission_context_digest,
                    approval.requested_scope,
                    approval.granted_scope,
                    canonical_json_bytes(list(approval.preview)).decode("utf-8"),
                    approval.preview_digest,
                    approval.row_version,
                    _unix(approval.created_at),
                    _unix(approval.expires_at),
                    approval.resolution.value,
                    _optional_unix(approval.resolved_at),
                    _optional_unix(approval.consumed_at),
                    approval.command_id,
                    approval.permission_snapshot_id,
                    approval.grant_id,
                    approval.isolation.value if approval.isolation is not None else None,
                    _optional_unix(approval.revoked_at),
                    approval.revocation_reason,
                ),
            )
            loaded = self.get_approval(workspace_id, approval.approval_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "operational approval could not be read"
                )
            return loaded

        return self.backend.transact(work)

    def get_approval(self, workspace_id: str, approval_id: str) -> DurableApproval | None:
        row = self.backend.read_one(
            f"SELECT {_APPROVAL_SELECT} FROM approvals a "
            "JOIN tool_executions e ON e.tool_execution_id = a.tool_execution_id "
            "WHERE a.approval_id = ? AND e.workspace_id = ?",
            (approval_id, workspace_id),
        )
        return _approval_from_row(row) if row is not None else None

    def get_approval_for_execution(
        self, workspace_id: str, tool_execution_id: str
    ) -> DurableApproval | None:
        row = self.backend.read_one(
            f"SELECT {_APPROVAL_SELECT} FROM approvals a "
            "JOIN tool_executions e ON e.tool_execution_id = a.tool_execution_id "
            "WHERE a.tool_execution_id = ? AND e.workspace_id = ?",
            (tool_execution_id, workspace_id),
        )
        return _approval_from_row(row) if row is not None else None

    def save_approval(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        return self.backend.transact(
            lambda: self.save_approval_in_txn(
                workspace_id, approval, expected_row_version=expected_row_version
            )
        )

    def list_approvals_for_grant(
        self, workspace_id: str, grant_id: str
    ) -> tuple[DurableApproval, ...]:
        rows = self.backend.read_all(
            f"SELECT {_APPROVAL_SELECT} FROM approvals a "
            "JOIN tool_executions e ON e.tool_execution_id = a.tool_execution_id "
            "WHERE e.workspace_id = ? AND a.grant_id = ? "
            "ORDER BY a.created_at_unix ASC, a.approval_id ASC",
            (workspace_id, grant_id),
        )
        return tuple(_approval_from_row(row) for row in rows)

    def revoke_approval_in_txn(
        self,
        workspace_id: str,
        approval_id: str,
        *,
        now: datetime,
        reason: str,
    ) -> DurableApproval | None:
        existing = self.get_approval(workspace_id, approval_id)
        if existing is None:
            return None
        revoked = revoke_approval(
            existing,
            expected_row_version=existing.row_version,
            now=now,
            reason=reason,
        )
        return self.save_approval_in_txn(
            workspace_id, revoked, expected_row_version=existing.row_version
        )

    def save_approval_in_txn(
        self,
        workspace_id: str,
        approval: DurableApproval,
        *,
        expected_row_version: int,
    ) -> DurableApproval:
        existing = self.get_approval(workspace_id, approval.approval_id)
        if existing is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "operational approval is missing")
        if existing.row_version != expected_row_version:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational approval row version is stale"
            )
        if approval.row_version != expected_row_version + 1:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational approval row version is stale"
            )
        if (
            existing.permission_snapshot_id != approval.permission_snapshot_id
            or existing.grant_id != approval.grant_id
            or existing.isolation != approval.isolation
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational approval permission evidence is immutable",
            )
        self.backend.executor().execute(
            """
            UPDATE approvals
            SET granted_scope = ?, row_version = ?, resolution = ?,
                resolved_at_unix = ?, consumed_at_unix = ?, command_id = ?,
                revoked_at_unix = ?, revocation_reason = ?
            WHERE approval_id = ? AND row_version = ?
            """,
            (
                approval.granted_scope,
                approval.row_version,
                approval.resolution.value,
                _optional_unix(approval.resolved_at),
                _optional_unix(approval.consumed_at),
                approval.command_id,
                _optional_unix(approval.revoked_at),
                approval.revocation_reason,
                approval.approval_id,
                expected_row_version,
            ),
        )
        loaded = self.get_approval(workspace_id, approval.approval_id)
        if loaded is None or loaded.row_version != approval.row_version:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational approval row version is stale"
            )
        return loaded

    def insert_execution(self, workspace_id: str, execution: DurableToolExecution) -> None:
        if execution.workspace_id != workspace_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution is outside the workspace"
            )
        run = self.get_agent_run(workspace_id, execution.agent_run_id)
        if (
            run is None
            or run.session_id != execution.session_id
            or run.turn_id != execution.turn_id
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution does not belong to the run"
            )
        if run.permission_snapshot_id != execution.permission_snapshot_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational execution permission snapshot does not match the run",
            )
        if execution.permission_snapshot_id is not None:
            snapshot = self.get_permission_snapshot(workspace_id, execution.permission_snapshot_id)
            if (
                snapshot is None
                or snapshot.agent_run_id != execution.agent_run_id
                or snapshot.task_run_id != execution.task_run_id
                or snapshot.turn_id != execution.turn_id
            ):
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "operational execution permission snapshot is mismatched",
                )
            elevated_intent = (
                execution.tool_name == "run_command"
                and execution.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
                and execution.intent.requires_approval
            )
            if snapshot.grant_id is None:
                if execution.grant_id is not None or execution.isolation is not None:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "ordinary execution cannot carry elevated permission evidence",
                    )
            elif elevated_intent:
                if execution.grant_id != snapshot.grant_id:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "operational execution grant does not match the snapshot",
                    )
                if execution.isolation is not IsolationLabel.UNCONFINED_HOST:
                    raise StorageError(
                        StorageErrorCode.UNAVAILABLE,
                        "elevated execution requires the unconfined_host label",
                    )
            elif execution.grant_id is not None or execution.isolation is not None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE,
                    "only an approved opaque Host command may carry elevated evidence",
                )
        task = self.get_task(workspace_id, execution.task_run_id)
        if task is None or task.session_id != execution.session_id:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational execution does not belong to the task"
            )
        self.validate_artifact_refs(
            workspace_id,
            execution.artifact_refs,
            session_id=execution.session_id,
            task_run_id=execution.task_run_id,
        )
        self.backend.executor().execute(
            f"INSERT INTO tool_executions({_EXECUTION_COLUMNS}) "
            f"VALUES ({', '.join('?' for _ in range(33))})",
            (
                execution.tool_execution_id,
                execution.workspace_id,
                execution.session_id,
                execution.task_run_id,
                execution.turn_id,
                execution.agent_run_id,
                execution.assistant_record_id,
                execution.call_id,
                execution.ordinal,
                execution.tool_name,
                execution.state.value,
                execution.disposition.value,
                execution.row_version,
                execution.retry_of_execution_id,
                execution.approval_id,
                canonical_json_bytes(execution.intent.model_dump(mode="json")).decode("utf-8"),
                intent_hash(execution.intent),
                execution.intent.schema_digest,
                execution.intent.permission_context_digest,
                _optional_json(execution.result_envelope),
                _optional_json(execution.facts),
                execution.error_code,
                execution.error_detail,
                _unix(execution.created_at),
                _optional_unix(execution.executing_at),
                _optional_unix(execution.handler_completed_at),
                _optional_unix(execution.closed_at),
                _optional_json(execution.artifact_refs),
                execution.permission_snapshot_id,
                execution.grant_id,
                execution.isolation.value if execution.isolation is not None else None,
                _optional_unix(execution.cancel_requested_at),
                execution.cancel_request_reason,
            ),
        )
        self.replace_artifact_refs(
            workspace_id,
            owner_kind="tool_execution",
            owner_id=execution.tool_execution_id,
            references=execution.artifact_refs,
            created_at=execution.created_at,
        )


def _optional_json(value: object | None) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
    elif isinstance(value, (tuple, list)):
        dumped = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value
        ]
    else:
        dumped = value
    return canonical_json_bytes(dumped).decode("utf-8")


def _artifact_refs_from_raw(raw: object) -> tuple[ArtifactReference, ...]:
    try:
        payload = json.loads(str(raw))
        if not isinstance(payload, list):
            raise TypeError
        return tuple(ArtifactReference.model_validate(item) for item in payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational artifact references are invalid"
        ) from exc


def _load_mapping(raw: object, *, label: str) -> dict:
    payload = json.loads(str(raw))
    if not isinstance(payload, dict):
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, f"{label} is not a mapping")
    return payload


def _execution_from_row(row: tuple[object, ...]) -> DurableToolExecution:
    try:
        intent = PreparedIntent.model_validate(_load_mapping(row[15], label="prepared intent"))
        envelope = (
            HandlerResultEnvelope.model_validate(
                _load_mapping(row[19], label="tool result envelope")
            )
            if row[19] is not None
            else None
        )
        facts = (
            DurableToolFacts.model_validate(_load_mapping(row[20], label="structured tool facts"))
            if row[20] is not None
            else None
        )
        artifact_refs = _artifact_refs_from_raw(row[27])
        return DurableToolExecution(
            tool_execution_id=str(row[0]),
            workspace_id=str(row[1]),
            session_id=str(row[2]),
            task_run_id=str(row[3]),
            turn_id=str(row[4]),
            agent_run_id=str(row[5]),
            assistant_record_id=str(row[6]) if row[6] is not None else None,
            call_id=str(row[7]),
            ordinal=int(row[8]),
            tool_name=str(row[9]),
            state=ToolExecutionState(str(row[10])),
            disposition=ToolExecutionDisposition(str(row[11])),
            row_version=int(row[12]),
            retry_of_execution_id=str(row[13]) if row[13] is not None else None,
            approval_id=str(row[14]) if row[14] is not None else None,
            intent=intent,
            result_envelope=envelope,
            facts=facts,
            artifact_refs=artifact_refs,
            error_code=str(row[21]) if row[21] is not None else None,
            error_detail=str(row[22]) if row[22] is not None else None,
            created_at=_from_unix(row[23]),
            executing_at=_from_unix(row[24]) if row[24] is not None else None,
            handler_completed_at=_from_unix(row[25]) if row[25] is not None else None,
            closed_at=_from_unix(row[26]) if row[26] is not None else None,
            permission_snapshot_id=str(row[28]) if row[28] is not None else None,
            grant_id=str(row[29]) if row[29] is not None else None,
            isolation=IsolationLabel(str(row[30])) if row[30] is not None else None,
            cancel_requested_at=_from_unix(row[31]) if row[31] is not None else None,
            cancel_request_reason=str(row[32]) if row[32] is not None else None,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational tool execution is invalid"
        ) from exc


def _approval_from_row(row: tuple[object, ...]) -> DurableApproval:
    try:
        preview_raw = json.loads(str(row[7]))
        if not isinstance(preview_raw, list) or any(
            not isinstance(item, str) for item in preview_raw
        ):
            raise ValueError("approval preview is not a string list")
        return DurableApproval(
            approval_id=str(row[0]),
            tool_execution_id=str(row[1]),
            intent_hash=str(row[2]),
            tool_schema_digest=str(row[3]),
            permission_context_digest=str(row[4]),
            requested_scope=str(row[5]),
            granted_scope=str(row[6]) if row[6] is not None else None,
            preview=tuple(str(item) for item in preview_raw),
            preview_digest=str(row[8]),
            row_version=int(row[9]),
            created_at=_from_unix(row[10]),
            expires_at=_from_unix(row[11]),
            resolution=ApprovalResolution(str(row[12])),
            resolved_at=_from_unix(row[13]) if row[13] is not None else None,
            consumed_at=_from_unix(row[14]) if row[14] is not None else None,
            command_id=str(row[15]) if row[15] is not None else None,
            permission_snapshot_id=str(row[16]) if row[16] is not None else None,
            grant_id=str(row[17]) if row[17] is not None else None,
            isolation=IsolationLabel(str(row[18])) if row[18] is not None else None,
            revoked_at=_from_unix(row[19]) if row[19] is not None else None,
            revocation_reason=str(row[20]) if row[20] is not None else None,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "operational approval is invalid"
        ) from exc
