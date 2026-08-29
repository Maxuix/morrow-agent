"""Build bounded Skill context from frozen package bytes."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.adapters.skills.managed_store import FrozenSkillFile, SkillPackageError
from morrow.core.domain import sha256_digest
from morrow.core.skills.context import (
    SKILL_CONTEXT_ENTRY_MAX_BYTES,
    SkillContextEntry,
    sanitize_skill_text,
)
from morrow.core.skills.selection import SkillSelection


class SkillContextError(ValueError):
    """A selected Skill cannot produce a safe bounded context projection."""


@dataclass(frozen=True, slots=True)
class BuiltSkillContext:
    entry: SkillContextEntry
    source: FrozenSkillFile


class SkillContextService:
    """Read exactly the frozen version and project only low-authority text."""

    def __init__(
        self,
        package_store,
        *,
        id_source=None,
        max_entry_bytes: int = SKILL_CONTEXT_ENTRY_MAX_BYTES,
    ) -> None:
        self.package_store = package_store
        self.id_source = id_source
        self.max_entry_bytes = max_entry_bytes

    def build(
        self,
        selection: SkillSelection,
        *,
        context_id: str | None = None,
    ) -> BuiltSkillContext:
        path = "SKILL.md"
        try:
            source = self.package_store.read_frozen_file(
                skill_id=selection.skill_id,
                version_id=selection.version_id,
                source_kind=selection.source_kind,
                scope_id=selection.scope_id,
                relative_path=path,
                expected_tree_digest=selection.tree_digest,
            )
        except (SkillPackageError, OSError, ValueError) as exc:
            raise SkillContextError("frozen Skill context is unavailable") from exc

        try:
            raw = source.content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SkillContextError("Skill context is not valid UTF-8") from exc
        text = _skill_body(raw)
        safe_text, omitted = sanitize_skill_text(text)
        entry = self._entry(
            selection,
            safe_text,
            omitted=omitted,
            context_id=context_id,
            context_budget=source.manifest.context_budget,
        )
        return BuiltSkillContext(entry=entry, source=source)

    def _entry(
        self,
        selection: SkillSelection,
        text: str,
        *,
        omitted: int,
        context_id: str | None,
        context_budget: int | None,
    ) -> SkillContextEntry:
        limit = self.max_entry_bytes
        if context_budget is not None:
            limit = min(limit, context_budget)
        current = text
        truncated = False
        # Metadata is part of the locked per-entry budget. Reduce only the
        # untrusted content until the complete row fits; never spill into the
        # AgentRun snapshot or silently choose another version.
        for _ in range(12):
            try:
                entry = SkillContextEntry(
                    context_id=context_id or self._new_context_id(),
                    agent_run_id=selection.agent_run_id,
                    selection_id=selection.selection_id,
                    skill_id=selection.skill_id,
                    version_id=selection.version_id,
                    scope=selection.scope,
                    scope_id=selection.scope_id,
                    tree_digest=selection.tree_digest,
                    content=current,
                    context_digest=sha256_digest(current.encode("utf-8")),
                    omitted_count=omitted + int(truncated),
                    truncated=truncated,
                )
                if len(entry.model_dump_json().encode("utf-8")) <= limit:
                    return entry
            except ValueError:
                pass
            raw = current.encode("utf-8")
            if not raw:
                break
            next_limit = max(0, min(len(raw) - 1, int(len(raw) * 0.7)))
            current = raw[:next_limit].decode("utf-8", errors="ignore")
            truncated = True
        # An empty context is still a durable proof that this exact selected
        # version was considered; omission metadata remains explicit.
        try:
            return SkillContextEntry(
                context_id=context_id or self._new_context_id(),
                agent_run_id=selection.agent_run_id,
                selection_id=selection.selection_id,
                skill_id=selection.skill_id,
                version_id=selection.version_id,
                scope=selection.scope,
                scope_id=selection.scope_id,
                tree_digest=selection.tree_digest,
                content="",
                context_digest=sha256_digest(b""),
                omitted_count=omitted + int(bool(text)),
                truncated=bool(text),
            )
        except ValueError as exc:
            raise SkillContextError("Skill context metadata exceeds its budget") from exc

    def _new_context_id(self) -> str:
        if self.id_source is None:
            raise SkillContextError("Skill context ID source is unavailable")
        return self.id_source.new_id("sctx")


def _skill_body(raw: str) -> str:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\r\n")
    return raw


__all__ = ["BuiltSkillContext", "SkillContextError", "SkillContextService"]
