from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from morrow.adapters.local.search import LocalSearchAdapter
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


@pytest.mark.parametrize("literal", [False, True])
def test_rg_receives_the_pattern_before_the_option_terminator(tmp_path, monkeypatch, literal):
    pattern = "-needle" if literal else "needle"
    (tmp_path / "a.txt").write_text(pattern + "\n", encoding="utf-8")
    observed = {}

    def run(argv, **kwargs):
        observed["argv"] = argv
        assert kwargs["cwd"] == tmp_path
        event = {
            "type": "match",
            "data": {
                "path": {"text": "a.txt"},
                "line_number": 1,
                "lines": {"text": pattern + "\n"},
                "submatches": [{"start": 0}],
            },
        }
        return subprocess.CompletedProcess(
            argv, 0, stdout=(json.dumps(event) + "\n").encode(), stderr=b""
        )

    monkeypatch.setattr("morrow.adapters.local.search.subprocess.run", run)
    result = _search(tmp_path, rg_path="/fake/rg").search_text(
        ".", query=SearchQuery(pattern=pattern, literal=literal)
    )

    argv = observed["argv"]
    pattern_index = argv.index("--regexp")
    assert argv[pattern_index + 1] == pattern
    assert argv[pattern_index + 2 :] == ["--", "."]
    assert ("--fixed-strings" in argv) is literal
    assert result.engine.value == "rg"
    assert result.matches[0].path == "a.txt"


def test_search_fallback_skips_ignored_and_binary_but_searches_keyword_files(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("needle visible", encoding="utf-8")
    (tmp_path / ".env").write_text("needle secret", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"needle\x00binary")
    service = _search(tmp_path, rg_path="/does/not/exist")

    result = service.search_text(".", query=SearchQuery(pattern="needle"))
    assert [match.path for match in result.matches] == [".env", "visible.txt"]
    assert "ignored.txt" not in [match.path for match in result.matches]
    assert result.protected_paths == ()
    assert "needle secret" in str(result.model_dump())


def test_search_includes_internal_symlink_targets_and_explicit_git_root(tmp_path):
    (tmp_path / ".env").write_text("needle alias-secret", encoding="utf-8")
    (tmp_path / "visible.txt").symlink_to(tmp_path / ".env")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        "needle https://user:token@example.invalid/repo", encoding="utf-8"
    )
    service = _search(tmp_path, rg_path="/does/not/exist")

    root = service.search_text(".", query=SearchQuery(pattern="needle"))
    git_root = service.search_text(".git", query=SearchQuery(pattern="needle"))

    assert {match.path for match in root.matches} >= {".env", "visible.txt"}
    assert [match.path for match in git_root.matches] == [".git/config"]
    assert root.protected_paths == ()
    assert git_root.protected_paths == ()


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
