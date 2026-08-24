"""Bounded recovery records for cross-authority Skill lifecycle operations."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus
from morrow.core.domain import canonical_json_bytes, validate_prefixed_id
from morrow.core.skills.trust import SourceKind

from .errors import SkillLifecycleError, SkillLifecycleNeedsResolution

MAX_OPERATION_BYTES = 32 * 1024
OPERATION_SCHEMA = "skill-lifecycle-v1"


class SkillRecoveryError(RuntimeError):
    """A Skill lifecycle operation cannot be safely resumed."""


@dataclass(frozen=True, slots=True)
class SkillOperationRecord:
    command_id: str
    request_digest: str
    operation: str
    skill_id: str
    scope: str
    scope_id: str | None
    version_id: str | None
    source_kind: str | None
    tree_digest: str | None
    before_yaml_digest: str | None
    after_yaml_digest: str | None
    phase: str

    def payload(self) -> dict[str, object]:
        return {
            "schema": OPERATION_SCHEMA,
            "command_id": self.command_id,
            "request_digest": self.request_digest,
            "operation": self.operation,
            "skill_id": self.skill_id,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "version_id": self.version_id,
            "source_kind": self.source_kind,
            "tree_digest": self.tree_digest,
            "before_yaml_digest": self.before_yaml_digest,
            "after_yaml_digest": self.after_yaml_digest,
            "phase": self.phase,
        }


class SkillOperationStore:
    """Filesystem-backed phase marker; it carries only sanitized identifiers/digests."""

    def __init__(self, data_root: Path) -> None:
        self.root = data_root / "skills" / ".operations"

    def path(self, command_id: str) -> Path:
        return self.root / f"{validate_prefixed_id(command_id, 'cmd')}.json"

    def save(self, record: SkillOperationRecord) -> None:
        data = canonical_json_bytes(record.payload())
        if len(data) > MAX_OPERATION_BYTES:
            raise SkillRecoveryError("Skill recovery record exceeds its byte budget")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(record.command_id)
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise SkillRecoveryError("Skill recovery record could not be saved") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, command_id: str) -> SkillOperationRecord | None:
        path = self.path(command_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema") != OPERATION_SCHEMA:
                raise ValueError
            if canonical_json_bytes(raw) != path.read_bytes():
                raise ValueError
            return SkillOperationRecord(
                command_id=validate_prefixed_id(raw["command_id"], "cmd"),
                request_digest=raw["request_digest"],
                operation=raw["operation"],
                skill_id=raw["skill_id"],
                scope=raw["scope"],
                scope_id=raw.get("scope_id"),
                version_id=raw.get("version_id"),
                source_kind=raw.get("source_kind"),
                tree_digest=raw.get("tree_digest"),
                before_yaml_digest=raw.get("before_yaml_digest"),
                after_yaml_digest=raw.get("after_yaml_digest"),
                phase=raw["phase"],
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SkillRecoveryError("Skill recovery record is corrupt") from exc

    def clear(self, command_id: str) -> None:
        self.path(command_id).unlink(missing_ok=True)


class SkillLifecycleRecoveryMixin:
    """Resume only operations whose published authorities can be verified exactly."""

    def recover(self, command_id: str):
        command_id = validate_prefixed_id(command_id, "cmd")
        record = self.operations.load(command_id)
        if record is None:
            raise SkillLifecycleError("not_found", "Skill lifecycle operation is unavailable")
        if record.operation == "import":
            if record.phase != "package_applied":
                raise SkillLifecycleNeedsResolution(
                    "Skill package was not published at a safely resumable boundary"
                )
        elif record.operation == "remove" and record.version_id is not None:
            if record.phase not in {"package_applied", "yaml_applied"}:
                raise SkillLifecycleNeedsResolution(
                    "Skill package removal did not reach a safely resumable boundary"
                )
            scope_id = record.scope_id
            scope = "global" if scope_id is None else "workspace"
            load = self.bindings.load(scope, scope_id=scope_id)
            if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
                raise SkillLifecycleNeedsResolution("Extension YAML cannot be verified")
            current_digest = self._document_digest(load.value)
            if current_digest == record.after_yaml_digest:
                pass
            elif record.phase == "package_applied" and current_digest == record.before_yaml_digest:
                try:
                    source_kind = SourceKind(record.source_kind) if record.source_kind else None
                    change = self.bindings.prepare_change(
                        operation="remove",
                        skill_id=record.skill_id,
                        scope=scope,
                        scope_id=scope_id,
                        source_kind=source_kind,
                    )
                    if self._document_digest(change.after_document) != record.after_yaml_digest:
                        raise SkillLifecycleNeedsResolution("Binding state changed during recovery")
                    self.bindings.apply_change(change)
                except SkillLifecycleNeedsResolution:
                    raise
                except Exception as exc:
                    raise SkillLifecycleNeedsResolution("Binding cannot be resumed safely") from exc
            else:
                raise SkillLifecycleNeedsResolution("Extension YAML drifted during recovery")
        else:
            if record.phase != "yaml_applied":
                raise SkillLifecycleNeedsResolution(
                    "Skill operation did not reach a safely resumable phase"
                )
            scope_id = record.scope_id
            scope = "global" if scope_id is None else "workspace"
            load = self.bindings.load(scope, scope_id=scope_id)
            if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
                raise SkillLifecycleNeedsResolution("Extension YAML cannot be verified")
            if self._document_digest(load.value) != record.after_yaml_digest:
                raise SkillLifecycleNeedsResolution("Extension YAML drifted during recovery")
        scope_id = record.scope_id
        try:
            source_kind = SourceKind(record.source_kind) if record.source_kind else None
        except ValueError as exc:
            raise SkillLifecycleNeedsResolution("Skill source provenance is invalid") from exc
        name = None
        display_version = None
        file_count = 0
        total_bytes = 0
        if record.operation == "import":
            view = self.catalog.scan_scope(scope_id)
            entry = view.entry(record.skill_id, scope_id=scope_id)
            if entry is None or record.version_id is None:
                raise SkillLifecycleNeedsResolution("managed Skill package is unavailable")
            version = next(
                (item for item in entry.versions if item.version_id == record.version_id), None
            )
            if version is None:
                raise SkillLifecycleNeedsResolution("managed Skill package is unavailable")
            name = entry.definition.name
            display_version = version.display_version
            file_count = version.file_count
            total_bytes = version.total_bytes
        result = self._finalize(
            command_id=record.command_id,
            request_digest=record.request_digest,
            operation=record.operation,
            skill_id=record.skill_id,
            scope_id=scope_id,
            version_id=record.version_id,
            source_kind=source_kind,
            tree_digest=record.tree_digest,
            name=name,
            display_version=display_version,
            file_count=file_count,
            total_bytes=total_bytes,
            delete_version=record.operation == "remove" and record.version_id is not None,
        )
        self.operations.clear(command_id)
        return result


__all__ = ["SkillOperationRecord", "SkillOperationStore", "SkillRecoveryError"]
