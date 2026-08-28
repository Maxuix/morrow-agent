"""Command idempotency, recovery evidence and SQLite finalization helpers."""

from __future__ import annotations

import hashlib

from morrow.application.skills.bindings import PreparedBindingChange
from morrow.application.skills.recovery import SkillOperationRecord
from morrow.core.application import (
    ApplicationCommandDisposition,
    ApplicationCommandReceipt,
    ApplicationEvent,
)
from morrow.core.domain import canonical_json_bytes, validate_prefixed_id
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillConflictStatus,
    SkillDefinition,
    SkillVersion,
)
from morrow.core.skills.identity import SKV_ID_PREFIX, validate_skv_id
from morrow.core.skills.trust import SourceKind

from .errors import SkillLifecycleError, SkillLifecycleNeedsResolution


class SkillLifecyclePersistenceMixin:
    """Durable command/recovery helpers mixed into the lifecycle service."""

    def _check_references(self, version_id: str) -> None:
        if self.reference_checker is not None and self.reference_checker(version_id):
            raise SkillLifecycleError(
                "conflict", "Skill version is referenced and cannot be removed"
            )
        checker = getattr(self.journal, "skill_version_references", None)
        if checker is not None and checker(version_id):
            raise SkillLifecycleError(
                "conflict", "Skill version is referenced and cannot be removed"
            )

    def _allocate_version_id(self, tree_digest: str) -> str:
        candidate = self.id_source.new_id(SKV_ID_PREFIX)
        try:
            return validate_skv_id(candidate)
        except ValueError:
            return f"{SKV_ID_PREFIX}_{hashlib.sha256(candidate.encode() + tree_digest.encode()).hexdigest()[:16]}"

    def _prepare_command(self, operation: str, payload: dict, command_id: str | None):
        command_id = command_id or self.id_source.new_id("cmd")
        command_id = validate_prefixed_id(command_id, "cmd")
        digest = hashlib.sha256(
            canonical_json_bytes({"operation": operation, **payload})
        ).hexdigest()
        memory = self._memory_receipts.get(command_id)
        if memory is not None:
            if memory[0] != digest:
                raise SkillLifecycleError(
                    "conflict", "command ID was reused with a different request"
                )
            return command_id, digest, memory[1].model_copy(update={"status": "replayed"})
        if self.journal is not None and self.workspace_id is not None:
            existing = self.journal.get_application_command_receipt(self.workspace_id, command_id)
            if existing is not None:
                if existing.request_digest != digest:
                    raise SkillLifecycleError(
                        "conflict", "command ID was reused with a different request"
                    )
                result = self._result_from_receipt(existing, operation)
                self._memory_receipts[command_id] = (digest, result)
                return command_id, digest, result.model_copy(update={"status": "replayed"})
        get_skill_operation = getattr(self.journal, "get_skill_operation", None)
        if get_skill_operation is not None:
            existing = get_skill_operation(command_id)
            if existing is not None:
                if existing.request_digest != digest:
                    raise SkillLifecycleError(
                        "conflict", "command ID was reused with a different request"
                    )
                result = self._result_from_skill_operation(existing)
                self._memory_receipts[command_id] = (digest, result)
                return command_id, digest, result.model_copy(update={"status": "replayed"})
        pending = self.operations.load(command_id)
        if pending is not None:
            if pending.request_digest != digest:
                raise SkillLifecycleError(
                    "conflict", "command ID was reused with a different request"
                )
            raise SkillLifecycleNeedsResolution(
                "Skill lifecycle operation is pending recovery; run recover first"
            )
        return command_id, digest, None

    @staticmethod
    def _result_from_skill_operation(record):
        from morrow.core.skills.bindings import SkillLifecycleResult

        return SkillLifecycleResult(
            operation=record.operation,
            command_id=record.operation_id,
            skill_id=record.skill_id,
            scope=record.scope,
            scope_id=record.scope_id,
            version_id=record.version_id,
            status="replayed",
            message="replayed recorded lifecycle result",
        )

    def _result_from_receipt(self, receipt, operation: str):
        result_id = receipt.result_id
        skill_id = None
        if self.journal is not None and self.workspace_id is not None:
            events = self.journal.list_application_events(
                self.workspace_id,
                after_cursor=max(0, (receipt.event_cursor or 1) - 1),
                limit=2,
            )
            event = next(
                (item for item in events if item.cursor == receipt.event_cursor),
                None,
            )
            if event is not None:
                skill_id = event.aggregate_id
        if skill_id is None:
            raise SkillLifecycleError("needs_resolution", "recorded Skill event is unavailable")
        version_id = None
        if operation in {"import", "pin", "rollback"}:
            version_id = result_id
        elif operation == "remove" and result_id and result_id.startswith("skv_"):
            version_id = result_id
        from morrow.core.skills.bindings import SkillLifecycleResult

        return SkillLifecycleResult(
            operation=operation,
            command_id=receipt.command_id,
            skill_id=skill_id,
            scope="workspace" if self.workspace_id else "global",
            scope_id=self.workspace_id,
            version_id=version_id,
            status="replayed",
            message="replayed recorded lifecycle result",
        )

    def _operation_record(
        self,
        command_id: str,
        request_digest: str,
        operation: str,
        change: PreparedBindingChange,
        *,
        version_id: str | None,
        source_kind: SourceKind | None,
        tree_digest: str | None,
    ) -> SkillOperationRecord:
        return SkillOperationRecord(
            command_id=command_id,
            request_digest=request_digest,
            operation=operation,
            skill_id=change.skill_id,
            scope=change.scope,
            scope_id=change.scope_id,
            version_id=version_id,
            source_kind=source_kind.value if source_kind else None,
            tree_digest=tree_digest,
            before_yaml_digest=self._document_digest(change.before_document),
            after_yaml_digest=self._document_digest(change.after_document),
            phase="prepared",
        )

    @staticmethod
    def _document_digest(document) -> str:
        value = document.model_dump(mode="json", by_alias=True)
        value.pop("updated_at", None)
        value.pop("revision", None)
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    def _finalize(
        self,
        *,
        command_id: str,
        request_digest: str,
        operation: str,
        skill_id: str,
        scope_id: str | None,
        version_id: str | None,
        source_kind: SourceKind | None,
        tree_digest: str | None,
        name: str | None = None,
        version: SkillVersion | None = None,
        enabled: bool | None = None,
        pinned_version_id: str | None = None,
        delete_version: bool = False,
    ):
        from morrow.core.skills.bindings import SkillLifecycleResult

        scope = "global" if scope_id is None else "workspace"
        result = SkillLifecycleResult(
            operation=operation,
            command_id=command_id,
            skill_id=skill_id,
            scope=scope,
            scope_id=scope_id,
            version_id=version_id,
            status="applied",
            enabled=enabled,
            pinned_version_id=pinned_version_id,
        )
        if self.journal is None:
            self._memory_receipts[command_id] = (request_digest, result)
            return result

        def work(txn):
            existing = (
                txn.get_application_command_receipt(self.workspace_id, command_id)
                if self.workspace_id is not None
                else None
            )
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise SkillLifecycleError(
                        "conflict", "command ID was reused with a different request"
                    )
                return self._result_from_receipt(existing, operation)
            get_skill_operation = getattr(txn, "get_skill_operation", None)
            existing_skill_operation = (
                get_skill_operation(command_id) if get_skill_operation is not None else None
            )
            if existing_skill_operation is not None:
                if existing_skill_operation.request_digest != request_digest:
                    raise SkillLifecycleError(
                        "conflict", "command ID was reused with a different request"
                    )
                return self._result_from_skill_operation(existing_skill_operation)
            if operation == "import" and name is not None and version_id is not None:
                if version is None or version.version_id != version_id:
                    raise SkillLifecycleError(
                        "needs_resolution", "managed Skill version projection is unavailable"
                    )
                definition = SkillDefinition(
                    skill_id=skill_id,
                    name=name,
                    source_kind=version.source_kind,
                    scope_id=version.scope_id,
                    availability=SkillAvailability.AVAILABLE,
                    conflict_status=SkillConflictStatus.NONE,
                    effective_trust=version.effective_trust,
                )
                txn.put_skill_definition(definition, updated_at=self.clock())
                txn.put_skill_version(version)
            if delete_version and version_id is not None:
                delete = getattr(txn, "delete_skill_version", None)
                if delete is not None:
                    delete(version_id)
            record_operation = getattr(txn, "record_skill_operation", None)
            if record_operation is not None:
                record_operation(
                    operation_id=command_id,
                    scope=scope,
                    scope_id=scope_id,
                    skill_id=skill_id,
                    version_id=version_id,
                    operation="import" if operation == "import" else operation,
                    disposition="applied",
                    evidence_digest=tree_digest or ("0" * 64),
                    reason=request_digest,
                    created_at=self.clock(),
                )
            if self.workspace_id is not None:
                event = txn.put_application_event_in_txn(
                    self.workspace_id,
                    ApplicationEvent(
                        event_id=self.id_source.new_id("evt"),
                        workspace_id=self.workspace_id,
                        event_type=f"skill.lifecycle.{operation}",
                        aggregate_kind="skill",
                        aggregate_id=skill_id,
                        payload={
                            "operation": operation,
                            "skill_id": skill_id,
                            "version_id": version_id,
                            "scope": scope,
                        },
                        created_at=self.clock(),
                    ),
                )
                txn.put_application_command_receipt_in_txn(
                    self.workspace_id,
                    ApplicationCommandReceipt(
                        command_id=command_id,
                        workspace_id=self.workspace_id,
                        operation=f"skill.{operation}",
                        request_digest=request_digest,
                        disposition=ApplicationCommandDisposition.ACCEPTED,
                        result_kind="skill",
                        result_id=version_id or skill_id,
                        event_cursor=event.cursor or None,
                        created_at=self.clock(),
                    ),
                )
            return result.model_copy(update={"message": "applied", "status": "applied"})

        finalized = self.journal.transact(work)
        if isinstance(finalized, SkillLifecycleResult):
            self._memory_receipts[command_id] = (request_digest, finalized)
            return finalized
        return result


__all__ = ["SkillLifecyclePersistenceMixin"]
