from __future__ import annotations

import json
import os
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


class _FakeRgPopen:
    """Pipe-backed Popen stand-in: select/read1/wait all behave like a real process."""

    def __init__(self, argv, *, payload: bytes, returncode: int = 0, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self._returncode = returncode
        self.returncode = None
        self.terminated = False
        self.killed = False
        read_fd, write_fd = os.pipe()
        os.write(write_fd, payload)
        os.close(write_fd)
        self.stdout = os.fdopen(read_fd, "rb")

    def wait(self, timeout=None):
        self.returncode = self._returncode
        return self._returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _rg_match_event(path: str, line_number: int, text: str) -> dict:
    return {
        "type": "match",
        "data": {
            "path": {"text": path},
            "line_number": line_number,
            "lines": {"text": text + "\n"},
            "submatches": [{"start": 0}],
        },
    }


def _rg_context_event(path: str, line_number: int, text: str) -> dict:
    return {
        "type": "context",
        "data": {"path": {"text": path}, "line_number": line_number, "lines": {"text": text}},
    }


@pytest.mark.parametrize("literal", [False, True])
def test_rg_receives_the_pattern_before_the_option_terminator(tmp_path, monkeypatch, literal):
    pattern = "-needle" if literal else "needle"
    (tmp_path / "a.txt").write_text(pattern + "\n", encoding="utf-8")
    observed = {}

    def popen(argv, **kwargs):
        observed["argv"] = argv
        assert kwargs["cwd"] == tmp_path
        payload = (json.dumps(_rg_match_event("a.txt", 1, pattern)) + "\n").encode()
        return _FakeRgPopen(argv, payload=payload, **kwargs)

    monkeypatch.setattr("morrow.adapters.local.search.subprocess.Popen", popen)
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


def test_rg_stream_stops_at_match_budget_and_terminates_process(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    events = [_rg_match_event("a.txt", number, "needle") for number in range(1, 31)]
    payload = ("".join(json.dumps(event) + "\n" for event in events)).encode()
    created = {}

    def popen(argv, **kwargs):
        proc = _FakeRgPopen(argv, payload=payload, returncode=0, **kwargs)
        created["proc"] = proc
        return proc

    monkeypatch.setattr("morrow.adapters.local.search.subprocess.Popen", popen)
    result = _search(tmp_path, rg_path="/fake/rg").search_text(
        ".", query=SearchQuery(pattern="needle", max_results=5)
    )

    assert len(result.matches) == 5
    assert result.truncated is True
    assert created["proc"].terminated is True


def test_rg_stream_keeps_trailing_after_context_at_budget(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    events = [
        _rg_context_event("a.txt", 1, "before"),
        _rg_match_event("a.txt", 2, "needle"),
        _rg_context_event("a.txt", 3, "after-one"),
        _rg_context_event("a.txt", 4, "after-two"),
        # Everything below the budget must not be consumed.
        _rg_match_event("a.txt", 5, "needle"),
        _rg_match_event("a.txt", 6, "needle"),
    ]
    payload = ("".join(json.dumps(event) + "\n" for event in events)).encode()

    def popen(argv, **kwargs):
        return _FakeRgPopen(argv, payload=payload, returncode=0, **kwargs)

    monkeypatch.setattr("morrow.adapters.local.search.subprocess.Popen", popen)
    result = _search(tmp_path, rg_path="/fake/rg").search_text(
        ".", query=SearchQuery(pattern="needle", max_results=1, context_lines=2)
    )

    assert len(result.matches) == 1
    assert result.matches[0].before == ("before",)
    assert result.matches[0].after == ("after-one", "after-two")
    assert result.truncated is True


def test_rg_completed_scan_reports_failure_returncode(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    observed = {}

    def popen(argv, **kwargs):
        proc = _FakeRgPopen(argv, payload=b"", returncode=2, **kwargs)
        observed["proc"] = proc
        return proc

    monkeypatch.setattr("morrow.adapters.local.search.subprocess.Popen", popen)
    with pytest.raises(LocalFileError) as error:
        _search(tmp_path, rg_path="/fake/rg").search_text(".", query=SearchQuery(pattern="needle"))
    assert error.value.code == "search_failed"
    assert observed["proc"].terminated is False


def test_rg_real_binary_matches_python_engine_results(tmp_path):
    if shutil_which_rg() is None:
        pytest.skip("rg is not installed")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("before\nNeedle here\nafter\n", encoding="utf-8")
    query = SearchQuery(pattern="needle", literal=True, case=SearchCase.INSENSITIVE)

    rg_result = _search(tmp_path).search_text(".", query=query, max_line_chars=200)
    py_result = _search(tmp_path, rg_path="/does/not/exist").search_text(
        ".", query=query, max_line_chars=200
    )

    assert rg_result.engine.value == "rg"
    assert [match.model_dump() for match in rg_result.matches] == [
        match.model_dump() for match in py_result.matches
    ]


def shutil_which_rg() -> str | None:
    import shutil

    return shutil.which("rg")


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


@pytest.mark.parametrize(
    "limit, reason", [("entries", "max_entries"), ("depth", "max_depth"), ("time", "timeout")]
)
def test_python_search_bounds_empty_directory_traversal(tmp_path, monkeypatch, limit, reason):
    from morrow.adapters.local import search

    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "d").mkdir()
    adapter = LocalSearchAdapter(rg_path="/does/not/exist")
    if limit == "entries":
        monkeypatch.setattr(search, "PYTHON_MAX_ENTRIES", 2)
    elif limit == "depth":
        monkeypatch.setattr(search, "PYTHON_MAX_DEPTH", 1)
    else:
        ticks = iter(range(100))
        adapter.monotonic = lambda: next(ticks)
        monkeypatch.setattr(search, "SEARCH_TIMEOUT_SECONDS", 2)
    result = adapter.search(
        workspace_root=tmp_path,
        search_root=tmp_path,
        relative_root=".",
        query=SearchQuery(pattern="needle"),
    )
    assert result.truncated
    assert result.budget_reason == reason


def test_python_search_bounds_read_when_file_grows_after_stat(tmp_path, monkeypatch):
    import io

    from morrow.adapters.local import search

    (tmp_path / "growing.txt").write_bytes(b"x")
    sizes = []

    class GrowingFile(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)

    original = Path.open

    def open_file(path, *args, **kwargs):
        if path.name == "growing.txt":
            return GrowingFile(b"x" * 100)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(search, "PYTHON_MAX_BYTES", 4)
    result = _search(tmp_path, rg_path="/does/not/exist").search_text(
        ".", query=SearchQuery(pattern="needle")
    )
    assert result.truncated
    assert result.budget_reason == "max_bytes"
    assert sizes == [5]
