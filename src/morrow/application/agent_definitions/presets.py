"""Explicit, idempotent materialization of the fixed preset catalog.

D04: presets become workspace-available through one explicit preparation
command; catalog reads and validate never write. The work is idempotent by
content hash: a concurrent or repeated preparation converges on the same
``AgentDefinitionVersion`` identity. Explicitly disabled heads are not
re-enabled and revoked versions are never silently revived; an upgraded preset
source creates a new version and never rewrites history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from morrow.core.agent_definitions import (
    AgentDefinitionHead,
    AgentDefinitionVersion,
)
from morrow.core.agent_presets import (
    PRESET_DEFINITION_IDS,
    PRESET_ROLE_BY_ID,
    AgentPresetRole,
    preset_source,
)
from morrow.core.domain import validate_prefixed_id

PresetMaterializationStatus = Literal["materialized", "current", "disabled", "revoked"]


@dataclass(frozen=True)
class PresetMaterialization:
    """One preset's exact available identity after an explicit preparation."""

    preset_id: str
    role: AgentPresetRole
    definition_id: str
    version_id: str | None
    content_hash: str
    enabled: bool
    status: PresetMaterializationStatus

    @property
    def available(self) -> bool:
        return self.status in {"materialized", "current"} and self.enabled


class AgentPresetMaterializationError(ValueError):
    """A preset cannot be materialized; the command fails without partial writes."""


class AgentPresetMaterializationService:
    """Single transactional entry that makes presets available in one workspace."""

    def __init__(self, journal, *, workspace_id, catalog, id_source):
        self.journal = journal
        self.workspace_id = workspace_id
        self.catalog = catalog
        self.id_source = id_source

    def prepare(self, *, command_id: str) -> tuple[PresetMaterialization, ...]:
        """Materialize every preset under one explicit command; replay-safe.

        The whole catalog is prepared in one transaction: either every preset
        converges on its exact identity or nothing is written.
        """
        validate_prefixed_id(command_id, "cmd")

        def work(txn):
            repo = txn.agent_definitions
            results = []
            for source in (
                preset_source(PRESET_ROLE_BY_ID[preset_id]) for preset_id in PRESET_DEFINITION_IDS
            ):
                results.append(self._prepare_one(repo, source, txn))
            return tuple(results)

        return self.journal.transact(work)

    def _prepare_one(self, repo, source, txn) -> PresetMaterialization:
        role = PRESET_ROLE_BY_ID[source.definition_id]
        base = PresetMaterialization(
            preset_id=source.definition_id,
            role=role,
            definition_id=source.definition_id,
            version_id=None,
            content_hash=source.content_hash,
            enabled=False,
            status="current",
        )
        head = repo.get_head(self.workspace_id, source.definition_id)
        version = repo.get_version(self.workspace_id, head.version_id) if head is not None else None
        if head is not None and version is not None:
            if version.content_hash == source.content_hash:
                if txn.agent_definitions.get_revocation(self.workspace_id, version.version_id):
                    return PresetMaterialization(
                        preset_id=base.preset_id,
                        role=role,
                        definition_id=source.definition_id,
                        version_id=version.version_id,
                        content_hash=version.content_hash,
                        enabled=False,
                        status="revoked",
                    )
                status: PresetMaterializationStatus = "current" if head.enabled else "disabled"
                return PresetMaterialization(
                    preset_id=base.preset_id,
                    role=role,
                    definition_id=source.definition_id,
                    version_id=version.version_id,
                    content_hash=version.content_hash,
                    enabled=head.enabled,
                    status=status,
                )
            # Upgraded preset source: create a new version; history stays intact.
            # A disabled head stays disabled; enablement is an explicit user act.
            created = self._create_version(repo, source, txn, previous_version=version, head=head)
            return PresetMaterialization(
                preset_id=base.preset_id,
                role=role,
                definition_id=source.definition_id,
                version_id=created.version_id,
                content_hash=created.content_hash,
                enabled=head.enabled,
                status="materialized" if head.enabled else "disabled",
            )
        if head is None:
            created = self._create_version(repo, source, txn, previous_version=None, head=None)
            return PresetMaterialization(
                preset_id=base.preset_id,
                role=role,
                definition_id=source.definition_id,
                version_id=created.version_id,
                content_hash=created.content_hash,
                enabled=True,
                status="materialized",
            )
        # Head exists but its version row is missing: the workspace needs repair.
        raise AgentPresetMaterializationError(
            "preset head references a missing AgentDefinition version"
        )

    def _create_version(self, repo, source, txn, *, previous_version, head):
        from morrow.application.agent_definitions.publication import validate_definition

        try:
            validate_definition(source, self.catalog)
        except ValueError as exc:
            raise AgentPresetMaterializationError(str(exc)) from None
        version = AgentDefinitionVersion(
            version_id=self.id_source.new_id("adev"),
            workspace_id=self.workspace_id,
            version=previous_version.version + 1 if previous_version else 1,
            source=source,
            content_hash=source.content_hash,
            source_revision=0,
            origin="builtin",
            created_at=txn.now(),
        )
        repo.put_version(version)
        repo.put_head(
            AgentDefinitionHead(
                workspace_id=self.workspace_id,
                definition_id=source.definition_id,
                version_id=version.version_id,
                source_revision=0,
                source_hash=source.content_hash,
                enabled=head.enabled if head is not None else True,
                row_version=(head.row_version if head is not None else 0) + 1,
            ),
            expected_row_version=head.row_version if head is not None else 0,
        )
        return version
