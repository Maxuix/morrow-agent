"""Read-only checks shared by existing backup and doctor owners."""

from __future__ import annotations

import json

from morrow.core.agent_definitions import AgentDefinitionRevocation, AgentDefinitionVersion
from morrow.core.domain import sha256_digest


def verify_definition_rows(executor):
    def rows(sql):
        result = executor.execute(sql)
        return tuple(result)

    if not rows(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_definition_versions'"
    ):
        return True, ()
    try:
        versions = {}
        for version_id, ws, definition_id, number, digest, body in rows(
            "SELECT version_id, workspace_id, definition_id, version, content_hash, body_json FROM agent_definition_versions"
        ):
            value = AgentDefinitionVersion.model_validate_json(body)
            if (
                value.version_id,
                value.workspace_id,
                value.source.definition_id,
                value.version,
                value.content_hash,
            ) != (version_id, ws, definition_id, number, digest):
                raise ValueError("version mismatch")
            versions[version_id] = value
        for ws, definition_id, version_id, source_revision, digest, enabled, row_version in rows(
            "SELECT workspace_id, definition_id, version_id, source_revision, source_hash, enabled, row_version FROM agent_definition_heads"
        ):
            value = versions[version_id]
            if (
                ws != value.workspace_id
                or definition_id != value.source.definition_id
                or digest != value.content_hash
                or source_revision != value.source_revision
                or enabled not in (0, 1)
                or row_version < 1
            ):
                raise ValueError("head mismatch")
        links = {}
        for version_id, skill_id in rows(
            "SELECT version_id, skill_version_id FROM agent_definition_skills"
        ):
            links.setdefault(version_id, set()).add(skill_id)
        for value in versions.values():
            if links.get(value.version_id, set()) != set(value.source.skill_version_ids):
                raise ValueError("Skill reference mismatch")
        for version_id, body in rows(
            "SELECT version_id, body_json FROM agent_definition_revocations"
        ):
            revocation = AgentDefinitionRevocation.model_validate_json(body)
            if (
                revocation.version_id != version_id
                or revocation.workspace_id != versions[version_id].workspace_id
            ):
                raise ValueError("revocation mismatch")
        workflow_agent_runs = {
            agent_run_id
            for (agent_run_id,) in rows("SELECT agent_run_id FROM workflow_agent_run_refs")
        }
        for agent_run_id, ws, session_id, body in rows(
            "SELECT r.agent_run_id, s.workspace_id, r.session_id, r.snapshot_json "
            "FROM agent_runs r JOIN sessions s USING(session_id)"
        ):
            snapshot = json.loads(body)
            ref = snapshot.get("definition_ref")
            if ref is None:
                continue
            value = versions[ref["version_id"]]
            frozen_cap = snapshot.get("max_agent_generation_requests")
            declared_cap = value.source.max_agent_generation_requests
            workflow_cap_matches = (
                agent_run_id in workflow_agent_runs
                and isinstance(frozen_cap, int)
                and not isinstance(frozen_cap, bool)
                and frozen_cap > 0
                and (declared_cap is None or frozen_cap <= declared_cap)
            )
            if (
                value.workspace_id != ws
                or ref["definition_id"] != value.source.definition_id
                or ref["content_hash"] != value.content_hash
                or snapshot.get("conversation_session_id") != session_id
                or snapshot.get("role_prompt_digest") != sha256_digest(value.source.role_prompt)
                or (frozen_cap != declared_cap and not workflow_cap_matches)
            ):
                raise ValueError("AgentRun definition evidence mismatch")
        return True, ()
    except (ValueError, TypeError, KeyError):
        return False, ("agent_definition_integrity",)
