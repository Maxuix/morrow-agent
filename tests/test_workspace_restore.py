"""Restore bytes, preserve later work, and reconcile durable intent without replay."""

import hashlib

import pytest

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
