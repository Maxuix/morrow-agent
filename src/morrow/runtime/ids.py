"""Production-safe identifiers shared by one application composition."""

from __future__ import annotations

import secrets


class RandomIdSource:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(12)}"
