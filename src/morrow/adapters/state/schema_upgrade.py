"""Small, transactional upgrades for the compact operational store.

The v42/v43 functions deliberately use the frozen v44 catalog.  Later changes
are data migrations over that catalog and do not rebuild unrelated tables.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable

from morrow.adapters.state.schema import LEGACY_SCHEMA_STATEMENTS

# Tables whose v44 DDL differs from v43: every value-set CHECK (enum memberships,
# value-conditioned consistency matrices, payload version pins) was dropped, so the
# constrained tables are rebuilt once. SQLite cannot ALTER CHECK constraints away.
_V44_REBUILT_TABLES = (
    "agent_run_model_requests",
    "agent_run_skill_contexts",
    "agent_run_skill_selections",
    "agent_run_terminal_metrics",
    "application_command_receipts",
    "approvals",
    "artifact_references",
    "artifacts",
    "capability_grants",
    "chat_activity_content",
    "chat_attachments",
    "chat_control_receipts",
    "chat_interactions",
    "chat_timeline_entries",
    "configuration_activations",
    "conversation_records",
    "learning_candidate_decisions",
    "learning_candidates",
    "learning_evidence",
    "learning_policies",
    "learning_reviews",
    "learning_suppressions",
    "mcp_catalog_revisions",
    "mcp_catalog_tools",
    "mcp_run_launch_snapshots",
    "mcp_run_tool_snapshots",
    "mcp_servers",
    "memory_search_terms",
    "memory_selection_items",
    "permission_snapshots",
    "preference_evidence",
    "preference_proposals",
    "preference_review_jobs",
    "preference_write_batches",
    "project_knowledge_heads",
    "project_knowledge_revisions",
    "promotion_operations",
    "recovery_reports",
    "runtime_control_queue",
    "sessions",
    "skill_catalog_operations",
    "skill_definitions",
    "skill_drafts",
    "skill_usage",
    "skill_versions",
    "task_command_receipts",
    "task_outcomes",
    "task_run_transitions",
    "task_runs",
    "tool_executions",
    "turn_submit_receipts",
    "workflow_artifact_bindings",
    "workflow_drafts",
    "workflow_node_runs",
    "workflow_node_segments",
    "workflow_node_steers",
    "workflow_pause_points",
    "workflow_plan_decisions",
    "workflow_planning_bindings",
    "workflow_planning_operations",
    "workflow_planning_pause_facts",
    "workflow_planning_request_outcomes",
    "workflow_run_execution_nodes",
    "workflow_runs",
    "workflow_task_plan_provenance",
)


def _run_upgrade(
    connection: sqlite3.Connection,
    tables: tuple[str, ...],
    suffix: str,
    version: int,
    before_commit,
    *,
    statements: tuple[str, ...] = LEGACY_SCHEMA_STATEMENTS,
) -> None:
    """Rebuild ``tables`` at the current DDL and stamp ``version`` atomically.

    The caller owns the maintenance lock and has made a verified SQLite backup.
    Foreign keys are checked before commit and reenabled even on rollback.
    """
    triggers = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    placeholders = ",".join("?" for _ in tables)
    indexes = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
        f"AND tbl_name IN ({placeholders})",
        tables,
    ).fetchall()
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("PRAGMA trusted_schema=ON")
    try:
        connection.execute("BEGIN EXCLUSIVE")
        for name, _ in triggers:
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        for table in tables:
            ddl = next(s for s in statements if s.startswith(f"CREATE TABLE {table} ("))
            connection.execute(
                ddl.replace(f"CREATE TABLE {table} (", f"CREATE TABLE {table}{suffix} (", 1)
            )
            connection.execute(f"INSERT INTO {table}{suffix} SELECT * FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {table}{suffix} RENAME TO {table}")
        for (sql,) in indexes:
            connection.execute(sql)
        for _, sql in triggers:
            connection.execute(sql)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError("upgrade foreign key validation failed")
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise sqlite3.IntegrityError("upgrade integrity validation failed")
        connection.execute(f"UPDATE store_identity SET schema_version={version} WHERE singleton=1")
        connection.execute(f"PRAGMA user_version={version}")
        before_commit()
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")


def upgrade_v42(connection: sqlite3.Connection, *, before_commit) -> None:
    """Rebuild the three v43-constrained tables; rows and JSON history are kept."""
    tables = ("workflow_node_segments", "workflow_pause_points", "agent_run_terminal_metrics")
    _run_upgrade(
        connection,
        tables,
        "_v43",
        43,
        before_commit,
        statements=LEGACY_SCHEMA_STATEMENTS,
    )


def upgrade_v43(connection: sqlite3.Connection, *, before_commit) -> None:
    """Rebuild every value-constrained table at v44, dropping value-set CHECKs.

    Column sets are unchanged, so rows copy verbatim; triggers and indexes are
    recreated from their saved definitions.
    """
    _run_upgrade(
        connection,
        _V44_REBUILT_TABLES,
        "_v44",
        44,
        before_commit,
        statements=LEGACY_SCHEMA_STATEMENTS,
    )


def _run_data_migration(
    connection: sqlite3.Connection,
    version: int,
    work: Callable[[sqlite3.Connection], None],
    before_commit,
) -> None:
    """Run one compact migration atomically without a migration ledger table."""

    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("PRAGMA trusted_schema=ON")
    try:
        connection.execute("BEGIN EXCLUSIVE")
        # Old guards refer to tables that are intentionally being removed.
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall():
            connection.execute('DROP TRIGGER "' + str(name).replace('"', '""') + '"')
        work(connection)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise sqlite3.IntegrityError("migration foreign key validation failed")
        connection.execute(f"PRAGMA user_version={version}")
        before_commit()
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")


_COMMAND_RECEIPTS_DDL = """CREATE TABLE command_receipts (
    receipt_kind TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    session_id TEXT,
    receipt_key TEXT NOT NULL,
    command_id TEXT,
    client_message_id TEXT,
    request_digest TEXT,
    payload_json TEXT NOT NULL,
    revision INTEGER,
    created_at_unix INTEGER,
    updated_at_unix INTEGER,
    PRIMARY KEY (receipt_kind, receipt_key)
)"""


def _copy_receipts(connection: sqlite3.Connection) -> None:
    connection.execute(_COMMAND_RECEIPTS_DDL)
    connection.execute(
        """INSERT INTO command_receipts
        (receipt_kind,workspace_id,session_id,receipt_key,command_id,client_message_id,
         request_digest,payload_json,revision,created_at_unix,updated_at_unix)
        SELECT 'turn_submit', s.workspace_id, r.session_id,
          r.session_id || ':' || r.client_message_id, r.command_id, r.client_message_id,
          r.request_digest, json_object('session_id',r.session_id,'client_message_id',r.client_message_id,
            'request_digest',r.request_digest,'disposition',r.disposition,'turn_id',r.turn_id,
            'command_id',r.command_id), NULL, NULL, NULL
        FROM turn_submit_receipts r JOIN sessions s ON s.session_id=r.session_id"""
    )
    connection.execute(
        """INSERT INTO command_receipts
        (receipt_kind,workspace_id,session_id,receipt_key,command_id,request_digest,
         payload_json,revision,created_at_unix)
        SELECT 'task_command',workspace_id,session_id,command_id,command_id,request_digest,
          json_object('command_id',command_id,'workspace_id',workspace_id,'session_id',session_id,
            'task_run_id',task_run_id,'operation',operation,'request_digest',request_digest,
            'disposition',disposition,'result_task_run_id',result_task_run_id,'outcome_id',outcome_id,
            'task_status',task_status,'row_version',row_version,'created_at',
            datetime(created_at_unix,'unixepoch') || 'Z'),
          row_version,created_at_unix
        FROM task_command_receipts"""
    )
    connection.execute(
        """INSERT INTO command_receipts
        (receipt_kind,workspace_id,session_id,receipt_key,command_id,request_digest,
         payload_json,revision,created_at_unix)
        SELECT 'application_command',workspace_id,session_id,command_id,command_id,request_digest,
          json_object('command_id',command_id,'workspace_id',workspace_id,'session_id',session_id,
            'operation',operation,'request_digest',request_digest,'disposition',disposition,
            'result_kind',result_kind,'result_id',result_id,'event_cursor',event_cursor,
            'row_version',row_version,'created_at',datetime(created_at_unix,'unixepoch') || 'Z'),
          row_version,created_at_unix
        FROM application_command_receipts"""
    )
    connection.execute(
        """INSERT INTO command_receipts
        (receipt_kind,workspace_id,session_id,receipt_key,command_id,client_message_id,
         payload_json,revision,created_at_unix,updated_at_unix)
        SELECT 'chat_control',workspace_id,session_id,session_id || ':' || command_id,command_id,
          client_message_id,json_object('workspace_id',workspace_id,'session_id',session_id,
            'command_id',command_id,'client_message_id',client_message_id,'text',text,'state',state,
            'intent',intent,'status',status,'disposition',disposition,'message',message,
            'result_kind',result_kind,'result_id',result_id,'error_code',error_code,
            'outcome',json(COALESCE(outcome_json,'{}')),'revision',revision,'created_at',
            datetime(created_at_unix,'unixepoch') || 'Z','updated_at',
            datetime(updated_at_unix,'unixepoch') || 'Z'),revision,created_at_unix,updated_at_unix
        FROM chat_control_receipts"""
    )
    connection.execute(
        """INSERT INTO command_receipts
        (receipt_kind,workspace_id,session_id,receipt_key,command_id,request_digest,
         payload_json)
        SELECT 'recovery',s.workspace_id,r.session_id,r.session_id || ':' || r.command_id,
          r.command_id,r.request_digest,json_object('session_id',r.session_id,'command_id',r.command_id,
            'request_digest',r.request_digest,'report_id',r.report_id,'item_id',r.item_id,
            'resolution',r.resolution)
        FROM recovery_receipts r JOIN sessions s ON s.session_id=r.session_id"""
    )


def upgrade_v45(connection: sqlite3.Connection, *, before_commit) -> None:
    """Delete reserved tables and consolidate the five operational receipts."""

    def work(db: sqlite3.Connection) -> None:
        for table in ("workflow_evaluations", "workflow_feedback", "workflow_policy_candidates"):
            if db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise sqlite3.IntegrityError(f"reserved table {table} is not empty")
        _copy_receipts(db)
        for table in (
            "workflow_evaluations",
            "workflow_feedback",
            "workflow_policy_candidates",
            "store_identity",
            "turn_submit_receipts",
            "task_command_receipts",
            "application_command_receipts",
            "chat_control_receipts",
            "recovery_receipts",
        ):
            db.execute(f"DROP TABLE {table}")

    _run_data_migration(connection, 45, work, before_commit)


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
    name = definition.split()[0]
    if name not in columns:
        db.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def upgrade_v46(connection: sqlite3.Connection, *, before_commit) -> None:
    """Embed one-to-one session, approval, retry and terminal state."""

    def work(db: sqlite3.Connection) -> None:
        for definition in (
            "metadata_json TEXT",
            "settings_json TEXT",
            "control_json TEXT",
        ):
            _add_column(db, "sessions", definition)
        for definition in ("retry_progress_json TEXT", "terminal_metrics_json TEXT"):
            _add_column(db, "agent_runs", definition)
        _add_column(db, "approvals", "scope_revision INTEGER")
        for row in db.execute(
            "SELECT session_id,title,pinned,revision FROM session_metadata"
        ).fetchall():
            db.execute(
                "UPDATE sessions SET metadata_json=? WHERE session_id=?",
                (_json({"title": row[1], "pinned": row[2], "revision": row[3]}), row[0]),
            )
        for row in db.execute(
            "SELECT session_id,settings_json,revision FROM chat_session_settings"
        ).fetchall():
            db.execute(
                "UPDATE sessions SET settings_json=? WHERE session_id=?",
                (_json({"value": json.loads(row[1]), "revision": row[2]}), row[0]),
            )
        for row in db.execute(
            "SELECT session_id,paused,revision FROM chat_session_control"
        ).fetchall():
            db.execute(
                "UPDATE sessions SET control_json=? WHERE session_id=?",
                (_json({"paused": bool(row[1]), "revision": row[2]}), row[0]),
            )
        for row in db.execute(
            "SELECT agent_run_id,workspace_id,consecutive_model_retries,total_retry_count,"
            "summary_retry_count,updated_at_unix FROM agent_run_retry_progress"
        ).fetchall():
            db.execute(
                "UPDATE agent_runs SET retry_progress_json=? WHERE agent_run_id=?",
                (
                    _json(
                        {
                            "agent_run_id": row[0],
                            "workspace_id": row[1],
                            "consecutive_model_retries": row[2],
                            "total_retry_count": row[3],
                            "summary_retry_count": row[4],
                            "updated_at_unix": row[5],
                        }
                    ),
                    row[0],
                ),
            )
        metrics_columns = [
            row[1] for row in db.execute("PRAGMA table_info(agent_run_terminal_metrics)")
        ]
        for row in db.execute("SELECT * FROM agent_run_terminal_metrics").fetchall():
            db.execute(
                "UPDATE agent_runs SET terminal_metrics_json=? WHERE agent_run_id=?",
                (_json(dict(zip(metrics_columns, row, strict=True))), row[0]),
            )
        for row in db.execute(
            "SELECT approval_id,scope_revision FROM approval_scope_bindings"
        ).fetchall():
            db.execute(
                "UPDATE approvals SET scope_revision=? WHERE approval_id=?", (row[1], row[0])
            )
        for table in (
            "session_metadata",
            "chat_session_settings",
            "chat_session_control",
            "agent_run_retry_progress",
            "agent_run_terminal_metrics",
            "approval_scope_bindings",
        ):
            db.execute(f"DROP TABLE {table}")

    _run_data_migration(connection, 46, work, before_commit)


def upgrade_v47(connection: sqlite3.Connection, *, before_commit) -> None:
    """Embed workflow leaf ownership and AgentRun references in node runs."""

    def work(db: sqlite3.Connection) -> None:
        for definition in (
            "agent_run_id TEXT",
            "leaf_session_id TEXT",
            "leaf_task_run_id TEXT",
        ):
            _add_column(db, "workflow_node_runs", definition)
        rows = db.execute(
            "SELECT a.agent_run_id,a.node_run_id,o.session_id,o.task_run_id "
            "FROM workflow_agent_run_refs a LEFT JOIN workflow_leaf_ownership o "
            "ON o.node_run_id=a.node_run_id"
        ).fetchall()
        for agent_run_id, node_run_id, session_id, task_run_id in rows:
            db.execute(
                "UPDATE workflow_node_runs SET agent_run_id=?,leaf_session_id=?,leaf_task_run_id=? "
                "WHERE node_run_id=?",
                (agent_run_id, session_id, task_run_id, node_run_id),
            )
        # Direct invoking-session nodes never had rows in the two auxiliary
        # tables; copy their embedded admission references into the new columns.
        for node_run_id, body in db.execute(
            "SELECT node_run_id, body_json FROM workflow_node_runs"
        ).fetchall():
            data = json.loads(body)
            db.execute(
                "UPDATE workflow_node_runs SET agent_run_id=COALESCE(agent_run_id,?), "
                "leaf_session_id=COALESCE(leaf_session_id,?), "
                "leaf_task_run_id=COALESCE(leaf_task_run_id,?) WHERE node_run_id=?",
                (
                    data.get("agent_run_id"),
                    data.get("conversation_session_id"),
                    data.get("leaf_task_run_id"),
                    node_run_id,
                ),
            )
        db.execute("DROP TABLE workflow_agent_run_refs")
        db.execute("DROP TABLE workflow_leaf_ownership")

    _run_data_migration(connection, 47, work, before_commit)


def upgrade_v48(connection: sqlite3.Connection, *, before_commit) -> None:
    """Remove the remaining business-validation triggers."""

    _run_data_migration(connection, 48, lambda _db: None, before_commit)


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        is not None
    )


def _json_list_value(value: object) -> list[object]:
    if value is None or value == "":
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise sqlite3.IntegrityError("embedded relationship payload is corrupt") from exc
    if not isinstance(parsed, list):
        raise sqlite3.IntegrityError("embedded relationship payload is not a list")
    return parsed


def _update_json_list(
    db: sqlite3.Connection,
    table: str,
    column: str,
    key_column: str,
    key: object,
    additions: list[object],
) -> None:
    row = db.execute(f"SELECT {column} FROM {table} WHERE {key_column}=?", (key,)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError(f"embedded relationship owner {table} is missing")
    values = _json_list_value(row[0])
    for item in additions:
        if item not in values:
            values.append(item)
    db.execute(
        f"UPDATE {table} SET {column}=? WHERE {key_column}=?",
        (_json(values), key),
    )


def _merge_agent_definition_skills(db: sqlite3.Connection) -> None:
    """Verify the version body already carries its skill bindings.

    AgentDefinitionVersion has always serialized ``source.skill_version_ids``;
    the old relationship table was only a query mirror.  Preserve a corrupt
    or hand-written legacy row by adding missing IDs to that JSON object before
    the mirror is removed.
    """

    if not _table_exists(db, "agent_definition_skills"):
        return
    rows = db.execute(
        "SELECT version_id, skill_version_id FROM agent_definition_skills "
        "ORDER BY version_id, skill_version_id"
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for version_id, skill_version_id in rows:
        if (
            db.execute(
                "SELECT 1 FROM skill_versions WHERE version_id=?", (skill_version_id,)
            ).fetchone()
            is None
        ):
            raise sqlite3.IntegrityError("agent definition Skill version is missing")
        grouped.setdefault(str(version_id), []).append(str(skill_version_id))
    for version_id, additions in grouped.items():
        row = db.execute(
            "SELECT body_json FROM agent_definition_versions WHERE version_id=?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise sqlite3.IntegrityError("agent definition skill owner is missing")
        try:
            payload = json.loads(str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise sqlite3.IntegrityError("agent definition body is corrupt") from exc
        if not isinstance(payload, dict):
            raise sqlite3.IntegrityError("agent definition body is not an object")
        source = payload.get("source")
        if not isinstance(source, dict):
            raise sqlite3.IntegrityError("agent definition source is missing")
        existing = source.get("skill_version_ids", [])
        if not isinstance(existing, list):
            raise sqlite3.IntegrityError("agent definition skill IDs are not a list")
        changed = False
        for skill_version_id in additions:
            if skill_version_id not in existing:
                existing.append(skill_version_id)
                changed = True
        if changed:
            db.execute(
                "UPDATE agent_definition_versions SET body_json=? WHERE version_id=?",
                (_json(payload), version_id),
            )


def upgrade_v49(connection: sqlite3.Connection, *, before_commit) -> None:
    """Fold narrow relationship mirrors into their owning JSON records.

    The v44 catalog contains eleven small junction tables whose facts already
    belong to an owning record (or are derivable from it).  v49 keeps the
    durable facts in bounded JSON columns and removes the per-relationship
    tables and indexes from fresh stores.  The operation is intentionally
    additive before each drop so a failed migration rolls back the original
    catalog unchanged.
    """

    def work(db: sqlite3.Connection) -> None:
        for table, definition in (
            ("artifacts", "references_json TEXT NOT NULL DEFAULT '[]'"),
            ("chat_attachments", "blob_refs_json TEXT NOT NULL DEFAULT '[]'"),
            (
                "project_knowledge_revisions",
                "evidence_ids_json TEXT NOT NULL DEFAULT '[]'",
            ),
            (
                "tool_executions",
                "mcp_artifact_links_json TEXT NOT NULL DEFAULT '[]'",
            ),
            (
                "workflow_planning_bindings",
                "artifact_ids_json TEXT NOT NULL DEFAULT '[]'",
            ),
        ):
            _add_column(db, table, definition)

        _merge_agent_definition_skills(db)

        if _table_exists(db, "artifact_references"):
            rows = db.execute(
                "SELECT r.artifact_id, r.workspace_id, a.workspace_id, r.owner_kind, "
                "r.owner_id, r.role, r.created_at_unix "
                "FROM artifact_references r LEFT JOIN artifacts a USING(artifact_id) "
                "ORDER BY r.artifact_id, r.owner_kind, r.owner_id, r.role"
            ).fetchall()
            for (
                artifact_id,
                workspace_id,
                artifact_workspace_id,
                owner_kind,
                owner_id,
                role,
                created_at,
            ) in rows:
                if artifact_workspace_id is None or str(artifact_workspace_id) != str(workspace_id):
                    raise sqlite3.IntegrityError("artifact reference workspace mismatch")
                _update_json_list(
                    db,
                    "artifacts",
                    "references_json",
                    "artifact_id",
                    artifact_id,
                    [
                        {
                            "owner_kind": str(owner_kind),
                            "owner_id": str(owner_id),
                            "role": str(role),
                            "created_at_unix": int(created_at),
                        }
                    ],
                )

        if _table_exists(db, "checkpoint_artifact_references"):
            rows = db.execute(
                "SELECT r.artifact_id, r.workspace_id, a.workspace_id, r.checkpoint_id, "
                "c.workspace_id, r.role, r.created_at_unix "
                "FROM checkpoint_artifact_references r "
                "LEFT JOIN artifacts a USING(artifact_id) "
                "LEFT JOIN context_checkpoints c USING(checkpoint_id) "
                "ORDER BY r.artifact_id, r.checkpoint_id, r.role"
            ).fetchall()
            for (
                artifact_id,
                workspace_id,
                artifact_workspace_id,
                checkpoint_id,
                checkpoint_workspace_id,
                role,
                _created_at,
            ) in rows:
                if (
                    artifact_workspace_id is None
                    or checkpoint_workspace_id is None
                    or str(artifact_workspace_id) != str(workspace_id)
                    or str(checkpoint_workspace_id) != str(workspace_id)
                ):
                    raise sqlite3.IntegrityError("checkpoint artifact workspace mismatch")
                _update_json_list(
                    db,
                    "context_checkpoints",
                    "artifact_refs_json",
                    "checkpoint_id",
                    checkpoint_id,
                    [{"artifact_id": str(artifact_id), "role": str(role)}],
                )

        if _table_exists(db, "chat_attachment_blobs"):
            rows = db.execute(
                "SELECT b.attachment_id, b.artifact_id, b.role, a.workspace_id, "
                "c.workspace_id FROM chat_attachment_blobs b "
                "LEFT JOIN artifacts a USING(artifact_id) "
                "LEFT JOIN chat_attachments c USING(attachment_id) "
                "ORDER BY b.attachment_id, b.artifact_id"
            ).fetchall()
            for (
                attachment_id,
                artifact_id,
                role,
                artifact_workspace_id,
                attachment_workspace_id,
            ) in rows:
                if (
                    artifact_workspace_id is None
                    or attachment_workspace_id is None
                    or str(artifact_workspace_id) != str(attachment_workspace_id)
                ):
                    raise sqlite3.IntegrityError("attachment artifact workspace mismatch")
                _update_json_list(
                    db,
                    "chat_attachments",
                    "blob_refs_json",
                    "attachment_id",
                    attachment_id,
                    [{"artifact_id": str(artifact_id), "role": str(role)}],
                )

        if _table_exists(db, "learning_candidate_evidence"):
            rows = db.execute(
                "SELECT l.candidate_id, l.evidence_id, c.workspace_id, e.workspace_id "
                "FROM learning_candidate_evidence l "
                "LEFT JOIN learning_candidates c USING(candidate_id) "
                "LEFT JOIN learning_evidence e USING(evidence_id) "
                "ORDER BY l.candidate_id, l.evidence_id"
            ).fetchall()
            for candidate_id, evidence_id, candidate_workspace_id, evidence_workspace_id in rows:
                if (
                    candidate_workspace_id is None
                    or evidence_workspace_id is None
                    or str(candidate_workspace_id) != str(evidence_workspace_id)
                ):
                    raise sqlite3.IntegrityError("candidate evidence workspace mismatch")
                _update_json_list(
                    db,
                    "learning_candidates",
                    "evidence_ids_json",
                    "candidate_id",
                    candidate_id,
                    [str(evidence_id)],
                )

        if _table_exists(db, "mcp_result_artifact_links"):
            rows = db.execute(
                "SELECT l.link_id, l.workspace_id, l.tool_execution_id, l.artifact_id, "
                "l.role, l.created_at_unix, e.workspace_id, a.workspace_id "
                "FROM mcp_result_artifact_links l "
                "LEFT JOIN tool_executions e USING(tool_execution_id) "
                "LEFT JOIN artifacts a USING(artifact_id) "
                "ORDER BY l.tool_execution_id, l.role, l.link_id"
            ).fetchall()
            for (
                link_id,
                workspace_id,
                execution_id,
                artifact_id,
                role,
                created_at,
                execution_workspace_id,
                artifact_workspace_id,
            ) in rows:
                if (
                    execution_workspace_id is None
                    or artifact_workspace_id is None
                    or str(execution_workspace_id) != str(workspace_id)
                    or str(artifact_workspace_id) != str(workspace_id)
                ):
                    raise sqlite3.IntegrityError("MCP artifact link workspace mismatch")
                _update_json_list(
                    db,
                    "tool_executions",
                    "mcp_artifact_links_json",
                    "tool_execution_id",
                    execution_id,
                    [
                        {
                            "link_id": str(link_id),
                            "workspace_id": str(workspace_id),
                            "tool_execution_id": str(execution_id),
                            "artifact_id": str(artifact_id),
                            "role": str(role),
                            "created_at_unix": int(created_at),
                        }
                    ],
                )

        if _table_exists(db, "preference_write_batch_proposals"):
            rows = db.execute(
                "SELECT l.batch_id, l.proposal_id, l.workspace_id, b.workspace_id, "
                "b.scope, p.workspace_id, p.scope FROM preference_write_batch_proposals l "
                "LEFT JOIN preference_write_batches b USING(batch_id) "
                "LEFT JOIN preference_proposals p USING(proposal_id) "
                "ORDER BY l.batch_id, l.proposal_id"
            ).fetchall()
            for (
                batch_id,
                proposal_id,
                workspace_id,
                batch_workspace_id,
                batch_scope,
                proposal_workspace_id,
                proposal_scope,
            ) in rows:
                if (
                    batch_workspace_id is None
                    or proposal_workspace_id is None
                    or str(batch_workspace_id) != str(workspace_id)
                    or str(proposal_workspace_id) != str(workspace_id)
                    or str(batch_scope) != str(proposal_scope)
                ):
                    raise sqlite3.IntegrityError("Preference batch proposal workspace mismatch")
                _update_json_list(
                    db,
                    "preference_write_batches",
                    "proposal_ids_json",
                    "batch_id",
                    batch_id,
                    [str(proposal_id)],
                )

        if _table_exists(db, "project_knowledge_evidence"):
            rows = db.execute(
                "SELECT l.knowledge_revision_id, l.evidence_id, l.workspace_id, "
                "r.workspace_id, e.workspace_id FROM project_knowledge_evidence l "
                "LEFT JOIN project_knowledge_revisions r USING(knowledge_revision_id) "
                "LEFT JOIN learning_evidence e USING(evidence_id) "
                "ORDER BY l.knowledge_revision_id, l.evidence_id"
            ).fetchall()
            for (
                revision_id,
                evidence_id,
                workspace_id,
                revision_workspace_id,
                evidence_workspace_id,
            ) in rows:
                if (
                    revision_workspace_id is None
                    or evidence_workspace_id is None
                    or str(revision_workspace_id) != str(workspace_id)
                    or str(evidence_workspace_id) != str(workspace_id)
                ):
                    raise sqlite3.IntegrityError("knowledge evidence workspace mismatch")
                _update_json_list(
                    db,
                    "project_knowledge_revisions",
                    "evidence_ids_json",
                    "knowledge_revision_id",
                    revision_id,
                    [str(evidence_id)],
                )

        if _table_exists(db, "workflow_planning_artifacts"):
            rows = db.execute(
                "SELECT l.planning_binding_id, l.artifact_id, l.workspace_id, "
                "b.workspace_id, a.workspace_id FROM workflow_planning_artifacts l "
                "LEFT JOIN workflow_planning_bindings b USING(planning_binding_id) "
                "LEFT JOIN artifacts a USING(artifact_id) "
                "ORDER BY l.planning_binding_id, l.artifact_id"
            ).fetchall()
            for (
                binding_id,
                artifact_id,
                workspace_id,
                binding_workspace_id,
                artifact_workspace_id,
            ) in rows:
                if (
                    binding_workspace_id is None
                    or artifact_workspace_id is None
                    or str(binding_workspace_id) != str(workspace_id)
                    or str(artifact_workspace_id) != str(workspace_id)
                ):
                    raise sqlite3.IntegrityError("planning artifact workspace mismatch")
                _update_json_list(
                    db,
                    "workflow_planning_bindings",
                    "artifact_ids_json",
                    "planning_binding_id",
                    binding_id,
                    [str(artifact_id)],
                )

        # The typed binding payload is the read authority after the migration.
        # Populate it even when an interrupted/hand-created v48 file no longer
        # has the legacy mirror table to provide rows.
        for binding_id, ids_json, body_json in db.execute(
            "SELECT planning_binding_id, artifact_ids_json, body_json "
            "FROM workflow_planning_bindings"
        ).fetchall():
            try:
                payload = json.loads(str(body_json))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise sqlite3.IntegrityError("planning binding body is corrupt") from exc
            if not isinstance(payload, dict):
                raise sqlite3.IntegrityError("planning binding body is not an object")
            payload["artifact_ids"] = [str(item) for item in _json_list_value(ids_json)]
            db.execute(
                "UPDATE workflow_planning_bindings SET body_json=? WHERE planning_binding_id=?",
                (_json(payload), binding_id),
            )

        if _table_exists(db, "learning_review_evidence"):
            invalid = db.execute(
                "SELECT 1 FROM learning_review_evidence l "
                "LEFT JOIN learning_reviews r USING(review_id) "
                "LEFT JOIN learning_evidence e USING(evidence_id) "
                "WHERE r.review_id IS NULL OR e.evidence_id IS NULL "
                "OR l.workspace_id != r.workspace_id OR l.workspace_id != e.workspace_id "
                "OR e.origin_review_id != l.review_id LIMIT 1"
            ).fetchone()
            if invalid is not None:
                raise sqlite3.IntegrityError("learning review evidence mirror is invalid")
        if _table_exists(db, "preference_proposal_evidence"):
            invalid = db.execute(
                "SELECT 1 FROM preference_proposal_evidence l "
                "LEFT JOIN preference_proposals p USING(proposal_id) "
                "LEFT JOIN preference_evidence e USING(evidence_id) "
                "WHERE p.proposal_id IS NULL OR e.evidence_id IS NULL "
                "OR l.workspace_id != p.workspace_id OR l.workspace_id != e.workspace_id "
                "OR p.evidence_id != l.evidence_id LIMIT 1"
            ).fetchone()
            if invalid is not None:
                raise sqlite3.IntegrityError("preference proposal evidence mirror is invalid")

        # Once the owner payloads and derived-owner checks above pass, the
        # relationship mirrors carry no facts that need to survive as tables.
        for table in (
            "agent_definition_skills",
            "artifact_references",
            "chat_attachment_blobs",
            "checkpoint_artifact_references",
            "learning_candidate_evidence",
            "learning_review_evidence",
            "mcp_result_artifact_links",
            "preference_proposal_evidence",
            "preference_write_batch_proposals",
            "project_knowledge_evidence",
            "workflow_planning_artifacts",
        ):
            if _table_exists(db, table):
                db.execute(f"DROP TABLE {table}")

    _run_data_migration(connection, 49, work, before_commit)


DATA_MIGRATIONS = {
    44: upgrade_v45,
    45: upgrade_v46,
    46: upgrade_v47,
    47: upgrade_v48,
    48: upgrade_v49,
}

__all__ = [
    "DATA_MIGRATIONS",
    "upgrade_v42",
    "upgrade_v43",
    "upgrade_v45",
    "upgrade_v46",
    "upgrade_v47",
    "upgrade_v48",
    "upgrade_v49",
]
