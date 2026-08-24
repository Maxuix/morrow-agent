"""Bounded JSON Schema normalization for MCP Catalog discovery."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from morrow.core.domain import sha256_digest

from .contracts import MCP_MAX_SCHEMA_BYTES, MCP_SCHEMA_DIALECT

MCP_MAX_SCHEMA_DEPTH = 16
MCP_MAX_SCHEMA_OBJECT_KEYS = 256
MCP_MAX_SCHEMA_ARRAY_ITEMS = 256


class McpSchemaError(ValueError):
    """Stable, sanitized schema failure; validator details never cross the boundary."""

    def __init__(self, code: str, message: str = "MCP schema is invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NormalizedJsonSchema:
    schema: dict[str, Any]
    dialect: str
    digest: str
    byte_size: int


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise McpSchemaError("non_json", "MCP schema is not bounded JSON") from exc


def _walk_schema(value: Any, *, depth: int = 0) -> None:
    if depth > MCP_MAX_SCHEMA_DEPTH:
        raise McpSchemaError("depth", "MCP schema exceeds its depth budget")
    if isinstance(value, dict):
        if len(value) > MCP_MAX_SCHEMA_OBJECT_KEYS:
            raise McpSchemaError("object_size", "MCP schema object is too large")
        for key, child in value.items():
            if not isinstance(key, str) or len(key.encode("utf-8")) > 256:
                raise McpSchemaError("key_size", "MCP schema key is invalid")
            if key in {"$ref", "$dynamicRef"}:
                if not isinstance(child, str) or not child.startswith("#"):
                    raise McpSchemaError("remote_ref", "MCP schema may use local refs only")
            _walk_schema(child, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MCP_MAX_SCHEMA_ARRAY_ITEMS:
            raise McpSchemaError("array_size", "MCP schema array is too large")
        for child in value:
            _walk_schema(child, depth=depth + 1)
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise McpSchemaError("non_json", "MCP schema contains a non-JSON value")


def normalize_json_schema(schema: dict[str, Any], *, label: str = "input") -> NormalizedJsonSchema:
    """Normalize and validate exactly the selected Draft 2020-12 dialect."""

    if not isinstance(schema, dict):
        raise McpSchemaError("shape", f"MCP {label} schema must be an object")
    normalized = copy.deepcopy(schema)
    dialect = normalized.get("$schema", MCP_SCHEMA_DIALECT)
    if dialect != MCP_SCHEMA_DIALECT:
        raise McpSchemaError("dialect", "MCP schema dialect is unsupported")
    normalized["$schema"] = MCP_SCHEMA_DIALECT
    _walk_schema(normalized)
    payload = _json_bytes(normalized)
    if len(payload) > MCP_MAX_SCHEMA_BYTES:
        raise McpSchemaError("bytes", "MCP schema exceeds its byte budget")
    try:
        Draft202012Validator.check_schema(normalized)
    except SchemaError as exc:
        raise McpSchemaError("invalid", "MCP schema failed Draft 2020-12 validation") from exc
    return NormalizedJsonSchema(
        schema=normalized,
        dialect=MCP_SCHEMA_DIALECT,
        digest=sha256_digest(payload),
        byte_size=len(payload),
    )


def schema_digest(schema: dict[str, Any]) -> str:
    return normalize_json_schema(schema).digest


__all__ = [
    "MCP_MAX_SCHEMA_ARRAY_ITEMS",
    "MCP_MAX_SCHEMA_DEPTH",
    "MCP_MAX_SCHEMA_OBJECT_KEYS",
    "McpSchemaError",
    "NormalizedJsonSchema",
    "normalize_json_schema",
    "schema_digest",
]
