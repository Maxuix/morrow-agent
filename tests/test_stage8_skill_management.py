"""Skill HTTP operations share immutable packages, bindings and scope with CLI."""

from test_skill_lifecycle import _source
from test_stage8_core_api import ServerFixture


async def test_skill_install_preview_stale_scope_replay_and_remove(tmp_path):
    fx = ServerFixture(tmp_path)
    source = _source(tmp_path)
    path = f"/v1/workspaces/{fx.workspace_id}/skill-actions"
    try:
        body = {"action": "validate", "path": str(source), "command_id": "cmd_preview"}
        preview = await fx.client.post(path, body)
        assert preview.status == 200, preview.body
        report = preview.json()
        assert report["valid"] and report["file_count"] == 2
        install = {
            **body,
            "action": "install",
            "command_id": "cmd_install",
            "confirmed": True,
            "expected_tree_digest": report["tree_digest"],
        }
        manifest = source / "SKILL.md"
        original = manifest.read_text()
        manifest.write_text(original + "\nChanged after preview\n")
        assert (await fx.client.post(path, install)).status == 409
        manifest.write_text(original)
        first = await fx.client.post(path, install)
        assert first.status == 200, first.body
        assert (await fx.client.post(path, install)).status == 200
        result = first.json()["result"]
        identity = result["skill_id"]
        query = f"/v1/skill-management/catalog?identity={identity}"
        local = (await fx.client.get(query)).json()
        assert local["skills"][0]["enabled"] is False
        global_view = (await fx.client.get("/v1/skill-management/catalog?scope=global")).json()
        assert not any(v["status"]["skill_id"] == identity for v in global_view["skills"])
        assert (
            await fx.client.get("/v1/workspaces/ws_other/skill-management/catalog")
        ).status == 403
        assert (await fx.client.post(path, {**install, "source": "generated"})).status == 409
        enabled = await fx.client.post(
            "/v1/management/skill-binding/" + identity,
            {
                "action": "enable",
                "command_id": "cmd_enable",
                "expected_digest": local["binding_digest"],
            },
        )
        assert enabled.status == 200, enabled.body
        bound = (await fx.client.get(query)).json()
        remove = {
            "action": "remove",
            "command_id": "cmd_remove_binding",
            "skill_id": identity,
            "source": "imported",
            "expected_binding_digest": bound["binding_digest"],
            "confirmed": True,
        }
        assert (
            await fx.client.post(path, {**remove, "expected_binding_digest": "0" * 64})
        ).status == 409
        assert (await fx.client.post(path, remove)).status == 200
        assert (await fx.client.post(path, remove)).status == 200
        unbound = (await fx.client.get(query)).json()
        assert not unbound["skills"][0]["enabled"]
        delete = {
            **remove,
            "command_id": "cmd_remove_version",
            "version_id": result["version_id"],
            "expected_binding_digest": unbound["binding_digest"],
        }
        deleted = await fx.client.post(path, delete)
        assert deleted.status == 200, deleted.body
        assert (await fx.client.post(path, delete)).status == 200
        from morrow.core.domain import sha256_digest
        from morrow.core.skills.trust import SourceKind

        replay = await fx.on_core(
            lambda: fx.host.context.context_management.skills.lifecycle.remove(
                identity,
                scope_id=fx.workspace_id,
                version_id=result["version_id"],
                source_kind=SourceKind.IMPORTED,
                confirmed=True,
                command_id="cmd_" + sha256_digest("cmd_remove_version:skill")[:48],
            )
        )
        assert replay.status == "replayed"
        assert (await fx.client.get("/v1/skill-management/usage?identity=" + identity)).json()[
            "items"
        ] == []
    finally:
        fx.close()


async def test_skill_preview_is_bounded_and_cannot_install_generated_or_symlinks(tmp_path):
    fx = ServerFixture(tmp_path)
    try:
        source = _source(tmp_path)
        body = {
            "action": "validate",
            "command_id": "cmd_generated",
            "source": "generated",
            "path": str(source),
        }
        preview = await fx.client.post("/v1/skill-actions", body)
        assert preview.status == 200, preview.body
        installed = await fx.client.post(
            "/v1/skill-actions",
            {
                **body,
                "action": "install",
                "command_id": "cmd_generated_install",
                "confirmed": True,
                "expected_tree_digest": preview.json()["tree_digest"],
            },
        )
        assert installed.status == 400
        manifest = source / "SKILL.md"
        original = tmp_path / "external.md"
        manifest.rename(original)
        manifest.symlink_to(original)
        invalid = await fx.client.post("/v1/skill-actions", {**body, "source": "imported"})
        assert invalid.status == 400
        assert str(original) not in invalid.body.decode()
        assert (await fx.client.get("/v1/skill-management/catalog?page=-1")).status == 400
    finally:
        fx.close()
