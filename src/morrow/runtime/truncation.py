"""Pi-compatible UTF-8/line truncation primitives for tool output shaping."""

from __future__ import annotations

from dataclasses import dataclass

PI_DEFAULT_MAX_BYTES = 50 * 1024
PI_DEFAULT_MAX_LINES = 2_000
PI_GREP_MAX_LINE_CHARS = 500


@dataclass(frozen=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: str | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    last_line_partial: bool = False
    first_line_exceeds_limit: bool = False
    max_lines: int = PI_DEFAULT_MAX_LINES
    max_bytes: int = PI_DEFAULT_MAX_BYTES


def _split_lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def _base_result(
    content: str,
    *,
    truncated: bool,
    truncated_by: str | None,
    output: str,
    max_lines: int,
    max_bytes: int,
    last_line_partial: bool = False,
    first_line_exceeds_limit: bool = False,
) -> TruncationResult:
    lines = _split_lines(content)
    return TruncationResult(
        content=output,
        truncated=truncated,
        truncated_by=truncated_by,
        total_lines=len(lines),
        total_bytes=len(content.encode("utf-8")),
        output_lines=len(_split_lines(output)),
        output_bytes=len(output.encode("utf-8")),
        last_line_partial=last_line_partial,
        first_line_exceeds_limit=first_line_exceeds_limit,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_head(
    content: str,
    *,
    max_lines: int = PI_DEFAULT_MAX_LINES,
    max_bytes: int = PI_DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep complete lines from the beginning, matching Pi's ``truncateHead``."""
    if max_lines <= 0 or max_bytes <= 0:
        raise ValueError("truncation limits must be positive")
    lines = _split_lines(content)
    total_bytes = len(content.encode("utf-8"))
    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return _base_result(
            content,
            truncated=False,
            truncated_by=None,
            output=content,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    if lines and len(lines[0].encode("utf-8")) > max_bytes:
        return _base_result(
            content,
            truncated=True,
            truncated_by="bytes",
            output="",
            max_lines=max_lines,
            max_bytes=max_bytes,
            first_line_exceeds_limit=True,
        )
    selected: list[str] = []
    used = 0
    by = "lines"
    for index, line in enumerate(lines[:max_lines]):
        line_bytes = len(line.encode("utf-8")) + (1 if index > 0 else 0)
        if used + line_bytes > max_bytes:
            by = "bytes"
            break
        selected.append(line)
        used += line_bytes
    if len(selected) >= max_lines and used <= max_bytes:
        by = "lines"
    return _base_result(
        content,
        truncated=True,
        truncated_by=by,
        output="\n".join(selected),
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def _take_utf8_tail(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    start = len(encoded) - max_bytes
    while start < len(encoded) and encoded[start] & 0xC0 == 0x80:
        start += 1
    return encoded[start:].decode("utf-8")


def truncate_tail(
    content: str,
    *,
    max_lines: int = PI_DEFAULT_MAX_LINES,
    max_bytes: int = PI_DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep complete lines from the end, with Pi's oversized-last-line exception."""
    if max_lines <= 0 or max_bytes <= 0:
        raise ValueError("truncation limits must be positive")
    lines = _split_lines(content)
    total_bytes = len(content.encode("utf-8"))
    if len(lines) <= max_lines and total_bytes <= max_bytes:
        return _base_result(
            content,
            truncated=False,
            truncated_by=None,
            output=content,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    selected: list[str] = []
    used = 0
    by = "lines"
    partial = False
    for line in reversed(lines):
        line_bytes = len(line.encode("utf-8")) + (1 if selected else 0)
        if used + line_bytes > max_bytes:
            by = "bytes"
            if not selected:
                selected.insert(0, _take_utf8_tail(line, max_bytes))
                partial = True
            break
        selected.insert(0, line)
        used += line_bytes
        if len(selected) >= max_lines:
            by = "lines"
            break
    output = "\n".join(selected)
    return _base_result(
        content,
        truncated=True,
        truncated_by=by,
        output=output,
        max_lines=max_lines,
        max_bytes=max_bytes,
        last_line_partial=partial,
    )


def truncate_line(line: str, max_chars: int = PI_GREP_MAX_LINE_CHARS) -> tuple[str, bool]:
    """Truncate a search match line by characters, matching Pi's grep helper."""
    if max_chars <= 0:
        raise ValueError("line limit must be positive")
    if len(line) <= max_chars:
        return line, False
    return f"{line[:max_chars]}... [truncated]", True


__all__ = [
    "PI_DEFAULT_MAX_BYTES",
    "PI_DEFAULT_MAX_LINES",
    "PI_GREP_MAX_LINE_CHARS",
    "TruncationResult",
    "truncate_head",
    "truncate_line",
    "truncate_tail",
]
