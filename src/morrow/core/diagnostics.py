"""Explicitly safe, bounded diagnostics that may cross the public Agent boundary."""

from __future__ import annotations

import re

from morrow.core.domain import refuse_secret_material

PUBLIC_DIAGNOSTIC_MAX_CHARS = 600
_DIAGNOSTIC_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PublicDiagnosticError(RuntimeError):
    """A domain failure whose code and message are safe for user-visible events.

    Callers must use reviewed, secret-free messages rather than raw exception text. Unknown
    exceptions intentionally do not implement this contract and remain generic at the Agent edge.
    """

    def __init__(self, code: str, message: str) -> None:
        if _DIAGNOSTIC_CODE.fullmatch(code) is None:
            raise ValueError("public diagnostic code is invalid")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > PUBLIC_DIAGNOSTIC_MAX_CHARS
            or any(char in message for char in "\x00\r\n")
        ):
            raise ValueError("public diagnostic message is invalid")
        refuse_secret_material(message, label="public diagnostic")
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def public_message(self) -> str:
        return f"{self.code}: {self.message}"


__all__ = ["PUBLIC_DIAGNOSTIC_MAX_CHARS", "PublicDiagnosticError"]
