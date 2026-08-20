"""SQLite repository for the v11 cross-store configuration promotion records."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.configuration_promotion import (
    ConfigurationActivation,
    ConfigurationActivationStatus,
    PromotionOperation,
    PromotionOperationState,
)
from morrow.core.store import StorageError, StorageErrorCode

_OPERATION_COLUMNS = (
    "operation_id, command_id, request_digest, workspace_id, candidate_id, "
    "candidate_row_version, target, scope, path, prepared_change_json, "
    "prepared_change_digest, state, before_revision, before_digest, after_digest, "
    "applied_revision, row_version, failure_code, created_at_unix, updated_at_unix, "
    "prepared_at_unix, finalized_at_unix"
)
_ACTIVATION_COLUMNS = (
    "activation_id, workspace_id, candidate_id, decision_id, operation_id, target, scope, path, "
    "operation, applied_revision, before_digest, after_digest, value_digest, "
    "inverse_command_json, inverse_command_digest, supersedes_activation_id, "
    "reverses_activation_id, status, created_at_unix, updated_at_unix"
)


def _unix(value: datetime | None) -> int | None:
    return None if value is None else int(value.timestamp())


def _from_unix(value: object | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(int(value), UTC)


def _workspace_error(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"{label} is outside the workspace")


class SqliteConfigurationPromotionJournal:
    """Bounded repository; all methods can participate in the caller's outer transaction."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def get_promotion_operation(
        self, workspace_id: str, operation_id: str
    ) -> PromotionOperation | None:
        row = self.backend.read_one(
            f"SELECT {_OPERATION_COLUMNS} FROM promotion_operations WHERE operation_id = ?",
            (operation_id,),
        )
        if row is None:
            return None
        if str(row[3]) != workspace_id:
            raise _workspace_error("promotion operation")
        return _operation_from_row(row)

    def get_promotion_operation_by_command(
        self, workspace_id: str, command_id: str
    ) -> PromotionOperation | None:
        row = self.backend.read_one(
            f"SELECT {_OPERATION_COLUMNS} FROM promotion_operations "
            "WHERE workspace_id = ? AND command_id = ?",
            (workspace_id, command_id),
        )
        return _operation_from_row(row) if row is not None else None

    def list_promotion_operations(
        self,
        workspace_id: str,
        *,
        state: PromotionOperationState | None = None,
        limit: int = 100,
    ) -> tuple[PromotionOperation, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion operation page is invalid")
        sql = f"SELECT {_OPERATION_COLUMNS} FROM promotion_operations WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if state is not None:
            sql += " AND state = ?"
            parameters.append(state.value)
        sql += " ORDER BY updated_at_unix ASC, operation_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _operation_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def put_promotion_operation(
        self, workspace_id: str, operation: PromotionOperation
    ) -> PromotionOperation:
        if operation.workspace_id != workspace_id:
            raise _workspace_error("promotion operation")

        def work() -> PromotionOperation:
            if self.get_promotion_operation(workspace_id, operation.operation_id) is not None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion operation exists")
            if (
                self.get_promotion_operation_by_command(workspace_id, operation.command_id)
                is not None
            ):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion command already exists")
            self.backend.executor().execute(
                f"INSERT INTO promotion_operations({_OPERATION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _operation_values(operation),
            )
            loaded = self.get_promotion_operation(workspace_id, operation.operation_id)
            if loaded is None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion operation unavailable")
            return loaded

        return self.backend.transact(work)

    def save_promotion_operation(
        self,
        workspace_id: str,
        operation: PromotionOperation,
        *,
        expected_row_version: int,
    ) -> PromotionOperation:
        if operation.workspace_id != workspace_id:
            raise _workspace_error("promotion operation")

        def work() -> PromotionOperation:
            existing = self.get_promotion_operation(workspace_id, operation.operation_id)
            if existing is None:
                raise StorageError(StorageErrorCode.NOT_FOUND, "promotion operation is missing")
            immutable = (
                "operation_id",
                "command_id",
                "request_digest",
                "workspace_id",
                "candidate_id",
                "candidate_row_version",
                "target",
                "scope",
                "path",
                "prepared_change_json",
                "prepared_change_digest",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(operation, name) for name in immutable):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion identity is immutable")
            if (
                existing.row_version != expected_row_version
                or operation.row_version != expected_row_version + 1
            ):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion operation is stale")
            self.backend.executor().execute(
                "UPDATE promotion_operations SET state = ?, before_revision = ?, "
                "before_digest = ?, after_digest = ?, applied_revision = ?, row_version = ?, "
                "failure_code = ?, updated_at_unix = ?, prepared_at_unix = ?, finalized_at_unix = ? "
                "WHERE operation_id = ? AND workspace_id = ? AND row_version = ?",
                (
                    operation.state.value,
                    operation.before_revision,
                    operation.before_digest,
                    operation.after_digest,
                    operation.applied_revision,
                    operation.row_version,
                    operation.failure_code.value if operation.failure_code is not None else None,
                    _unix(operation.updated_at),
                    _unix(operation.prepared_at),
                    _unix(operation.finalized_at),
                    operation.operation_id,
                    workspace_id,
                    expected_row_version,
                ),
            )
            loaded = self.get_promotion_operation(workspace_id, operation.operation_id)
            if loaded is None or loaded.row_version != operation.row_version:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "promotion operation is stale")
            return loaded

        return self.backend.transact(work)

    def get_configuration_activation(
        self, workspace_id: str, activation_id: str
    ) -> ConfigurationActivation | None:
        row = self.backend.read_one(
            f"SELECT {_ACTIVATION_COLUMNS} FROM configuration_activations WHERE activation_id = ?",
            (activation_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("configuration activation")
        return _activation_from_row(row)

    def list_configuration_activations(
        self,
        workspace_id: str,
        *,
        target: str | None = None,
        path: str | None = None,
        status: ConfigurationActivationStatus | None = None,
        limit: int = 100,
    ) -> tuple[ConfigurationActivation, ...]:
        if not 1 <= limit <= 500:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "configuration activation page is invalid"
            )
        sql = f"SELECT {_ACTIVATION_COLUMNS} FROM configuration_activations WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if target is not None:
            sql += " AND target = ?"
            parameters.append(target)
        if path is not None:
            sql += " AND path = ?"
            parameters.append(path)
        if status is not None:
            sql += " AND status = ?"
            parameters.append(status.value)
        sql += " ORDER BY created_at_unix ASC, activation_id ASC LIMIT ?"
        parameters.append(limit)
        return tuple(
            _activation_from_row(row) for row in self.backend.read_all(sql, tuple(parameters))
        )

    def put_configuration_activation(
        self, workspace_id: str, activation: ConfigurationActivation
    ) -> ConfigurationActivation:
        if activation.workspace_id != workspace_id:
            raise _workspace_error("configuration activation")

        def work() -> ConfigurationActivation:
            if (
                self.get_configuration_activation(workspace_id, activation.activation_id)
                is not None
            ):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "configuration activation exists")
            self.backend.executor().execute(
                f"INSERT INTO configuration_activations({_ACTIVATION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _activation_values(activation),
            )
            loaded = self.get_configuration_activation(workspace_id, activation.activation_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "configuration activation unavailable"
                )
            return loaded

        return self.backend.transact(work)

    def save_configuration_activation(
        self,
        workspace_id: str,
        activation: ConfigurationActivation,
    ) -> ConfigurationActivation:
        if activation.workspace_id != workspace_id:
            raise _workspace_error("configuration activation")

        def work() -> ConfigurationActivation:
            existing = self.get_configuration_activation(workspace_id, activation.activation_id)
            if existing is None:
                raise StorageError(
                    StorageErrorCode.NOT_FOUND, "configuration activation is missing"
                )
            immutable = (
                "activation_id",
                "workspace_id",
                "candidate_id",
                "decision_id",
                "operation_id",
                "target",
                "scope",
                "path",
                "operation",
                "applied_revision",
                "before_digest",
                "after_digest",
                "value_digest",
                "inverse_command_json",
                "inverse_command_digest",
                "supersedes_activation_id",
                "reverses_activation_id",
                "created_at",
            )
            if any(getattr(existing, name) != getattr(activation, name) for name in immutable):
                raise StorageError(StorageErrorCode.UNAVAILABLE, "activation identity is immutable")
            self.backend.executor().execute(
                "UPDATE configuration_activations SET status = ?, updated_at_unix = ? "
                "WHERE activation_id = ? AND workspace_id = ?",
                (
                    activation.status.value,
                    _unix(activation.updated_at),
                    activation.activation_id,
                    workspace_id,
                ),
            )
            loaded = self.get_configuration_activation(workspace_id, activation.activation_id)
            if loaded is None:
                raise StorageError(
                    StorageErrorCode.UNAVAILABLE, "configuration activation unavailable"
                )
            return loaded

        return self.backend.transact(work)


def _operation_values(operation: PromotionOperation) -> tuple[object, ...]:
    return (
        operation.operation_id,
        operation.command_id,
        operation.request_digest,
        operation.workspace_id,
        operation.candidate_id,
        operation.candidate_row_version,
        operation.target,
        operation.scope.value,
        operation.path,
        operation.prepared_change_json,
        operation.prepared_change_digest,
        operation.state.value,
        operation.before_revision,
        operation.before_digest,
        operation.after_digest,
        operation.applied_revision,
        operation.row_version,
        operation.failure_code.value if operation.failure_code is not None else None,
        _unix(operation.created_at),
        _unix(operation.updated_at),
        _unix(operation.prepared_at),
        _unix(operation.finalized_at),
    )


def _activation_values(activation: ConfigurationActivation) -> tuple[object, ...]:
    return (
        activation.activation_id,
        activation.workspace_id,
        activation.candidate_id,
        activation.decision_id,
        activation.operation_id,
        activation.target,
        activation.scope.value,
        activation.path,
        activation.operation.value,
        activation.applied_revision,
        activation.before_digest,
        activation.after_digest,
        activation.value_digest,
        activation.inverse_command_json,
        activation.inverse_command_digest,
        activation.supersedes_activation_id,
        activation.reverses_activation_id,
        activation.status.value,
        _unix(activation.created_at),
        _unix(activation.updated_at),
    )


def _operation_from_row(row: tuple[object, ...]) -> PromotionOperation:
    return PromotionOperation(
        operation_id=str(row[0]),
        command_id=str(row[1]),
        request_digest=str(row[2]),
        workspace_id=str(row[3]),
        candidate_id=str(row[4]),
        candidate_row_version=int(row[5]),
        target=str(row[6]),
        scope=str(row[7]),
        path=str(row[8]),
        prepared_change_json=str(row[9]),
        prepared_change_digest=str(row[10]),
        state=str(row[11]),
        before_revision=int(row[12]) if row[12] is not None else None,
        before_digest=str(row[13]) if row[13] is not None else None,
        after_digest=str(row[14]) if row[14] is not None else None,
        applied_revision=int(row[15]) if row[15] is not None else None,
        row_version=int(row[16]),
        failure_code=str(row[17]) if row[17] is not None else None,
        created_at=_from_unix(row[18]),
        updated_at=_from_unix(row[19]),
        prepared_at=_from_unix(row[20]),
        finalized_at=_from_unix(row[21]),
    )


def _activation_from_row(row: tuple[object, ...]) -> ConfigurationActivation:
    return ConfigurationActivation(
        activation_id=str(row[0]),
        workspace_id=str(row[1]),
        candidate_id=str(row[2]),
        decision_id=str(row[3]),
        operation_id=str(row[4]),
        target=str(row[5]),
        scope=str(row[6]),
        path=str(row[7]),
        operation=str(row[8]),
        applied_revision=int(row[9]),
        before_digest=str(row[10]) if row[10] is not None else None,
        after_digest=str(row[11]),
        value_digest=str(row[12]),
        inverse_command_json=str(row[13]),
        inverse_command_digest=str(row[14]),
        supersedes_activation_id=str(row[15]) if row[15] is not None else None,
        reverses_activation_id=str(row[16]) if row[16] is not None else None,
        status=str(row[17]),
        created_at=_from_unix(row[18]),
        updated_at=_from_unix(row[19]),
    )


__all__ = ["SqliteConfigurationPromotionJournal"]
