"""Transient, line-framed text projection; never writes conversation history."""

from __future__ import annotations

import re

from morrow.core.domain import redact_workflow_text

_OPEN_SECRET_VALUE = re.compile(
    r"(?i)(?:[\"']?(?:api[_-]?key|authorization|password|credentials?)[\"']?\s*[:=]"
    r"\s*[\"']?|--(?:api[-_]?key|authorization|password|credentials?)\s+|Bearer\s+)$"
)


def project_text(text: str) -> str:
    """Use the existing value-sensitive redactor, retaining ordinary code vocabulary."""
    safe, _ = redact_workflow_text(text)
    return "".join(c for c in safe if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))


class TextStreamProjection:
    """Release complete lines so credential fragments cannot escape chunk-by-chunk.

    The final unterminated line waits for semantic completion. Retry attempts have separate
    projections; their already displayed text is provisional and never becomes chat history.
    """

    def __init__(self) -> None:
        self.pending = ""
        self.emitted = ""

    def feed(self, text: str) -> str:
        self.pending += text
        split = self.pending.rfind("\n") + 1
        if not split:
            return ""
        ready = self.pending[:split]
        unfinished = _OPEN_SECRET_VALUE.search(ready)
        if unfinished is not None:
            # Keep an assignment whose value begins on the next line in the same redaction unit.
            split = ready.rfind("\n", 0, unfinished.start()) + 1
            ready = self.pending[:split]
        self.pending = self.pending[split:]
        visible = project_text(ready)
        self.emitted += visible
        return visible

    def finish(self, content: str) -> tuple[str, bool]:
        visible = project_text(content)
        reset = not visible.startswith(self.emitted)
        remainder = visible if reset else visible[len(self.emitted) :]
        self.pending = ""
        self.emitted = visible
        return remainder, reset
