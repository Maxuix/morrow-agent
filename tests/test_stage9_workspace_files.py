from __future__ import annotations

import hashlib
from urllib.parse import quote

import pytest

from morrow.application.workspace_files import content_disposition
from test_stage8_core_api import ServerFixture


async def test_workspace_files_round_trip_conflict_and_confined_paths(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        source = fixture.workspace_dir / "main.py"
        source.write_text("print('before')\n", encoding="utf-8")
        root = f"/v1/workspaces/{fixture.workspace_id}/files"

        listing = await fixture.client.get(root + "/tree?path=.&limit=1")
        assert listing.status == 200, listing.body
        first = listing.json()
        assert first["entries"][0]["path"] == "main.py"
        assert first["next_cursor"] is None

        read = await fixture.client.get(root + "/content?path=main.py")
        assert read.status == 200, read.body
        file = read.json()["file"]
        old_hash = file["revision"]["sha256"]
        assert file["text"] == "print('before')\n"

        saved = await fixture.client.request(
            "PUT",
            root + "/content",
            body={
                "command_id": "cmd_gui_save",
                "path": "main.py",
                "content": "print('after')\n",
                "expected_sha256": old_hash,
            },
        )
        assert saved.status == 200, saved.body
        assert saved.json()["disposition"] == "accepted"
        assert saved.json()["file"]["text"] == "print('after')\n"
        assert source.read_text(encoding="utf-8") == "print('after')\n"
        assert (
            await fixture.on_core(
                lambda: fixture.host.context.workspaces.default.journal.list_artifacts(
                    fixture.workspace_id
                )
            )
            == ()
        )

        source.write_text("print('external')\n", encoding="utf-8")
        conflict = await fixture.client.request(
            "PUT",
            root + "/content",
            body={
                "command_id": "cmd_gui_stale",
                "path": "main.py",
                "content": "print('draft')\n",
                "expected_sha256": old_hash,
            },
        )
        assert conflict.status == 409, conflict.body
        assert conflict.json()["error"]["code"] == "stale"
        assert source.read_text(encoding="utf-8") == "print('external')\n"

        outside = tmp_path / "outside.py"
        outside.write_text("outside\n", encoding="utf-8")
        traversal = await fixture.client.get(root + "/content?path=../outside.py")
        assert traversal.status == 400, traversal.body
        symlink = fixture.workspace_dir / "outside-link.py"
        symlink.symlink_to(outside)
        escaped = await fixture.client.get(root + "/content?path=outside-link.py")
        assert escaped.status == 403, escaped.body
    finally:
        fixture.close()


async def test_workspace_file_tree_cursor_and_write_replay(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        for name in ("a.txt", "b.txt", "c.txt"):
            (fixture.workspace_dir / name).write_text(name, encoding="utf-8")
        root = f"/v1/workspaces/{fixture.workspace_id}/files"
        first = await fixture.client.get(root + "/tree?limit=2")
        assert first.status == 200, first.body
        page = first.json()
        assert [entry["path"] for entry in page["entries"]] == ["a.txt", "b.txt"]
        second = await fixture.client.get(root + "/tree?limit=2&after=" + page["next_cursor"])
        assert second.status == 200, second.body
        assert [entry["path"] for entry in second.json()["entries"]] == ["c.txt"]

        source = fixture.workspace_dir / "a.txt"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        body = {
            "command_id": "cmd_gui_replay",
            "path": "a.txt",
            "content": "updated",
            "expected_sha256": digest,
        }
        accepted = await fixture.client.request("PUT", root + "/content", body=body)
        replay = await fixture.client.request("PUT", root + "/content", body=body)
        assert accepted.status == 200, accepted.body
        assert replay.status == 200, replay.body
        assert replay.json()["disposition"] == "replay"
        assert source.read_text(encoding="utf-8") == "updated"
    finally:
        fixture.close()


async def test_workspace_file_info_normalizes_and_never_leaks_internal_paths(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        (fixture.workspace_dir / "docs").mkdir()
        text_file = fixture.workspace_dir / "docs" / "我的 文件.md"
        text_file.write_text("# 标题\n", encoding="utf-8")
        binary_file = fixture.workspace_dir / "logo.png"
        binary_file.write_bytes(b"\x89PNG\x00\x01")
        root = f"/v1/workspaces/{fixture.workspace_id}/files"

        relative = await fixture.client.get(
            root + "/info?path=docs/%E6%88%91%E7%9A%84%20%E6%96%87%E4%BB%B6.md"
        )
        assert relative.status == 200, relative.body
        info = relative.json()["file"]
        assert info["path"] == "docs/我的 文件.md"
        assert info["text"] is True and info["editable"] is True and info["preview"] == "text"
        assert info["byte_size"] == len(text_file.read_bytes())
        assert str(fixture.workspace_dir).encode() not in relative.body

        # An absolute path inside the workspace is relativized, not echoed back.
        absolute = await fixture.client.get(root + "/info?path=" + quote(str(text_file), safe=""))
        assert absolute.status == 200, absolute.body
        assert absolute.json()["file"]["path"] == "docs/我的 文件.md"

        binary = await fixture.client.get(root + "/info?path=logo.png")
        assert binary.status == 200, binary.body
        assert binary.json()["file"]["text"] is False
        assert binary.json()["file"]["editable"] is False
        assert binary.json()["file"]["preview"] == "image"

        outside = tmp_path / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        assert (
            await fixture.client.get(root + "/info?path=" + quote(str(outside), safe=""))
        ).status == 403
        assert (await fixture.client.get(root + "/info?path=../outside.md")).status == 400
        missing = await fixture.client.get(root + "/info?path=docs/missing.md")
        assert missing.status == 404, missing.body
    finally:
        fixture.close()


async def test_workspace_file_replace_reconciles_a_written_file_without_receipt(tmp_path):
    """故障注入：文件已写好、回执未提交时重试必须诚实，且绝不覆盖新内容。"""

    from morrow.application.workspace_files import WorkspaceFilesApplicationService
    from morrow.core.application import ApplicationError

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.py"
    source.write_text("print('before')\n", encoding="utf-8")
    service = WorkspaceFilesApplicationService(workspace, "ws_local")

    baseline = service.content("main.py")["revision"]["sha256"]
    written = service.replace(
        path="main.py",
        content="print('after')\n",
        expected_sha256=baseline,
        command_id="cmd_gui_once",
    )
    # 服务层只在接续路径上给出 disposition；正常写入由 HTTP 层标记为 accepted。
    assert written.get("disposition") is None
    assert written["file"]["text"] == "print('after')\n"
    assert source.read_text(encoding="utf-8") == "print('after')\n"

    # 回执丢失后的重试：磁盘内容与本次提交一致，报告为已生效而不是冲突。
    retried = service.replace(
        path="main.py",
        content="print('after')\n",
        expected_sha256=baseline,
        command_id="cmd_gui_retry",
    )
    assert retried["disposition"] == "reconciled"
    assert retried["mutation"] is None
    assert retried["file"]["text"] == "print('after')\n"
    assert source.read_text(encoding="utf-8") == "print('after')\n"

    # 陈旧基线 + 不同内容仍然是冲突：旧 expected hash 永远不会覆盖新内容。
    source.write_text("print('external')\n", encoding="utf-8")
    with pytest.raises(ApplicationError) as conflict:
        service.replace(
            path="main.py",
            content="print('draft')\n",
            expected_sha256=baseline,
            command_id="cmd_gui_stale",
        )
    assert conflict.value.code.value == "stale"
    assert source.read_text(encoding="utf-8") == "print('external')\n"


async def test_workspace_file_download_is_bounded_and_typed(tmp_path):
    """预览/下载字节出口：类型、处置、nosniff 与 20 MiB 上限都按约定。"""

    fixture = ServerFixture(tmp_path)
    try:
        root = f"/v1/workspaces/{fixture.workspace_id}/files"
        image = fixture.workspace_dir / "logo.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

        inline = await fixture.client.get(root + "/download?path=logo.png")
        assert inline.status == 200, inline.body
        headers = dict(inline.headers)
        assert headers["content-type"] == "image/png"
        assert headers["content-disposition"].startswith("inline;")
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"
        assert inline.body == image.read_bytes()

        attachment = await fixture.client.get(
            root + "/download?path=logo.png&disposition=attachment"
        )
        assert attachment.status == 200, attachment.body
        assert dict(attachment.headers)["content-disposition"].startswith("attachment;")

        document = fixture.workspace_dir / "我的 文件.md"
        document.write_text("# 标题\n", encoding="utf-8")
        named = await fixture.client.get(root + "/download?path=" + quote("我的 文件.md", safe=""))
        assert named.status == 200, named.body
        assert (
            dict(named.headers)["content-disposition"]
            == "inline; filename=\"download.md\"; filename*=UTF-8''%E6%88%91%E7%9A%84%20%E6%96%87%E4%BB%B6.md"
        )

        oversized = fixture.workspace_dir / "big.bin"
        oversized.write_bytes(b"0" * (20 * 1024 * 1024 + 1))
        too_large = await fixture.client.get(root + "/download?path=big.bin")
        assert too_large.status == 400, too_large.body
        assert "20 MiB" in too_large.json()["error"]["message"]

        assert (await fixture.client.get(root + "/download?path=../outside.png")).status == 400
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"nope")
        link = fixture.workspace_dir / "outside-link.png"
        link.symlink_to(outside)
        assert (await fixture.client.get(root + "/download?path=outside-link.png")).status == 403
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("filename", "fallback"),
    [
        ("report.md", "report.md"),
        ("archive.tar.gz", "archive.tar.gz"),
        ("交付 报告.md", "download.md"),
        ("交付报告", "download"),
        (".env", "download.env"),
        ('quo"te\\back.md', "quoteback.md"),
        ("bad\x01name.txt", "badname.txt"),
    ],
)
def test_content_disposition_keeps_a_usable_ascii_fallback(filename, fallback):
    """非 ASCII/特殊字符名称仍得到可用 ASCII 回退名，真名放 RFC 5987 参数。"""

    header = content_disposition("attachment", filename)
    assert header == (f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}")
