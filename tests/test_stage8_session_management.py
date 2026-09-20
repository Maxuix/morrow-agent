"""Metadata OCC, original lifecycle and immutable checkpoint/edit forks."""

from urllib.parse import quote

import pytest

from morrow.core.application import ApplicationError
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


async def test_metadata_search_pin_archive_unarchive_and_default_title(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        assert (await fixture.client.get(path + "/metadata")).json()["title"] == "新对话"
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "hello", "text": "讨论工作区管理"}
        )
        await drain(fixture, sid)
        data = (await fixture.client.get(path + "/metadata")).json()
        assert data["title"] == "讨论工作区管理"
        response = await fixture.on_core(
            lambda: fixture.host.context.api.update_session_metadata(
                sid,
                command_id="cmd_title",
                expected_revision=data["revision"],
                title="项目计划",
                pinned=True,
            )
        )
        assert response.value["pinned"]
        snapshot = (await fixture.client.get(path + "/snapshot")).json()
        assert snapshot["session"]["metadata"]["title"] == "项目计划"
        assert snapshot["session"]["metadata"]["pinned"]
        root = path.rsplit("/", 1)[0]
        page = (
            await fixture.client.get(root + "?search=" + quote("项目") + "&archived=false")
        ).json()
        assert [s["session_id"] for s in page["sessions"]] == [sid]
        with pytest.raises(ApplicationError, match="changed"):
            await fixture.on_core(
                lambda: fixture.host.context.api.update_session_metadata(
                    sid,
                    command_id="cmd_stale",
                    expected_revision=data["revision"],
                    title="stale",
                )
            )
        session = (await fixture.client.get(path)).json()["session"]
        archived = await fixture.client.post(
            path + "/archive",
            {"command_id": "cmd_archive_busy", "expected_updated_at": session["updated_at"]},
        )
        assert archived.status == 400  # Original TaskRun must be closed explicitly.
        await fixture.on_core(
            lambda: fixture.host.context.api.task_cancel(
                session["current_task_run_id"], command_id="cmd_cancel"
            )
        )
        session = (await fixture.client.get(path)).json()["session"]
        archived = await fixture.client.post(
            path + "/archive",
            {"command_id": "cmd_archive", "expected_updated_at": session["updated_at"]},
        )
        assert archived.status == 200, archived.body
        assert (await fixture.client.get(root + "?archived=true")).json()["sessions"][0][
            "session_id"
        ] == sid
        response = await fixture.client.post(
            path + "/unarchive",
            {
                "command_id": "cmd_restore",
                "expected_updated_at": archived.json()["session"]["updated_at"],
            },
        )
        assert response.status == 200, response.body
        assert response.json()["session"]["lifecycle"] == "active"
        assert (await fixture.client.get(path + "/snapshot")).json()["session"][
            "lifecycle"
        ] == "active"
        assert (await fixture.client.get(path + "/metadata")).json()["title"] == "项目计划"
    finally:
        fixture.close()


async def test_checkpoint_fork_and_edit_preserve_source_and_cutoff(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        for i in range(2):
            await fixture.client.post(
                path + "/interactions", {"client_message_id": f"msg-{i}", "text": f"original {i}"}
            )
            await drain(fixture, sid)
        original = (await fixture.client.get(path + "/timeline")).json()
        users = [i for i in original["items"] if i["kind"] == "user_message"]
        response = await fixture.client.post(path + "/fork", {"command_id": "cmd_fork"})
        assert response.status == 200, response.body
        child = response.json()["session"]
        assert child["parent_session_id"] == sid
        fork_path = path.rsplit("/", 1)[0] + "/" + child["session_id"]
        assert [
            i["content"]
            for i in (await fixture.client.get(fork_path + "/timeline")).json()["items"]
            if i["kind"] == "user_message"
        ] == ["original 0", "original 1"]
        response = await fixture.client.post(
            path + "/fork",
            {"command_id": "cmd_edit", "edit_record_id": users[1]["source"]["record_id"]},
        )
        assert response.status == 200, response.body
        edited_sid = response.json()["session"]["session_id"]
        edited_path = path.rsplit("/", 1)[0] + "/" + edited_sid
        before = (await fixture.client.get(edited_path + "/timeline")).json()
        assert [i["content"] for i in before["items"] if i["kind"] == "user_message"] == [
            "original 0"
        ]
        await fixture.client.post(
            edited_path + "/interactions", {"client_message_id": "edited", "text": "replacement"}
        )
        await drain(fixture, edited_sid)
        assert (await fixture.client.get(path + "/timeline")).json() == original
        replay = await fixture.client.post(
            path + "/fork",
            {"command_id": "cmd_edit", "edit_record_id": users[1]["source"]["record_id"]},
        )
        assert replay.json()["session"]["session_id"] == edited_sid
        empty = await fixture.client.post(
            path + "/fork",
            {"command_id": "cmd_edit_first", "edit_record_id": users[0]["source"]["record_id"]},
        )
        assert empty.status == 200, empty.body
        assert empty.json()["empty_prefix"] and empty.json()["session"]["parent_session_id"] is None
        _, stranger = await new_session(fixture, "cmd_stranger")
        assert (
            await fixture.client.post(
                stranger + "/fork",
                {"command_id": "cmd_cross", "edit_record_id": users[0]["source"]["record_id"]},
            )
        ).status == 404
    finally:
        fixture.close()


async def test_edit_fork_rejects_workflow_leaf_record_without_side_effects(tmp_path):
    from test_stage8_chat_timeline import indexed_fixture, seed_workflow_leaf

    fixture = await indexed_fixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "root", "text": "根消息"}
        )
        await drain(fixture, sid)
        await seed_workflow_leaf(fixture, sid, "ses_leafp03", "rec_leafp03", "叶子消息")
        root = path.rsplit("/", 1)[0]
        sessions_before = len((await fixture.client.get(root)).json()["sessions"])

        # The workflow-leaf record is a visible timeline entry but never a
        # legal edit source: the strict ancestry precheck rejects it before
        # any session or checkpoint is created.
        response = await fixture.client.post(
            path + "/fork",
            {"command_id": "cmd_edit_leaf", "edit_record_id": "rec_leafp03"},
        )
        assert response.status == 404, response.body
        assert len((await fixture.client.get(root)).json()["sessions"]) == sessions_before
        assert (await fixture.client.get(path + "/checkpoints")).json()["checkpoints"] == []
        # The leaf body stays readable (read scope), while forking keeps 404.
        assert (await fixture.client.get(path + "/content/rec_leafp03")).json()[
            "content"
        ] == "叶子消息"
        replay = await fixture.client.post(
            path + "/fork",
            {"command_id": "cmd_edit_leaf", "edit_record_id": "rec_leafp03"},
        )
        assert replay.status == 404
        # A legal root user message still forks exactly as before.
        view = (await fixture.client.get(path + "/timeline")).json()
        root_record = next(
            i["source"]["record_id"]
            for i in view["items"]
            if i["kind"] == "user_message" and i["source"].get("origin_session_id") == sid
        )
        legal = await fixture.client.post(
            path + "/fork",
            {"command_id": "cmd_edit_root", "edit_record_id": root_record},
        )
        assert legal.status == 200, legal.body
        assert len((await fixture.client.get(root)).json()["sessions"]) == sessions_before + 1
    finally:
        fixture.close()
