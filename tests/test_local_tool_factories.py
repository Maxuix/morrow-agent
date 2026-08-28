from __future__ import annotations

import json

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.local_tools import _tool_error
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import PermissionPreset, PermissionProfile
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolApprovalDecision
from morrow.runtime.tool_arguments import JsonSchemaArgumentsValidator, ToolArgumentsValidationError
from morrow.runtime.tools import ToolErrorCode
from morrow.services.files import LocalFileError
from morrow.testing import ScriptedModelProvider


def test_local_error_mapping_preserves_recoverable_file_failures():
    assert _tool_error(LocalFileError("not_found", "missing")).code is ToolErrorCode.NOT_FOUND
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
                        name="read",
                        arguments='{"path":"sample.txt","limit":2}',
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
                tool_calls=(FunctionToolCall(id="list", name="ls", arguments='{"path":"src"}'),)
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="search",
                        name="grep",
                        arguments='{"path":"src","pattern":"needle","literal":true}',
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="read-1",
                        name="read",
                        arguments='{"path":"src/bug.py","limit":2}',
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="read-2",
                        name="read",
                        arguments='{"path":"src/bug.py","offset":3,"limit":2}',
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


class _Approve:
    def __init__(self):
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=True)


@pytest.mark.asyncio
async def test_pi_style_edit_and_write_infer_revision_mode_and_accept_absolute_inside_path(
    tmp_path,
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    source = project / "sample.txt"
    source.write_text("old\n", encoding="utf-8")
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="read",
                        name="read",
                        arguments=json.dumps({"path": str(source), "unused": "ignored"}),
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="edit",
                        name="edit",
                        arguments=json.dumps(
                            {
                                "path": str(source),
                                "edits": [{"oldText": "old", "newText": "new"}],
                                "unused": True,
                            }
                        ),
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="create",
                        name="write",
                        arguments=json.dumps(
                            {"path": str(project / "created.txt"), "content": "first\n"}
                        ),
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="replace",
                        name="write",
                        arguments=json.dumps({"path": "created.txt", "content": "second\n"}),
                    ),
                )
            ),
            AssistantMessage(content="done"),
        ]
    )
    approval = _Approve()
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    [item async for item in session_app.orchestrator.stream("edit and write")]

    assert source.read_text(encoding="utf-8") == "new\n"
    assert (project / "created.txt").read_text(encoding="utf-8") == "second\n"
    payloads = [
        json.loads(message.content)
        for message in session_app.session.messages
        if message.role == "tool"
    ]
    assert [payload["ok"] for payload in payloads] == [True, True, True, True]
    assert [payloads[index]["result"]["operation"] for index in (1, 2, 3)] == [
        "patch",
        "create",
        "replace",
    ]
    assert approval.requests == []


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
        "ls",
        "read",
        "find",
        "grep",
        "edit",
        "write",
        "bash",
        "run_skill_script",
    }
    assert "lookup_record" not in names
    assert "calculate" not in names
    for definition in session_app.orchestrator.runtime.loop.tool_executor.definitions:
        parameters = definition.function.parameters
        assert "PermissionProfile" not in str(parameters)
        assert "approval" not in str(parameters).casefold()
        assert "sandbox" not in str(parameters).casefold()
    assert all(
        session_app.orchestrator.runtime.loop.tool_executor.capability_policy is not None
        for _ in [0]
    )


def test_bash_schema_uses_the_common_command_shape(tmp_path):
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
        if tool.function.name == "bash"
    )
    schema = definition.function.parameters
    validator = JsonSchemaArgumentsValidator(schema)
    for payload in ({}, {"command": None}, {"command": ["pwd"]}):
        with pytest.raises(ToolArgumentsValidationError):
            validator.validate(json.dumps(payload))
    assert validator.validate('{"command":"pwd"}') == {"command": "pwd"}
    assert validator.validate('{"command":"pwd","extra":true}') == {
        "command": "pwd",
        "extra": True,
    }
    assert set(schema["properties"]) == {"command", "timeout"}
    assert schema["required"] == ["command"]
    assert "oneOf" not in schema
    assert "additionalProperties" not in schema


def test_core_provider_schemas_match_the_pi_style_field_surface(tmp_path):
    def schema_keywords(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "properties":
                    for property_schema in item.values():
                        yield from schema_keywords(property_schema)
                    continue
                yield key
                yield from schema_keywords(item)
        elif isinstance(value, list):
            for item in value:
                yield from schema_keywords(item)

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
    definitions = {
        tool.function.name: tool.function
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
        if tool.function.name in {"read", "bash", "edit", "write", "grep", "find", "ls"}
    }
    expected = {
        "read": ({"path", "offset", "limit"}, {"path"}),
        "bash": ({"command", "timeout"}, {"command"}),
        "edit": ({"path", "edits"}, {"path", "edits"}),
        "write": ({"path", "content"}, {"path", "content"}),
        "grep": (
            {"pattern", "path", "glob", "literal", "ignoreCase", "context", "limit"},
            {"pattern"},
        ),
        "find": ({"pattern", "path", "limit"}, {"pattern"}),
        "ls": ({"path", "limit"}, set()),
    }

    assert set(definitions) == set(expected)
    for name, (properties, required) in expected.items():
        function = definitions[name]
        schema = function.parameters
        assert set(schema["properties"]) == properties
        assert set(schema.get("required", [])) == required
        assert function.description
        keywords = set(schema_keywords(schema))
        for protocol_keyword in (
            "additionalProperties",
            "oneOf",
            "pattern",
            "maxLength",
            "maxItems",
            "expected_sha256",
            "timeout_seconds",
        ):
            assert protocol_keyword not in keywords


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
        "ls",
        "read",
        "find",
        "grep",
        "edit",
        "write",
        "bash",
        "run_skill_script",
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
                        name="read",
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
    assert payload["error"]["code"] == ToolErrorCode.OUTSIDE_WORKSPACE.value
    assert "outside.txt" not in tool_message.content
