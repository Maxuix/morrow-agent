import hashlib

import pytest

from morrow.adapters.local.restore import identity
from morrow.core.workspace_changes import FileManifest, WorkspaceChange
from test_stage8_core_api import ServerFixture, create_session_and_task


async def seed(fx, sid, task):
    def work():
        c = fx.host.context
        snapshots = [
            c.api.artifacts.publish_workspace_snapshot(raw, session_id=sid, task_run_id=task)
            for raw in (b"user\n", b"agent\n")
        ]
        manifests = [
            FileManifest(
                exists=True,
                sha256=hashlib.sha256(raw).hexdigest(),
                byte_size=len(raw),
                mode=0o644,
                snapshot_id=snapshot.artifact_id,
            )
            for raw, snapshot in zip((b"user\n", b"agent\n"), snapshots, strict=True)
        ]
        (fx.workspace_dir / "a.txt").write_bytes(b"agent\n")
        (fx.workspace_dir / "a.txt").chmod(0o644)
        return c.journal.workspace_changes.insert(
            WorkspaceChange(
                change_id="chg_one",
                workspace_id=fx.workspace_id,
                root_identity=identity(fx.workspace_dir),
                session_id=sid,
                task_run_id=task,
                mutation_key="tex_one:1",
                path="a.txt",
                operation="replace",
                before=manifests[0],
                after=manifests[1],
                effect_state="applied",
            )
        )

    return await fx.on_core(work)


@pytest.mark.asyncio
async def test_review_preview_apply_replay_and_scope(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        sid, task, _ = await create_session_and_task(fx.client)
        row = await seed(fx, sid, task)
        root = f"/v1/workspaces/{fx.workspace_id}/sessions/{sid}"
        response = await fx.client.get(root + "/changes?task_run_id=" + task)
        assert response.status == 200, response.body
        assert response.json()["items"][0]["restore_support"] == "ready"
        detail = await fx.client.get(root + "/changes/chg_one/diff?task_run_id=" + task)
        assert detail.status == 200, detail.body
        assert "-user" in detail.json()["diff"]
        body = {
            "command_id": "cmd_review",
            "change_ids": [row.change_id],
            "versions": {row.change_id: row.row_version},
            "task_run_id": task,
        }
        reviewed = await fx.client.request("POST", root + "/changes/review", body=body)
        assert reviewed.status == 200, reviewed.body
        assert (await fx.client.request("POST", root + "/changes/review", body=body)).status == 200
        body = {"command_id": "cmd_preview", "change_ids": [row.change_id], "task_run_id": task}
        response = await fx.client.request("POST", root + "/restores/preview", body=body)
        assert response.status == 200, response.body
        preview = response.json()
        body = {
            "command_id": "cmd_apply",
            "preview_id": preview["operation_id"],
            "preview_digest": preview["preview_digest"],
        }
        response = await fx.client.request("POST", root + "/restores", body=body)
        assert response.status == 200, response.body
        result = response.json()
        assert result["state"] == "completed"
        assert (fx.workspace_dir / "a.txt").read_bytes() == b"user\n"
        assert (await fx.client.request("POST", root + "/restores", body=body)).json() == result
        status = await fx.client.get(root + "/restores/" + result["operation_id"])
        assert status.json() == result
        other, _, _ = await create_session_and_task(fx.client)
        assert (
            await fx.client.get(root.replace(sid, other) + "/changes/chg_one/diff")
        ).status == 404
        assert (
            await fx.client.get(root.replace(sid, other) + "/restores/" + result["operation_id"])
        ).status == 404
    finally:
        fx.close()
