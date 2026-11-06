"""Restore bytes, preserve later work, and reconcile durable intent without replay."""

import hashlib

import pytest

from morrow.adapters.local.restore import identity
from morrow.application.workspace_restore import WorkspaceRestoreService
from morrow.core.application import ApplicationError
from morrow.core.workspace_changes import FileManifest, WorkspaceChange
from test_stage7_multi_agent_pipeline import WS, PipelineFixture


@pytest.fixture
def fx(tmp_path):
    value = PipelineFixture(tmp_path / "state")
    value.root = tmp_path / "project"
    value.root.mkdir()
    value.restore = WorkspaceRestoreService(
        value.journal, value.artifacts, value.root, WS, clock=value.clock.now
    )
    yield value
    value.close()


def record(fx, path="a.txt", before=b"user\r\n", after=b"agent\r\n", *, mode=0o755):
    def side(raw):
        if raw is None:
            return FileManifest()
        snap = fx.artifacts.publish_workspace_snapshot(
            raw, session_id="ses_root", task_run_id="task_root"
        )
        return FileManifest(
            exists=True,
            sha256=hashlib.sha256(raw).hexdigest(),
            byte_size=len(raw),
            mode=mode,
            snapshot_id=snap.artifact_id,
        )

    p = fx.root / path
    if after is not None:
        p.write_bytes(after)
        p.chmod(mode)
    row = WorkspaceChange(
        change_id=fx.ids.new_id("chg"),
        workspace_id=WS,
        root_identity=identity(fx.root),
        session_id="ses_root",
        task_run_id="task_root",
        mutation_key=fx.ids.new_id("key"),
        path=path,
        operation="create" if before is None else "replace",
        before=side(before),
        after=side(after),
        effect_state="applied",
        created_at=fx.clock.now(),
    )
    return fx.journal.workspace_changes.insert(row)


def preview(fx, rows, command="cmd_preview"):
    return fx.restore.preview(
        "ses_root",
        [r.change_id for r in rows],
        command_id=command,
        selectors={"task_run_id": "task_root"},
    )


def apply(fx, op, command="cmd_apply"):
    return fx.restore.apply(
        "ses_root", op.operation_id, preview_digest=op.preview_digest, command_id=command
    )


@pytest.mark.parametrize(
    "before,after",
    [
        (b"\xef\xbb\xbfuser\r\n", b"\xef\xbb\xbfagent\r\n"),
        (b"", b"new"),
        (b"old", b""),
        (None, b""),
    ],
)
def test_restore_and_redo_roundtrip(fx, before, after):
    row = record(fx, before=before, after=after)
    op = apply(fx, preview(fx, [row]))
    assert op.state == "completed"
    path = fx.root / "a.txt"
    assert path.read_bytes() == before if before is not None else not path.exists()
    assert (fx.root / op.items[0].capture_name).read_bytes() == after
    assert apply(fx, op) == op
    compensation = fx.journal.workspace_changes.get(WS, op.items[0].change_id)
    redo = apply(fx, preview(fx, [compensation], "cmd_redo_preview"), "cmd_redo")
    assert redo.state == "completed"
    assert path.read_bytes() == after
    assert path.stat().st_mode & 0o777 == 0o755


def test_current_user_content_and_chmod_invalidate_preview(fx):
    row = record(fx)
    op = preview(fx, [row])
    path = fx.root / "a.txt"
    path.write_bytes(b"external")
    with pytest.raises(ApplicationError):
        apply(fx, op)
    assert path.read_bytes() == b"external"
    path.write_bytes(b"agent\r\n")
    path.chmod(0o600)
    with pytest.raises(ApplicationError):
        apply(fx, op)
    assert path.stat().st_mode & 0o777 == 0o600


def test_contiguous_chain_and_gap(fx):
    a = record(fx, before=b"A", after=b"B")
    b = record(fx, before=b"B", after=b"C")
    op = apply(fx, preview(fx, [a, b]))
    assert op.state == "completed"
    assert (fx.root / "a.txt").read_bytes() == b"A"
    a = record(fx, before=b"A", after=b"B")
    b = record(fx, before=b"USER", after=b"C")
    op = preview(fx, [a, b], "cmd_gap")
    assert op.items[0].state == "conflict"
    assert apply(fx, op, "cmd_gap_apply").state == "blocked"
    assert (fx.root / "a.txt").read_bytes() == b"C"


def test_expired_preview_and_command_mismatch(fx):
    row = record(fx)
    op = preview(fx, [row])
    fx.clock.advance(301)
    with pytest.raises(ApplicationError):
        apply(fx, op)
    with pytest.raises(ApplicationError):
        fx.restore.preview("ses_root", ["chg_other"], command_id="cmd_preview")


def test_partial_failure_stops_remaining_files(fx, monkeypatch):
    rows = [record(fx, path=f"{i}.txt") for i in range(5)]
    op = preview(fx, rows)
    publish = fx.restore.publisher.publish

    def faulty(root, path, **kw):
        if path == "2.txt":
            raise OSError("injected")
        return publish(root, path, **kw)

    monkeypatch.setattr(fx.restore.publisher, "publish", faulty)
    op = apply(fx, op)
    assert op.state == "partially_applied"
    assert [i.state for i in op.items] == [
        "applied",
        "applied",
        "conflict",
        "not_executed",
        "not_executed",
    ]
    assert (fx.root / "2.txt").read_bytes() == b"agent\r\n"


def test_crash_after_publication_reconciles_without_second_write(fx, monkeypatch):
    row = record(fx)
    op = preview(fx, [row])
    save = fx.restore.records.save

    def crash(row):
        if row.origin == "restore":
            raise RuntimeError("injected crash")
        return save(row)

    monkeypatch.setattr(fx.restore.records, "save", crash)
    with pytest.raises(RuntimeError):
        apply(fx, op)
    assert (fx.root / "a.txt").read_bytes() == b"user\r\n"
    monkeypatch.setattr(fx.restore.records, "save", save)
    monkeypatch.setattr(
        fx.restore.publisher, "publish", lambda *a, **k: pytest.fail("must not replay")
    )
    result = apply(fx, op)
    assert result.state == "completed"
    assert result.items[0].state == "target_reached"


def test_scope_and_review_version_and_pagination(fx):
    a = record(fx)
    record(fx, path="b.txt")
    with pytest.raises(ApplicationError):
        fx.restore.detail("ses_other", a.change_id)
    page = fx.restore.listing("ses_root", selectors={"task_run_id": "task_root"}, limit=1)
    assert page["next_cursor"]
    fx.restore.review(
        "ses_root",
        [a.change_id],
        {a.change_id: a.row_version},
        selectors={"task_run_id": "task_root"},
    )
    with pytest.raises(ApplicationError):
        fx.restore.listing(
            "ses_root", selectors={"task_run_id": "task_root"}, limit=1, cursor=page["next_cursor"]
        )
    with pytest.raises(ApplicationError):
        fx.restore.review("ses_root", [a.change_id], {a.change_id: 1})


def test_target_already_present_does_not_invent_compensation(fx):
    row = record(fx)
    (fx.root / "a.txt").write_bytes(b"user\r\n")
    op = preview(fx, [row])
    assert op.items[0].state == "target_reached"
    result = apply(fx, op)
    assert result.state == "completed"
    assert result.items[0].change_id is None


def test_new_record_after_preview_invalidates_it(fx):
    row = record(fx)
    op = preview(fx, [row])
    record(fx, before=b"agent\r\n", after=b"other")
    (fx.root / "a.txt").write_bytes(b"agent\r\n")
    with pytest.raises(ApplicationError):
        apply(fx, op)
    assert (fx.root / "a.txt").read_bytes() == b"agent\r\n"


def test_independent_restore_switch_keeps_history_readable(fx, monkeypatch):
    row = record(fx)
    monkeypatch.setenv("MORROW_RESTORE_CHANGES", "0")
    listing = fx.restore.listing("ses_root", selectors={"task_run_id": "task_root"})
    assert listing["items"][0]["restore_reason"] == "restore_disabled"
    assert fx.restore.detail("ses_root", row.change_id)["diff"]
    with pytest.raises(ApplicationError):
        preview(fx, [row])


def test_snapshot_corruption_refuses_restore(fx):
    row = record(fx)
    metadata = fx.artifacts.get(row.before.snapshot_id)
    path = fx.artifacts.filesystem.layout.artifacts_dir / (metadata.artifact_id + ".artifact")
    path.write_bytes(b"corrupt")
    with pytest.raises(ApplicationError):
        preview(fx, [row])
    assert (fx.root / "a.txt").read_bytes() == b"agent\r\n"


def test_git_staged_and_user_unstaged_bytes_survive_restore(fx):
    import os
    import subprocess

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=fx.root,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            check=True,
            capture_output=True,
        ).stdout

    git("init", "-q")
    target = fx.root / "a.txt"
    target.write_bytes(b"staged")
    git("add", "a.txt")
    index_before = (fx.root / ".git/index").read_bytes()
    row = record(fx, before=b"staged plus user unstaged", after=b"agent changes")
    assert apply(fx, preview(fx, [row])).state == "completed"
    assert target.read_bytes() == b"staged plus user unstaged"
    assert (fx.root / ".git/index").read_bytes() == index_before
    assert git("show", ":a.txt") == b"staged"


def test_same_bytes_on_new_inode_invalidates_preview(fx):
    row = record(fx)
    op = preview(fx, [row])
    replacement = fx.root / "replacement"
    replacement.write_bytes(b"agent\r\n")
    replacement.chmod(0o755)
    replacement.replace(fx.root / "a.txt")
    with pytest.raises(ApplicationError):
        apply(fx, op)
    assert (fx.root / "a.txt").read_bytes() == b"agent\r\n"


def test_expired_snapshot_can_be_pinned_and_prepared_change_reconciled(fx):
    row = record(fx)
    fx.clock.advance(31 * 86400)
    with pytest.raises(ApplicationError):
        preview(fx, [row])
    fx.restore.pin("ses_root", [row.change_id])
    assert preview(fx, [row]).items[0].state == "pending"
    latest = fx.journal.workspace_changes.get(WS, row.change_id)
    fx.journal.workspace_changes.save(latest.model_copy(update={"effect_state": "prepared"}))
    result = fx.restore.reconcile_changes("ses_root", [row.change_id])
    assert result[0].effect_state == "applied"
    assert not any(r.origin == "restore" for r in fx.journal.workspace_changes.list(WS))


def test_review_of_prepared_record_cannot_race_capture_settlement(fx):
    row = record(fx)
    row = fx.journal.workspace_changes.save(row.model_copy(update={"effect_state": "prepared"}))
    with pytest.raises(ApplicationError):
        fx.restore.review("ses_root", [row.change_id], {row.change_id: row.row_version})
    assert fx.journal.workspace_changes.get(WS, row.change_id).row_version == row.row_version


def test_metadata_only_change_cannot_claim_pinned_snapshot(fx):
    row = record(fx)
    row = fx.journal.workspace_changes.save(
        row.model_copy(update={"coverage": "metadata_only", "omission_reason": "sensitive_content"})
    )
    with pytest.raises(ApplicationError):
        fx.restore.pin("ses_root", [row.change_id])


def hunk_preview(fx, row, indexes=(0,), *, command="cmd_hunk"):
    view = fx.restore.detail("ses_root", row.change_id, selectors={"task_run_id": "task_root"})
    ids = tuple(view["hunks"][i]["hunk_id"] for i in indexes)
    return fx.restore.preview(
        "ses_root",
        [row.change_id],
        command_id=command,
        selectors={"task_run_id": "task_root"},
        hunk_ids=ids,
    )


def bind_after_identity(fx, row):
    actual = fx.restore.reader.current(row.path)
    return fx.restore.records.save(
        row.model_copy(
            update={"after": row.after.model_copy(update={"file_identity": actual.file_identity})}
        )
    )


@pytest.mark.parametrize("drift", [False, True])
def test_select_hunk_and_preserve_other_changes_byte_for_byte(fx, drift):
    before = b"\xef\xbb\xbfhead\r\nleft\r\nold\r\nright\r\nspace\r\nold final"
    after = before.replace(b"old", b"new")
    row = bind_after_identity(fx, record(fx, before=before, after=after))
    path = fx.root / row.path
    current = b"\xef\xbb\xbfinserted\r\n" + after[3:] if drift else after
    path.write_bytes(current)
    op = hunk_preview(fx, row)
    item = op.items[0]
    assert item.state == "pending"
    assert item.expected.snapshot_id is item.desired.snapshot_id is None
    assert path.read_bytes() == current
    diff = fx.restore.operation_view(op)["items"][0]["preview_diff"]
    assert "-new" in diff and "+old" in diff
    result = apply(fx, op)
    assert result.state == "completed"
    assert path.read_bytes() == current.replace(b"new\r\n", b"old\r\n")
    assert path.stat().st_mode & 0o777 == 0o755
    assert apply(fx, op) == result
    compensation = fx.restore.records.get(WS, result.items[0].change_id)
    assert fx.restore.bytes(compensation.before) == current
    assert apply(fx, preview(fx, [compensation], "cmd_redo"), "cmd_redo_apply").state == "completed"
    assert path.read_bytes() == current


@pytest.mark.parametrize(
    "before,after,current",
    [
        (b"head\nold\ntail\nend", b"head\nnew\ntail\nend", b"head\nuser\ntail\nend"),
        (b"same\nold\nsame\nend", b"same\nnew\nsame\nend", b"same\nnew\nsame\nmore"),
        (b"head\nold\ntail\nend", b"head\ntail\nend", b"head\nuser\ntail\nend"),
    ],
)
def test_hunks_reject_overlap_and_ambiguous_anchors(fx, before, after, current):
    row = bind_after_identity(fx, record(fx, before=before, after=after))
    path = fx.root / row.path
    path.write_bytes(current)
    op = hunk_preview(fx, row)
    assert op.items[0].state == "conflict"
    assert apply(fx, op).state == "blocked"
    assert path.read_bytes() == current


def test_hunk_preview_invalidated_by_drift_or_disabled_merge(fx, monkeypatch):
    row = bind_after_identity(fx, record(fx, before=b"a\nold\nb\nc", after=b"a\nnew\nb\nc"))
    path = fx.root / row.path
    op = hunk_preview(fx, row)
    path.write_bytes(b"extra\na\nnew\nb\nc")
    assert fx.restore.operation_view(op)["items"][0]["preview_unavailable"]
    with pytest.raises(ApplicationError):
        apply(fx, op)
    monkeypatch.setenv("MORROW_RESTORE_MERGE", "0")
    blocked = hunk_preview(fx, row, command="cmd_disabled")
    assert blocked.items[0].state == "conflict"
    assert path.read_bytes() == b"extra\na\nnew\nb\nc"


def test_hunk_ids_bind_revision_and_multiple_selected_hunks(fx):
    before = b"start\nold\nseparator\nold final"
    after = b"start\nnew\nseparator\nnew final"
    first = bind_after_identity(fx, record(fx, before=before, after=after))
    op = hunk_preview(fx, first, (0, 1))
    assert apply(fx, op).state == "completed"
    assert (fx.root / first.path).read_bytes() == before
    second = bind_after_identity(fx, record(fx, before=before, after=after))
    bad = fx.restore.preview(
        "ses_root",
        [second.change_id],
        command_id="cmd_wrong",
        selectors={"task_run_id": "task_root"},
        hunk_ids=op.items[0].hunk_ids,
    )
    assert bad.items[0].state == "conflict"
