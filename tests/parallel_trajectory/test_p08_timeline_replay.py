"""P08 acceptance: mixed pagination, snapshot consistency and durable replay.

Exercises the index-backed unified timeline over the isolated current store:
cursor binding, unsupported-cursor reset, fork-cut and run-leaf authorization, coverage of
planning/published/multi-run/un-started sources, and bounded pages beyond 500
entries.
"""

from __future__ import annotations

import base64
import json

import pytest

from morrow.core.application import ApplicationError
from morrow.core.contracts import TimelineCursor
from parallel_trajectory.test_p06_timeline_index import (
    CHILD,
    SESSION,
    WORKSPACE,
    append,
    build_journal,
    make_index,
    seed_interaction,
    seed_receipt,
    seed_session,
    seed_workflow,
)


def page(index, session_id, *, before=None, limit=50, after_position=None):
    return index.snapshot_page(
        session_id, before=before, limit=limit, after_position=after_position
    )


def test_pages_over_600_entries_stay_bounded_and_gapless(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    for position in range(1, 601):
        append(
            journal, SESSION, f"rec_s{position}", position, {"role": "assistant", "content": "y"}
        )
    first = page(index, SESSION, limit=100)
    assert len(first["items"]) == 100 and first["has_more"]
    # The first page holds the newest entries; display order is oldest → newest.
    assert first["items"][0]["timeline_position"] == 500
    collected = [i["item_id"] for i in first["items"]]
    cursor = first["next_cursor"]
    pages = 1
    while cursor:
        current = page(index, SESSION, limit=100, before=cursor)
        collected.extend(i["item_id"] for i in current["items"])
        cursor = current["next_cursor"]
        pages += 1
    assert pages == 6
    assert len(collected) == 600 and len(set(collected)) == 600
    assert first["bytes"] <= 1024 * 1024


def test_new_appends_never_move_old_page_boundaries(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    for position in range(1, 21):
        append(
            journal, SESSION, f"rec_a{position}", position, {"role": "assistant", "content": "x"}
        )
    first = page(index, SESSION, limit=10)
    window_before = page(index, SESSION, limit=10, before=first["next_cursor"])
    # Facts commit while the client walks its older cursor window.
    for position in range(21, 26):
        append(
            journal, SESSION, f"rec_a{position}", position, {"role": "assistant", "content": "z"}
        )
    window_after = page(index, SESSION, limit=10, before=first["next_cursor"])
    assert [i["item_id"] for i in window_after["items"]] == [
        i["item_id"] for i in window_before["items"]
    ]
    fresh = page(index, SESSION, limit=100)
    assert len(fresh["items"]) == 25  # new entries exist only on the fresh page


def test_cursor_round_trips_through_the_frozen_contract(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    for position in range(1, 31):
        append(
            journal, SESSION, f"rec_c{position}", position, {"role": "assistant", "content": "x"}
        )
    first = page(index, SESSION, limit=10)
    cursor = TimelineCursor.model_validate(
        json.loads(base64.urlsafe_b64decode(first["next_cursor"].encode()).decode())
    )
    assert cursor.schema_version == 1
    assert cursor.root_session_id == SESSION and cursor.high_water == 29
    # The cursor points at the page's oldest entry; the next page continues
    # strictly older from there.
    assert cursor.before_position == first["items"][0]["timeline_position"]
    second = page(index, SESSION, limit=10, before=first["next_cursor"])
    assert second["items"][-1]["timeline_position"] == cursor.before_position - 1


def test_snapshot_revision_advances_with_every_mutation(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    first = page(index, SESSION)
    assert first["revision"] >= 1 and first["high_water"] == -1
    append(journal, SESSION, "rec_v1", 1, {"role": "user", "content": "hi"})
    second = page(index, SESSION)
    assert second["revision"] > first["revision"]
    assert second["high_water"] >= 0
    append(journal, SESSION, "rec_v2", 2, {"role": "assistant", "content": "yo"})
    third = page(index, SESSION)
    assert third["revision"] > second["revision"]


def test_history_covers_planning_published_runs_and_unstarted_drafts(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    append(journal, SESSION, "rec_goal", 1, {"role": "user", "content": "发布一个工作流"})
    seed_interaction(journal, SESSION, "cmid_plan", intent="explicit_workflow")  # admitted goal
    seed_interaction(journal, SESSION, "cmid_draft", intent="explicit_workflow")  # never started
    seed_receipt(journal, SESSION, "cmd_pause_9")
    seed_workflow(journal, SESSION)
    view = page(index, SESSION, limit=100)
    kinds = {i["kind"] for i in view["items"]}
    assert {"planning_input", "control_input", "node_progress", "user_message"} <= kinds
    item_ids = {i["item_id"] for i in view["items"]}
    assert "input:ses_trajroot1:cmid_draft" in item_ids  # un-started draft stays visible
    assert "run:wfr_traj1" in item_ids and "node:wfr_traj1:noderun_traj1" in item_ids


def test_fork_child_pages_inherit_only_the_cut_prefix(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    append(journal, SESSION, "rec_p1", 1, {"role": "assistant", "content": "one"})
    append(journal, SESSION, "rec_p2", 2, {"finish_reason": "stop"}, kind="terminal")
    append(journal, SESSION, "rec_p3", 3, {"role": "assistant", "content": "after cut"})
    seed_session(journal, CHILD, parent=(SESSION, "rec_p2", 2))
    append(journal, CHILD, "rec_c1", 3, {"role": "user", "content": "继续"})
    index = make_index(journal)
    root_page = page(index, SESSION, limit=50)
    child_page = page(index, CHILD, limit=50)
    assert [i["item_id"] for i in root_page["items"]] == [
        "reply:ses_trajroot1:1",
        "rec_p2",
        "reply:ses_trajroot1:3",
    ]
    assert [i["item_id"] for i in child_page["items"]] == [
        "reply:ses_trajroot1:1",
        "rec_p2",
        "input:ses_trajchild1:rec:rec_c1",
    ]
    # A foreign cursor is rejected, never silently re-scoped.
    from morrow.core.application import ApplicationError

    try:
        page(index, CHILD, limit=10, before=root_page["next_cursor"])
        raised = False
    except ApplicationError:
        raised = True
    assert raised or root_page["next_cursor"] is None


def test_run_leaf_entries_are_visible_to_the_root_via_the_run_chain(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    seed_workflow(journal, SESSION, run_id="wfr_leaf", node_id="noderun_leaf")
    leaf_session = "ses_trajleaf1"
    seed_session(journal, leaf_session)
    append(journal, leaf_session, "rec_leaf1", 1, {"role": "assistant", "content": "叶子输出"})

    # Bind the leaf session to the run through the durable node body.
    def bind(_):
        journal._backend.executor().execute(
            "UPDATE workflow_node_runs SET body_json=? WHERE node_run_id='noderun_leaf'",
            (json.dumps({"conversation_session_id": leaf_session}),),
        )

    journal.transact(bind)
    index.reconcile(SESSION)
    view = page(index, SESSION, limit=100)
    assert "reply:ses_trajleaf1:1" in {i["item_id"] for i in view["items"]}
    cutoffs = index.visible_cutoffs(SESSION)
    assert cutoffs.visible(leaf_session, 1)


def test_user_message_edit_fork_actions_and_leaf_content_scope(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    append(journal, SESSION, "rec_p1", 1, {"role": "user", "content": "根输入"})
    append(journal, SESSION, "rec_p2", 2, {"finish_reason": "stop"}, kind="terminal")
    seed_session(journal, CHILD, parent=(SESSION, "rec_p2", 2))
    append(journal, CHILD, "rec_c1", 3, {"role": "user", "content": "继续"})
    seed_workflow(journal, SESSION, run_id="wfr_scope", node_id="noderun_scope")
    leaf_session = "ses_trajleaf1"
    seed_session(journal, leaf_session)
    append(journal, leaf_session, "rec_l1", 1, {"role": "user", "content": "叶子输入"})

    # Bind the leaf session to the run through the durable node body.
    def bind(_):
        journal._backend.executor().execute(
            "UPDATE workflow_node_runs SET body_json=? WHERE node_run_id='noderun_scope'",
            (json.dumps({"conversation_session_id": leaf_session}),),
        )

    journal.transact(bind)
    index.reconcile(SESSION)

    root_view = page(index, SESSION, limit=100)
    root_users = {i["item_id"]: i for i in root_view["items"] if i["kind"] == "user_message"}
    assert root_users["input:ses_trajroot1:rec:rec_p1"]["actions"] == {
        "edit_fork": {"available": True, "reason_code": None}
    }
    assert root_users["input:ses_trajleaf1:rec:rec_l1"]["actions"] == {
        "edit_fork": {"available": False, "reason_code": "workflow_leaf"}
    }
    # Only user_message entries with a record carry the actions projection.
    assert all("actions" not in i for i in root_view["items"] if i["kind"] != "user_message")

    # Snapshot, before-pagination and replay project the same actions.
    expected = {item_id: item["actions"] for item_id, item in root_users.items()}
    first = page(index, SESSION, limit=2)
    assert first["next_cursor"]
    views = [
        page(index, SESSION, limit=100, before=first["next_cursor"]),
        page(index, SESSION, limit=100, after_position=0),
    ]
    for view in views:
        seen = {
            i["item_id"]: i["actions"]
            for i in view["items"]
            if i["kind"] == "user_message" and i["item_id"] in expected
        }
        assert seen
        for item_id, actions in seen.items():
            assert actions == expected[item_id]

    # A fork child keeps its ancestors as legal edit sources and still sees
    # the run leaf as a visible-but-not-forkable workflow record.
    child_view = page(index, CHILD, limit=100)
    child_users = {i["item_id"]: i for i in child_view["items"] if i["kind"] == "user_message"}
    assert child_users["input:ses_trajroot1:rec:rec_p1"]["actions"] == {
        "edit_fork": {"available": True, "reason_code": None}
    }
    assert child_users["input:ses_trajchild1:rec:rec_c1"]["actions"] == {
        "edit_fork": {"available": True, "reason_code": None}
    }
    assert child_users["input:ses_trajleaf1:rec:rec_l1"]["actions"] == {
        "edit_fork": {"available": False, "reason_code": "workflow_leaf"}
    }

    # The view-scoped resolver reads the leaf body from the root view, while
    # the strict fork-lineage walk keeps rejecting it (reading never grants
    # forking) and an unrelated session stays unreadable either way.
    body = index.read_record_content_authorized(SESSION, "rec_l1")
    assert body["record_id"] == "rec_l1" and body["content"] == "叶子输入"
    with pytest.raises(ApplicationError):
        journal.chat_timeline.content(WORKSPACE, SESSION, "rec_l1")
    stranger = "ses_trajstranger1"
    seed_session(journal, stranger)
    append(journal, stranger, "rec_s1", 1, {"role": "user", "content": "外人"})
    with pytest.raises(ApplicationError):
        index.read_record_content_authorized(SESSION, "rec_s1")


def test_index_projects_top_level_attachments_independent_of_body_size(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    reference = {
        "version": 1,
        "attachment_id": "att_hist1",
        "content_digest": "a" * 64,
        "representation_id": "art_hist1",
        "representation_digest": "b" * 64,
    }
    big_content = "长正文" + "x" * (20 * 1024)
    assert len(big_content.encode()) > 16 * 1024
    append(
        journal,
        SESSION,
        "rec_small",
        1,
        {"role": "user", "content": "短消息", "attachments": [reference]},
    )
    append(
        journal,
        SESSION,
        "rec_big",
        2,
        {"role": "user", "content": big_content, "attachments": [reference]},
    )

    def bump(_):
        journal._backend.executor().execute(
            "UPDATE sessions SET conversation_position=? WHERE session_id=?", (2, SESSION)
        )

    journal.transact(bump)
    view = page(index, SESSION, limit=50)
    items = {i["item_id"]: i for i in view["items"]}
    small = items["input:ses_trajroot1:rec:rec_small"]
    big = items["input:ses_trajroot1:rec:rec_big"]
    for item, record_id in ((small, "rec_small"), (big, "rec_big")):
        # The only wire shape: top-level attachments, source keeps identity only.
        assert item["attachments"] == [reference]
        assert "attachments" not in item["source"]
        assert item["source"]["record_id"] == record_id
    # The long body stays behind the inline gate; its reference does not.
    assert big["content"] is None
    assert small["content"] == "短消息"

    # Pagination and replay windows project the same top-level references.
    first = page(index, SESSION, limit=1)
    assert first["next_cursor"]
    windows = [
        page(index, SESSION, limit=50, before=first["next_cursor"]),
        page(index, SESSION, limit=50, after_position=0),
    ]
    for window in windows:
        for item in window["items"]:
            if item["source"].get("record_id") in {"rec_small", "rec_big"}:
                assert item["attachments"] == [reference]
                assert "attachments" not in item["source"]


def test_run_leaf_record_attachments_project_to_the_root_view(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    reference = {
        "version": 1,
        "attachment_id": "att_leaf1",
        "content_digest": "c" * 64,
        "representation_id": "art_leaf1",
        "representation_digest": "d" * 64,
    }
    seed_workflow(journal, SESSION, run_id="wfr_att", node_id="noderun_att")
    leaf_session = "ses_trajleaf1"
    seed_session(journal, leaf_session)
    append(
        journal,
        leaf_session,
        "rec_leaf_att",
        1,
        {"role": "user", "content": "叶子附件输入", "attachments": [reference]},
    )

    def bind(_):
        journal._backend.executor().execute(
            "UPDATE workflow_node_runs SET body_json=? WHERE node_run_id='noderun_att'",
            (json.dumps({"conversation_session_id": leaf_session}),),
        )

    journal.transact(bind)
    index.reconcile(SESSION)
    view = page(index, SESSION, limit=100)
    leaf_item = {i["item_id"]: i for i in view["items"]}["input:ses_trajleaf1:rec:rec_leaf_att"]
    assert leaf_item["attachments"] == [reference]
    assert "attachments" not in leaf_item["source"]
    assert leaf_item["source"]["record_id"] == "rec_leaf_att"
    # Cursor pagination agrees with the snapshot for the workflow-leaf entry.
    first = page(index, SESSION, limit=2)
    assert first["next_cursor"]
    older = page(index, SESSION, limit=100, before=first["next_cursor"])
    older_leaf = {i["item_id"]: i for i in older["items"]}["input:ses_trajleaf1:rec:rec_leaf_att"]
    assert older_leaf["attachments"] == [reference]
    assert "attachments" not in older_leaf["source"]


def test_index_survives_empty_memory_restart(tmp_path):
    journal = build_journal(tmp_path)
    seed_session(journal)
    index = make_index(journal)
    for position in range(1, 11):
        append(
            journal, SESSION, f"rec_r{position}", position, {"role": "assistant", "content": "x"}
        )
    index.reconcile(SESSION)
    # Cold restart: a brand-new service instance over the same durable store.
    fresh_index = make_index(journal)
    collected: list[str] = []
    cursor = None
    while True:
        view = page(fresh_index, SESSION, limit=5, before=cursor)
        collected.extend(i["item_id"] for i in view["items"])
        cursor = view["next_cursor"]
        if not cursor:
            break
    assert len(collected) == 10 and len(set(collected)) == 10
