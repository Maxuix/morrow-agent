"""Agent definition persistence sharing the Operational Store transaction backend."""

from morrow.core.agent_definitions import (
    AgentDefinitionHead,
    AgentDefinitionRevocation,
    AgentDefinitionVersion,
)
from morrow.core.store import StorageError, StorageErrorCode


class SqliteAgentDefinitionJournal:
    def __init__(self, backend):
        self.backend = backend

    def get_version(self, workspace_id, version_id):
        row = self.backend.read_one(
            "SELECT body_json, definition_id, version, content_hash FROM agent_definition_versions "
            "WHERE workspace_id=? AND version_id=?",
            (workspace_id, version_id),
        )
        if row is None:
            return None
        try:
            value = AgentDefinitionVersion.model_validate_json(row[0])
            if (
                value.workspace_id,
                value.version_id,
                value.source.definition_id,
                value.version,
                value.content_hash,
            ) != (workspace_id, version_id, *row[1:]):
                raise ValueError("identity mismatch")
            return value
        except ValueError:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Agent definition is corrupt"
            ) from None

    def list_versions(self, workspace_id):
        rows = self.backend.read_all(
            "SELECT version_id FROM agent_definition_versions WHERE workspace_id=? ORDER BY definition_id, version",
            (workspace_id,),
        )
        return tuple(self.get_version(workspace_id, row[0]) for row in rows)

    def get_head(self, workspace_id, definition_id):
        row = self.backend.read_one(
            "SELECT version_id, source_revision, source_hash, enabled, row_version "
            "FROM agent_definition_heads WHERE workspace_id=? AND definition_id=?",
            (workspace_id, definition_id),
        )
        if row is None:
            return None
        return AgentDefinitionHead(
            workspace_id=workspace_id,
            definition_id=definition_id,
            version_id=row[0],
            source_revision=row[1],
            source_hash=row[2],
            enabled=bool(row[3]),
            row_version=row[4],
        )

    def get_revocation(self, workspace_id, version_id):
        row = self.backend.read_one(
            "SELECT r.body_json FROM agent_definition_revocations r "
            "JOIN agent_definition_versions v USING(version_id) WHERE v.workspace_id=? AND v.version_id=?",
            (workspace_id, version_id),
        )
        if row is None:
            return None
        try:
            value = AgentDefinitionRevocation.model_validate_json(row[0])
            if value.workspace_id != workspace_id or value.version_id != version_id:
                raise ValueError("identity mismatch")
            return value
        except ValueError:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Agent revocation is corrupt"
            ) from None

    def publication(self, workspace_id, command_id):
        return self.backend.read_one(
            "SELECT request_hash, version_id FROM agent_definition_publications WHERE workspace_id=? AND command_id=?",
            (workspace_id, command_id),
        )

    def put_publication(self, workspace_id, command_id, request_hash, version_id):
        self.backend.executor().execute(
            "INSERT INTO agent_definition_publications VALUES(?,?,?,?)",
            (workspace_id, command_id, request_hash, version_id),
        )

    def put_version(self, value):
        self.backend.executor().execute(
            "INSERT INTO agent_definition_versions VALUES(?,?,?,?,?,?)",
            (
                value.version_id,
                value.workspace_id,
                value.source.definition_id,
                value.version,
                value.content_hash,
                value.model_dump_json(),
            ),
        )
        for skill_id in value.source.skill_version_ids:
            self.backend.executor().execute(
                "INSERT INTO agent_definition_skills VALUES(?,?)",
                (value.version_id, skill_id),
            )

    def put_head(self, value, *, expected_row_version):
        current = self.get_head(value.workspace_id, value.definition_id)
        if (current.row_version if current else 0) != expected_row_version:
            raise ValueError("Agent definition head revision conflict")
        if value.row_version != expected_row_version + 1:
            raise ValueError("Agent definition head revision must advance once")
        self.backend.executor().execute(
            "INSERT INTO agent_definition_heads VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace_id, definition_id) DO UPDATE SET version_id=excluded.version_id, "
            "source_revision=excluded.source_revision, source_hash=excluded.source_hash, "
            "enabled=excluded.enabled, row_version=excluded.row_version",
            (
                value.workspace_id,
                value.definition_id,
                value.version_id,
                value.source_revision,
                value.source_hash,
                int(value.enabled),
                value.row_version,
            ),
        )

    def put_revocation(self, value):
        self.backend.executor().execute(
            "INSERT INTO agent_definition_revocations VALUES(?,?)",
            (value.version_id, value.model_dump_json()),
        )
