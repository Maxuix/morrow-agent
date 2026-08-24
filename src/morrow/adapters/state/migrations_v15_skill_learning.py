"""Operational Store v15 DDL for generated Skill Drafts and Usage."""

from __future__ import annotations

V15_NAME = "skill_drafts_and_usage"

V15_STATEMENTS = (
    """
    CREATE TABLE skill_drafts (
        draft_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        candidate_id TEXT NOT NULL REFERENCES learning_candidates(candidate_id),
        root_draft_id TEXT NOT NULL,
        parent_draft_id TEXT REFERENCES skill_drafts(draft_id),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        status TEXT NOT NULL CHECK (
            status IN ('draft', 'validated', 'accepted', 'rejected', 'superseded')
        ),
        skill_id TEXT NOT NULL,
        name TEXT NOT NULL,
        display_version TEXT,
        scope_id TEXT NOT NULL,
        candidate_fingerprint TEXT NOT NULL CHECK (length(candidate_fingerprint) = 64),
        evidence_refs_json TEXT NOT NULL,
        package_ref TEXT NOT NULL,
        tree_digest TEXT NOT NULL CHECK (length(tree_digest) = 64),
        file_count INTEGER NOT NULL CHECK (file_count BETWEEN 0 AND 4096),
        total_bytes INTEGER NOT NULL CHECK (total_bytes BETWEEN 0 AND 16777216),
        validation_id TEXT,
        accepted_version_id TEXT REFERENCES skill_versions(version_id),
        acceptance_command_id TEXT,
        rejection_reason TEXT,
        row_version INTEGER NOT NULL CHECK (row_version >= 1),
        created_at_unix INTEGER NOT NULL,
        updated_at_unix INTEGER NOT NULL,
        UNIQUE (root_draft_id, revision),
        UNIQUE (candidate_id, revision)
    )
    """,
    """
    CREATE INDEX skill_drafts_workspace_status
        ON skill_drafts(workspace_id, status, updated_at_unix, draft_id)
    """,
    """
    CREATE INDEX skill_drafts_candidate
        ON skill_drafts(workspace_id, candidate_id, revision)
    """,
    """
    CREATE TABLE skill_draft_validations (
        validation_id TEXT PRIMARY KEY,
        draft_id TEXT NOT NULL REFERENCES skill_drafts(draft_id),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        validator_version TEXT NOT NULL CHECK (length(validator_version) BETWEEN 1 AND 64),
        valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
        report_json TEXT NOT NULL CHECK (length(report_json) BETWEEN 2 AND 32768),
        report_bytes INTEGER NOT NULL CHECK (report_bytes BETWEEN 2 AND 32768),
        report_digest TEXT NOT NULL CHECK (length(report_digest) = 64),
        created_at_unix INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX skill_draft_validations_draft
        ON skill_draft_validations(draft_id, revision, created_at_unix, validation_id)
    """,
    """
    CREATE TABLE skill_usage (
        usage_id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        agent_run_id TEXT NOT NULL REFERENCES agent_runs(agent_run_id),
        task_run_id TEXT REFERENCES task_runs(task_run_id),
        selection_id TEXT,
        skill_id TEXT NOT NULL,
        version_id TEXT NOT NULL REFERENCES skill_versions(version_id),
        activation_reason TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('succeeded', 'failed', 'cancelled', 'interrupted', 'unknown')
        ),
        user_correction INTEGER NOT NULL CHECK (user_correction IN (0, 1)),
        input_tokens INTEGER NOT NULL CHECK (input_tokens BETWEEN 0 AND 1000000000),
        output_tokens INTEGER NOT NULL CHECK (output_tokens BETWEEN 0 AND 1000000000),
        duration_ms INTEGER NOT NULL CHECK (duration_ms BETWEEN 0 AND 1000000000),
        tool_call_count INTEGER NOT NULL CHECK (tool_call_count BETWEEN 0 AND 1000000000),
        artifact_refs_json TEXT NOT NULL,
        facts_digest TEXT NOT NULL CHECK (length(facts_digest) = 64),
        created_at_unix INTEGER NOT NULL,
        UNIQUE (agent_run_id, skill_id, version_id, created_at_unix)
    )
    """,
    """
    CREATE INDEX skill_usage_workspace_skill
        ON skill_usage(workspace_id, skill_id, version_id, created_at_unix, usage_id)
    """,
    """
    CREATE INDEX skill_usage_run
        ON skill_usage(workspace_id, agent_run_id, usage_id)
    """,
)

__all__ = ["V15_NAME", "V15_STATEMENTS"]
