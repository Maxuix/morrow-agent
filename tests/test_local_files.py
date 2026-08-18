from __future__ import annotations

import os

import pytest

from morrow.core.capabilities import DefaultSensitiveResourcePolicy
from morrow.core.local_tools import LocalFileKind
from morrow.services.files import LocalFileError, WorkspaceFileService, WorkspacePathResolver


def _files(tmp_path):
    return WorkspaceFileService(WorkspacePathResolver(tmp_path))


def test_path_resolver_rejects_escape_forms_and_special_targets(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("ok\n", encoding="utf-8")
    os.mkfifo(tmp_path / "fifo")
    files = _files(tmp_path)

    for value in ("../outside", "/tmp/outside", "~/secret", "C:/outside", "src\\main.py"):
        with pytest.raises(LocalFileError) as error:
            files.preflight_file(value)
        assert error.value.code == "invalid_path"
    with pytest.raises(LocalFileError) as error:
        files.preflight_file("fifo")
    assert error.value.code == "invalid_target"


def test_external_symlink_is_rejected_and_internal_file_symlink_is_readable(tmp_path):
    outside = tmp_path.parent / "morrow-outside-sentinel.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    internal = tmp_path / "inside.txt"
    internal.write_text("inside\n", encoding="utf-8")
    (tmp_path / "inside-link.txt").symlink_to(internal)
    (tmp_path / "outside-link.txt").symlink_to(outside)
    files = _files(tmp_path)

    assert files.read_file("inside-link.txt").text == "inside\n"
    with pytest.raises(LocalFileError) as error:
        files.preflight_file("outside-link.txt")
    assert error.value.code == "outside_workspace"
    assert outside.read_text(encoding="utf-8") == "outside-secret"


def test_protected_symlink_target_and_git_metadata_are_metadata_only(tmp_path):
    protected = tmp_path / ".env"
    protected.write_text("TOKEN=do-not-disclose", encoding="utf-8")
    (tmp_path / "visible.txt").symlink_to(protected)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("url=https://user:token@example.invalid/repo", encoding="utf-8")
    files = _files(tmp_path)

    alias = files.read_file("visible.txt")
    git_config = files.read_file(".git/config")
    listing = files.list_directory(".git")
    found = files.find_files(".", pattern="*.txt")

    assert alias.protected is True and alias.text == ""
    assert git_config.protected is True and git_config.text == ""
    assert all(entry.protected for entry in listing.entries)
    assert found.paths == ()
    assert {item.path for item in found.protected_paths} == {"visible.txt"}
    rendered = str((alias.model_dump(), git_config.model_dump(), listing.model_dump()))
    assert "do-not-disclose" not in rendered
    assert "user:token" not in rendered


def test_mutation_resolution_reports_missing_paths_without_following_symlinks(tmp_path):
    files = _files(tmp_path)
    assert files.resolver.resolve_mutation("new/file.txt").kind == "missing"
    outside = tmp_path.parent / "mutation-resolution-outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LocalFileError) as error:
        files.resolver.resolve_mutation("link/file.txt")
    assert error.value.code == "symlink_not_allowed"


def test_directory_listing_is_stable_and_does_not_traverse_directory_symlinks(tmp_path):
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "A.txt").write_text("a", encoding="utf-8")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "nested.txt").write_text("nested", encoding="utf-8")
    (tmp_path / "hidden-link").symlink_to(hidden, target_is_directory=True)
    entries = _files(tmp_path).list_directory(".", depth=3).entries

    assert [entry.path for entry in entries] == [
        ".hidden",
        ".hidden/nested.txt",
        "A.txt",
        "hidden-link",
        "z.txt",
    ]
    assert all(
        entry.kind is not LocalFileKind.DIRECTORY or entry.path != "hidden-link"
        for entry in entries
    )


def test_read_file_reports_revision_newline_and_actionable_continuation(tmp_path):
    (tmp_path / "sample.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    files = _files(tmp_path)

    first = files.read_file("sample.txt", start_line=1, line_count=2)
    second = files.read_file("sample.txt", start_line=first.next_start_line or 1, line_count=2)

    assert first.text == "one\ntwo\n"
    assert first.end_line == 2
    assert first.next_start_line == 3
    assert first.newline.value == "lf"
    assert len(first.revision.sha256) == 64
    assert second.text == "three\n"
    assert second.truncated is False

    past_eof = files.read_file("sample.txt", start_line=4, line_count=2)
    assert past_eof.text == ""
    assert past_eof.end_line == 3
    assert past_eof.total_lines == 3
    assert past_eof.truncated is False
    assert past_eof.next_start_line is None


def test_read_file_allows_an_empty_mid_file_window_when_result_budget_trims_every_line(
    tmp_path,
):
    (tmp_path / "long.txt").write_text(
        "first\n" + ("x" * 1_000) + "\nlast\n",
        encoding="utf-8",
    )

    result = _files(tmp_path).read_file(
        "long.txt",
        start_line=2,
        line_count=1,
        result_limit=400,
    )

    assert result.text == ""
    assert result.start_line == 2
    assert result.end_line == 1
    assert result.truncated is True
    assert result.next_start_line == 2


@pytest.mark.parametrize("raw", [b"\x00binary", b"\xff\xfeinvalid"])
def test_read_rejects_binary_and_invalid_utf8_without_content(tmp_path, raw):
    (tmp_path / "bad.bin").write_bytes(raw)
    with pytest.raises(LocalFileError) as error:
        _files(tmp_path).read_file("bad.bin")
    assert error.value.code in {"binary_file", "invalid_utf8"}
    assert "invalid" not in error.value.message.lower() or error.value.code == "invalid_utf8"


def test_protected_paths_and_magic_headers_return_metadata_only(tmp_path):
    (tmp_path / ".env").write_text("TOKEN=do-not-disclose", encoding="utf-8")
    (tmp_path / "ordinary.txt").write_text(
        "-----BEGIN PRIVATE KEY-----\nsecret\n", encoding="utf-8"
    )
    files = _files(tmp_path)

    path_result = files.read_file(".env")
    magic_result = files.read_file("ordinary.txt")
    assert path_result.protected is True and path_result.text == ""
    assert magic_result.protected is True and magic_result.text == ""
    assert "do-not-disclose" not in str(path_result.model_dump())
    assert DefaultSensitiveResourcePolicy().is_protected_path(".env.example") is False


def test_oversized_source_is_rejected_before_content_is_returned(tmp_path):
    path = tmp_path / "large.txt"
    with path.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(LocalFileError) as error:
        _files(tmp_path).read_file("large.txt")
    assert error.value.code == "file_too_large"


def test_find_files_uses_stable_paths_and_does_not_leak_external_symlinks(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")
    outside = tmp_path.parent / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "src" / "outside.py").symlink_to(outside)
    result = _files(tmp_path).find_files(".", pattern="*.py")

    assert result.paths == ("src/main.py",)
    assert "outside.py" not in str(result.model_dump())
