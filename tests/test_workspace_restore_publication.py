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
