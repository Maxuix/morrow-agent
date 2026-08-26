from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import OpenAICompatibleProvider, serialize_tool
from morrow.application.local_tools import (
    RUN_COMMAND_PROVIDER_SCHEMA,
    WRITE_FILE_PROVIDER_SCHEMA,
    RunCommandArguments,
    WriteFileArguments,
    _tool_error,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PermissionPreset,
    PermissionProfile,
    PolicyDecision,
    PolicyVerdict,
    ProcessIsolation,
    WorkspaceCapability,
)
from morrow.core.execution import tool_declaration
from morrow.core.models import FunctionToolCall, ModelRef, ToolEffect, UserMessage
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tool_arguments import (
    MAX_STRING_CHARS,
    JsonSchemaArgumentsValidator,
    PydanticArgumentsValidator,
    ToolArgumentsValidationError,
)
from morrow.runtime.tools import (
    ToolContractAuditError,
    ToolErrorCode,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
    audit_registered_tool,
    make_tool,
    tool_parameters_from_model,
)
from morrow.testing import ScriptedModelProvider, make_run_policy


class _Chunks:
    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="ok", reasoning_content=None),
                    finish_reason="stop",
                )
            ]
        )


class _Completions:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return _Chunks()


def _tool(name, model):
    async def handler(_arguments):
        return {"ok": True}

    schema = {
        "run_command": RUN_COMMAND_PROVIDER_SCHEMA,
        "write_file": WRITE_FILE_PROVIDER_SCHEMA,
    }[name]
    expected_shape = (
        "exactly_one_of:argv,shell" if name == "run_command" else "write_file_mode_revision"
    )
    declaration = (
        tool_declaration(name, process_isolation=ProcessIsolation.HOST)
        if name == "run_command"
        else tool_declaration(name)
    )
    return make_tool(
        name=name,
        description=name,
        arguments_model=model,
        provider_schema=schema,
        expected_shape=expected_shape,
        handler=handler,
        recovery_declaration=declaration,
    )


async def _captured_schema(tool):
    completions = _Completions()
    provider = OpenAICompatibleProvider("https://provider.invalid", "credential-sentinel")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    events = [
        event
        async for event in provider.stream(
            ModelRef(provider_id="p", model_id="m"),
            [UserMessage(content="inspect")],
            (tool.definition,),
        )
    ]
    assert events[-1].kind == "completed"
    assert completions.kwargs is not None
    assert "credential-sentinel" not in json.dumps(completions.kwargs, ensure_ascii=False)
    assert completions.kwargs["tools"] == [serialize_tool(tool.definition)]
    return completions.kwargs["tools"][0]["function"]["parameters"]


async def _captured_tools(definitions):
    completions = _Completions()
    provider = OpenAICompatibleProvider("https://provider.invalid", "credential-sentinel")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    events = [
        event
        async for event in provider.stream(
            ModelRef(provider_id="p", model_id="m"),
            [UserMessage(content="audit")],
            tuple(definitions),
        )
    ]
    assert events[-1].kind == "completed"
    assert completions.kwargs is not None
    assert "credential-sentinel" not in json.dumps(completions.kwargs, ensure_ascii=False)
    return completions.kwargs["tools"]


def _valid_inventory_arguments(name):
    return {
        "apply_patch": {
            "path": "file.txt",
            "expected_sha256": "0" * 64,
            "edits": [{"old_text": "a", "new_text": "b"}],
        },
        "find_files": {"pattern": "*.py"},
        "git_diff": {},
        "git_status": {},
        "list_directory": {},
        "read_file": {"path": "README.md"},
        "run_command": {"argv": ["echo"]},
        "run_skill_script": {
            "selection_id": "ssel_selection",
            "skill_id": "demo",
            "version_id": "skv_12345678",
            "tree_digest": "0" * 64,
            "script_path": "scripts/check.py",
        },
        "search_text": {"query": "needle"},
        "show_changes": {},
        "update_configuration": {
            "scope": "workspace",
            "target": "profile",
            "operation": "set",
            "path": "summary",
            "value": "x",
        },
        "write_file": {"path": "new.txt", "content": "x", "mode": "create"},
        "promote_sandbox_changes": {
            "change_set_id": "sbx_" + "0" * 24,
            "paths": ["out.txt"],
        },
    }[name]


@pytest.mark.asyncio
async def test_actual_provider_wire_exposes_run_command_shapes_runtime_rejects():
    tool = _tool("run_command", RunCommandArguments)
    schema = await _captured_schema(tool)
    wire_validator = JsonSchemaArgumentsValidator(schema)
    runtime_validator = PydanticArgumentsValidator(
        RunCommandArguments,
        provider_schema=RUN_COMMAND_PROVIDER_SCHEMA,
        expected_shape="exactly_one_of:argv,shell",
    )

    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate("{}")
    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate('{"argv":["pwd"],"shell":"pwd"}')
    with pytest.raises(ToolArgumentsValidationError):
        runtime_validator.validate("{}")
    with pytest.raises(ToolArgumentsValidationError):
        runtime_validator.validate('{"argv":["pwd"],"shell":"pwd"}')
    assert runtime_validator.validate('{"argv":["pwd"]}').argv == ("pwd",)
    assert runtime_validator.validate('{"shell":"pwd"}').shell == "pwd"
    with pytest.raises(ToolArgumentsValidationError) as invalid:
        runtime_validator.validate("{}")
    assert invalid.value.expected == "exactly_one_of:argv,shell"


def test_provider_wire_exposes_write_file_revision_branch_runtime_rejects():
    tool = _tool("write_file", WriteFileArguments)
    schema = serialize_tool(tool.definition)["function"]["parameters"]
    runtime_validator = PydanticArgumentsValidator(
        WriteFileArguments,
        provider_schema=WRITE_FILE_PROVIDER_SCHEMA,
        expected_shape="write_file_mode_revision",
    )

    create_with_revision = {
        "path": "new.txt",
        "content": "new",
        "mode": "create",
        "expected_sha256": "0" * 64,
    }
    replace_without_revision = {"path": "new.txt", "content": "new", "mode": "replace"}
    wire_validator = JsonSchemaArgumentsValidator(schema)
    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate(json.dumps(create_with_revision))
    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate(json.dumps(replace_without_revision))
    with pytest.raises(ToolArgumentsValidationError):
        runtime_validator.validate(json.dumps(create_with_revision))
    with pytest.raises(ToolArgumentsValidationError):
        runtime_validator.validate(json.dumps(replace_without_revision))
    assert (
        runtime_validator.validate(
            json.dumps({"path": "new.txt", "content": "new", "mode": "create"})
        ).mode.value
        == "create"
    )
    assert (
        runtime_validator.validate(
            json.dumps(
                {
                    "path": "new.txt",
                    "content": "new",
                    "mode": "replace",
                    "expected_sha256": "0" * 64,
                }
            )
        ).mode.value
        == "replace"
    )


def test_provider_wire_advertises_content_larger_than_raw_argument_budget():
    tool = _tool("write_file", WriteFileArguments)
    schema = serialize_tool(tool.definition)["function"]["parameters"]
    content_schema = schema["properties"]["content"]
    raw = json.dumps({"path": "new.txt", "content": "x" * (MAX_STRING_CHARS + 1), "mode": "create"})

    assert content_schema["maxLength"] <= MAX_STRING_CHARS
    wire_validator = JsonSchemaArgumentsValidator(schema)
    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate(raw)
    with pytest.raises(ToolArgumentsValidationError) as invalid:
        PydanticArgumentsValidator(
            WriteFileArguments, provider_schema=WRITE_FILE_PROVIDER_SCHEMA
        ).validate(raw)
    assert invalid.value.code == "budget"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission_profile", [None, PermissionProfile.from_preset(PermissionPreset.AUTO_SANDBOXED)]
)
async def test_static_direct_inventory_audit_matches_captured_openai_wire(
    tmp_path, permission_profile
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
        permission_profile=permission_profile,
    )
    executor = session_app.orchestrator.runtime.loop.tool_executor
    definitions = executor.definitions
    captured = await _captured_tools(definitions)

    assert executor.audit
    assert {item.tool_name for item in executor.audit} == {
        definition.function.name for definition in definitions
    }
    assert len(captured) == len(definitions)
    for definition, wire, audit in zip(definitions, captured, executor.audit, strict=True):
        assert wire == serialize_tool(definition)
        schema = JsonSchemaArgumentsValidator(definition.function.parameters)
        assert schema.schema == definition.function.parameters
        assert audit.tool_name == definition.function.name
        assert audit.schema_digest == schema.schema_digest
        assert len(audit.wire_digest) == 64
        registered = executor.tool_set.tools[definition.function.name]
        valid = _valid_inventory_arguments(definition.function.name)
        raw = json.dumps(valid)
        assert schema.validate(raw) == valid
        registered.arguments_validator.validate(raw)
        invalid = {**valid, "unexpected_contract_field": True}
        with pytest.raises(ToolArgumentsValidationError):
            schema.validate(json.dumps(invalid))
        with pytest.raises(ToolArgumentsValidationError):
            registered.arguments_validator.validate(json.dumps(invalid))


def test_tool_contract_audit_fails_closed_on_definition_drift_and_open_schema():
    async def handler(_arguments):
        return {"ok": True}

    tool = make_tool(
        name="run_command",
        description="audit",
        arguments_model=RunCommandArguments,
        provider_schema=RUN_COMMAND_PROVIDER_SCHEMA,
        expected_shape="exactly_one_of:argv,shell",
        handler=handler,
        recovery_declaration=tool_declaration(
            "run_command", process_isolation=ProcessIsolation.HOST
        ),
    )
    tool.definition.function.parameters["drift"] = {"type": "string"}
    with pytest.raises(ToolContractAuditError):
        audit_registered_tool(tool)

    open_schema_tool = make_tool(
        name="open_schema",
        description="open",
        arguments_validator=JsonSchemaArgumentsValidator({"type": "object"}),
        handler=handler,
    )
    with pytest.raises(ToolContractAuditError):
        audit_registered_tool(open_schema_tool, require_closed_schema=True)


def test_model_schema_helper_returns_normalized_budgeted_schema():
    schema = tool_parameters_from_model(WriteFileArguments)
    assert schema["$schema"]
    assert schema["properties"]["content"]["maxLength"] == MAX_STRING_CHARS


@pytest.mark.asyncio
async def test_error_code_matrix_keeps_policy_target_search_and_handler_failures_distinct(
    tmp_path,
):
    from pydantic import BaseModel, ConfigDict

    from morrow.services.files import LocalFileError

    class EmptyArguments(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def mapped(error):
        raise _tool_error(error)

    registry = ToolRegistry()
    for name, error in (
        ("invalid_target", LocalFileError("invalid_target", "target")),
        ("missing_target", LocalFileError("not_found", "missing")),
        ("search_backend", LocalFileError("search_failed", "backend")),
    ):

        async def handler(_arguments, error=error):
            await mapped(error)

        registry.register(
            make_tool(
                name=name,
                description=name,
                arguments_model=EmptyArguments,
                handler=handler,
            )
        )

    async def execution_failure(_arguments):
        raise ToolExecutionError(ToolErrorCode.EXECUTION_FAILED, "handler failed")

    registry.register(
        make_tool(
            name="handler_failure",
            description="handler_failure",
            arguments_model=EmptyArguments,
            handler=execution_failure,
        )
    )

    async def denied_handler(_arguments):
        raise AssertionError("policy denied handler must not run")

    def intent(_arguments, _context):
        return OperationIntent(kind=OperationKind.INTERNAL_READ, effect=ToolEffect.NONE)

    def deny(_intent, _context, _allow_unconfined_host):
        return PolicyDecision(
            verdict=PolicyVerdict.DENY,
            reason_codes=("contract_test_denied",),
        )

    policy_registry = ToolRegistry()
    policy_registry.register(
        make_tool(
            name="policy_denied",
            description="policy_denied",
            arguments_model=EmptyArguments,
            handler=denied_handler,
            intent_resolver=intent,
            policy_resolver=deny,
        )
    )
    executor = ToolExecutor(registry.snapshot(), make_run_policy())
    policy_executor = ToolExecutor(
        policy_registry.snapshot(),
        make_run_policy(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(), WorkspaceCapability(workspace_id="w1", root=tmp_path)
        ),
    )

    def call(name):
        return FunctionToolCall(id=f"call-{name}", name=name, arguments="{}")

    outcomes = {
        name: await executor.execute(call(name))
        for name in (
            "invalid_target",
            "missing_target",
            "search_backend",
            "handler_failure",
        )
    }
    policy_outcome = await policy_executor.execute(call("policy_denied"))
    assert policy_outcome.error_code is ToolErrorCode.PERMISSION_DENIED
    assert outcomes["invalid_target"].error_code is ToolErrorCode.INVALID_TARGET
    assert outcomes["missing_target"].error_code is ToolErrorCode.NOT_FOUND
    assert outcomes["search_backend"].error_code is ToolErrorCode.SEARCH_FAILED
    assert outcomes["handler_failure"].error_code is ToolErrorCode.EXECUTION_FAILED
    assert {json.loads(outcome.envelope)["error"]["code"] for outcome in outcomes.values()} == {
        "invalid_target",
        "not_found",
        "search_failed",
        "execution_failed",
    }
