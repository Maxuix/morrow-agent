"""Upgrade actual v42/v43 constraints without rewriting durable task history."""

import json
import sqlite3

import pytest

from fixtures.v43_schema import V43_SCHEMA_STATEMENTS
from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import APPLICATION_ID, APPLICATION_NAME, StoreOpenMode


def _seed(store, statements, version):
    store.ensure_layout()
    with sqlite3.connect(store.layout.database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        for sql in statements:
            db.execute(sql)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute(f"PRAGMA user_version={version}")
        db.execute("INSERT INTO store_identity VALUES (1,?,?,123)", (APPLICATION_NAME, version))
        db.execute(
            "INSERT INTO sessions(session_id,workspace_id,lifecycle,health,"
            "conversation_position,created_at_unix,updated_at_unix) "
            "VALUES ('ses_old','ws_old','active','ok',0,123,123)"
        )
    return store


def v42_store(tmp_path):
    statements = []
    for sql in V43_SCHEMA_STATEMENTS:
        if sql.startswith("CREATE TABLE agent_run_terminal_metrics ("):
            sql = sql.replace(
                "('stop', 'steered', 'cancelled', 'error', 'interrupted')",
                "('stop', 'steered', 'cancelled', 'error')",
            )
            sql = sql.replace("'internal', 'user_pause', 'process_interrupted'", "'internal'")
            sql = sql.replace(
                "(finish_reason IN ('error', 'interrupted') AND stop_code IS NOT NULL)",
                "(finish_reason = 'error' AND stop_code IS NOT NULL)",
            )
        statements.append(
            sql.replace(
                "'user_interrupt','node_boundary','provider_failure','process_interrupt'",
                "'user_interrupt','node_boundary'",
            )
        )
    return _seed(OperationalStore(tmp_path), statements, 42)


def v43_store(tmp_path):
    return _seed(OperationalStore(tmp_path), V43_SCHEMA_STATEMENTS, 43)


def _seed_legacy_receipts(store):
    digest = "d" * 64
    with sqlite3.connect(store.layout.database) as db:
        db.executemany(
            "INSERT INTO turn_submit_receipts VALUES (?,?,?,?,?,?)",
            [("ses_old", "cmsg_old", digest, "accepted_closed", None, "cmd_turn")],
        )
        db.executemany(
            "INSERT INTO task_command_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "cmd_task",
                    "ws_old",
                    "ses_old",
                    None,
                    "task",
                    digest,
                    "accepted",
                    None,
                    None,
                    None,
                    None,
                    123,
                )
            ],
        )
        db.executemany(
            "INSERT INTO application_command_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "cmd_app",
                    "ws_old",
                    "ses_old",
                    "inspect",
                    digest,
                    "accepted",
                    None,
                    None,
                    None,
                    None,
                    124,
                )
            ],
        )
        db.executemany(
            "INSERT INTO chat_control_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "ws_old",
                    "ses_old",
                    "cmd_chat",
                    "cmsg_control",
                    "继续",
                    None,
                    "continue",
                    "accepted",
                    "accepted",
                    None,
                    None,
                    None,
                    None,
                    None,
                    1,
                    125,
                    125,
                )
            ],
        )
        db.execute(
            "INSERT INTO recovery_reports(report_id,workspace_id,session_id,status,payload_json,"
            "payload_bytes,created_at_unix) VALUES (?,?,?,?,?,?,?)",
            ("report_old", "ws_old", "ses_old", "open", "{}", 2, 126),
        )
        db.executemany(
            "INSERT INTO recovery_receipts VALUES (?,?,?,?,?,?)",
            [("ses_old", "cmd_recovery", digest, "report_old", None, "retry")],
        )


def _seed_legacy_session_facts(store):
    with sqlite3.connect(store.layout.database) as db:
        db.execute(
            "INSERT INTO session_metadata(session_id,title,pinned,revision) VALUES (?,?,?,?)",
            ("ses_old", "Legacy title", 1, 4),
        )
        db.execute(
            "INSERT INTO chat_session_settings(session_id,settings_json,revision) VALUES (?,?,?)",
            ("ses_old", "{}", 3),
        )
        db.execute(
            "INSERT INTO chat_session_control(session_id,paused,revision) VALUES (?,?,?)",
            ("ses_old", 1, 5),
        )


@pytest.mark.parametrize("mode", [StoreOpenMode.CREATE, StoreOpenMode.READ_WRITE])
def test_v42_upgrade_preserves_rows_and_verified_backup(tmp_path, mode):
    store = v42_store(tmp_path)
    with store.open(mode) as session:
        assert session.schema_version == 49
    with sqlite3.connect(store.layout.database) as db:
        assert db.execute("SELECT session_id FROM sessions").fetchall() == [("ses_old",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert "command_receipts" in names
        assert not names.intersection(
            {
                "store_identity",
                "turn_submit_receipts",
                "task_command_receipts",
                "application_command_receipts",
                "chat_control_receipts",
                "recovery_receipts",
                "session_metadata",
                "chat_session_settings",
                "chat_session_control",
                "agent_run_retry_progress",
                "agent_run_terminal_metrics",
                "approval_scope_bindings",
                "workflow_leaf_ownership",
                "workflow_agent_run_refs",
                "workflow_evaluations",
                "workflow_feedback",
                "workflow_policy_candidates",
            }
        )
        assert (
            db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0
        )
        pause_ddl = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='workflow_pause_points'"
        ).fetchone()[0]
        assert "provider_failure" not in pause_ddl
    backups = {
        version: list(store.layout.backups_dir.glob(f"pre-v{version}-*.sqlite"))
        for version in range(43, 50)
    }
    assert all(len(items) == 1 for items in backups.values())
    with sqlite3.connect(backups[43][0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 42
        assert db.execute("SELECT session_id FROM sessions").fetchone()[0] == "ses_old"
    store.open(StoreOpenMode.READ_WRITE).close()
    assert all(
        len(list(store.layout.backups_dir.glob(f"pre-v{version}-*.sqlite"))) == 1
        for version in range(43, 50)
    )


@pytest.mark.parametrize("mode", [StoreOpenMode.CREATE, StoreOpenMode.READ_WRITE])
def test_v43_upgrade_drops_value_checks_and_preserves_rows(tmp_path, mode):
    store = v43_store(tmp_path)
    _seed_legacy_receipts(store)
    _seed_legacy_session_facts(store)
    with sqlite3.connect(store.layout.database) as db:
        sessions_ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[
            0
        ]
        assert "'archived'" in sessions_ddl
    with store.open(mode) as session:
        assert session.schema_version == 49
    with sqlite3.connect(store.layout.database) as db:
        assert db.execute("SELECT session_id FROM sessions").fetchall() == [("ses_old",)]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        receipts = db.execute(
            "SELECT receipt_kind, payload_json FROM command_receipts ORDER BY receipt_kind"
        ).fetchall()
        assert [row[0] for row in receipts] == [
            "application_command",
            "chat_control",
            "recovery",
            "task_command",
            "turn_submit",
        ]
        assert all(isinstance(json.loads(row[1]), dict) for row in receipts)
        session_facts = db.execute(
            "SELECT metadata_json, settings_json, control_json FROM sessions WHERE session_id='ses_old'"
        ).fetchone()
        assert json.loads(session_facts[0]) == {
            "title": "Legacy title",
            "pinned": 1,
            "revision": 4,
        }
        assert json.loads(session_facts[1]) == {"value": {}, "revision": 3}
        assert json.loads(session_facts[2]) == {"paused": True, "revision": 5}
        sessions_ddl = db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[
            0
        ]
        assert "'archived'" not in sessions_ddl
        # Value-set membership is no longer enforced by DDL.
        db.execute(
            "INSERT INTO sessions(session_id,workspace_id,lifecycle,health,"
            "conversation_position,created_at_unix,updated_at_unix) "
            "VALUES ('ses_future','ws_old','future_state','ok',0,123,123)"
        )
    backups = {
        version: list(store.layout.backups_dir.glob(f"pre-v{version}-*.sqlite"))
        for version in range(44, 49)
    }
    assert all(len(items) == 1 for items in backups.values())
    assert list(store.layout.backups_dir.glob("pre-v43-*.sqlite")) == []
    with sqlite3.connect(backups[44][0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 43
        assert db.execute("SELECT session_id FROM sessions").fetchone()[0] == "ses_old"
    store.open(StoreOpenMode.READ_WRITE).close()
    assert all(
        len(list(store.layout.backups_dir.glob(f"pre-v{version}-*.sqlite"))) == 1
        for version in range(44, 49)
    )


def test_v42_upgrade_rolls_back_and_diagnose_does_not_upgrade(tmp_path):
    store = v42_store(tmp_path)
    store.open(StoreOpenMode.DIAGNOSE).close()
    assert store.classify().schema_version == 42

    def fail(point):
        if point == "upgrade_before_commit":
            raise RuntimeError("injected upgrade failure")

    store.failure_injector = fail
    with pytest.raises(RuntimeError, match="injected"):
        store.open(StoreOpenMode.READ_WRITE)
    assert store.classify().schema_version == 42
    with sqlite3.connect(store.layout.database) as db:
        assert (
            "provider_failure"
            not in db.execute(
                "SELECT sql FROM sqlite_master WHERE name='workflow_pause_points'"
            ).fetchone()[0]
        )
        assert db.execute("SELECT session_id FROM sessions").fetchone()[0] == "ses_old"


def test_v43_upgrade_rolls_back_and_diagnose_does_not_upgrade(tmp_path):
    store = v43_store(tmp_path)
    store.open(StoreOpenMode.DIAGNOSE).close()
    assert store.classify().schema_version == 43

    def fail(point):
        if point == "upgrade_before_commit":
            raise RuntimeError("injected upgrade failure")

    store.failure_injector = fail
    with pytest.raises(RuntimeError, match="injected"):
        store.open(StoreOpenMode.READ_WRITE)
    assert store.classify().schema_version == 43
    with sqlite3.connect(store.layout.database) as db:
        assert (
            "'archived'"
            in db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0]
        )
        assert db.execute("SELECT session_id FROM sessions").fetchone()[0] == "ses_old"
