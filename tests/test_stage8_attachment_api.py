"""Upload boundary and actual model context through the production Session writer."""

import asyncio
import io

import pytest
from PIL import Image

from morrow.adapters.models.openai_compatible import serialize_message
from morrow.core.models import UserMessage
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import ServerFixture


async def uploaded(fx, sid, data, mime="text/plain", key="upload.1"):
    root = f"/v1/workspaces/{fx.workspace_id}/attachments"
    result = await fx.client.post(
        root,
        {
            "command_id": key,
            "session_id": sid,
            "name": "example",
            "media_type": mime,
            "byte_size": len(data),
        },
    )
    assert result.status == 201, result.body
    row = result.json()
    path = root + "/" + row["attachment_id"]
    result = await fx.client.request(
        "PUT",
        path + f"/content?session_id={sid}&revision={row['revision']}",
        body=data,
        content_type=mime,
    )
    assert result.status == 202, result.body

    async def wait():
        task = fx.host.context.chat.attachments.jobs.get(row["attachment_id"])
        if task:
            await asyncio.shield(task)

    await fx.host.execute_preparation(wait)
    result = await fx.client.get(path + f"?session_id={sid}")
    assert result.status == 200, result.body
    assert result.json()["state"] == "ready", result.body
    return result.json(), path


async def test_attachment_only_send_reaches_model_and_sole_log_writer(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        row, path = await uploaded(fx, sid, b"unique file content 2468")
        body = {"client_message_id": "attached.1", "text": "", "attachments": [row["reference"]]}
        response = await fx.client.post(session + "/interactions", body)
        assert response.status == 202, response.body
        await drain(fx, sid)
        receipt = (await fx.client.get(session + "/interactions/attached.1")).json()["receipt"]
        assert receipt["status"] == "settled", receipt

        def verify():
            journal = fx.host.context.journal
            records = journal.load_records(fx.workspace_id, sid)
            users = [r.payload for r in records if r.payload.get("role") == "user"]
            assert len(users) == 1 and users[0]["content"] == ""
            assert users[0]["attachments"] == [row["reference"]]
            assert "unique file content" not in str(users)
            run = journal.get_agent_run(fx.workspace_id, receipt["agent_run_id"])
            assert run.snapshot.input_attachments[0].model_dump(mode="json") == row["reference"]
            calls = [call for p in fx.bank.providers for call in p.stream_calls]
            assert "unique file content 2468" in str(calls)

        await fx.on_core(verify)
        assert (await fx.client.post(session + "/interactions", body)).status == 202
        other, _ = await new_session(fx, "cmd_other")
        assert (await fx.client.get(path + f"?session_id={other}")).status == 404
    finally:
        fx.close()


async def test_image_sdk_parts_tool_continuation_and_capability_refusal(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        output = io.BytesIO()
        Image.new("RGB", (12, 15), "blue").save(output, format="PNG")
        row, _ = await uploaded(fx, sid, output.getvalue(), mime="image/png")
        body = {
            "client_message_id": "image.1",
            "text": "Describe",
            "attachments": [row["reference"]],
        }
        assert (await fx.client.post(session + "/interactions", body)).status == 400

        def verify():
            from morrow.application.attachments import hydrate_attachment_message

            message = UserMessage.model_validate(
                {"content": "Describe", "attachments": [row["reference"]]}
            )
            with pytest.raises(ValueError, match="not been resolved"):
                serialize_message(message)
            hydrated = hydrate_attachment_message(fx.host.context.api.artifacts, message)
            wire = serialize_message(hydrated)
            assert wire["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
            assert "data:" not in hydrated.model_dump_json()
            assert serialize_message(hydrated) == wire

        await fx.on_core(verify)
    finally:
        fx.close()


async def test_upload_does_not_relax_json_or_origin_and_rejects_over_limit(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        root = f"/v1/workspaces/{fx.workspace_id}/attachments"
        row = (
            await fx.client.post(
                root,
                {
                    "command_id": "guard",
                    "session_id": sid,
                    "name": "sample",
                    "media_type": "text/plain",
                    "byte_size": 3,
                },
            )
        ).json()
        path = root + "/" + row["attachment_id"] + f"/content?session_id={sid}&revision=1"
        assert (
            await fx.client.request(
                "POST", session + "/interactions", body=b"x", content_type="text/plain"
            )
        ).status == 415
        assert (
            await fx.client.request(
                "PUT", path, body=b"abc", content_type="text/plain", origin="http://evil.test"
            )
        ).status == 403
        assert (
            await fx.client.request("PUT", path, body=b"abc", content_type="image/svg+xml")
        ).status == 415
        assert (
            await fx.client.request("PUT", path, body=b"abcd", content_type="text/plain")
        ).status == 400
        state = (
            await fx.client.get(root + "/" + row["attachment_id"] + f"?session_id={sid}")
        ).json()
        assert state["state"] == "failed"
    finally:
        fx.close()


async def test_workspace_selection_freezes_content_and_refuses_symlinks(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fx)
        selected = fx.workspace_dir / "source.py"
        selected.write_text("frozen = 123")
        outside = tmp_path / "outside.txt"
        outside.write_text("outside")
        (fx.workspace_dir / "escape").symlink_to(outside)
        root = f"/v1/workspaces/{fx.workspace_id}"
        search = await fx.client.get(root + "/files/search?query=source")
        assert search.status == 200 and search.json()["files"][0]["path"] == "source.py"
        for path in ("escape", "../outside.txt", str(outside)):
            result = await fx.client.post(
                root + "/attachments/reference",
                {"command_id": "invalid", "session_id": sid, "path": path},
            )
            assert result.status == 400, result.body
        result = await fx.client.post(
            root + "/attachments/reference",
            {"command_id": "selection", "session_id": sid, "path": "source.py"},
        )
        assert result.status == 202, result.body
        selected.write_text("changed = 456")
        identity = result.json()["attachment_id"]

        async def verify():
            service = fx.host.context.chat.attachments
            task = service.jobs.get(identity)
            if task:
                await asyncio.shield(task)
            from morrow.core.attachments import AttachmentRef

            rep = service.representation(
                AttachmentRef.model_validate(service.get(identity)["reference"])
            )
            assert (
                service.artifacts.read(rep.parts[0].artifact_id, max_bytes=100).content
                == b"frozen = 123"
            )

        await fx.host.execute_preparation(verify)
    finally:
        fx.close()


async def test_submitted_attachment_fork_backup_and_draft_release_replay(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        row, path = await uploaded(fx, sid, b"inherited file content")
        assert (
            await fx.client.post(
                session + "/interactions",
                {
                    "client_message_id": "fork.input",
                    "text": "read",
                    "attachments": [row["reference"]],
                },
            )
        ).status == 202
        await drain(fx, sid)
        response = await fx.client.post(session + "/fork", {"command_id": "cmd_attachment_fork"})
        assert response.status == 200, response.body
        child = response.json()["session"]["session_id"]
        inherited = await fx.client.get(path + f"?session_id={child}")
        assert inherited.status == 200, inherited.body
        assert inherited.json()["reference"] == row["reference"]

        def backup():
            from morrow.adapters.state.operational import OperationalStore
            from morrow.application.backup import OperationalBackupService

            store = OperationalStore(fx.state_root)
            service = OperationalBackupService(store)
            report = service.create("attachment-fixture")
            assert service.verify(store.layout.backups_dir / report.bundle_name).ok
            assert row["reference"]["representation_id"] in {
                a.artifact_id for a in report.artifacts
            }

        await fx.on_core(backup)
        draft, draft_path = await uploaded(fx, sid, b"disposable", key="disposable")
        body = {
            "command_id": "cmd_release",
            "session_id": sid,
            "expected_revision": draft["revision"],
        }
        for _ in range(2):
            released = await fx.client.post(draft_path + "/release", body)
            assert released.status == 200, released.body

        def verify():
            service = fx.host.context.chat.attachments
            assert service.artifacts.get(draft["reference"]["representation_id"]) is None
            assert not service.artifacts.filesystem.final_path(
                draft["reference"]["representation_id"]
            ).exists()
            assert service.artifacts.get(row["reference"]["representation_id"]) is not None

        await fx.on_core(verify)
    finally:
        fx.close()


async def test_attachment_only_follow_up_preserves_empty_original_text(tmp_path):
    from test_stage8_chat_runtime_control import held_fixture

    fx, sid, path, cell, run = await held_fixture(tmp_path)
    try:
        row, _ = await uploaded(fx, sid, b"queued attachment content")
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "attached.follow",
                "intent": "follow_up",
                "text": "",
                "target_agent_run_id": run,
                "attachments": [row["reference"]],
            },
        )
        assert sent.status == 202, sent.body
        await fx.on_core(lambda: cell["gate"].set())
        await drain(fx, sid)

        def verify():
            users = [
                r.payload
                for r in fx.host.context.journal.load_records(fx.workspace_id, sid)
                if r.payload.get("role") == "user"
            ]
            assert users[-1]["content"] == "" and users[-1]["attachments"] == [row["reference"]]

        await fx.on_core(verify)
    finally:
        fx.close()


async def test_clone_for_message_edit_retains_original_after_draft_release(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        row, path = await uploaded(fx, sid, b"edit source remains frozen")
        assert (
            await fx.client.post(
                session + "/interactions",
                {"client_message_id": "source", "attachments": [row["reference"]]},
            )
        ).status == 202
        await drain(fx, sid)
        target, _ = await new_session(fx, "cmd_target")
        body = {"command_id": "cmd_copy", "source_session": sid, "target_session": target}
        clone = await fx.client.post(path + "/clone", body)
        assert clone.status == 200, clone.body
        assert (await fx.client.post(path + "/clone", body)).json() == clone.json()
        copied = clone.json()
        assert copied["reference"]["attachment_id"] != row["reference"]["attachment_id"]
        assert copied["reference"]["representation_id"] == row["reference"]["representation_id"]
        release = await fx.client.post(
            path.rsplit("/", 1)[0] + "/" + copied["attachment_id"] + "/release",
            {
                "command_id": "cmd_discard_copy",
                "session_id": target,
                "expected_revision": copied["revision"],
            },
        )
        assert release.status == 200, release.body
        original = await fx.client.get(path + "?session_id=" + sid)
        assert original.status == 200 and original.json()["state"] == "submitted"
    finally:
        fx.close()


async def test_history_wire_projects_top_level_attachment_refs_and_preview_limits(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        long_text = "# frozen heading\n" + "x" * 40000
        row, path = await uploaded(fx, sid, long_text.encode())
        sent = await fx.client.post(
            session + "/interactions",
            {"client_message_id": "long.att", "text": "读附件", "attachments": [row["reference"]]},
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)

        def timeline_items(payload):
            return payload["items"] if "items" in payload else payload["timeline"]["items"]

        timeline = (await fx.client.get(session + "/timeline?limit=50")).json()
        attached = [i for i in timeline_items(timeline) if i.get("attachments")]
        assert attached, timeline
        item = attached[-1]
        # The only wire shape: top-level references, source keeps identity only.
        assert item["attachments"] == [row["reference"]]
        assert "attachments" not in item["source"]
        assert item["source"]["record_id"]
        # The durable replay window agrees with the snapshot. The first
        # committed entry lives at timeline_position 0; the replay window
        # starts strictly after it, so window entries must match exactly.
        replay = (await fx.client.get(session + "/timeline?limit=50&after_position=0")).json()
        replay_attached = {
            i["item_id"]: i.get("attachments")
            for i in timeline_items(replay)
            if i.get("attachments")
        }
        for refs in replay_attached.values():
            assert refs == [row["reference"]]
        assert item["timeline_position"] == 0 or (
            replay_attached[item["item_id"]] == [row["reference"]]
        )

        # The preview surfaces the text truncation at the metadata/content wire.
        meta = await fx.client.get(path + f"?session_id={sid}")
        assert meta.status == 200, meta.body
        representation = meta.json()["representation"]
        assert representation["omitted_chars"] == len(long_text) - 32768
        part = representation["parts"][0]
        content = await fx.client.get(
            path + f"/content?session_id={sid}&artifact_id={part['artifact_id']}"
        )
        assert content.status == 200
        assert content.body.decode() == long_text[:32768]
    finally:
        fx.close()


async def test_pixel_limit_rejects_images_over_16mp_over_the_wire(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fx)
        output = io.BytesIO()
        Image.new("RGB", (4001, 4000), "white").save(output, format="PNG")
        data = output.getvalue()
        assert len(data) <= 8 * 1024 * 1024
        root = f"/v1/workspaces/{fx.workspace_id}/attachments"
        reserved = (
            await fx.client.post(
                root,
                {
                    "command_id": "pixels.wire",
                    "session_id": sid,
                    "name": "huge.png",
                    "media_type": "image/png",
                    "byte_size": len(data),
                },
            )
        ).json()
        path = root + "/" + reserved["attachment_id"]
        uploaded_response = await fx.client.request(
            "PUT",
            path + f"/content?session_id={sid}&revision={reserved['revision']}",
            body=data,
            content_type="image/png",
        )
        assert uploaded_response.status == 202, uploaded_response.body

        async def wait():
            task = fx.host.context.chat.attachments.jobs.get(reserved["attachment_id"])
            if task:
                await asyncio.shield(task)

        await fx.host.execute_preparation(wait)
        state = (await fx.client.get(path + f"?session_id={sid}")).json()
        assert state["state"] == "failed", state
    finally:
        fx.close()


async def test_workflow_leaf_referenced_attachment_is_readable_from_root_view(tmp_path):
    from morrow.core.domain import DurableConversationRecord, DurableSession

    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        leaf = "ses_leaf_att"

        def seed_leaf_session():
            journal = fx.host.context.journal
            journal.transact(
                lambda _: journal.create_session(
                    DurableSession(session_id=leaf, workspace_id=fx.workspace_id)
                )
            )

        await fx.on_core(seed_leaf_session)
        row, path = await uploaded(fx, leaf, b"leaf attachment bytes")

        def bind_run():
            import json

            journal = fx.host.context.journal

            def work(_):
                executor = journal._backend.executor()
                executor.execute(
                    "INSERT INTO task_runs (task_run_id, session_id, workspace_id, status,"
                    " row_version, attempt, created_at_unix, updated_at_unix)"
                    " VALUES ('task_leaf_att', ?, ?, 'open', 1, 1, 1000, 1000)",
                    (sid, fx.workspace_id),
                )
                executor.execute(
                    "INSERT INTO workflow_revisions (workflow_revision_id, workspace_id,"
                    " workflow_definition_id, revision, content_hash, body_json)"
                    " VALUES ('rev_leaf_att', ?, 'wf_att', 1, ?, '{}')",
                    (fx.workspace_id, "a" * 64),
                )
                executor.execute(
                    "INSERT INTO workflow_runs (workflow_run_id, workspace_id,"
                    " workflow_revision_id, root_task_run_id, status,"
                    " lineage_budget_root_run_id, body_json)"
                    " VALUES ('wfr_leaf_att', ?, 'rev_leaf_att', 'task_leaf_att', 'running',"
                    " 'wfr_leaf_att', '{}')",
                    (fx.workspace_id,),
                )
                executor.execute(
                    "INSERT INTO workflow_node_runs (node_run_id, workspace_id,"
                    " workflow_run_id, node_id, attempt, status, body_json)"
                    " VALUES ('noderun_leaf_att', ?, 'wfr_leaf_att', 'alpha', '1', 'running', ?)",
                    (fx.workspace_id, json.dumps({"conversation_session_id": leaf})),
                )
                journal.append_records(
                    fx.workspace_id,
                    [
                        DurableConversationRecord(
                            record_id="rec_leaf_att",
                            session_id=leaf,
                            conversation_position=1,
                            kind="message",
                            payload={
                                "role": "user",
                                "content": "叶子附件输入",
                                "attachments": [row["reference"]],
                            },
                        )
                    ],
                )

            journal.transact(work)

        await fx.on_core(bind_run)

        # The root view wire projects the leaf record's reference top-level.
        timeline = (await fx.client.get(session + "/timeline?limit=50")).json()
        items = timeline["items"] if "items" in timeline else timeline["timeline"]["items"]
        leaf_items = [i for i in items if i["source"].get("record_id") == "rec_leaf_att"]
        assert leaf_items, timeline
        assert leaf_items[0]["attachments"] == [row["reference"]]
        assert "attachments" not in leaf_items[0]["source"]

        # Authorization follows the visible record: the root session reads it.
        meta = await fx.client.get(path + f"?session_id={sid}")
        assert meta.status == 200, meta.body
        part = meta.json()["representation"]["parts"][0]
        content = await fx.client.get(
            path + f"/content?session_id={sid}&artifact_id={part['artifact_id']}"
        )
        assert content.status == 200 and content.body == b"leaf attachment bytes"

        # An unrelated session in the same workspace stays unauthorized.
        other, _ = await new_session(fx, "cmd_leaf_stranger")
        assert (await fx.client.get(path + f"?session_id={other}")).status == 404
    finally:
        fx.close()


async def test_attachment_referenced_after_fork_cut_is_not_inherited(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        before, before_path = await uploaded(fx, sid, b"before cut bytes", key="cut.before")
        after, after_path = await uploaded(fx, sid, b"after cut bytes", key="cut.after")
        assert (
            await fx.client.post(
                session + "/interactions",
                {
                    "client_message_id": "cut.1",
                    "text": "读",
                    "attachments": [before["reference"]],
                },
            )
        ).status == 202
        await drain(fx, sid)
        child = (await fx.client.post(session + "/fork", {"command_id": "cmd_cut_fork"})).json()[
            "session"
        ]["session_id"]
        # The pre-cut reference is inherited through the fork chain.
        assert (await fx.client.get(before_path + f"?session_id={child}")).status == 200
        # A sibling attachment that no visible record references is not.
        assert (await fx.client.get(after_path + f"?session_id={child}")).status == 404
        assert (
            await fx.client.post(
                session + "/interactions",
                {
                    "client_message_id": "cut.2",
                    "text": "再读",
                    "attachments": [after["reference"]],
                },
            )
        ).status == 202
        await drain(fx, sid)
        # A reference recorded only past the cut never grants the child access.
        assert (await fx.client.get(after_path + f"?session_id={child}")).status == 404
        for path, row in ((before_path, before), (after_path, after)):
            owner = await fx.client.get(path + f"?session_id={sid}")
            assert owner.status == 200 and owner.json()["state"] == "submitted"
            assert owner.json()["reference"] == row["reference"]
    finally:
        fx.close()


async def test_clone_from_unauthorized_source_fails_and_parent_stays_intact(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, session = await new_session(fx)
        row, path = await uploaded(fx, sid, b"clone guard bytes")
        assert (
            await fx.client.post(
                session + "/interactions",
                {"client_message_id": "guard.src", "attachments": [row["reference"]]},
            )
        ).status == 202
        await drain(fx, sid)
        target, _ = await new_session(fx, "cmd_guard_target")
        bad = await fx.client.post(
            path + "/clone",
            {
                "command_id": "cmd_bad_clone",
                "source_session": target,
                "target_session": target,
            },
        )
        assert bad.status == 404, bad.body
        # The failed clone never consumes or mutates the parent attachment.
        original = await fx.client.get(path + f"?session_id={sid}")
        assert original.status == 200 and original.json()["state"] == "submitted"
        assert original.json()["reference"] == row["reference"]
        good = await fx.client.post(
            path + "/clone",
            {"command_id": "cmd_good_clone", "source_session": sid, "target_session": target},
        )
        assert good.status == 200, good.body
    finally:
        fx.close()


async def test_slow_parser_does_not_block_stop_or_revive_cancelled_draft(tmp_path):
    import asyncio

    from test_stage8_chat_runtime_control import held_fixture

    fx, sid, path, cell, run = await held_fixture(tmp_path)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()

    class SlowPool:
        active = 0
        concurrency = 2
        reservations = set()

        async def parse(self, *args):
            self.active += 1
            loop.call_soon_threadsafe(entered.set)
            try:
                await asyncio.Event().wait()
            finally:
                self.active -= 1

    try:
        pool = SlowPool()
        await fx.on_core(lambda: setattr(fx.host.context.chat.attachments, "pool", pool))
        base = f"/v1/workspaces/{fx.workspace_id}/attachments"
        row = (
            await fx.client.post(
                base,
                {
                    "command_id": "slow",
                    "session_id": sid,
                    "name": "slow.txt",
                    "media_type": "text/plain",
                    "byte_size": 4,
                },
            )
        ).json()
        uploaded_response = await fx.client.request(
            "PUT",
            base
            + "/"
            + row["attachment_id"]
            + f"/content?session_id={sid}&revision={row['revision']}",
            body=b"slow",
            content_type="text/plain",
        )
        assert uploaded_response.status == 202, uploaded_response.body
        await asyncio.wait_for(entered.wait(), 5)
        queue = (await fx.client.get(path + "/queue")).json()
        stopped = await fx.client.post(
            path + "/control",
            {
                "command_id": "cmd_stop_slow",
                "action": "stop",
                "target_agent_run_id": run,
                "expected_revision": queue["revision"],
            },
        )
        assert stopped.status == 200, stopped.body
        await drain(fx, sid)
        from test_stage8_chat_permissions import pending_approval
        from test_stage8_core_api import WRITE_CALL_SCRIPT

        fx.bank.on_create = lambda _: setattr(fx.bank.providers[-1], "responses", WRITE_CALL_SCRIPT)
        other, other_path = await new_session(fx, "cmd_approval_session")
        assert (
            await fx.client.post(
                other_path + "/interactions",
                {"client_message_id": "approve-with-parser", "text": "write"},
            )
        ).status == 202
        approval = await pending_approval(fx)
        resolved = await fx.client.post(
            f"/v1/approvals/{approval['approval_id']}/resolve",
            {"command_id": "cmd_parser_approval", "decision": "allow_once", "approved": True},
        )
        assert resolved.status == 200, resolved.body
        await drain(fx, other)
        assert pool.active == 1
        processing = uploaded_response.json()
        released = await fx.client.post(
            base + "/" + row["attachment_id"] + "/release",
            {
                "command_id": "cmd_cancel_slow",
                "session_id": sid,
                "expected_revision": processing["revision"],
            },
        )
        assert released.status == 200, released.body

        async def settle():
            await fx.host.context.chat.attachments.shutdown()
            assert pool.active == 0 and not pool.reservations

        await fx.host.execute_preparation(settle)
        assert (
            await fx.client.get(base + "/" + row["attachment_id"] + "?session_id=" + sid)
        ).json()["state"] == "released"
    finally:
        fx.close()
