"""Narrow display formatters near the tool declarations (master plan P4.2).

The formatter reuses already-parsed inputs and already-produced outcomes; it
never re-executes tools, scans workspaces or fabricates counts. Output is
bounded projection text: a title (label + projected target) and an optional
summary built from declared safe facts (paths, counts, exit codes).
"""

from __future__ import annotations

import json

from morrow.core.models import sanitize_text

_TITLE_TARGET_MAX_CHARS = 160

_READ_TOOLS = frozenset({"read", "read_artifact"})
_SEARCH_TOOLS = frozenset({"find", "grep", "search"})
_WRITE_TOOLS = frozenset({"edit", "write"})
_RUN_TOOLS = frozenset({"bash", "run_command"})

_TARGET_KEYS = ("path", "query", "pattern", "command", "url")


def _projected_target(arguments_json: str | None) -> str | None:
    """Extract one bounded, redacted target from the parsed call arguments."""
    if not arguments_json:
        return None
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(arguments, dict):
        return None
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            projected = sanitize_text(value, max_length=_TITLE_TARGET_MAX_CHARS)
            if key == "command":
                projected = projected.replace("\n", " ")
            return projected[:_TITLE_TARGET_MAX_CHARS]
    return None


def _fact_paths(facts: tuple) -> list[str]:
    paths: list[str] = []
    for fact in facts:
        values = getattr(fact, "relative_paths", ())
        for value in values:
            if isinstance(value, str) and value not in paths:
                paths.append(value)
    return paths


def _fact_counts(facts: tuple) -> str | None:
    """One bounded count phrase from declared facts, e.g. command exit codes."""
    for fact in facts:
        exit_code = getattr(fact, "exit_code", None)
        if isinstance(exit_code, int):
            return f"退出码 {exit_code}"
    return None


def format_tool_display(
    tool_name: str,
    arguments_json: str | None,
    *,
    facts: tuple = (),
) -> tuple[str, str | None]:
    """Return (safe_title, safe_summary) for one tool observation."""
    target = _projected_target(arguments_json)
    paths = _fact_paths(facts)
    if not target and paths:
        target = paths[0]
    title = f"{tool_name} {target}" if target else tool_name
    if len(title) > 200:
        title = title[:200]
    summary_parts: list[str] = []
    if len(paths) > 1:
        summary_parts.append(f"涉及 {len(paths)} 个路径")
    counts = _fact_counts(facts)
    if counts is not None:
        summary_parts.append(counts)
    summary = " · ".join(summary_parts) if summary_parts else None
    return title, summary
