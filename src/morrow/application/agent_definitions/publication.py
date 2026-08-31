"""Pure definition validation and the single transactional publication authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from morrow.application.agent_definitions.errors import (
    AgentDefinitionAdmissionError,
    DefinitionFailure,
)
from morrow.core.agent_definitions import (
    AgentDefinitionHead,
    AgentDefinitionRevocation,
    AgentDefinitionSource,
    AgentDefinitionVersion,
)
from morrow.core.domain import canonical_json_bytes, sha256_digest, validate_prefixed_id
from morrow.core.models import ModelRef


@dataclass(frozen=True)
class DefinitionCatalog:
    """Static catalogs and task-policy ceiling; never backend-health probes."""

    models: tuple[ModelRef, ...]
    skill_version_ids: frozenset[str]
    tool_access: Mapping[str, str]
    allowed_tools: frozenset[str]


@dataclass(frozen=True)
class DefinitionValidation:
    source: AgentDefinitionSource
    tool_names: tuple[str, ...]
    diagnostics: tuple[str, ...]


def validate_definition(source: AgentDefinitionSource, catalog: DefinitionCatalog):
    if (
        isinstance(source.model_selection, ModelRef)
        and source.model_selection not in catalog.models
    ):
        raise ValueError("definition ModelRef is absent from the configured catalog")
    if not set(source.skill_version_ids) <= catalog.skill_version_ids:
        raise ValueError("definition Skill version is absent from the catalog")
    return resolve_definition_tools(source, catalog)


def resolve_definition_tools(source, catalog):
    tools, diagnostics = [], []
    for item in source.tool_requirements:
        if item.requirement == "forbidden":
            continue
        access = catalog.tool_access.get(item.name)
        permitted = item.name in catalog.allowed_tools and access is not None
        permitted = permitted and (source.access_mode_ceiling == "write" or access == "read")
        if not permitted:
            if item.requirement == "required":
                raise ValueError("required tool is absent or denied by the capability ceiling")
            diagnostics.append(f"optional_removed:{item.name}")
        else:
            tools.append(item.name)
    return DefinitionValidation(source, tuple(tools), tuple(diagnostics))


class AgentDefinitionPublicationService:
    def __init__(self, journal, *, workspace_id, catalog, id_source):
        self.journal = journal
        self.workspace_id = validate_prefixed_id(workspace_id, "ws")
        self.catalog = catalog
        self.id_source = id_source

    def validate(self, source):
        return validate_definition(source, self.catalog)

    def publish(
        self,
        source,
        *,
        source_revision,
        expected_head_revision,
        command_id,
        enabled=True,
        origin="user",
    ):
        self.validate(source)
        if (
            type(source_revision) is not int
            or source_revision < 0
            or type(expected_head_revision) is not int
            or expected_head_revision < 0
            or type(enabled) is not bool
            or origin not in {"user", "builtin"}
        ):
            raise ValueError("invalid Agent publication metadata")
        if source.definition_id.startswith("builtin_") != (origin == "builtin"):
            raise ValueError("Agent definition origin conflicts with its ID")
        if origin == "builtin":
            from morrow.application.agent_definitions.builtins import builtin_definitions

            model = (
                source.model_selection
                if isinstance(source.model_selection, ModelRef)
                else ModelRef(provider_id="builtin", model_id="fixture")
            )
            if source not in builtin_definitions(model):
                raise ValueError("built-in definitions are read-only; publish a new user ID")
        validate_prefixed_id(command_id, "cmd")
        request_hash = sha256_digest(
            canonical_json_bytes(
                {
                    "source": source.content_hash,
                    "source_revision": source_revision,
                    "expected_head_revision": expected_head_revision,
                    "enabled": enabled,
                    "origin": origin,
                }
            )
        )

        def work(txn):
            repo = txn.agent_definitions
            replay = repo.publication(self.workspace_id, command_id)
            if replay is not None:
                if replay[0] != request_hash:
                    raise ValueError("publication command conflicts with prior receipt")
                version = repo.get_version(self.workspace_id, replay[1])
                self.require_unrevoked(version)
                return version
            head = repo.get_head(self.workspace_id, source.definition_id)
            if (head.row_version if head else 0) != expected_head_revision:
                raise ValueError("Agent definition head revision conflict")
            old = repo.get_version(self.workspace_id, head.version_id) if head else None
            if old is not None and old.content_hash == source.content_hash:
                self.require_unrevoked(old)
                version = old
            else:
                version = AgentDefinitionVersion(
                    version_id=self.id_source.new_id("adev"),
                    workspace_id=self.workspace_id,
                    version=old.version + 1 if old else 1,
                    source=source,
                    content_hash=source.content_hash,
                    source_revision=source_revision,
                    origin=origin,
                    created_at=txn.now(),
                )
                repo.put_version(version)
                repo.put_head(
                    AgentDefinitionHead(
                        workspace_id=self.workspace_id,
                        definition_id=source.definition_id,
                        version_id=version.version_id,
                        source_revision=source_revision,
                        source_hash=source.content_hash,
                        enabled=head.enabled if head else enabled,
                        row_version=expected_head_revision + 1,
                    ),
                    expected_row_version=expected_head_revision,
                )
            repo.put_publication(self.workspace_id, command_id, request_hash, version.version_id)
            return version

        return self.journal.transact(work)

    def set_enabled(self, definition_id, *, enabled, expected_head_revision):
        def work(txn):
            repo = txn.agent_definitions
            head = repo.get_head(self.workspace_id, definition_id)
            if head is None:
                raise ValueError("publish the Agent definition first")
            updated = AgentDefinitionHead.model_validate(
                {
                    **head.model_dump(),
                    "enabled": enabled,
                    "row_version": head.row_version + 1,
                }
            )
            repo.put_head(updated, expected_row_version=expected_head_revision)
            return updated

        return self.journal.transact(work)

    def revoke(self, version_id, *, reason, command_id):
        validate_prefixed_id(command_id, "cmd")

        def work(txn):
            repo = txn.agent_definitions
            version = repo.get_version(self.workspace_id, version_id)
            if version is None:
                raise ValueError("published Agent version is missing")
            existing = repo.get_revocation(self.workspace_id, version_id)
            if existing is not None:
                if existing.command_id == command_id and existing.reason == reason:
                    return existing
                raise ValueError("revocation is one-way and cannot be replaced")
            value = AgentDefinitionRevocation(
                workspace_id=self.workspace_id,
                version_id=version_id,
                reason=reason,
                command_id=command_id,
                created_at=txn.now(),
            )
            repo.put_revocation(value)
            return value

        return self.journal.transact(work)

    def require_unrevoked(self, version):
        if version is None or version.workspace_id != self.workspace_id:
            raise AgentDefinitionAdmissionError(DefinitionFailure.MISSING)
        if self.journal.agent_definitions.get_revocation(self.workspace_id, version.version_id):
            raise AgentDefinitionAdmissionError(DefinitionFailure.REVOKED)
        return version

    def admit(self, version_id):
        version = self.require_unrevoked(
            self.journal.agent_definitions.get_version(self.workspace_id, version_id)
        )
        head = self.journal.agent_definitions.get_head(
            self.workspace_id, version.source.definition_id
        )
        if head is None or not head.enabled:
            raise AgentDefinitionAdmissionError(DefinitionFailure.DISABLED)
        return version
