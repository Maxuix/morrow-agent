"""MCP control-plane contracts without SDK or process dependencies."""

from .contracts import *  # noqa: F403
from .contracts import __all__ as __all__
from .schema import (
    MCP_MAX_SCHEMA_ARRAY_ITEMS,
    MCP_MAX_SCHEMA_DEPTH,
    MCP_MAX_SCHEMA_OBJECT_KEYS,
    McpSchemaError,
    NormalizedJsonSchema,
    normalize_json_schema,
    schema_digest,
)

__all__ += [
    "MCP_MAX_SCHEMA_ARRAY_ITEMS",
    "MCP_MAX_SCHEMA_DEPTH",
    "MCP_MAX_SCHEMA_OBJECT_KEYS",
    "McpSchemaError",
    "NormalizedJsonSchema",
    "normalize_json_schema",
    "schema_digest",
]
