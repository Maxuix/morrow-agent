"""Installed-rg and deterministic stdlib search adapters."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from morrow.core.capabilities import SensitiveResourcePolicy
from morrow.core.local_tools import (
    ProtectedPath,
    SearchCase,
    SearchEngine,
    SearchMatch,
    SearchQuery,
)

SEARCH_TIMEOUT_SECONDS = 10.0
PYTHON_MAX_FILES = 10_000
PYTHON_MAX_BYTES = 32 * 1024 * 1024
MAX_SNIPPET_CHARS = 512


class SearchAdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SearchScan:
    engine: SearchEngine
    matches: tuple[SearchMatch, ...]
    protected_paths: tuple[ProtectedPath, ...] = ()
    truncated: bool = False
    budget_reason: str | None = None


class LocalSearchAdapter:
    """Run only fixed-argv local searches; never downloads or invokes a shell."""

    def __init__(self, *, rg_path: str | None = None, monotonic=None) -> None:
        self.rg_path = rg_path
        self.monotonic = monotonic or time.monotonic

    def search(
        self,
        *,
        workspace_root: Path,
        search_root: Path,
        relative_root: str,
        query: SearchQuery,
        sensitive_policy: SensitiveResourcePolicy,
    ) -> SearchScan:
        effective_case = _effective_case(query.case, query.pattern)
        if not query.literal:
            try:
                re.compile(query.pattern, _regex_flags(effective_case))
            except re.error as exc:
                raise SearchAdapterError("invalid_pattern", "正则表达式无效") from exc
        if query.glob is not None:
            _validate_glob(query.glob)
        rg_path = self.rg_path or shutil.which("rg")
        if rg_path:
            try:
                return self._search_rg(
                    workspace_root=workspace_root,
                    relative_root=relative_root,
                    query=query,
                    sensitive_policy=sensitive_policy,
                    rg_path=rg_path,
                )
            except SearchAdapterError as exc:
                if exc.code != "rg_unavailable":
                    raise
        return self._search_python(
            workspace_root=workspace_root,
            search_root=search_root,
            relative_root=relative_root,
            query=query,
            sensitive_policy=sensitive_policy,
        )

    def _search_rg(
        self,
        *,
        workspace_root: Path,
        relative_root: str,
        query: SearchQuery,
        sensitive_policy: SensitiveResourcePolicy,
        rg_path: str,
    ) -> SearchScan:
        argv = [
            rg_path,
            "--json",
            "--hidden",
            "--no-config",
            "--color=never",
            "--glob",
            "!**/.git/**",
            "--glob",
            "!**/.morrow/**",
            "--glob",
            "!**/.venv/**",
            "--glob",
            "!**/node_modules/**",
            "--glob",
            "!**/{build,dist}/**",
        ]
        argv.append("--fixed-strings" if query.literal else "--regexp")
        if query.case is SearchCase.INSENSITIVE:
            argv.append("--ignore-case")
        elif query.case is SearchCase.SENSITIVE:
            argv.append("--case-sensitive")
        else:
            argv.append("--smart-case")
        if query.glob:
            argv.extend(("--glob", query.glob))
        if query.context_lines:
            argv.extend(("--context", str(query.context_lines)))
        argv.extend(("--", query.pattern, relative_root))
        env = {"PATH": str(Path(rg_path).parent), "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace_root,
                env=env,
                shell=False,
                capture_output=True,
                timeout=SEARCH_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SearchAdapterError("rg_unavailable", "rg 不可用") from exc
        except subprocess.TimeoutExpired as exc:
            raise SearchAdapterError("rg_timeout", "搜索超过本地时间上限") from exc
        except OSError as exc:
            raise SearchAdapterError("search_failed", "本地搜索启动失败") from exc
        if completed.returncode not in (0, 1):
            raise SearchAdapterError("search_failed", "本地搜索失败")
        return self._parse_rg_output(
            completed.stdout,
            query=query,
            workspace_root=workspace_root,
            relative_root=relative_root,
            sensitive_policy=sensitive_policy,
        )

    def _parse_rg_output(
        self,
        output: bytes,
        *,
        query: SearchQuery,
        workspace_root: Path,
        relative_root: str,
        sensitive_policy: SensitiveResourcePolicy,
    ) -> SearchScan:
        contexts: dict[tuple[str, int], str] = {}
        match_records: list[tuple[str, int, int, str]] = []
        protected: dict[str, ProtectedPath] = {}
        protected_content: dict[str, bool] = {}
        for raw_line in output.splitlines():
            try:
                event = json.loads(raw_line.decode("utf-8", errors="replace"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if event.get("type") not in {"match", "context"}:
                continue
            data = event.get("data") or {}
            path_data = data.get("path") or {}
            path_text = path_data.get("text")
            line_number = data.get("line_number")
            lines = data.get("lines") or {}
            if not isinstance(path_text, str) or not isinstance(line_number, int):
                continue
            relative = _normalize_rg_path(path_text, relative_root)
            snippet = str(lines.get("text", "")).rstrip("\r\n")
            path_protected = sensitive_policy.is_protected_path(relative)
            if not path_protected and relative not in protected_content:
                protected_content[relative] = _path_has_protected_content(
                    workspace_root, relative, sensitive_policy
                )
            if (
                path_protected
                or protected_content.get(relative, False)
                or sensitive_policy.is_protected_content(snippet.encode("utf-8", errors="ignore"))
            ):
                protected[relative] = ProtectedPath(path=relative)
                continue
            contexts[(relative, line_number)] = snippet[:MAX_SNIPPET_CHARS]
            if event.get("type") != "match":
                continue
            submatches = data.get("submatches") or []
            column = int(submatches[0].get("start", 0)) + 1 if submatches else 1
            match_records.append(
                (relative, line_number, column, snippet[:MAX_SNIPPET_CHARS] or " ")
            )
        matches: list[SearchMatch] = []
        for relative, line_number, column, snippet in match_records:
            if relative in protected:
                continue
            before = tuple(
                contexts[(relative, number)]
                for number in range(max(1, line_number - query.context_lines), line_number)
                if (relative, number) in contexts
            )
            after = tuple(
                contexts[(relative, number)]
                for number in range(line_number + 1, line_number + query.context_lines + 1)
                if (relative, number) in contexts
            )
            matches.append(
                SearchMatch(
                    path=relative,
                    line=line_number,
                    column=column,
                    snippet=snippet[:MAX_SNIPPET_CHARS] or " ",
                    before=before,
                    after=after,
                )
            )
            if len(matches) >= query.max_results:
                break
        return SearchScan(
            engine=SearchEngine.RG,
            matches=tuple(matches),
            protected_paths=tuple(sorted(protected.values(), key=lambda item: item.path)),
            truncated=len(matches) >= query.max_results,
            budget_reason="max_matches" if len(matches) >= query.max_results else None,
        )

    def _search_python(
        self,
        *,
        workspace_root: Path,
        search_root: Path,
        relative_root: str,
        query: SearchQuery,
        sensitive_policy: SensitiveResourcePolicy,
    ) -> SearchScan:
        effective_case = _effective_case(query.case, query.pattern)
        regex = None if query.literal else re.compile(query.pattern, _regex_flags(effective_case))
        needle = (
            query.pattern if effective_case is SearchCase.SENSITIVE else query.pattern.casefold()
        )
        matches: list[SearchMatch] = []
        protected: dict[str, ProtectedPath] = {}
        stack = [search_root]
        scanned_files = 0
        scanned_bytes = 0
        deadline = self.monotonic() + SEARCH_TIMEOUT_SECONDS
        truncated = False
        reason = None
        while stack:
            if self.monotonic() >= deadline:
                truncated, reason = True, "timeout"
                break
            directory = stack.pop()
            try:
                entries = sorted(
                    os.scandir(directory), key=lambda item: (item.name.casefold(), item.name)
                )
            except OSError:
                continue
            for entry in entries:
                relative = _relative_path(workspace_root, Path(entry.path))
                if _ignored(relative, workspace_root):
                    continue
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if entry.name not in {".git", ".morrow"}:
                        stack.append(Path(entry.path))
                    continue
                if scanned_files >= PYTHON_MAX_FILES:
                    truncated, reason = True, "max_files"
                    break
                if stat.S_ISLNK(metadata.st_mode):
                    try:
                        target = Path(entry.path).resolve(strict=True)
                        if not target.is_relative_to(workspace_root) or not target.is_file():
                            continue
                        metadata = target.stat()
                        read_path = target
                        target_relative = _relative_path(workspace_root, target)
                    except OSError:
                        continue
                elif stat.S_ISREG(metadata.st_mode):
                    read_path = Path(entry.path)
                    target_relative = relative
                else:
                    continue
                if sensitive_policy.is_protected_path(
                    relative
                ) or sensitive_policy.is_protected_path(target_relative):
                    protected[relative] = ProtectedPath(path=relative)
                    continue
                remaining = PYTHON_MAX_BYTES - scanned_bytes
                if metadata.st_size > remaining:
                    truncated, reason = True, "max_bytes"
                    break
                try:
                    raw = read_path.read_bytes()
                except OSError:
                    continue
                scanned_files += 1
                scanned_bytes += len(raw)
                if b"\x00" in raw or sensitive_policy.is_protected_content(raw):
                    if sensitive_policy.is_protected_content(raw):
                        protected[relative] = ProtectedPath(path=relative)
                    continue
                try:
                    text = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    continue
                file_lines = text.splitlines()
                for index, line in enumerate(file_lines, start=1):
                    columns = _match_columns(line, needle, regex, effective_case)
                    for column in columns:
                        before = tuple(
                            file_lines[max(0, index - 1 - query.context_lines) : index - 1]
                        )
                        after = tuple(file_lines[index : index + query.context_lines])
                        matches.append(
                            SearchMatch(
                                path=relative,
                                line=index,
                                column=column,
                                snippet=line[:MAX_SNIPPET_CHARS] or " ",
                                before=tuple(item[:MAX_SNIPPET_CHARS] for item in before),
                                after=tuple(item[:MAX_SNIPPET_CHARS] for item in after),
                            )
                        )
                        if len(matches) >= query.max_results:
                            truncated, reason = True, "max_matches"
                            break
                    if truncated:
                        break
                if truncated:
                    break
            if truncated:
                break
        matches.sort(key=lambda item: (item.path.casefold(), item.path, item.line, item.column))
        return SearchScan(
            engine=SearchEngine.PYTHON,
            matches=tuple(matches[: query.max_results]),
            protected_paths=tuple(
                sorted(protected.values(), key=lambda item: (item.path.casefold(), item.path))
            ),
            truncated=truncated,
            budget_reason=reason,
        )


def _regex_flags(case: SearchCase) -> int:
    return re.IGNORECASE if case is SearchCase.INSENSITIVE else 0


def _effective_case(case: SearchCase, pattern: str) -> SearchCase:
    if case is SearchCase.SMART:
        return (
            SearchCase.SENSITIVE
            if any(char.isupper() for char in pattern)
            else SearchCase.INSENSITIVE
        )
    return case


def _match_columns(
    line: str, needle: str, regex: re.Pattern[str] | None, case: SearchCase
) -> tuple[int, ...]:
    if regex is not None:
        return tuple(match.start() + 1 for match in regex.finditer(line))
    haystack = line.casefold() if case is SearchCase.INSENSITIVE else line
    positions: list[int] = []
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return tuple(positions)
        positions.append(index + 1)
        start = index + max(1, len(needle))


def _normalize_rg_path(value: str, relative_root: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if relative_root != "." and normalized == relative_root:
        return relative_root
    return normalized


def _relative_path(root: Path, path: Path) -> str:
    value = path.relative_to(root).as_posix()
    return value or "."


def _path_has_protected_content(
    workspace_root: Path, relative: str, sensitive_policy: SensitiveResourcePolicy
) -> bool:
    try:
        candidate = (workspace_root / Path(*relative.split("/"))).resolve(strict=True)
        if not candidate.is_relative_to(workspace_root) or not candidate.is_file():
            return True
        target_relative = candidate.relative_to(workspace_root).as_posix()
        if sensitive_policy.is_protected_path(target_relative):
            return True
        with candidate.open("rb") as stream:
            return sensitive_policy.is_protected_content(stream.read(16_384))
    except OSError:
        return True


def _validate_glob(value: str) -> None:
    if not value or "\x00" in value or "\\" in value or value.startswith("/"):
        raise SearchAdapterError("invalid_glob", "文件匹配模式无效")
    if any(part == ".." for part in value.split("/")):
        raise SearchAdapterError("invalid_glob", "文件匹配模式无效")


def _ignored(relative: str, root: Path) -> bool:
    for name in (".gitignore", ".ignore", ".rgignore"):
        path = root / name
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for raw in lines:
            rule = raw.strip()
            if not rule or rule.startswith("#") or rule.startswith("!"):
                continue
            rule = rule.lstrip("/")
            if rule.endswith("/"):
                rule += "*"
            if fnmatch.fnmatchcase(relative, rule) or fnmatch.fnmatchcase(
                Path(relative).name, rule
            ):
                return True
    return False
