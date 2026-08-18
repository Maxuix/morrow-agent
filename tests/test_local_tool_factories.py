from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.local_tools import (
    FindFilesArguments,
    ListDirectoryArguments,
    ReadFileArguments,
    SearchTextArguments,
    _tool_error,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef
from morrow.runtime.tools import ToolErrorCode
from morrow.services.files import LocalFileError
from morrow.services.git import GitServiceError
from morrow.testing import ScriptedModelProvider


def test_read_search_arguments_are_strict_and_workspace_relative():
    assert ListDirectoryArguments.model_validate({"path": "."}, strict=True).path == "."
    with pytest.raises(ValidationError):
        ReadFileArguments.model_validate({"path": "/tmp/secret"}, strict=True)
    with pytest.raises(ValidationError):
        FindFilesArguments.model_validate(
            {"path": "src", "pattern": "*.py", "extra": 1}, strict=True
        )
    with pytest.raises(ValidationError):
        SearchTextArguments.model_validate({"path": "src\\main.py", "pattern": "x"}, strict=True)


def test_local_error_mapping_preserves_recoverable_not_found_and_git_failures():
    assert _tool_error(LocalFileError("not_found", "missing")).code is ToolErrorCode.NOT_FOUND
    assert _tool_error(GitServiceError("git_failed", "failed")).code is ToolErrorCode.GIT_FAILED
    assert (
        _tool_error(LocalFileError("unsupported_newline", "mixed")).code
        is ToolErrorCode.INVALID_ARGUMENTS
    )


@pytest.mark.asyncio
async def test_production_read_tools_use_semantic_result_and_continuation(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="read-1",
                        name="read_file",
                        arguments='{"path":"sample.txt","line_count":2}',
                    ),
                )
            ),
            AssistantMessage(content="文件的前两行是 one 和 two，下一行从 3 开始。"),
        ]
    )
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    items = [item async for item in session_app.orchestrator.stream("读取 sample.txt")]

    assert items[-1].action is None
    tool_message = [message for message in session_app.session.messages if message.role == "tool"][
        0
    ]
    payload = json.loads(tool_message.content)
    assert payload["ok"] is True
    assert payload["result"]["text"] == "one\ntwo\n"
    assert payload["result"]["next_start_line"] == 3
    assert payload["result"]["truncated"] is True
    assert "PermissionProfile" not in str(provider.stream_tools[0])
    assert "sample.txt" in str(provider.stream_calls[1])


@pytest.mark.asyncio
async def test_fake_provider_can_list_search_read_continue_and_explain(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    source = project / "src"
    source.mkdir()
    (source / "bug.py").write_text(
        "def broken():\n    return 1\nneedle = broken()\n", encoding="utf-8"
    )
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(id="list", name="list_directory", arguments='{"path":"src"}'),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="search",
                        name="search_text",
                        arguments='{"path":"src","pattern":"needle"}',
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="read-1",
                        name="read_file",
                        arguments='{"path":"src/bug.py","line_count":2}',
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="read-2",
                        name="read_file",
                        arguments='{"path":"src/bug.py","start_line":3,"line_count":2}',
                    ),
                )
            ),
            AssistantMessage(content="已定位 src/bug.py 的 needle，并读取了全部三行。"),
        ]
    )
    approval = _NoApproval()
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    items = [item async for item in session_app.orchestrator.stream("定位并读取 needle")]

    assert items[-1].action is None
    assert approval.requests == []
    tool_messages = [message for message in session_app.session.messages if message.role == "tool"]
    payloads = [json.loads(message.content) for message in tool_messages]
    assert [payload["ok"] for payload in payloads] == [True, True, True, True]
    assert payloads[0]["result"]["entries"][0]["path"] == "src/bug.py"
    assert payloads[1]["result"]["matches"][0]["line"] == 3
    assert payloads[2]["result"]["next_start_line"] == 3
    assert payloads[3]["result"]["text"] == "needle = broken()\n"


class _NoApproval:
    def __init__(self):
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        raise AssertionError("read-only tools must not request approval")


def test_production_inventory_is_exact_and_demo_tools_are_not_exposed(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    names = {
        tool.function.name
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
    }

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
    assert "lookup_record" not in names
    assert "calculate" not in names
    for definition in session_app.orchestrator.runtime.loop.tool_executor.definitions:
        parameters = definition.function.parameters
        assert parameters.get("additionalProperties") is False
        assert "PermissionProfile" not in str(parameters)
        assert "approval" not in str(parameters).casefold()
        assert "sandbox" not in str(parameters).casefold()
    assert all(
        session_app.orchestrator.runtime.loop.tool_executor.capability_policy is not None
        for _ in [0]
    )


def test_run_command_schema_requires_xor_and_forbids_install_or_network(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    definition = next(
        tool
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
        if tool.function.name == "run_command"
    )
    description = definition.function.description
    schema = definition.function.parameters
    assert "argv" in description and "shell" in description
    assert "二选一" in description
    assert "安装依赖" in description
    assert "网络" in description
    assert "python3" in description
    assert "二选一" in schema["properties"]["argv"]["description"]
    assert "安装依赖" in schema["properties"]["shell"]["description"]


def test_supported_auto_sandbox_inventory_adds_only_current_run_promotion(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
        permission_profile=PermissionProfile.from_preset(PermissionPreset.AUTO_SANDBOXED),
    )
    names = {
        tool.function.name
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
    }
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
        "promote_sandbox_changes",
    }


@pytest.mark.asyncio
async def test_invalid_read_path_is_bounded_and_handler_does_not_disclose_outside(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="bad",
                        name="read_file",
                        arguments='{"path":"../outside.txt"}',
                    ),
                )
            ),
            AssistantMessage(content="路径无效，已继续。"),
        ]
    )
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    [item async for item in session_app.orchestrator.stream("读取上级目录文件")]

    tool_message = [message for message in session_app.session.messages if message.role == "tool"][
        0
    ]
    payload = json.loads(tool_message.content)
    assert payload["ok"] is False
    assert payload["error"]["code"] == ToolErrorCode.INVALID_PATH.value
    assert "outside.txt" not in tool_message.content
