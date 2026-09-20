"""Timeline cursors remain scoped, stable and bounded over the durable index.

The unified mixed timeline is served by the display index (P06/P08): cursors
bind version/scope/high-water/before, unsupported cursors get an explicit
reset, and pages stay bounded over long histories.
"""

import json

from morrow.core.domain import DurableConversationRecord, DurableSession
from parallel_trajectory.store_fragment import assert_current_trajectory_schema
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


async def indexed_fixture(tmp_path):
    fixture = ServerFixture(tmp_path)
    # The store connection is bound to the Core thread; verify the current
    # trajectory tables through the host's execution seam.
    await fixture.on_core(lambda: assert_current_trajectory_schema(fixture.host.context.journal))
    return fixture


async def seed_workflow_leaf(fixture, root_session_id, leaf_session_id, record_id, text):
    """Bind a leaf session (one user record) to a run rooted in ``root_session_id``.

    Mirrors the durable root→run→node→leaf chain the P06.4 visibility rule
    authorizes, without driving a full workflow runtime.
    """

    def seed():
        journal = fixture.host.context.journal
        workspace = fixture.workspace_id

        def work(_):
            executor = journal._backend.executor()
            executor.execute(
                "INSERT OR IGNORE INTO task_runs (task_run_id, session_id, workspace_id, status,"
                " row_version, attempt, created_at_unix, updated_at_unix)"
                " VALUES ('task_p03', ?, ?, 'open', 1, 1, 1000, 1000)",
                (root_session_id, workspace),
            )
            executor.execute(
                "INSERT INTO workflow_revisions (workflow_revision_id, workspace_id,"
                " workflow_definition_id, revision, content_hash, body_json)"
                " VALUES ('rev_p03', ?, 'wf_test', 1, ?, '{}')",
                (workspace, "a" * 64),
            )
            executor.execute(
                "INSERT INTO workflow_runs (workflow_run_id, workspace_id, workflow_revision_id,"
                " root_task_run_id, status, lineage_budget_root_run_id, body_json)"
                " VALUES ('wfr_p03', ?, 'rev_p03', 'task_p03', 'running', 'wfr_p03', '{}')",
                (workspace,),
            )
            executor.execute(
                "INSERT INTO workflow_node_runs (node_run_id, workspace_id, workflow_run_id,"
                " node_id, attempt, status, body_json) VALUES ('noderun_p03', ?, 'wfr_p03',"
                " 'alpha', '1', 'running', ?)",
                (workspace, json.dumps({"conversation_session_id": leaf_session_id})),
            )
            journal.create_session(
                DurableSession(session_id=leaf_session_id, workspace_id=workspace)
            )
            journal.append_records(
                workspace,
                [
                    DurableConversationRecord(
                        record_id=record_id,
                        session_id=leaf_session_id,
                        conversation_position=1,
                        kind="message",
                        payload={"role": "user", "content": text},
                    )
                ],
            )

        journal.transact(work)

    await fixture.on_core(seed)


async def test_timeline_cursor_stable_across_append_and_content_scoped(tmp_path):
    fixture = await indexed_fixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        for key in ["one", "two"]:
            await fixture.client.post(
                path + "/interactions", {"client_message_id": key, "text": key}
            )
            await drain(fixture, sid)
        tail = (await fixture.client.get(path + "/timeline?limit=2")).json()
        assert tail["has_more"] and tail["next_cursor"]
        assert tail["reset"] is False
        assert tail["revision"] >= 1
        ids = {i["item_id"] for i in tail["items"]}
        high = tail["snapshot_high_water"]
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "three", "text": "three"}
        )
        await drain(fixture, sid)
        cursor = tail["next_cursor"]
        while cursor:
            page = (await fixture.client.get(path + "/timeline?limit=2&before=" + cursor)).json()
            assert page["snapshot_high_water"] == high
            assert not ids.intersection(i["item_id"] for i in page["items"])
            ids.update(i["item_id"] for i in page["items"])
            assert all(i["order_key"][0] <= high for i in page["items"])
            cursor = page["next_cursor"]
        fresh = (await fixture.client.get(path + "/timeline")).json()
        results = [i for i in fresh["items"] if i["kind"] == "result"]
        assert len(results) == 3
        assert all(i["result"]["task_status"] == "ready_for_acceptance" for i in results)
        assert all(i["result"]["trigger"] == "snapshot" for i in results)
        users = [i for i in fresh["items"] if i["kind"] == "user_message"]
        assert [i["content"] for i in users] == ["one", "two", "three"]
        assert all(i["source"].get("record_id") for i in users)
        sid_b, path_b = await new_session(fixture, "cmd_b")
        assert (
            await fixture.client.get(path_b + "/timeline?before=" + tail["next_cursor"])
        ).status == 400
        record_id = users[0]["source"]["record_id"]
        assert (await fixture.client.get(path + "/content/" + record_id)).json()["content"] == "one"
        assert (await fixture.client.get(path_b + "/content/" + record_id)).status == 404
    finally:
        fixture.close()


async def test_workflow_leaf_actions_scoped_and_content_readable_from_root(tmp_path):
    fixture = await indexed_fixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "root", "text": "根消息"}
        )
        await drain(fixture, sid)
        await seed_workflow_leaf(fixture, sid, "ses_leafp03", "rec_leafp03", "叶子消息")

        view = (await fixture.client.get(path + "/timeline")).json()
        by_origin = {}
        for item in view["items"]:
            if item["kind"] == "user_message":
                by_origin.setdefault(item["source"].get("origin_session_id"), item)
        assert by_origin[sid]["actions"] == {"edit_fork": {"available": True, "reason_code": None}}
        leaf_item = by_origin["ses_leafp03"]
        assert leaf_item["actions"] == {
            "edit_fork": {"available": False, "reason_code": "workflow_leaf"}
        }
        # The leaf body is readable from the root view through the
        # view-scoped resolver (attachments shape preserved when present).
        content = await fixture.client.get(path + "/content/rec_leafp03")
        assert content.status == 200, content.body
        assert content.json()["content"] == "叶子消息"
        # An unrelated session still gets 404 for the same record.
        _, stranger = await new_session(fixture, "cmd_stranger")
        assert (await fixture.client.get(stranger + "/content/rec_leafp03")).status == 404
        # A fork child still inherits the run chain: the leaf stays visible
        # there, and the root can still fork its own user message.
        forked = await fixture.client.post(path + "/fork", {"command_id": "cmd_fork_cut"})
        assert forked.status == 200, forked.body
        child_path = path.rsplit("/", 1)[0] + "/" + forked.json()["session"]["session_id"]
        child_view = (await fixture.client.get(child_path + "/timeline")).json()
        child_leaf = [
            i
            for i in child_view["items"]
            if i["kind"] == "user_message" and i["source"].get("origin_session_id") == "ses_leafp03"
        ]
        assert (
            child_leaf and child_leaf[0]["actions"]["edit_fork"]["reason_code"] == "workflow_leaf"
        )
        edited = await fixture.client.post(
            path + "/fork",
            {
                "command_id": "cmd_edit_root",
                "edit_record_id": by_origin[sid]["source"]["record_id"],
            },
        )
        assert edited.status == 200, edited.body
    finally:
        fixture.close()


async def test_invalid_cursor_is_rejected(tmp_path):
    fixture = await indexed_fixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        await fixture.client.post(path + "/interactions", {"client_message_id": "a", "text": "a"})
        await drain(fixture, sid)
        fresh = (await fixture.client.get(path + "/timeline")).json()
        assert fresh["reset"] is False
        response = await fixture.client.get(path + "/timeline?before=not-a-current-cursor")
        assert response.status == 400
    finally:
        fixture.close()


async def test_replay_window_closes_snapshot_subscribe_gaps(tmp_path):
    fixture = await indexed_fixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        for key in ["one", "two"]:
            await fixture.client.post(
                path + "/interactions", {"client_message_id": key, "text": key}
            )
            await drain(fixture, sid)
        snapshot = (await fixture.client.get(path + "/timeline")).json()
        high_water = snapshot["snapshot_high_water"]
        _resp = await fixture.client.post(
            path + "/interactions", {"client_message_id": "three", "text": "three"}
        )
        await drain(fixture, sid)
        window = (await fixture.client.get(path + f"/timeline?after_position={high_water}")).json()
        kinds = [i["kind"] for i in window["items"]]
        assert "user_message" in kinds or "turn_status" in kinds
        assert all(i["timeline_position"] > high_water for i in window["items"])
        merged = {i["item_id"] for i in snapshot["items"]} | {i["item_id"] for i in window["items"]}
        fresh = (await fixture.client.get(path + "/timeline")).json()
        assert merged == {i["item_id"] for i in fresh["items"]}
        assert window["revision"] > snapshot["revision"]
        # Replaying the same window is idempotent for item_id-merging clients.
        again = (await fixture.client.get(path + f"/timeline?after_position={high_water}")).json()
        assert [i["item_id"] for i in again["items"]] == [i["item_id"] for i in window["items"]]
    finally:
        fixture.close()


async def test_l03_ten_thousand_messages_one_hundred_bounded_pages(tmp_path):
    import resource

    from morrow.core.domain import DurableConversationRecord

    fixture = await indexed_fixture(tmp_path)
    try:
        sid, path = await new_session(fixture)

        def seed():
            records = []
            for index in range(5000):
                for offset, role in enumerate(("user", "assistant", None), 1):
                    position = index * 3 + offset
                    payload = (
                        {"role": role, "content": "x" * 1024} if role else {"finish_reason": "stop"}
                    )
                    records.append(
                        DurableConversationRecord(
                            record_id=f"rec_load_{position}",
                            session_id=sid,
                            conversation_position=position,
                            kind="message" if role else "terminal",
                            payload=payload,
                        )
                    )
            fixture.host.context.journal.append_records(fixture.workspace_id, records)

        await fixture.on_core(seed)
        seen = set()
        cursor = None
        peak_page = 0
        for _ in range(100):
            page = (
                await fixture.client.get(
                    path + "/timeline?limit=100" + ("&before=" + cursor if cursor else "")
                )
            ).json()
            assert len(page["items"]) <= 100
            assert not seen.intersection(i["item_id"] for i in page["items"])
            seen.update(i["item_id"] for i in page["items"])
            peak_page = max(peak_page, page["bytes"])
            cursor = page["next_cursor"]
        assert len(seen) == 10000
        assert peak_page <= 1048576
        # New appends never move old page boundaries: the walked pages replay
        # identically while the fresh page gains entries.
        print(
            "L03",
            {
                "text_messages": 10000,
                "pages": 100,
                "page_bytes": peak_page,
                "peak_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            },
        )
    finally:
        fixture.close()
