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
        "application/turn_permissions.py",
        "application/tool_persistence.py",
        "application/turn_lifecycle.py",
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


def test_tool_cycle_executor_does_not_own_chat_history_or_public_events():
    path = SOURCE_ROOT / "runtime/tool_cycle.py"
    source = path.read_text(encoding="utf-8")
    agent_source = (SOURCE_ROOT / "runtime/agent.py").read_text(encoding="utf-8")

    assert "morrow.runtime.conversation" not in _imports(path)
    assert "morrow.core.events" not in _imports(path)
    assert "commit_tool_message" not in source
    assert "commit_tool_message" in agent_source


def test_session_persistence_delegates_permission_evidence():
    source = (SOURCE_ROOT / "application/turns.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    persistence = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionPersistence"
    )
    methods = {
        node.name: node
        for node in persistence.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "build_permission_snapshot" not in source
    assert "assert_grant_snapshot_matches" not in source
    assert "_assert_execution_permission" not in methods
    for name in (
        "freeze_permission_snapshot",
        "has_active_unconfined_grant",
        "assert_handler_may_enter",
    ):
        assert any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "permissions"
            for node in ast.walk(methods[name])
        ), name


def test_session_persistence_delegates_durable_tool_state():
    source = (SOURCE_ROOT / "application/turns.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    persistence = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionPersistence"
    )
    methods = {
        node.name: node
        for node in persistence.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for symbol in (
        "prepare_cycle_executions",
        "transition_execution",
        "resolve_approval",
        "approval_preview_digest",
    ):
        assert symbol not in source
    assert "_close_execution_before_handler" not in methods

    owners = {
        "prepare_and_commit_assistant": "tool_conversation",
        "execution_is_visible": "tool_executions",
        "create_pending_approval": "tool_executions",
        "consume_and_mark_executing": "tool_executions",
        "mark_executing": "tool_executions",
        "get_execution": "tool_executions",
        "deny_execution_before_handler": "tool_executions",
        "cancel_execution_before_handler": "tool_executions",
        "record_handler_completed": "tool_executions",
        "commit_tool_message": "tool_conversation",
    }
    for name, owner in owners.items():
        assert any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == owner
            for node in ast.walk(methods[name])
        ), name


def test_session_persistence_delegates_turn_lifecycle_and_restore():
    source = (SOURCE_ROOT / "application/turns.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    persistence = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionPersistence"
    )
    methods = {
        node.name: node
        for node in persistence.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for symbol in (
        "build_agent_run_snapshot",
        "session_can_start_work",
        "restore_conversation_log",
        "_apply_task_terminal_in_txn",
        "_close_open_receipt_in_txn",
    ):
        assert symbol not in source

    owners = {
        "commit": "turn_submission",
        "close_open_receipt": "turn_submission",
        "submit_user": "turn_submission",
        "start_new_session": "session_restore",
        "restore_into": "session_restore",
        "synchronize_projection": "session_restore",
        "synchronize_recovery_projection": "session_restore",
    }
    for name, owner in owners.items():
        assert any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == owner
            for node in ast.walk(methods[name])
        ), name


def test_application_collaborators_do_not_mutate_persistence_private_state():
    api_source = (SOURCE_ROOT / "application/api.py").read_text(encoding="utf-8")
    recovery_source = (SOURCE_ROOT / "application/api_recovery.py").read_text(encoding="utf-8")
    commands_source = (SOURCE_ROOT / "application/commands.py").read_text(encoding="utf-8")
    combined = api_source + recovery_source + commands_source

    assert "persistence._session" not in combined
    assert "persistence._last_client_message_id" not in combined
    assert "persistence.current_task_run_id =" not in combined
    assert "session.committer.current_task_run_id" not in combined
