from test_stage8_core_api import ServerFixture


async def test_workspace_management_and_scoped_legacy_resources(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        root = str(tmp_path)
        opened = await fixture.client.post(
            "/v1/workspaces",
            {
                "command_id": "cmd_project",
                "action": "create",
                "path": root,
                "name": "project-b",
            },
        )
        assert opened.status == 200, opened.body
        wid = opened.json()["workspace"]["workspace_id"]
        replay = await fixture.client.post(
            "/v1/workspaces",
            {
                "command_id": "cmd_project",
                "action": "create",
                "path": root,
                "name": "project-b",
            },
        )
        assert replay.json()["disposition"] == "replay"
        base = "/v1/workspaces/" + wid
        meta = await fixture.client.get(base + "/meta")
        assert meta.status == 200, meta.body
        assert meta.json()["workspace_id"] == wid
        created = await fixture.client.post(base + "/sessions", {"command_id": "cmd_new"})
        sid = created.json()["result"]["session"]["session_id"]
        assert (await fixture.client.get(base + "/sessions/" + sid + "/tasks")).status == 200
        task = await fixture.client.post(
            base + "/tasks", {"command_id": "cmd_task", "session_id": sid}
        )
        assert task.status == 200, task.body
        tid = task.json()["result"]["task"]["task_run_id"]
        assert (await fixture.client.get("/v1/tasks/" + tid)).status == 404
        assert (await fixture.client.get(base + "/tasks/" + tid)).status == 200
        rev = (await fixture.client.get("/v1/workspaces")).json()["revision"]
        renamed = await fixture.client.post(
            base + "/rename",
            {"command_id": "cmd_name", "expected_revision": rev, "display_name": "Second"},
        )
        assert renamed.status == 200, renamed.body
        stale = await fixture.client.post(
            base + "/rename",
            {"command_id": "cmd_stale", "expected_revision": rev, "display_name": "Stale"},
        )
        assert stale.status == 409
        removed = await fixture.client.post(
            base + "/remove", {"command_id": "cmd_remove", "expected_revision": rev + 1}
        )
        assert removed.status == 200, removed.body
        assert (tmp_path / "project-b").is_dir()
        assert (await fixture.client.get(base + "/meta")).status == 403
        reopened = await fixture.client.post(
            "/v1/workspaces",
            {"command_id": "cmd_reopen", "action": "open", "path": str(tmp_path / "project-b")},
        )
        assert reopened.json()["workspace"]["workspace_id"] == wid
        assert (await fixture.client.get(base + "/tasks/" + tid)).status == 200
    finally:
        fixture.close()


async def test_directory_auth_origin_scope_before_mutation(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        response = await fixture.client.get("/v1/directories?path=" + str(tmp_path))
        assert response.status == 200, response.body
        assert response.json()["path"] == str(tmp_path)
        response = await fixture.client.get("/v1/directories?path=/etc")
        assert response.status == 409
        response = await fixture.client.post(
            "/v1/workspaces",
            {
                "command_id": "cmd_bad",
                "action": "create",
                "path": str(tmp_path),
                "name": "forbidden",
            },
            origin="http://evil.test",
        )
        assert response.status == 403
        assert not (tmp_path / "forbidden").exists()
    finally:
        fixture.close()
