from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.local.search import LocalSearchAdapter
from morrow.core.capabilities import DefaultSensitiveResourcePolicy
from morrow.core.local_tools import SearchCase, SearchQuery
from morrow.services.files import LocalFileError, WorkspaceFileService, WorkspacePathResolver
from morrow.services.search import WorkspaceSearchService


def _search(tmp_path: Path, *, rg_path: str | None = None):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    return WorkspaceSearchService(files, adapter=LocalSearchAdapter(rg_path=rg_path))


def test_search_literal_case_glob_context_and_empty_result(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("before\nNeedle here\nafter\n", encoding="utf-8")
    service = _search(tmp_path, rg_path="/does/not/exist")

    result = service.search_text(
        ".",
        query=SearchQuery(
            pattern="needle",
            literal=True,
            case=SearchCase.INSENSITIVE,
            glob="*.py",
            context_lines=1,
        ),
    )
    empty = service.search_text(".", query=SearchQuery(pattern="not-present"))

    assert result.engine.value == "python"
    assert result.matches[0].path == "src/main.py"
    assert result.matches[0].line == 2
    assert result.matches[0].before == ("before",)
    assert result.matches[0].after == ("after",)
    assert empty.matches == ()
    assert empty.truncated is False


def test_search_regex_smart_case_and_invalid_pattern(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nALPHA\n", encoding="utf-8")
    service = _search(tmp_path, rg_path="/does/not/exist")

    insensitive = service.search_text(".", query=SearchQuery(pattern="alpha"))
    sensitive = service.search_text(
        ".", query=SearchQuery(pattern="Alpha", literal=False, case=SearchCase.SMART)
    )
    assert len(insensitive.matches) == 2
    assert len(sensitive.matches) == 0
    with pytest.raises(LocalFileError) as error:
        service.search_text(".", query=SearchQuery(pattern="[", literal=False))
    assert error.value.code == "invalid_pattern"


def test_search_fallback_skips_ignored_binary_and_protected_content(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("needle visible", encoding="utf-8")
    (tmp_path / ".env").write_text("needle secret", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"needle\x00binary")
    service = _search(tmp_path, rg_path="/does/not/exist")

    result = service.search_text(".", query=SearchQuery(pattern="needle"))
    assert [match.path for match in result.matches] == ["visible.txt"]
    assert "ignored.txt" not in [match.path for match in result.matches]
    assert any(item.path == ".env" for item in result.protected_paths)
    assert "needle secret" not in str(result.model_dump())


def test_search_blocks_protected_symlink_targets_and_explicit_git_root(tmp_path):
    (tmp_path / ".env").write_text("needle alias-secret", encoding="utf-8")
    (tmp_path / "visible.txt").symlink_to(tmp_path / ".env")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        "needle https://user:token@example.invalid/repo", encoding="utf-8"
    )
    service = _search(tmp_path, rg_path="/does/not/exist")

    root = service.search_text(".", query=SearchQuery(pattern="needle"))
    git_root = service.search_text(".git", query=SearchQuery(pattern="needle"))

    assert root.matches == ()
    assert {item.path for item in root.protected_paths} >= {".env", "visible.txt"}
    assert git_root.matches == ()
    assert {item.path for item in git_root.protected_paths} == {".git/config"}
    rendered = str((root.model_dump(), git_root.model_dump()))
    assert "alias-secret" not in rendered
    assert "user:token" not in rendered


def test_search_result_budget_is_semantic_and_bounded(tmp_path):
    (tmp_path / "a.txt").write_text("needle\n" * 50, encoding="utf-8")
    service = _search(tmp_path, rg_path="/does/not/exist")
    result = service.search_text(
        ".", query=SearchQuery(pattern="needle", max_results=100), result_limit=500
    )

    assert len(str(result.model_dump())) > 0
    assert result.truncated is True
    assert result.budget_reason == "result_budget"
    assert len(result.model_dump_json()) <= 500


def test_sensitive_policy_is_frozen_and_local_only():
    policy = DefaultSensitiveResourcePolicy()
    for marker in (
        b"-----BEGIN PRIVATE KEY-----",
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"-----BEGIN EC PRIVATE KEY-----",
        b"-----BEGIN DSA PRIVATE KEY-----",
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    ):
        assert policy.is_protected_content(marker)
    with pytest.raises((TypeError, AttributeError)):
        policy.protected_suffixes = (".secret",)
