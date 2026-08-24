"""Provider control-plane contracts shared by adapters and application services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from morrow.core.models import ModelCapabilityOverrides, ProtocolModel

if TYPE_CHECKING:
    from morrow.core.models import ProviderConfig


PROVIDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"


class DiscoveredModel(ProtocolModel):
    """One sanitized model projection returned by an explicit adapter discovery."""

    model_id: str = Field(min_length=1, max_length=128)
    api_model_id: str = Field(min_length=1, max_length=256)
    capabilities: ModelCapabilityOverrides | None = None

    @field_validator("model_id")
    @classmethod
    def valid_model_id(cls, value: str) -> str:
        import re

        if not re.match(MODEL_ID_PATTERN, value):
            raise ValueError("model_id contains unsupported characters")
        return value

    @field_validator("api_model_id")
    @classmethod
    def non_empty_api_model_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("api_model_id must not be empty")
        return value


ModelDiscovery = Callable[
    ["ProviderConfig", str],
    tuple[DiscoveredModel, ...]
    | list[DiscoveredModel]
    | Awaitable[tuple[DiscoveredModel, ...] | list[DiscoveredModel]],
]


def validate_provider_id(value: str) -> str:
    import re

    if not re.match(PROVIDER_ID_PATTERN, value):
        raise ValueError("provider_id contains unsupported characters")
    return value


def validate_model_id(value: str) -> str:
    import re

    if not re.match(MODEL_ID_PATTERN, value):
        raise ValueError("model_id contains unsupported characters")
    return value


def validate_base_url(value: str) -> str:
    """Accept only an HTTP(S) origin/path without embedded secret carriers."""

    if not value or any(char in value for char in "\x00\r\n\t @"):
        raise ValueError("base_url must be a non-empty HTTP(S) URL without userinfo")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain userinfo, query or fragment")
    return value.rstrip("/")


__all__ = [
    "DiscoveredModel",
    "MODEL_ID_PATTERN",
    "ModelDiscovery",
    "PROVIDER_ID_PATTERN",
    "validate_base_url",
    "validate_model_id",
    "validate_provider_id",
]
