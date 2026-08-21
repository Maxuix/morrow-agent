"""Atomic same-scope Preference Writer saga.

The Writer keeps YAML as the active authority.  SQLite stores the bounded
before/after record before YAML publication and records the terminal state only
after the YAML document has been verified.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any

from morrow.adapters.state.preference_yaml import PreferenceYamlStore
from morrow.adapters.state.preference_yaml_types import (
    PreferenceYamlConflict,
    PreferenceYamlLoadStatus,
)
from morrow.core.domain import canonical_json_bytes
from morrow.core.ports import IdSource
from morrow.core.preference_documents import (
    GlobalConfigV2,
    PreferenceDocument,
    PreferenceEntriesPayload,
    WorkspacePreferenceDocumentV3,
)
from morrow.core.preference_models import (
    PREFERENCE_MAX_OPERATIONS,
    PREFERENCE_WRITE_BATCH_ID_PREFIX,
    PreferenceLifecycleOperation,
    PreferenceOperation,
    PreferenceScope,
)
from morrow.core.preference_operations import (
    PreferenceOperationError,
    reduce_preference_operations,
)
from morrow.core.preference_persistence_models import (
    PreferenceWriteBatch,
    PreferenceWriteBatchStatus,
)
from morrow.core.store import StorageError, StorageErrorCode
from morrow.runtime.ids import RandomIdSource


class PreferenceWriterError(RuntimeError):
    """Sanitized error raised by the direct Preference Writer boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PreferenceWriterConflict(PreferenceWriterError):
    def __init__(self, message: str = "Preference write conflicts with current state") -> None:
        super().__init__("conflict", message)


class PreferenceWriterNeedsResolution(PreferenceWriterError):
    def __init__(self, message: str = "Preference write requires recovery") -> None:
        super().__init__("needs_resolution", message)


class _WritePhase(StrEnum):
    PREPARED = "prepared"
    YAML_APPLIED = "yaml_applied"
    FINALIZED = "finalized"


@dataclass(frozen=True)
class PreferenceWritePreparation:
    """The immutable data needed to apply one prepared batch."""

    batch: PreferenceWriteBatch
    before_document: PreferenceDocument
    after_document: PreferenceDocument


@dataclass(frozen=True)
class PreferenceWriteResult:
    """Sanitized result returned after an apply or an idempotent replay."""

    batch: PreferenceWriteBatch
    document: PreferenceDocument
    replayed: bool = False


def _now(clock: Callable[[], datetime] | Any | None) -> datetime:
    if clock is None:
        value = datetime.now(UTC)
    else:
        value = clock() if callable(clock) else clock.now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Preference Writer clock must return an aware datetime")
    return value.astimezone(UTC)


def _digest(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _document_json(document: PreferenceDocument) -> str:
    return canonical_json_bytes(document.model_dump(mode="json")).decode("utf-8")


def _document_digest(document: PreferenceDocument) -> str:
    return _digest(
        {
            "schema_version": document.schema_version,
            "scope": document.scope,
            "revision": document.revision,
            "entries": [entry.model_dump(mode="json") for entry in document.entries],
        }
    )


def _authority_digest(value: GlobalConfigV2 | WorkspacePreferenceDocumentV3) -> str:
    payload = value.model_dump(mode="json")
    payload.pop("updated_at", None)
    return _digest(payload)


def _load_document_json(value: str | None, *, label: str) -> PreferenceDocument:
    if value is None:
        raise PreferenceWriterNeedsResolution(f"{label} document is missing")
    try:
        parsed = json.loads(value)
        if canonical_json_bytes(parsed).decode("utf-8") != value:
            raise ValueError("document is not canonical")
        return PreferenceDocument.model_validate(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PreferenceWriterNeedsResolution(f"{label} document is invalid") from exc


def _scope_value(scope: PreferenceScope | str) -> PreferenceScope:
    try:
        return scope if isinstance(scope, PreferenceScope) else PreferenceScope(scope)
    except ValueError as exc:
        raise PreferenceWriterError("invalid_scope", "Preference scope is invalid") from exc


class PreferenceWriter:
    """Prepare and apply one bounded same-scope Preference write."""

    def __init__(
        self,
        yaml_store: PreferenceYamlStore,
        journal: Any,
        workspace_id: str,
        *,
        id_source: IdSource | None = None,
        clock: Callable[[], datetime] | Any | None = None,
    ) -> None:
        self.yaml_store = yaml_store
        self.journal = getattr(journal, "preference_journal", journal)
        self.workspace_id = workspace_id
        self.id_source = id_source or RandomIdSource()
        self.clock = clock

    def prepare(
        self,
        scope: PreferenceScope | str,
        expected_revision: int,
        command_id: str,
        operations: Iterable[PreferenceOperation],
        *,
        lifecycle_operations: Iterable[PreferenceLifecycleOperation] = (),
        proposal_ids: Iterable[str] = (),
    ) -> PreferenceWritePreparation:
        durable_scope = _scope_value(scope)
        if durable_scope is PreferenceScope.SESSION:
            raise PreferenceWriterError(
                "invalid_scope", "durable Preference writes cannot use session scope"
            )
        operation_tuple = tuple(operations)
        lifecycle_tuple = tuple(lifecycle_operations)
        proposal_tuple = tuple(proposal_ids)
        if not operation_tuple and not lifecycle_tuple:
            raise PreferenceWriterError(
                "operation_count", "Preference batch must contain one to eight operations"
            )
        if len(operation_tuple) + len(lifecycle_tuple) > PREFERENCE_MAX_OPERATIONS:
            raise PreferenceWriterError(
                "operation_count", "Preference batch must contain one to eight operations"
            )
        existing = self.journal.get_preference_write_batch_by_command(self.workspace_id, command_id)
        if existing is not None:
            if not self._same_request(
                existing,
                durable_scope,
                expected_revision,
                operation_tuple,
                proposal_tuple,
                lifecycle_tuple,
            ):
                raise PreferenceWriterConflict("command ID was reused with a different write")
            return self._preparation(existing)

        load = self._load_authority(durable_scope)
        if load.status is not PreferenceYamlLoadStatus.OK or load.value is None:
            raise PreferenceWriterError("unavailable", "Preference YAML is not writable")
        if load.revision != expected_revision:
            raise PreferenceWriterConflict("Preference document revision is stale")
        before = self._document_from_value(durable_scope, load.value)
        if before.revision != expected_revision:
            raise PreferenceWriterConflict("Preference document revision is stale")

        allocated: list[str] = []

        def allocate_id(scope_value: PreferenceScope, index: int, operation: PreferenceOperation):
            del scope_value, index, operation
            identifier = self.id_source.new_id("pref")
            allocated.append(identifier)
            return identifier

        try:
            after = reduce_preference_operations(
                before,
                operation_tuple,
                lifecycle_tuple,
                now=_now(self.clock),
                allocate_id=allocate_id,
            )
        except PreferenceOperationError as exc:
            raise PreferenceWriterError(exc.code, str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise PreferenceWriterError(
                "invalid_operation", "Preference operation is invalid"
            ) from exc
        timestamp = _now(self.clock)
        batch = PreferenceWriteBatch(
            batch_id=self.id_source.new_id(PREFERENCE_WRITE_BATCH_ID_PREFIX),
            workspace_id=self.workspace_id,
            scope=durable_scope,
            command_id=command_id,
            operations=operation_tuple,
            lifecycle_operations=lifecycle_tuple,
            allocated_add_ids=tuple(allocated),
            proposal_ids=proposal_tuple,
            expected_document_revision=expected_revision,
            before_document_revision=before.revision,
            before_document_digest=_document_digest(before),
            after_document_revision=after.revision,
            after_document_digest=_document_digest(after),
            before_document_json=_document_json(before),
            after_document_json=_document_json(after),
            created_at=timestamp,
            prepared_at=timestamp,
        )
        try:
            stored = self.journal.put_preference_write_batch(self.workspace_id, batch)
        except StorageError as exc:
            raise self._translate_storage(exc) from exc
        return PreferenceWritePreparation(stored, before, after)

    def apply(
        self, prepared: PreferenceWritePreparation | PreferenceWriteBatch
    ) -> PreferenceWriteResult:
        batch = prepared.batch if isinstance(prepared, PreferenceWritePreparation) else prepared
        stored = self.journal.get_preference_write_batch(self.workspace_id, batch.batch_id)
        if stored is None:
            raise PreferenceWriterError("not_found", "Preference write batch is missing")
        if not self._same_identity(stored, batch):
            raise PreferenceWriterConflict("Preference write batch identity changed")
        preparation = (
            prepared
            if isinstance(prepared, PreferenceWritePreparation)
            else self._preparation(stored)
        )
        if stored.status is PreferenceWriteBatchStatus.FINALIZED:
            return PreferenceWriteResult(stored, preparation.after_document, replayed=True)
        if stored.status in {
            PreferenceWriteBatchStatus.NEEDS_RESOLUTION,
            PreferenceWriteBatchStatus.FAILED,
        }:
            raise PreferenceWriterNeedsResolution()

        current_load = self._load_authority(stored.scope)
        if current_load.status is not PreferenceYamlLoadStatus.OK or current_load.value is None:
            raise PreferenceWriterNeedsResolution("Preference YAML cannot be read for recovery")
        current = self._document_from_value(stored.scope, current_load.value)
        if self._matches(current, preparation.after_document, stored.after_document_digest):
            if stored.status is PreferenceWriteBatchStatus.PREPARED:
                stored = self._save_phase(stored, _WritePhase.YAML_APPLIED)
            return self._finalize(stored, preparation.after_document)
        if not self._matches(current, preparation.before_document, stored.before_document_digest):
            stored = self._save_needs_resolution(stored, "document_mismatch")
            raise PreferenceWriterNeedsResolution()

        try:
            self._publish(
                stored.scope,
                preparation.after_document,
                expected_revision=stored.before_document_revision,
                expected_value_digest=_authority_digest(current_load.value),
            )
        except PreferenceYamlConflict as exc:
            self._save_needs_resolution(stored, "revision_conflict")
            raise PreferenceWriterConflict() from exc
        except Exception as exc:
            raise PreferenceWriterError(
                "publish_failed", "Preference YAML publication failed"
            ) from exc

        verified_load = self._load_authority(stored.scope)
        if verified_load.status is not PreferenceYamlLoadStatus.OK or verified_load.value is None:
            raise PreferenceWriterNeedsResolution("Preference YAML cannot be verified")
        verified = self._document_from_value(stored.scope, verified_load.value)
        if not self._matches(verified, preparation.after_document, stored.after_document_digest):
            stored = self._save_needs_resolution(stored, "post_publish_mismatch")
            raise PreferenceWriterNeedsResolution()
        stored = self._save_phase(stored, _WritePhase.YAML_APPLIED)
        return self._finalize(stored, preparation.after_document)

    def add(
        self,
        scope: PreferenceScope | str,
        expected_revision: int,
        command_id: str,
        statement: str,
        *,
        evidence_ids: Iterable[str] = (),
        proposal_ids: Iterable[str] = (),
    ) -> PreferenceWriteResult:
        return self.apply(
            self.prepare(
                scope,
                expected_revision,
                command_id,
                (
                    PreferenceOperation(
                        operation="add",
                        scope=_scope_value(scope),
                        statement=statement,
                        evidence_ids=tuple(evidence_ids),
                    ),
                ),
                proposal_ids=proposal_ids,
            )
        )

    def replace(
        self,
        scope: PreferenceScope | str,
        expected_revision: int,
        command_id: str,
        preference_id: str,
        statement: str,
        *,
        evidence_ids: Iterable[str] = (),
        proposal_ids: Iterable[str] = (),
    ) -> PreferenceWriteResult:
        return self.apply(
            self.prepare(
                scope,
                expected_revision,
                command_id,
                (
                    PreferenceOperation(
                        operation="replace",
                        scope=_scope_value(scope),
                        preference_id=preference_id,
                        statement=statement,
                        evidence_ids=tuple(evidence_ids),
                    ),
                ),
                proposal_ids=proposal_ids,
            )
        )

    def remove(
        self,
        scope: PreferenceScope | str,
        expected_revision: int,
        command_id: str,
        preference_id: str,
        *,
        proposal_ids: Iterable[str] = (),
    ) -> PreferenceWriteResult:
        return self.apply(
            self.prepare(
                scope,
                expected_revision,
                command_id,
                (
                    PreferenceOperation(
                        operation="remove",
                        scope=_scope_value(scope),
                        preference_id=preference_id,
                    ),
                ),
                proposal_ids=proposal_ids,
            )
        )

    def enable(
        self,
        scope: PreferenceScope | str,
        expected_revision: int,
        command_id: str,
        preference_id: str,
    ) -> PreferenceWriteResult:
        return self.apply(
            self.prepare(
                scope,
                expected_revision,
                command_id,
                (),
                lifecycle_operations=(
                    PreferenceLifecycleOperation(
                        operation="enable",
                        scope=_scope_value(scope),
                        preference_id=preference_id,
                    ),
                ),
            )
        )

    def disable(
        self,
        scope: PreferenceScope | str,
        expected_revision: int,
        command_id: str,
        preference_id: str,
    ) -> PreferenceWriteResult:
        return self.apply(
            self.prepare(
                scope,
                expected_revision,
                command_id,
                (),
                lifecycle_operations=(
                    PreferenceLifecycleOperation(
                        operation="disable",
                        scope=_scope_value(scope),
                        preference_id=preference_id,
                    ),
                ),
            )
        )

    def _load_authority(self, scope: PreferenceScope):
        return (
            self.yaml_store.load_global()
            if scope is PreferenceScope.GLOBAL
            else self.yaml_store.load_workspace(self.workspace_id)
        )

    @staticmethod
    def _document_from_value(
        scope: PreferenceScope, value: GlobalConfigV2 | WorkspacePreferenceDocumentV3
    ) -> PreferenceDocument:
        if scope is PreferenceScope.GLOBAL:
            if not isinstance(value, GlobalConfigV2):
                raise PreferenceWriterError("scope", "global Preference YAML has wrong type")
            entries = value.preferences.entries
            return PreferenceDocument(
                scope="global",
                revision=value.revision,
                updated_at=value.updated_at,
                entries=entries,
            )
        if not isinstance(value, WorkspacePreferenceDocumentV3):
            raise PreferenceWriterError("scope", "workspace Preference YAML has wrong type")
        return PreferenceDocument(
            scope="workspace",
            revision=value.revision,
            updated_at=value.updated_at,
            entries=value.entries or (),
        )

    def _publish(
        self,
        scope: PreferenceScope,
        document: PreferenceDocument,
        *,
        expected_revision: int,
        expected_value_digest: str,
    ) -> None:
        current = self._load_authority(scope)
        if current.value is None:
            raise PreferenceWriterNeedsResolution()
        if scope is PreferenceScope.GLOBAL:
            assert isinstance(current.value, GlobalConfigV2)
            payload = PreferenceEntriesPayload(entries=document.entries)
            value = GlobalConfigV2.model_validate(
                current.value.model_dump(mode="python") | {"preferences": payload}
            )
            self.yaml_store.write_global(
                value,
                expected_revision=expected_revision,
                expected_value_digest=expected_value_digest,
            )
            return
        value = WorkspacePreferenceDocumentV3(
            revision=current.revision,
            updated_at=current.value.updated_at
            if isinstance(current.value, WorkspacePreferenceDocumentV3)
            else document.updated_at,
            state="present",
            entries=document.entries,
        )
        self.yaml_store.write_workspace(
            self.workspace_id,
            value,
            expected_revision=expected_revision,
            expected_value_digest=expected_value_digest,
        )

    def _preparation(self, batch: PreferenceWriteBatch) -> PreferenceWritePreparation:
        return PreferenceWritePreparation(
            batch=batch,
            before_document=_load_document_json(batch.before_document_json, label="before"),
            after_document=_load_document_json(batch.after_document_json, label="after"),
        )

    @staticmethod
    def _same_identity(left: PreferenceWriteBatch, right: PreferenceWriteBatch) -> bool:
        return (
            left.batch_id == right.batch_id
            and left.command_id == right.command_id
            and left.scope is right.scope
            and left.before_document_digest == right.before_document_digest
            and left.after_document_digest == right.after_document_digest
        )

    @staticmethod
    def _same_request(
        batch: PreferenceWriteBatch,
        scope: PreferenceScope,
        expected_revision: int,
        operations: tuple[PreferenceOperation, ...],
        proposal_ids: tuple[str, ...],
        lifecycle_operations: tuple[PreferenceLifecycleOperation, ...],
    ) -> bool:
        return (
            batch.scope is scope
            and batch.expected_document_revision == expected_revision
            and batch.operations == operations
            and batch.lifecycle_operations == lifecycle_operations
            and batch.proposal_ids == proposal_ids
        )

    @staticmethod
    def _matches(
        document: PreferenceDocument, expected: PreferenceDocument, expected_digest: str
    ) -> bool:
        return (
            document.revision == expected.revision and _document_digest(document) == expected_digest
        )

    def _save_phase(self, batch: PreferenceWriteBatch, phase: _WritePhase) -> PreferenceWriteBatch:
        timestamp = _now(self.clock)
        updates: dict[str, object] = {
            "status": (
                PreferenceWriteBatchStatus.YAML_APPLIED
                if phase is _WritePhase.YAML_APPLIED
                else PreferenceWriteBatchStatus.FINALIZED
            ),
            "row_version": batch.row_version + 1,
            "applied_at": batch.applied_at or timestamp,
        }
        if phase is _WritePhase.FINALIZED:
            updates["finalized_at"] = timestamp
        changed = PreferenceWriteBatch.model_validate(batch.model_dump(mode="python") | updates)
        try:
            return self.journal.save_preference_write_batch(
                self.workspace_id, changed, expected_row_version=batch.row_version
            )
        except StorageError as exc:
            raise self._translate_storage(exc) from exc
        except Exception as exc:
            raise PreferenceWriterError(
                "journal_failed", "Preference batch state could not be saved"
            ) from exc

    def _save_needs_resolution(
        self, batch: PreferenceWriteBatch, recovery_code: str
    ) -> PreferenceWriteBatch:
        changed = PreferenceWriteBatch.model_validate(
            batch.model_dump(mode="python")
            | {
                "status": PreferenceWriteBatchStatus.NEEDS_RESOLUTION,
                "recovery_code": recovery_code,
                "row_version": batch.row_version + 1,
            }
        )
        try:
            return self.journal.save_preference_write_batch(
                self.workspace_id, changed, expected_row_version=batch.row_version
            )
        except StorageError as exc:
            raise self._translate_storage(exc) from exc
        except Exception as exc:
            raise PreferenceWriterError(
                "journal_failed", "Preference recovery state could not be saved"
            ) from exc

    def _finalize(
        self, batch: PreferenceWriteBatch, document: PreferenceDocument
    ) -> PreferenceWriteResult:
        if batch.status is PreferenceWriteBatchStatus.FINALIZED:
            return PreferenceWriteResult(batch, document, replayed=True)
        finalized = self._save_phase(batch, _WritePhase.FINALIZED)
        return PreferenceWriteResult(finalized, document)

    @staticmethod
    def _translate_storage(exc: StorageError) -> PreferenceWriterError:
        if exc.code is StorageErrorCode.NOT_FOUND:
            return PreferenceWriterError("not_found", str(exc))
        if exc.code is StorageErrorCode.BUSY:
            return PreferenceWriterError("busy", str(exc))
        if exc.code is StorageErrorCode.NEEDS_REPAIR:
            return PreferenceWriterNeedsResolution()
        return PreferenceWriterError("unavailable", str(exc))


__all__ = [
    "PreferenceWritePreparation",
    "PreferenceWriteResult",
    "PreferenceWriter",
    "PreferenceWriterConflict",
    "PreferenceWriterError",
    "PreferenceWriterNeedsResolution",
]
