"""Cross-fragment reasoning redaction with bounded hold (master plan P3.3).

Vendor-visible reasoning is transient display content. A fragment that is
already public at emission can never be recalled, so the filter decides BEFORE
release: safe prefixes flow immediately, a tail that could still grow into a
secret is held across fragments, and a held tail that exceeds its bound is
hidden behind an ellipsis marker instead of being flushed unfiltered.

Detection contract (explicit and bounded — never a claim of perfect secrecy):

- credential-shaped tokens: ``sk-`` followed by 20+ token characters;
- labeled assignments: ``api_key`` / ``api-key`` / ``apikey`` /
  ``authorization`` / ``token`` / ``password`` / ``credential`` followed by
  ``:`` or ``=`` and the value that starts after it;
- matching is case-insensitive, spans fragment boundaries and hides the whole
  match including the label's value;
- normal code identifiers, paths, commands and prose stay visible: only the
  matched span (label plus value) is hidden, never the surrounding text.
"""

from __future__ import annotations

import re

HOLD_LIMIT_CHARS = 64
HIDDEN_MARK = "…"

_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api_key|api-key|apikey|authorization|token|password|credential)\s*[:=]\s*\S+"
)


def _first_match(text: str, start: int) -> re.Match | None:
    token = _TOKEN_PATTERN.search(text, start)
    assignment = _ASSIGNMENT_PATTERN.search(text, start)
    matches = [m for m in (token, assignment) if m is not None]
    return min(matches, key=lambda m: m.start()) if matches else None


# Suffix shapes that could grow into a full match with the next fragment; the
# held tail is bounded, so an unresolved prefix is hidden, never flushed.
_PARTIAL_TOKEN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{0,19}$")
_PARTIAL_LABEL = re.compile(
    r"(?i)\b(api_key|api-key|apikey|authorization|token|password|credential)\s*[:=]?\s*$"
)


class ReasoningRedactor:
    """Incremental filter: ``feed`` returns the safe prefix (possibly '')."""

    def __init__(self) -> None:
        self._hold: str = ""

    def feed(self, fragment: str) -> str:
        if not fragment:
            return ""
        pending = self._hold + fragment
        self._hold = ""
        out: list[str] = []
        pos = 0
        while pos < len(pending):
            match = _first_match(pending, pos)
            if match is None:
                break
            out.append(pending[pos : match.start()])
            if len(pending) - match.end() < HOLD_LIMIT_CHARS:
                # The match ends near the input edge: its value could continue
                # in the next fragment, so hold from the match start.
                self._hold = pending[match.start() :]
                return "".join(out)
            out.append(HIDDEN_MARK)
            pos = match.end()
        # No complete match left: only a tail that could still grow into one
        # is held; everything else flows immediately.
        tail_start = max(pos, len(pending) - HOLD_LIMIT_CHARS)
        partial = _PARTIAL_TOKEN.search(pending, tail_start) or _PARTIAL_LABEL.search(
            pending, tail_start
        )
        if partial is not None:
            out.append(pending[pos : partial.start()])
            self._hold = pending[partial.start() :]
            return "".join(out)
        out.append(pending[pos:])
        return "".join(out)

    def finish(self) -> str:
        """Stream end: release unambiguous text, mask matches, drop ambiguity."""
        if not self._hold:
            return ""
        tail, self._hold = self._hold, ""
        out: list[str] = []
        pos = 0
        while True:
            match = _first_match(tail, pos)
            if match is None:
                out.append(tail[pos:])
                return "".join(out)
            out.append(tail[pos : match.start()])
            out.append(HIDDEN_MARK)
            pos = match.end()
