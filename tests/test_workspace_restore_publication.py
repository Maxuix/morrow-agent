import os

import pytest

from morrow.adapters.local.filesystem import FileSystemMutationError
from morrow.adapters.local.restore import RestorePublisher, identity


def publish(tmp_path, publisher=None, **kw):
    return (publisher or RestorePublisher()).publish(
        tmp_path,
        "a.txt",
        expected=b"agent",
        expected_mode=0o644,
        desired=kw.pop("desired", b"user"),
        desired_mode=0o644,
        capture_name=".morrow-restore-capture",
        temporary_name=".morrow-restore-new",
        root_identity=identity(tmp_path),
        parent_identity=identity(tmp_path),
        **kw,
    )


@pytest.mark.skipif(not RestorePublisher.supported(), reason="no no-clobber primitive")
def test_preserves_original_and_open_editor_descriptor(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"agent")
    target.chmod(0o644)
    fd = os.open(target, os.O_WRONLY)
    try:
        publish(tmp_path)
        os.write(fd, b"later")
        assert target.read_bytes() == b"user"
        assert (tmp_path / ".morrow-restore-capture").read_bytes() == b"later"
    finally:
        os.close(fd)


def test_target_claim_after_capture_never_overwrites(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_bytes(b"agent")
    target.chmod(0o644)
    publisher = RestorePublisher()
    rename = publisher._rename_no_replace

    def race(src, source, dst, destination):
        if destination == "a.txt":
            target.write_bytes(b"external")
        rename(src, source, dst, destination)

    monkeypatch.setattr(publisher, "_rename_no_replace", race)
    with pytest.raises(FileSystemMutationError) as error:
        publish(tmp_path, publisher)
    assert error.value.effect_applied
    assert target.read_bytes() == b"external"
    assert (tmp_path / ".morrow-restore-capture").read_bytes() == b"agent"


def test_create_undo_retains_file_and_does_not_remove_parent(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"agent")
    target.chmod(0o644)
    publish(tmp_path, desired=None)
    assert not target.exists()
    assert (tmp_path / ".morrow-restore-capture").read_bytes() == b"agent"


def test_hardlink_refused(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"agent")
    target.chmod(0o644)
    os.link(target, tmp_path / "other")
    with pytest.raises(FileSystemMutationError):
        publish(tmp_path)
    assert target.read_bytes() == b"agent"


def test_parent_replacement_after_capture_keeps_external_tree(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    parent = root / "sub"
    parent.mkdir()
    target = parent / "a.txt"
    target.write_bytes(b"agent")
    target.chmod(0o644)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"external")
    publisher = RestorePublisher()
    sync = publisher._fsync_required
    moved = root / "moved"

    def swap(fd):
        sync(fd)
        if parent.is_dir() and not parent.is_symlink():
            parent.rename(moved)
            parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(publisher, "_fsync_required", swap)
    with pytest.raises(FileSystemMutationError) as error:
        publisher.publish(
            root,
            "sub/a.txt",
            expected=b"agent",
            expected_mode=0o644,
            desired=b"user",
            desired_mode=0o644,
            capture_name=".morrow-restore-capture",
            temporary_name=".morrow-restore-new",
            root_identity=identity(root),
            parent_identity=identity(parent),
        )
    assert error.value.effect_applied
    assert (outside / "a.txt").read_bytes() == b"external"
    assert (moved / ".morrow-restore-capture").read_bytes() == b"agent"


def test_external_rewrite_before_capture_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "a.txt"
    path.write_bytes(b"agent")
    path.chmod(0o644)
    publisher = RestorePublisher()
    rename = publisher._rename_no_replace

    def rewrite(src, source, dst, destination):
        if destination == ".morrow-restore-capture":
            path.write_bytes(b"external")
        rename(src, source, dst, destination)

    monkeypatch.setattr(publisher, "_rename_no_replace", rewrite)
    with pytest.raises(FileSystemMutationError):
        publish(tmp_path, publisher)
    assert path.read_bytes() == b"external"


def test_cancelled_but_live_host_process_stays_busy(monkeypatch):
    from types import SimpleNamespace

    from morrow.core.local_tools import TrackedCommandStatus
    from morrow.services.tracked_process import TrackedProcessRegistry

    registry = TrackedProcessRegistry()
    registry._items["one"] = SimpleNamespace(
        status=TrackedCommandStatus.CANCELLED,
        spawned=SimpleNamespace(process=SimpleNamespace(pid=123, returncode=None)),
        stop_requested=True,
    )
    monkeypatch.setattr("morrow.services.tracked_process._group_alive", lambda pid: True)
    assert registry.has_running()
    monkeypatch.setattr("morrow.services.tracked_process._group_alive", lambda pid: False)
    assert not registry.has_running()
