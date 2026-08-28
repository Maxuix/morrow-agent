"""Deterministic, fail-closed Skill selection for new AgentRuns."""

from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime

from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus, extension_document_digest
from morrow.application.skills.context import SkillContextError, SkillContextService
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.skills.bindings import SkillBinding, SkillSelectionMode
from morrow.core.skills.catalog import SkillAvailability, SkillCatalogEntry, SkillVersion
from morrow.core.skills.selection import (
    SKILL_OMISSION_MAX_COUNT,
    SKILL_SELECTION_MAX_COUNT,
    SkillOmission,
    SkillSelection,
    SkillSelectionPlan,
)

_EXPLICIT_SKILL_PATTERN = re.compile(r"(?:@skill:|\bskill:)\s*([a-z0-9][a-z0-9_-]{0,63})")
_TOKEN_PATTERN = re.compile(r"[a-z0-9_][a-z0-9_-]{1,63}|[\u4e00-\u9fff]")


class SkillSelectionError(ValueError):
    """A selection request is malformed at the application boundary."""


class SkillSelectionService:
    """Resolve current Binding/Catalog state once for one new AgentRun.

    The result is a complete frozen plan.  It is not reused for recovery and
    it never performs learned routing or enables a disabled Binding.
    """

    def __init__(
        self,
        catalog,
        bindings,
        package_store,
        *,
        id_source,
        context_service: SkillContextService | None = None,
        workspace_id: str | None = None,
        description_fallback_enabled: bool = True,
        max_selections: int = SKILL_SELECTION_MAX_COUNT,
        max_description_matches: int = 4,
        available_tools: Iterable[str] | None = None,
        available_mcp_servers: Iterable[str] | None = None,
        available_capabilities: Iterable[str] | None = None,
        platform: str | None = None,
    ) -> None:
        self.catalog = catalog
        self.bindings = bindings
        self.package_store = package_store
        self.id_source = id_source
        self.workspace_id = workspace_id
        self.description_fallback_enabled = description_fallback_enabled
        self.max_selections = max(0, min(max_selections, SKILL_SELECTION_MAX_COUNT))
        self.max_description_matches = max(0, max_description_matches)
        self.available_tools = frozenset(available_tools) if available_tools is not None else None
        self.available_mcp_servers = (
            frozenset(available_mcp_servers) if available_mcp_servers is not None else None
        )
        self.available_capabilities = (
            frozenset(available_capabilities) if available_capabilities is not None else None
        )
        self.platform = platform or sys.platform
        self.context_service = context_service or SkillContextService(
            package_store, id_source=id_source
        )

    def select(
        self,
        *,
        agent_run_id: str,
        user_input: str = "",
        workspace_id: str | None = None,
        explicit_skill_ids: Iterable[str] = (),
        now: datetime | None = None,
    ) -> SkillSelectionPlan:
        workspace_id = workspace_id if workspace_id is not None else self.workspace_id
        explicit = set(_EXPLICIT_SKILL_PATTERN.findall(user_input))
        explicit.update(str(item).strip() for item in explicit_skill_ids if str(item).strip())
        omissions: list[SkillOmission] = []
        candidates: list[tuple[SkillBinding, SkillCatalogEntry, SkillVersion, str]] = []
        binding_digests: list[dict] = []
        catalog_digests: list[dict] = []
        description_candidates: list[tuple[int, SkillBinding, SkillCatalogEntry]] = []
        seen_binding_ids: set[str] = set()

        for scope_id in (None, workspace_id) if workspace_id is not None else (None,):
            scope = "global" if scope_id is None else "workspace"
            load = self.bindings.load(scope, scope_id=scope_id)
            if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
                if load.error:
                    omissions.append(
                        SkillOmission(
                            skill_id="unknown-skill",
                            scope=scope,
                            scope_id=scope_id,
                            reason="binding_unavailable",
                        )
                    )
                continue
            binding_digests.append(
                {
                    "scope": scope,
                    "scope_id": scope_id,
                    "revision": load.value.revision,
                    "digest": extension_document_digest(load.value),
                }
            )
            view = self.catalog.scan_scope(scope_id)
            catalog_digests.append({"scope": scope, "scope_id": scope_id, "entries": view.entries})
            by_id = {entry.definition.skill_id: entry for entry in view.entries}
            for binding in sorted(load.value.bindings, key=lambda item: item.skill_id):
                seen_binding_ids.add(binding.skill_id)
                if not binding.enabled:
                    if binding.skill_id in explicit:
                        omissions.append(self._omission(binding, "binding_disabled"))
                    continue
                if (
                    binding.selection_mode is SkillSelectionMode.EXPLICIT
                    and binding.skill_id not in explicit
                ):
                    continue
                if (
                    binding.selection_mode is SkillSelectionMode.WORKSPACE_DEFAULT
                    and scope != "workspace"
                ):
                    omissions.append(self._omission(binding, "workspace_default_scope_invalid"))
                    continue
                entry = by_id.get(binding.skill_id)
                if entry is None:
                    omissions.append(self._omission(binding, "skill_unavailable"))
                    continue
                if binding.selection_mode is SkillSelectionMode.DESCRIPTION_MATCH:
                    score = _description_score(user_input, entry.definition.description)
                    if not self.description_fallback_enabled:
                        omissions.append(self._omission(binding, "description_fallback_disabled"))
                        continue
                    if score <= 0:
                        omissions.append(self._omission(binding, "description_no_match"))
                        continue
                    description_candidates.append((score, binding, entry))
                    continue
                resolved = self._resolve(binding, entry, scope_id)
                if isinstance(resolved, SkillOmission):
                    omissions.append(resolved)
                else:
                    candidates.append((*resolved, _activation_reason(binding)))

        description_candidates.sort(
            key=lambda item: (
                -item[0],
                item[1].skill_id,
                item[2].definition.source_kind.value,
            )
        )
        for _, binding, entry in description_candidates[: self.max_description_matches]:
            resolved = self._resolve(binding, entry, binding.scope_id)
            if isinstance(resolved, SkillOmission):
                omissions.append(resolved)
            else:
                candidates.append((*resolved, "description_match"))
        for _, binding, _ in description_candidates[self.max_description_matches :]:
            omissions.append(self._omission(binding, "description_match_limit"))
        for skill_id in sorted(explicit - seen_binding_ids):
            omissions.append(
                SkillOmission(
                    skill_id=skill_id,
                    scope="global",
                    reason="explicit_skill_unavailable",
                )
            )

        selected: list[SkillSelection] = []
        seen: set[tuple[str, str | None, str]] = set()
        for binding, _entry, version, reason in candidates:
            key = (binding.skill_id, binding.scope_id, version.version_id)
            if key in seen:
                continue
            if len(selected) >= self.max_selections:
                omissions.append(self._omission(binding, "selection_limit", version.version_id))
                continue
            seen.add(key)
            selected.append(
                SkillSelection(
                    selection_id=self.id_source.new_id("ssel"),
                    agent_run_id=agent_run_id,
                    skill_id=version.skill_id,
                    version_id=version.version_id,
                    scope=binding.scope,
                    scope_id=binding.scope_id,
                    source_kind=version.source_kind,
                    selection_mode=binding.selection_mode,
                    activation_reason=reason,
                    tree_digest=version.tree_digest,
                )
            )

        contexts = []
        context_omissions: list[SkillOmission] = []
        for selection in selected:
            try:
                built = self.context_service.build(selection)
            except SkillContextError:
                context_omissions.append(
                    SkillOmission(
                        skill_id=selection.skill_id,
                        scope=selection.scope,
                        scope_id=selection.scope_id,
                        version_id=selection.version_id,
                        reason="context_unavailable",
                    )
                )
                continue
            contexts.append(built.entry)
        if context_omissions:
            blocked = {
                (item.skill_id, item.scope_id, item.version_id) for item in context_omissions
            }
            selected = [
                item
                for item in selected
                if (item.skill_id, item.scope_id, item.version_id) not in blocked
            ]
            omissions.extend(context_omissions)
            contexts = [
                item
                for item in contexts
                if (item.skill_id, item.scope_id, item.version_id) not in blocked
            ]
        omissions = omissions[:SKILL_OMISSION_MAX_COUNT]
        binding_digest = (
            sha256_digest(canonical_json_bytes(binding_digests)) if binding_digests else None
        )
        catalog_digest = (
            sha256_digest(
                canonical_json_bytes(
                    [
                        {
                            "scope": item["scope"],
                            "scope_id": item["scope_id"],
                            "entries": [entry.model_dump(mode="json") for entry in item["entries"]],
                        }
                        for item in catalog_digests
                    ]
                )
            )
            if catalog_digests
            else None
        )
        return SkillSelectionPlan(
            selections=tuple(selected),
            omissions=tuple(omissions),
            contexts=tuple(contexts),
            binding_digest=binding_digest,
            catalog_digest=catalog_digest,
        )

    def sync_catalog(self, txn, selections: Iterable[SkillSelection], *, now=None) -> None:
        """Persist selected catalog facts inside the same new-run admission.

        This keeps composition/recovery free of Catalog reads while still
        satisfying the v14 version foreign keys for packages installed by a
        process that did not have an operational journal open at install time.
        """

        selected = tuple(selections)
        if not selected:
            return
        views = {}
        for selection in selected:
            view = views.setdefault(selection.scope_id, self.catalog.scan_scope(selection.scope_id))
            entry = view.entry(selection.skill_id, scope_id=selection.scope_id)
            if entry is None:
                raise SkillSelectionError("selected Skill is unavailable")
            version = next(
                (item for item in entry.versions if item.version_id == selection.version_id),
                None,
            )
            if version is None:
                raise SkillSelectionError("selected Skill version is unavailable")
            txn.put_skill_definition(entry.definition, updated_at=now or txn.now())
            txn.put_skill_version(version)

    def _resolve(
        self,
        binding: SkillBinding,
        entry: SkillCatalogEntry,
        scope_id: str | None,
    ) -> tuple[SkillBinding, SkillCatalogEntry, SkillVersion] | SkillOmission:
        if entry.definition.availability is not SkillAvailability.AVAILABLE:
            return self._omission(binding, f"catalog_{entry.definition.availability.value}")
        versions = [item for item in entry.versions if item.scope_id == scope_id]
        if binding.source_kind is not None:
            versions = [item for item in versions if item.source_kind is binding.source_kind]
        elif len({item.source_kind for item in versions}) != 1:
            return self._omission(binding, "source_ambiguous")
        if not versions:
            return self._omission(binding, "source_unavailable")
        if binding.pinned_version_id is not None:
            version = next(
                (item for item in versions if item.version_id == binding.pinned_version_id), None
            )
            if version is None:
                return self._omission(
                    binding, "pinned_version_unavailable", binding.pinned_version_id
                )
        else:
            version = max(
                versions,
                key=lambda item: (
                    item.created_at or datetime.min.replace(tzinfo=UTC),
                    item.version_id,
                ),
            )
        try:
            manifest = self.package_store.manifest_for_version(
                skill_id=version.skill_id,
                version_id=version.version_id,
                source_kind=version.source_kind,
                scope_id=scope_id,
            )
        except (OSError, ValueError):
            return self._omission(binding, "package_unavailable", version.version_id)
        if manifest.platform_constraints and self.platform not in manifest.platform_constraints:
            return self._omission(binding, "platform_unavailable", version.version_id)
        missing: list[str] = []
        if self.available_tools is not None:
            missing.extend(
                f"tool:{item}"
                for item in manifest.required_tools
                if item not in self.available_tools
            )
        if self.available_mcp_servers is not None:
            missing.extend(
                f"mcp:{item}"
                for item in manifest.required_mcp_servers
                if item not in self.available_mcp_servers
            )
        if self.available_capabilities is not None:
            missing.extend(
                f"capability:{item}"
                for item in manifest.requested_permissions
                if item not in self.available_capabilities
            )
        if missing:
            return self._omission(
                binding, "missing_dependency:" + ",".join(sorted(missing)[:4]), version.version_id
            )
        return binding, entry, version

    @staticmethod
    def _omission(
        binding: SkillBinding, reason: str, version_id: str | None = None
    ) -> SkillOmission:
        return SkillOmission(
            skill_id=binding.skill_id,
            scope=binding.scope,
            scope_id=binding.scope_id,
            reason=reason[:128],
            version_id=version_id,
        )


def _activation_reason(binding: SkillBinding) -> str:
    if binding.selection_mode is SkillSelectionMode.WORKSPACE_DEFAULT:
        return "workspace_default"
    return "explicit"


def _description_score(query: str, description: str | None) -> int:
    if not query or not description:
        return 0
    query_tokens = set(_tokens(query))
    description_tokens = set(_tokens(description))
    overlap = query_tokens & description_tokens
    if not overlap:
        return 0
    # One exact identifier is enough; ordinary prose needs two shared tokens
    # to avoid turning every broad Skill description into an implicit router.
    if any("_" in item or "-" in item or item.isascii() and len(item) > 5 for item in overlap):
        return 2 + len(overlap)
    return len(overlap) if len(overlap) >= 2 else 0


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return tuple(_TOKEN_PATTERN.findall(normalized))


__all__ = ["SkillSelectionError", "SkillSelectionService"]
