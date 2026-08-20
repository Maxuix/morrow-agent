"""Dependency-boundary regression tests without an external lint dependency."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "morrow"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


def test_core_does_not_depend_on_outer_layers():
    forbidden = ("morrow.adapters", "morrow.application", "morrow.interfaces", "morrow.runtime")

    for path in (SOURCE_ROOT / "core").glob("*.py"):
        violations = sorted(module for module in _imports(path) if module.startswith(forbidden))
        assert violations == [], f"{path.name} imports outer layers: {violations}"


def test_domain_services_depend_on_journal_ports_not_sqlite_adapter():
    port_owned_modules = (
        "application/artifacts.py",
        "application/checkpoints.py",
        "application/grants.py",
        "application/recovery.py",
        "application/tasks.py",
        "runtime/durable_log.py",
        "application/api_permissions.py",
        "application/api_recovery.py",
    )

    for relative in port_owned_modules:
        imports = _imports(SOURCE_ROOT / relative)
        assert "morrow.adapters.state.journal" not in imports, relative


def test_recovery_lifecycle_has_one_application_command_owner():
    turns = ast.parse((SOURCE_ROOT / "application/turns.py").read_text(encoding="utf-8"))
    persistence = next(
        node
        for node in turns.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionPersistence"
    )
    methods = {
        node.name
        for node in persistence.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "apply_recovery" not in methods


def test_cli_does_not_reach_through_operational_api_to_the_journal():
    source = (SOURCE_ROOT / "interfaces/cli.py").read_text(encoding="utf-8")

    assert "api.journal" not in source


def test_agent_loop_uses_one_explicit_durable_runtime_contract():
    agent_source = (SOURCE_ROOT / "runtime/agent.py").read_text(encoding="utf-8")
    session_tree = ast.parse((SOURCE_ROOT / "runtime/session.py").read_text(encoding="utf-8"))
    turns_tree = ast.parse((SOURCE_ROOT / "application/turns.py").read_text(encoding="utf-8"))

    assert "session.committer" not in agent_source
    assert 'getattr(session, "committer"' not in agent_source
    assert "getattr(session.committer" not in agent_source
    assert ".journal.get_capability_grant" not in agent_source

    contract = next(
        node
        for node in session_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DurableRunCoordinator"
    )
    implementation = next(
        node
        for node in turns_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionPersistence"
    )
    required = {
        node.name
        for node in contract.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    provided = {
        node.name
        for node in implementation.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert required <= provided
