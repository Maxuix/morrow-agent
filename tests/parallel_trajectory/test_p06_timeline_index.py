"""P06 acceptance: durable display index, source dedup, rollback and lineage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.core.application import ApplicationError
from morrow.core.contracts import TimelineEntryIdentity
from morrow.core.domain import DurableConversationRecord, DurableSession
from morrow.core.store import SUPPORTED_SCHEMA_VERSION

WORKSPACE = "ws_traj1"
SESSION = "ses_trajroot1"
CHILD = "ses_trajchild1"


def build_journal(tmp_path: Path) -> SqliteOperationalJournal:
    """Open a store with the complete current schema."""

    store = OperationalStore(tmp_path / "state")
    handle = store.initialize()
    assert handle.schema_version == SUPPORTED_SCHEMA_VERSION

    return SqliteOperationalJournal(handle)


def make_index(journal):
    from morrow.application.timeline_index import TimelineIndexService

    return TimelineIndexService(journal, WORKSPACE)


def seed_session(journal, session_id=SESSION, parent=None) -> None:
    journal.transact(
        lambda _: journal.create_session(
            DurableSession(
                session_id=session_id,
                workspace_id=WORKSPACE,
                **(
                    {
                        "parent_session_id": parent[0],
                        "parent_cut_record_id": parent[1],
                        "parent_cut_position": parent[2],
                        "fork_reason": "continue",
                        "conversation_position": parent[2],
                    }
                    if parent
                    else {}
                ),
            )
        )
    )


def append(journal, session_id, record_id, position, payload, kind="message") -> None:
    journal.append_records(
        WORKSPACE,
        [
            DurableConversationRecord(
                record_id=record_id,
                session_id=session_id,
                conversation_position=position,
                kind=kind,
                payload=payload,
            )
        ],
    )


def seed_turn(journal, session_id, turn_id, client_message_id, created_at=1000) -> None:
    def work(_):
        journal._backend.executor().execute(
            "INSERT INTO task_runs (task_run_id, session_id, workspace_id, status, row_version,"
            " attempt, created_at_unix, updated_at_unix) VALUES (?,?,?,'open',1,1,?,?)",
            (f"task_{turn_id}", session_id, WORKSPACE, created_at, created_at),
        )
        journal._backend.executor().execute(
            "INSERT INTO turns (turn_id, session_id, task_run_id, client_message_id,"
            " created_at_unix) VALUES (?,?,?,?,?)",
            (turn_id, session_id, f"task_{turn_id}", client_message_id, created_at),
        )

    journal.transact(work)


def seed_interaction(
    journal,
    session_id,
    client_message_id,
    *,
    intent="explicit_workflow",
    user_record_id=None,
    status="queued",
) -> None:
    def work(_):
        journal._backend.executor().execute(
            "INSERT INTO chat_interactions (interaction_id, workspace_id, session_id,"
            " client_message_id, request_digest, binding_digest, request_json, binding_json,"
            " status, user_record_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"ia_{session_id}_{client_message_id}",
                WORKSPACE,
                session_id,
                client_message_id,
                "d" * 8,
                "b" * 8,
                json.dumps({"intent": intent, "text": "目标"}),
                "{}",
                status,
                user_record_id,
            ),
        )

    journal.transact(work)


def seed_receipt(journal, session_id, command_id, *, status="accepted") -> None:
    def work(_):
        journal._backend.executor().execute(
            "INSERT INTO command_receipts (receipt_kind, workspace_id, session_id, receipt_key,"
            " command_id, payload_json, revision, created_at_unix, updated_at_unix)"
            " VALUES ('chat_control', ?, ?, ?, ?, ?, 1, 1000, 1000)",
            (
                WORKSPACE,
                session_id,
                f"{session_id}:{command_id}",
                command_id,
                json.dumps(
                    {
                        "workspace_id": WORKSPACE,
                        "session_id": session_id,
                        "command_id": command_id,
                        "text": "继续",
                        "status": status,
                        "revision": 1,
                        "created_at": "1970-01-01T00:16:40+00:00",
                        "updated_at": "1970-01-01T00:16:40+00:00",
                    }
                ),
            ),
        )

    journal.transact(work)


def seed_outcome(journal, session_id, outcome_id, *, task_run_id="task_turn_1") -> None:
    def work(_):
        journal._backend.executor().execute(
            "INSERT INTO task_outcomes (outcome_id, workspace_id, session_id, task_run_id,"
            " version, trigger, task_status, payload_json, payload_bytes, created_at_unix)"
            " VALUES (?,?,?,?,1,'acceptance','accepted','{}',2,1000)",
            (outcome_id, WORKSPACE, session_id, task_run_id),
        )

    journal.transact(work)


def seed_workflow(journal, session_id, run_id="wfr_traj1", node_id="noderun_traj1") -> None:
    def work(_):
        executor = journal._backend.executor()
        executor.execute(
            "INSERT OR IGNORE INTO task_runs (task_run_id, session_id, workspace_id, status,"
            " row_version, attempt, created_at_unix, updated_at_unix)"
            " VALUES ('task_turn_1', ?, ?, 'open', 1, 1, 1000, 1000)",
            (session_id, WORKSPACE),
        )
        executor.execute(
            "INSERT INTO workflow_revisions (workflow_revision_id, workspace_id,"
            " workflow_definition_id, revision, content_hash, body_json)"
            " VALUES (?,?,?,?,?,'{}')",
            (f"rev_{run_id}", WORKSPACE, "wf_test", 1, "a" * 64),
        )
        executor.execute(
            "INSERT INTO workflow_runs (workflow_run_id, workspace_id, workflow_revision_id,"
            " root_task_run_id, status, lineage_budget_root_run_id, body_json)"
            " VALUES (?,?,?,?, 'running', ?, '{}')",
            (run_id, WORKSPACE, f"rev_{run_id}", "task_turn_1", run_id),
        )
        executor.execute(
            "INSERT INTO workflow_node_runs (node_run_id, workspace_id, workflow_run_id,"
            " node_id, attempt, status, body_json) VALUES (?,?,?,?,'1','running','{}')",
            (node_id, WORKSPACE, run_id, "alpha"),
        )

    journal.transact(work)


def seed_outcomes(journal, session_id, count, task_run_id="task_turn_1") -> None:
    """One session with more outcomes than the bounded mutable-source window."""

    def work(_):
        executor = journal._backend.executor()
        for index in range(1, count + 1):
            executor.execute(
                "INSERT INTO task_outcomes (outcome_id, workspace_id, session_id, task_run_id,"
                " version, trigger, task_status, payload_json, payload_bytes, created_at_unix)"
                " VALUES (?,?,?,?,?,'acceptance','accepted','{}',2,1000)",
                (f"ocm_bulk{index}", WORKSPACE, session_id, task_run_id, index),
            )

    journal.transact(work)


def test_long_history_outcomes_stay_indexed_within_the_bounded_sweep(tmp_path):
    """More outcomes than the window: none may be dropped from the index."""

    journal = build_journal(tmp_path)
    seed_session(journal)
    seed_turn(journal, SESSION, "turn_1", "cmid_1")
    total = trajectory_window() + 50
    seed_outcomes(journal, SESSION, total)
    index = make_index(journal)
    # One bounded batch per reconcile: converge instead of scanning everything.
    for _ in range(8):
        index.reconcile(SESSION)
        if len([key for key in entries(index, SESSION) if key.startswith("result:")]) == total:
            break
    indexed = entries(index, SESSION)
    results = [key for key in indexed if key.startswith("result:")]
    assert len(results) == total
    # A second sweep stays idempotent and never reorders existing entries.
    positions = {key: row["timeline_position"] for key, row in indexed.items()}
    assert all(count == 0 for key, count in index.reconcile(SESSION).items())
    assert {
        key: row["timeline_position"] for key, row in entries(index, SESSION).items()
    } == positions


def trajectory_window() -> int:
    from morrow.core import chat_trajectory

    return chat_trajectory.MUTABLE_SOURCE_WINDOW


def entries(index, session_id, **filters):
    cutoffs = index.visible_cutoffs(session_id)
    rows = index.repository.page(
        WORKSPACE, cutoffs.root_session_id, before_position=10**9, limit=1000
    )
    visible = []
    for row in rows:
        if not cutoffs.visible(row["source_session_id"], row["source_position"]):
            continue
        if filters and not all(row.get(key) == value for key, value in filters.items()):
            continue
        visible.append(row)
    return {row["item_id"]: row for row in visible}


def test_current_trajectory_schema_is_present_and_replays_cleanly(tmp_path):
    journal = build_journal(tmp_path)
    # The current store creates both trajectory tables directly.
    assert journal.schema_version() == SUPPORTED_SCHEMA_VERSION
    rows = journal._backend.read_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('chat_timeline_entries','chat_activity_content')",
        (),
    )
    assert {row[0] for row in rows} == {"chat_timeline_entries", "chat_activity_content"}


def test_sink_requires_the_callers_transaction(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    identity = TimelineEntryIdentity(
        workspace_id=WORKSPACE,
        root_session_id=SESSION,
        item_id="input:ses_trajroot1:cmid_1",
        kind="user_message",
        source_kind="chat_interaction",
        source_id="ia_1",
        source_session_id=SESSION,
        occurred_at="2026-09-09T12:00:00Z",
    )
    with pytest.raises(ApplicationError):
        index.record(identity)


def test_sink_rolls_back_with_the_source_fact(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)

    def work(_):
        index.record(
            TimelineEntryIdentity(
                workspace_id=WORKSPACE,
                root_session_id=SESSION,
                item_id="reply:ses_trajroot1:1",
                kind="assistant_message",
                source_kind="conversation_record",
                source_id="rec_1",
                source_session_id=SESSION,
                source_position=1,
                occurred_at="2026-09-09T12:00:00Z",
            )
        )
        raise RuntimeError("source fact failed")

    with pytest.raises(RuntimeError):
        journal.transact(work)
    assert (
        journal.chat_timeline.index.page(WORKSPACE, SESSION, before_position=10**9, limit=10) == []
    )


def test_source_identity_dedup_keeps_position_stable(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    identity = TimelineEntryIdentity(
        workspace_id=WORKSPACE,
        root_session_id=SESSION,
        item_id="reply:ses_trajroot1:1",
        kind="assistant_message",
        source_kind="conversation_record",
        source_id="rec_1",
        source_session_id=SESSION,
        source_position=1,
        revision=1,
        occurred_at="2026-09-09T12:00:00Z",
    )

    def work(_):
        first = index.record(identity)
        duplicate = index.record(identity)
        assert first.timeline_position == duplicate.timeline_position
        bumped = index.record(identity.model_copy(update={"revision": 4}))
        assert bumped.timeline_position == first.timeline_position
        assert bumped.revision == 4
        other = index.record(
            identity.model_copy(update={"source_id": "rec_2", "item_id": "reply:ses_trajroot1:2"})
        )
        assert other.timeline_position == first.timeline_position + 1

    journal.transact(work)
    rows = journal.chat_timeline.index.page(WORKSPACE, SESSION, before_position=10**9, limit=10)
    assert len(rows) == 2
    assert [row["revision"] for row in rows] == [1, 4]  # descending timeline_position


def test_reconcile_indexes_every_source_exactly_once(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    seed_turn(journal, SESSION, "turn_1", "cmid_1")
    append(journal, SESSION, "rec_u1", 1, {"role": "user", "content": "规划一个任务"})
    append(journal, SESSION, "rec_a1", 2, {"role": "assistant", "content": "好的"})
    append(journal, SESSION, "rec_t1", 3, {"finish_reason": "stop"}, kind="terminal")
    seed_interaction(journal, SESSION, "cmid_1", user_record_id="rec_u1")
    seed_receipt(journal, SESSION, "cmd_pause_1")
    seed_outcome(journal, SESSION, "ocm_1")
    seed_workflow(journal, SESSION)
    index = make_index(journal)

    stats = index.reconcile(SESSION)
    assert stats["conversation_record"] == 2  # the user record is claimed by the interaction
    indexed = entries(index, SESSION)
    assert indexed["input:ses_trajroot1:cmid_1"]["kind"] == "planning_input"
    assert indexed["input:ses_trajroot1:cmid_1"]["content_ref"].endswith("/content/rec_u1")
    assert indexed["reply:ses_trajroot1:2"]["kind"] == "assistant_message"
    assert indexed["rec_t1"]["kind"] == "turn_status"
    assert indexed["control:ses_trajroot1:cmd_pause_1"]["kind"] == "control_input"
    assert indexed["result:ses_trajroot1:task_turn_1:ocm_1"]["kind"] == "result"
    assert indexed["run:wfr_traj1"]["kind"] == "node_progress"
    assert indexed["node:wfr_traj1:noderun_traj1"]["parent_item_id"] == "run:wfr_traj1"
    positions = sorted(row["timeline_position"] for row in indexed.values())
    assert positions == list(range(len(positions)))

    # Idempotent: a second sweep indexes nothing new and never reorders.
    again = index.reconcile(SESSION)
    assert all(count == 0 for key, count in again.items() if key != "chat_interaction")
    assert entries(index, SESSION).keys() == indexed.keys()


def test_reconcile_backfills_entries_missing_after_a_crash_window(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    append(journal, SESSION, "rec_u1", 1, {"role": "user", "content": "hi"})
    append(journal, SESSION, "rec_a1", 2, {"role": "assistant", "content": "hello"})
    assert index.reconcile(SESSION)["conversation_record"] == 2

    def erase(_):
        journal._backend.executor().execute(
            "DELETE FROM chat_timeline_entries WHERE source_id='rec_a1'"
        )

    journal.transact(erase)
    assert index.reconcile(SESSION)["conversation_record"] == 1
    assert "reply:ses_trajroot1:2" in entries(index, SESSION)


def test_fork_cut_limits_inherited_entries_without_dropping_the_root_view(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    append(journal, SESSION, "rec_p1", 1, {"role": "assistant", "content": "x"})
    append(journal, SESSION, "rec_p2", 2, {"finish_reason": "stop"}, kind="terminal")
    append(journal, SESSION, "rec_p3", 3, {"role": "assistant", "content": "after cut"})
    seed_session(journal, CHILD, parent=(SESSION, "rec_p2", 2))
    append(journal, CHILD, "rec_c1", 3, {"role": "user", "content": "继续"})
    index = make_index(journal)
    index.reconcile(CHILD)

    root_view = entries(index, SESSION)
    assert set(root_view) == {"reply:ses_trajroot1:1", "rec_p2", "reply:ses_trajroot1:3"}
    child_view = entries(index, CHILD)
    assert set(child_view) == {"reply:ses_trajroot1:1", "rec_p2", "input:ses_trajchild1:rec:rec_c1"}
    cutoffs = index.visible_cutoffs(CHILD)
    assert cutoffs.visible(SESSION, 2) and not cutoffs.visible(SESSION, 3)
    assert cutoffs.visible(CHILD, 3)
    root_cutoffs = index.visible_cutoffs(SESSION)
    assert root_cutoffs.visible(SESSION, 3) and not root_cutoffs.visible(CHILD, 3)


def test_outbox_broadcasts_after_commit_and_replays_after_crash(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    identity = TimelineEntryIdentity(
        workspace_id=WORKSPACE,
        root_session_id=SESSION,
        item_id="reply:ses_trajroot1:1",
        kind="assistant_message",
        source_kind="conversation_record",
        source_id="rec_1",
        source_session_id=SESSION,
        source_position=1,
        occurred_at="2026-09-09T12:00:00Z",
    )
    fan_out: list[str] = []
    index.broadcast = lambda entry: fan_out.append(entry.item_id)

    def committed(_):
        index.record(identity)
        index.notify_after_commit(identity)

    journal.transact(committed)
    assert fan_out == ["reply:ses_trajroot1:1"]
    assert index.pending_broadcasts(SESSION) == []

    def crashed(_):
        index.record(
            identity.model_copy(update={"source_id": "rec_2", "item_id": "reply:ses_trajroot1:2"})
        )
        # notify_after_commit never ran: the process died right here.

    journal.transact(crashed)
    assert len(index.pending_broadcasts(SESSION)) == 1
    assert index.replay_outbox(SESSION) == 1
    assert fan_out == ["reply:ses_trajroot1:1", "reply:ses_trajroot1:2"]
    assert index.pending_broadcasts(SESSION) == []


def test_incremental_reconcile_stays_bounded_and_ordered(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    for position in range(1, 401):
        append(
            journal,
            SESSION,
            f"rec_b{position}",
            position,
            {"role": "assistant", "content": "x"},
        )
    assert index.reconcile(SESSION)["conversation_record"] == 400
    for position in range(401, 406):
        append(
            journal,
            SESSION,
            f"rec_b{position}",
            position,
            {"role": "assistant", "content": "y"},
        )
    assert index.reconcile(SESSION)["conversation_record"] == 5
    rows = index.repository.page(WORKSPACE, SESSION, before_position=10**9, limit=1000)
    assert [row["timeline_position"] for row in reversed(rows)] == list(range(405))
    assert [row["source_position"] for row in reversed(rows)] == list(range(1, 406))


def test_wire_entries_round_trip_through_the_frozen_contract(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    append(journal, SESSION, "rec_u1", 1, {"role": "user", "content": "hi"})
    index.reconcile(SESSION)
    row = entries(index, SESSION)["input:ses_trajroot1:rec:rec_u1"]
    wire = index.repository.get_by_source(WORKSPACE, SESSION, row["source_kind"], row["source_id"])
    from morrow.application.timeline_index import _wire_entry

    entry = _wire_entry(wire)
    dumped = entry.model_dump(mode="json")
    assert dumped["occurred_at"].endswith("Z") or "+00:00" in dumped["occurred_at"]
    assert dumped["kind"] == "user_message" and dumped["availability"] == "committed"
