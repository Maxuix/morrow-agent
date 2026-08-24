"""Stable Skill lifecycle error contracts."""

from __future__ import annotations


class SkillLifecycleError(RuntimeError):
    """Sanitized lifecycle failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SkillLifecycleNeedsResolution(SkillLifecycleError):
    def __init__(self, message: str = "Skill lifecycle operation needs resolution") -> None:
        super().__init__("needs_resolution", message)


__all__ = ["SkillLifecycleError", "SkillLifecycleNeedsResolution"]
