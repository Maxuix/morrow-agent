"""Subplan 63 budget measurements: serialized sizes that lock Stage 6 budgets.

Deterministic, offline, no MCP SDK required. Reports (via assertions against
the locked budgets) the headroom in the existing 64 KiB AgentRun snapshot for
reference-only Skill/MCP fields, the per-run Skill context budget, and the MCP
tool catalog/result budget. The measured numbers are recorded in
``docs/research/stage6-mcp-dependency-spike.md``.
"""

from __future__ import annotations

import json

from morrow.core.agent_runs import (
    ProviderCapabilities,
    ProviderRuntimeSnapshot,
    exact_model_capabilities,
)
from morrow.core.domain import (
    AGENT_RUN_SNAPSHOT_MAX_BYTES,
    AgentRunSnapshot,
    FrozenRunPreference,
    ModelRef,
    SourceRevisionRef,
)
from morrow.core.models import CredentialRef
from morrow.runtime.policy import load_runtime_policy

# Locked budgets (bytes of canonical JSON, no trailing whitespace).
SKILL_REF_MAX_BYTES = 512
MCP_SERVER_REF_MAX_BYTES = 512
SKILL_CONTEXT_ENTRY_MAX_BYTES = 2048
SKILL_CONTEXT_TOTAL_MAX_BYTES = 16 * 1024
MCP_TOOL_ENTRY_MAX_BYTES = 4096
MCP_CATALOG_MAX_BYTES = 64 * 1024
RESULT_CHUNK_MAX_BYTES = 128 * 1024

DIGEST = "a" * 64  # SHA-256 hex digest placeholder (fixed, deterministic)


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True))


def _realistic_source_revisions() -> tuple[SourceRevisionRef, ...]:
    return (
        SourceRevisionRef(kind="global_config", revision=41, content_sha256=DIGEST),
        SourceRevisionRef(kind="workspace_profile", revision=17, content_sha256=DIGEST),
        SourceRevisionRef(kind="workspace_preferences", revision=9, content_sha256=DIGEST),
        SourceRevisionRef(kind="session_preferences", revision=3, content_sha256=DIGEST),
    )


def _realistic_snapshot() -> AgentRunSnapshot:
    preferences = tuple(
        FrozenRunPreference(
            preference_id=f"pref_{i:08d}",
            statement=f"Representative frozen Preference statement number {i} for budget "
            "measurements, bounded to a realistic length.",
            scope=("global", "workspace", "session")[i % 3],
            revision=i + 1,
            updated_at=__import__("datetime").datetime(2026, 8, 1, 12, 0, 0),
        )
        for i in range(8)
    )
    model = ModelRef(provider_id="openai-compatible", model_id="gpt-5-mini")
    run_policy = load_runtime_policy().agent_run.resolve(
        model,
        tool_protocol="openai_function",
        multiple_tool_calls=True,
        context_window_tokens=None,
    )
    provider_runtime = ProviderRuntimeSnapshot(
        provider_id="openai-compatible",
        adapter_id="openai-compatible",
        model=model,
        api_model_id="gpt-5-mini",
        endpoint="https://api.example.test/v1",
        credential_ref=CredentialRef(ref="provider:openai-compatible:ab12", version=1),
        capabilities=exact_model_capabilities(
            "openai-compatible",
            ProviderCapabilities(
                tool_protocol="openai_function",
                multiple_tool_calls=True,
                structured_output=True,
            ),
            model,
        ),
        config_revision=41,
        config_digest=DIGEST,
    )
    return AgentRunSnapshot(
        model=model,
        provider_id="openai-compatible",
        source_revisions=_realistic_source_revisions(),
        run_policy_digest=DIGEST,
        tool_schema_digest=DIGEST,
        permission_profile_digest=DIGEST,
        runtime_instance_id="inst-12345",
        memory_selection_id="msel_00000000",
        memory_selection_digest=DIGEST,
        memory_snapshot_revision=7,
        frozen_preferences=preferences,
        preference_projection_digest=DIGEST,
        preference_omitted_count=2,
        preference_source_scopes=("global", "workspace", "session"),
        preference_refresh_status="ok",
        provider_runtime=provider_runtime,
        run_policy=run_policy,
    )


def _skill_run_ref(number: int) -> dict:
    return {
        "skill_id": f"skl_{number:08d}",
        "binding_kind": "workspace",
        "version": "1.2.3",
        "tree_digest": DIGEST,
        "scope": "workspace",
        "enabled": True,
    }


def _mcp_server_ref() -> dict:
    return {"server_id": "mcp_00000000", "config_digest": DIGEST, "enabled": True}


def test_agent_run_snapshot_headroom_for_stage6_refs() -> None:
    base = _json_bytes(_realistic_snapshot().model_dump(mode="json"))
    assert base < 16 * 1024  # far below the 64 KiB ceiling; recorded precisely below

    stages6_refs = [_skill_run_ref(i) for i in range(8)] + [_mcp_server_ref()]
    total_refs = sum(_json_bytes(ref) for ref in stages6_refs)
    assert max(_json_bytes(_skill_run_ref(1)), _json_bytes(_mcp_server_ref())) < SKILL_REF_MAX_BYTES

    with_extensions = base + total_refs
    print(
        "AgentRunSnapshot base JSON:",
        base,
        "bytes; +8 Skill refs +1 MCP ref:",
        total_refs,
        "bytes; total",
        with_extensions,
        "of",
        AGENT_RUN_SNAPSHOT_MAX_BYTES,
    )
    assert with_extensions < AGENT_RUN_SNAPSHOT_MAX_BYTES


def test_skill_context_budget() -> None:
    entries = [_skill_run_ref(i) for i in range(8)]
    # The per-run context entry also carries a short bounded context summary.
    context_entries = [
        {**entry, "context": "short bounded skill context summary for selection"}
        for entry in entries
    ]
    sizes = [_json_bytes(entry) for entry in context_entries]
    total = sum(sizes)
    print("Skill context entry sizes (1/4/8):", sizes[0], sizes[3], sizes[7], "bytes; total", total)
    assert max(sizes) <= SKILL_CONTEXT_ENTRY_MAX_BYTES
    assert total <= SKILL_CONTEXT_TOTAL_MAX_BYTES


def _tool_corpus() -> list[dict]:
    """Realistic MCP tool schemas: six families; one family carries a large schema."""
    families = {
        "echo": {
            "description": "Echo the given text back to the caller.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to echo."}},
                "required": ["text"],
            },
        },
        "summarize": {
            "description": "Produce a bounded summary of the given document text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Document text."},
                    "max_words": {"type": "integer", "minimum": 10, "maximum": 500},
                    "style": {"type": "string", "enum": ["plain", "bullet"]},
                },
                "required": ["text"],
            },
        },
        "search": {
            "description": "Search documents by query with pagination and filters.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "offset": {"type": "integer", "minimum": 0},
                    "filter": {"type": "string", "enum": ["all", "draft", "final"]},
                },
                "required": ["query"],
            },
        },
        "create_issue": {
            "description": "Create an issue on the configured tracker with labels.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "body": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "assignee": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                },
                "required": ["title"],
            },
        },
        "list_items": {
            "description": "List items in a project with a cursor-based result set.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"]},
                    "cursor": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
        "configure": {
            "description": "Configure the server with a rich nested settings object.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "settings": {
                        "type": "object",
                        "properties": {
                            "verbose": {"type": "boolean"},
                            "timeout_seconds": {"type": "number", "minimum": 1},
                            "targets": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "weight": {"type": "number"},
                                    },
                                },
                            },
                        },
                    },
                    "mode": {"type": "string", "enum": ["strict", "lenient"]},
                },
                "required": ["settings"],
            },
        },
    }
    tools: list[dict] = []
    for _family_index, (stem, spec) in enumerate(families.items()):
        for copy in range(11):
            base = {"name": f"{stem}_{copy:02d}", **spec}
            tools.append(base)
    tools = tools[:64]
    assert len(tools) == 64
    return tools


def test_mcp_tool_catalog_budget() -> None:
    corpus = _tool_corpus()
    size_of = lambda tools: sum(_json_bytes(tool) for tool in tools)  # noqa: E731
    print(
        "MCP tool snapshot sizes: 1 tool =",
        size_of(corpus[:1]),
        "bytes; 16 tools =",
        size_of(corpus[:16]),
        "bytes; 64 tools =",
        size_of(corpus),
        "bytes",
    )
    largest = max(_json_bytes(tool) for tool in corpus)
    assert largest <= MCP_TOOL_ENTRY_MAX_BYTES
    assert size_of(corpus) <= MCP_CATALOG_MAX_BYTES
    assert RESULT_CHUNK_MAX_BYTES >= 64 * 1024  # result budget sanity floor
