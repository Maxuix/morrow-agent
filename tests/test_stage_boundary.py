"""Capability boundary guard.

Inspects constructed application behavior and state instead of rejecting
future module directories by name.  The guard is extended — never weakened —
as Stage 2 slices add real behavior.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

import morrow.core.models as core_models
from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.yaml import ProjectStateYamlStore
from morrow.bootstrap import build_application, build_session_application
from morrow.core.execution import missing_declarations
from morrow.core.models import FunctionToolCall, ModelRef
from morrow.testing import FixedIdSource, ScriptedModelProvider, make_run_policy

# Capability families that must stay outside the current Stage 3F
# slice: arbitrary shell, Git writes, network, browser, MCP and skill access.
FORBIDDEN_TOOL_KEYWORDS = frozenset(
    {
        "shell",
        "bash",
        "zsh",
        "exec",
        "subprocess",
        "commit",
        "checkout",
        "network",
        "http",
        "fetch",
        "request",
        "web",
        "browser",
        "browse",
        "navigate",
        "mcp",
        "skill",
    }
)

# State-document surface that must stay persistent-data-only.
PERSISTENCE_FORBIDDEN_KEYWORDS = ("conversation", "history", "summary", "chat", "log")


def _iter_graph(root: Any, depth: int = 0, seen: set[int] | None = None):
    if seen is None:
        seen = set()
    if root is None or depth > 6:
        return
    if isinstance(root, (str, bytes, int, float, bool, complex)):
        return
    marker = id(root)
    if marker in seen:
        return
    seen.add(marker)
    yield root
    if isinstance(root, dict):
        for key, value in root.items():
            yield from _iter_graph(key, depth + 1, seen)
            yield from _iter_graph(value, depth + 1, seen)
        return
    if isinstance(root, (list, tuple, set, frozenset)):
        for item in root:
            yield from _iter_graph(item, depth + 1, seen)
        return
    if dataclasses.is_dataclass(root) and not isinstance(root, type):
        for field in dataclasses.fields(root):
            yield from _iter_graph(getattr(root, field.name, None), depth + 1, seen)
        return
    if hasattr(root, "__dict__") and not isinstance(root, type):
        for value in vars(root).values():
            yield from _iter_graph(value, depth + 1, seen)


def _collect_tool_names(objects: Any) -> set[str]:
    """Collect names from any tool-registry-like surface on the graph."""
    names: set[str] = set()
    for obj in objects:
        for attr_name in ("tool_definitions", "definitions", "tools"):
            attr = getattr(obj, attr_name, None)
            candidates: Any = ()
            if callable(attr):
                try:
                    candidates = attr()
                except Exception:
                    candidates = ()
            elif isinstance(attr, (list, tuple, set, frozenset)):
                candidates = attr
            for definition in candidates:
                function = getattr(definition, "function", None)
                name = getattr(function, "name", None) or getattr(definition, "name", None)
                if isinstance(name, str):
                    names.add(name)
    return names


def _build_session_products(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    return (
        app,
        build_session_application(
            app,
            identity,
            provider=ScriptedModelProvider(["stage guard reply"]),
            model=ModelRef(provider_id="p", model_id="m"),
        ),
    )


def test_construction_returns_named_session_application(tmp_path):
    app, products = _build_session_products(tmp_path)
    assert app.provider_service is not None
    assert products.session.session_id.startswith("ses_")
    assert products.context_builder is not None
    assert products.commands is not None
    assert products.orchestrator is not None


def test_no_forbidden_tool_capability_is_registered_or_exposed(tmp_path):
    app, products = _build_session_products(tmp_path)
    graph = [
        app,
        products.session,
        products.context_builder,
        products.commands,
        products.orchestrator,
    ]
    names = _collect_tool_names(_iter_graph(graph))
    assert names == {
        "update_configuration",
        "list_directory",
        "read_file",
        "find_files",
        "search_text",
        "apply_patch",
        "write_file",
        "show_changes",
        "run_command",
        "git_status",
        "git_diff",
    }
    for name in names:
        casefolded = name.casefold()
        assert not any(keyword in casefolded for keyword in FORBIDDEN_TOOL_KEYWORDS), name
    assert missing_declarations(tuple(sorted(names))) == ()


def test_workspace_state_documents_are_exactly_preferences_and_profile():
    document_types = {
        name
        for name, value in vars(core_models).items()
        if isinstance(value, type)
        and issubclass(value, core_models.WorkspaceDocument)
        and value is not core_models.WorkspaceDocument
    }
    assert document_types == {"ProjectPreferencesDocument", "ProfileDocument"}


def test_handoff_implementation_symbols_are_absent_from_product_surface():
    assert not hasattr(core_models, "Handoff")
    assert not hasattr(core_models, "HandoffDocument")
    assert not hasattr(core_models, "Decision")
    public_store_api = {name for name in dir(ProjectStateYamlStore) if not name.startswith("_")}
    assert not any("handoff" in name.casefold() for name in public_store_api)


def test_state_store_api_has_no_conversation_or_summary_surface():
    public_api = {name for name in dir(ProjectStateYamlStore) if not name.startswith("_")}
    offenders = {
        name
        for name in public_api
        if any(keyword in name.casefold() for keyword in PERSISTENCE_FORBIDDEN_KEYWORDS)
    }
    assert offenders == set()


@pytest.mark.asyncio
async def test_plain_chat_turn_persists_no_state_document(tmp_path):
    app, products = _build_session_products(tmp_path)

    def state_files() -> set[str]:
        return {
            str(path.relative_to(app.data_root.root))
            for path in app.data_root.root.rglob("*")
            if path.is_file()
        }

    before = {
        path
        for path in state_files()
        if not path.startswith("store/") and not path.startswith("locks/operational")
    }
    assert before
    consumed = [item async for item in products.orchestrator.stream("普通对话输入")]
    assert consumed
    after = {
        path
        for path in state_files()
        if not path.startswith("store/") and not path.startswith("locks/operational")
    }
    assert after == before
    assert (app.data_root.root / "store" / "operational.sqlite").is_file()


@pytest.mark.asyncio
async def test_session_restart_restores_persisted_conversation(tmp_path):
    app, products = _build_session_products(tmp_path)
    session_id = products.session.session_id
    consumed = [item async for item in products.orchestrator.stream("ephemeral")]
    assert consumed
    assert products.session.messages
    resumed = build_session_application(
        app,
        app.workspace_service.resolve(tmp_path / "project").identity,
        provider=ScriptedModelProvider(["stage guard reply"]),
        model=ModelRef(provider_id="p", model_id="m"),
        resume_session_id=session_id,
    )
    assert [message.content for message in resumed.session.messages] == [
        "ephemeral",
        "stage guard reply",
    ]


def test_unsupported_adapter_capability_preserves_plain_chat_without_tools(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    app.registry.register(
        "openai-compatible",
        lambda config, credential: ScriptedModelProvider(["plain"]),
        tool_protocol="none",
        multiple_tool_calls=False,
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["plain"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )

    assert session_app.orchestrator.runtime.loop.tool_executor is None
    assert session_app.context_builder.run_policy.provider_tool_support.tool_protocol == "none"
    assert session_app.context_builder.run_policy.effective_request_chars == 160000


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_demo_tool_registry_names_are_exactly_lookup_record_and_calculate():
    from morrow.runtime.tools import ToolRegistry, make_calculate_tool, make_lookup_record_tool

    registry = ToolRegistry()
    registry.register(make_lookup_record_tool({("plans", "starter"): {"monthly_price": 29.0}}))
    registry.register(make_calculate_tool())
    names = {definition.function.name for definition in registry.snapshot().definitions}
    assert names == {"lookup_record", "calculate"}
    for keyword in FORBIDDEN_TOOL_KEYWORDS:
        for name in names:
            assert keyword not in name.casefold()


@pytest.mark.asyncio
async def test_demo_tools_leave_temporary_workspace_byte_identical(tmp_path):
    import asyncio

    from morrow.runtime.tools import (
        ToolExecutor,
        ToolRegistry,
        make_calculate_tool,
        make_lookup_record_tool,
    )

    registry = ToolRegistry()
    registry.register(make_lookup_record_tool({("plans", "starter"): {"monthly_price": 29.0}}))
    registry.register(make_calculate_tool())
    executor = ToolExecutor(registry.snapshot(), make_run_policy())
    before = _snapshot_tree(tmp_path)
    outcomes = await asyncio.gather(
        executor.execute(
            FunctionToolCall(
                id="c1", name="lookup_record", arguments='{"dataset": "plans", "key": "starter"}'
            )
        ),
        executor.execute(
            FunctionToolCall(
                id="c2",
                name="calculate",
                arguments='{"operation": "multiply", "values": [29.0, 3, 1.19]}',
            )
        ),
    )
    assert all(outcome.ok for outcome in outcomes)
    assert _snapshot_tree(tmp_path) == before
