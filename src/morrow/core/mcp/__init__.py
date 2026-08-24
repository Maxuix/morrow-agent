"""MCP control-plane contracts without SDK or process dependencies."""

from .contracts import *  # noqa: F403
from .contracts import __all__ as __all__
from .results import (
    MCP_RESULT_MAX_BYTES,
    MCP_RESULT_MAX_ITEMS,
    MCP_RESULT_MAX_LINK_BYTES,
    MCP_RESULT_MAX_STRUCTURED_DEPTH,
    MCP_RESULT_MAX_TEXT_BYTES,
    McpArtifactRef,
    McpEmbeddedResourceRef,
    McpNormalizedResult,
    McpResourceLocator,
)
from .review import MCP_REVIEW_MAX_BYTES, MCP_REVIEW_MAX_ITEMS, McpReviewEvidence, McpReviewRisk
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
    "MCP_REVIEW_MAX_BYTES",
    "MCP_REVIEW_MAX_ITEMS",
    "McpReviewEvidence",
    "McpReviewRisk",
    "MCP_RESULT_MAX_BYTES",
    "MCP_RESULT_MAX_ITEMS",
    "MCP_RESULT_MAX_LINK_BYTES",
    "MCP_RESULT_MAX_STRUCTURED_DEPTH",
    "MCP_RESULT_MAX_TEXT_BYTES",
    "McpArtifactRef",
    "McpEmbeddedResourceRef",
    "McpNormalizedResult",
    "McpResourceLocator",
]
