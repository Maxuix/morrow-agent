"""Explicit dependencies and command bookkeeping shared by application command handlers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from morrow.application.checkpoints import ContextCheckpointError
from morrow.application.grants import CapabilityGrantError
from morrow.application.recovery import RecoveryService
from morrow.application.tasks import TaskCommandConflict, TaskCommandError, TaskService
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
    ApplicationEvent,
)
from morrow.core.artifacts import ArtifactError
from morrow.core.domain import COMMAND_ID_PREFIX, canonical_json_bytes, sha256_digest
from morrow.core.ports import IdSource
from morrow.core.recovery import RecoveryDecisionError, RecoveryReport
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.durable_log import restore_conversation_log


def request_digest(operation: str, payload: dict[str, object]) -> str:
    return sha256_digest(canonical_json_bytes({"operation": operation, **payload}))


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ApplicationCommandContext:
    """Narrow, explicit dependency set for transactional command handlers."""

    journal: Any
    workspace_id: str
    id_source: IdSource
    clock: Callable[[], datetime]
    tasks: TaskService
    recovery: RecoveryService | None
    persistence: Any = None

    def get_recovery(self, report_id: str) -> RecoveryReport | None:
        if self.recovery is None:
            return None
        return self._query(lambda: self.journal.get_report(self.workspace_id, report_id))

    def _prepare(self, operation: str, payload: dict[str, object], command_id: str | None):
        command_id = command_id or self.id_source.new_id(COMMAND_ID_PREFIX)
        digest = request_digest(operation, payload)
        try:
            existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
        except Exception as exc:
            raise self._translate_exception(exc) from exc
        if existing is not None:
            if existing.request_digest != digest:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT,
                    "command ID was reused with a different request",
                )
            return (
                command_id,
                digest,
                existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY}),
            )
        return command_id, digest, None

    def _replay_in_txn(self, txn, command_id: str, digest: str):
        existing = txn.get_application_command_receipt(self.workspace_id, command_id)
        if existing is None:
            return None
        if existing.request_digest != digest:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT,
                "command ID was reused with a different request",
            )
        return existing.model_copy(update={"disposition": ApplicationCommandDisposition.REPLAY})

    def _receipt(
        self,
        txn,
        *,
        command_id: str,
        operation: str,
        digest: str,
        session_id: str | None,
        result_kind: str,
        result_id: str,
        event_cursor: int,
        row_version: int | None = None,
    ) -> ApplicationCommandReceipt:
        return txn.put_application_command_receipt_in_txn(
            self.workspace_id,
            ApplicationCommandReceipt(
                command_id=command_id,
                workspace_id=self.workspace_id,
                session_id=session_id,
                operation=operation,
                request_digest=digest,
                result_kind=result_kind,
                result_id=result_id,
                event_cursor=event_cursor,
                row_version=row_version,
            ),
        )

    def _event(
        self,
        txn,
        *,
        event_type: str,
        aggregate_kind: str,
        aggregate_id: str,
        payload: dict[str, object],
    ) -> ApplicationEvent:
        return txn.put_application_event_in_txn(
            self.workspace_id,
            ApplicationEvent(
                event_id=self.id_source.new_id("evt"),
                workspace_id=self.workspace_id,
                event_type=event_type,
                aggregate_kind=aggregate_kind,
                aggregate_id=aggregate_id,
                payload=payload,
                created_at=_now(self.clock),
            ),
        )

    def _translate[T](self, call: Callable[[], T]) -> T:
        try:
            return call()
        except ApplicationError:
            raise
        except Exception as exc:
            raise self._translate_exception(exc) from exc

    def _query[T](self, call: Callable[[], T]) -> T:
        return self._translate(call)

    def _restore_log_projection(self, log, session_id: str) -> None:
        try:
            log.install_snapshot(
                restore_conversation_log(self.journal, self.workspace_id, session_id).snapshot()
            )
        except Exception:
            return

    @staticmethod
    def _translate_exception(exc: Exception) -> ApplicationError:
        if isinstance(exc, StorageError):
            if "outside the workspace" in str(exc):
                return ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, str(exc))
            mapping = {
                StorageErrorCode.NOT_FOUND: ApplicationErrorCode.NOT_FOUND,
                StorageErrorCode.BUSY: ApplicationErrorCode.BUSY,
                StorageErrorCode.NEEDS_REPAIR: ApplicationErrorCode.NEEDS_RECOVERY,
                StorageErrorCode.UNAVAILABLE: ApplicationErrorCode.UNAVAILABLE,
                StorageErrorCode.FUTURE_SCHEMA: ApplicationErrorCode.UNAVAILABLE,
                StorageErrorCode.IDENTITY_MISMATCH: ApplicationErrorCode.NEEDS_RECOVERY,
            }
            return ApplicationError(mapping[exc.code], str(exc))
        if isinstance(exc, TaskCommandConflict):
            return ApplicationError(ApplicationErrorCode.CONFLICT, str(exc))
        if isinstance(exc, (TaskCommandError, RecoveryDecisionError, ContextCheckpointError)):
            text = str(exc)
            code = getattr(exc, "application_code", None) or (
                ApplicationErrorCode.STALE
                if "stale" in text.casefold()
                else ApplicationErrorCode.INVALID
            )
            return ApplicationError(code, text)
        if isinstance(exc, CapabilityGrantError):
            return ApplicationError(exc.code, str(exc))
        if isinstance(exc, ArtifactError):
            code = (
                ApplicationErrorCode.NOT_FOUND
                if exc.code.value.endswith("missing")
                else ApplicationErrorCode.CONFLICT
                if exc.code.value.endswith("conflict")
                else ApplicationErrorCode.INVALID
            )
            return ApplicationError(code, exc.message)
        if isinstance(exc, ValueError):
            return ApplicationError(ApplicationErrorCode.INVALID, "application input is invalid")
        return ApplicationError(ApplicationErrorCode.UNAVAILABLE, "application command failed")
