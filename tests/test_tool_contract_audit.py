from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import (
    OpenAICompatibleProvider,
    normalize_tool_schema,
    serialize_tool,
)
from morrow.application.configuration import CONFIGURATION_PROVIDER_SCHEMA
from morrow.application.local_tools import (
    BASH_PROVIDER_SCHEMA,
    PROMOTE_SANDBOX_PROVIDER_SCHEMA,
    WRITE_PROVIDER_SCHEMA,
    BashArguments,
    WriteArguments,
    _tool_error,
)
from morrow.application.preferences.tool import (
    PREFERENCE_MANAGEMENT_PROVIDER_SCHEMA,
    make_preference_management_tool,
)
from morrow.application.skills.scripts import SKILL_SCRIPT_PROVIDER_SCHEMA
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
from morrow.runtime.policy import ToolApproval, ToolExecutionPolicy
from morrow.runtime.tool_arguments import (
    MAX_ARGUMENT_BYTES,
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
        "bash": BASH_PROVIDER_SCHEMA,
        "write": WRITE_PROVIDER_SCHEMA,
    }[name]
    declaration = (
        tool_declaration(name, process_isolation=ProcessIsolation.HOST)
        if name == "bash"
        else tool_declaration(name)
    )
    return make_tool(
        name=name,
        description=name,
        arguments_model=model,
        provider_schema=schema,
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
        "bash": {"command": "echo"},
        "edit": {"path": "file.txt", "edits": [{"oldText": "a", "newText": "b"}]},
        "find": {"pattern": "*.py"},
        "grep": {"pattern": "needle"},
        "ls": {},
        "read": {"path": "README.md"},
        "read_artifact": {"artifact_id": "art_12345678"},
        "write": {"path": "new.txt", "content": "x"},
        "run_skill_script": {
            "selection_id": "ssel_selection",
            "skill_id": "demo",
            "version_id": "skv_12345678",
            "tree_digest": "0" * 64,
            "script_path": "scripts/check.py",
        },
        "update_configuration": {
            "scope": "workspace",
            "target": "profile",
            "operation": "set",
            "path": "summary",
            "value": "x",
        },
        "manage_preferences": {
            "scope": "workspace",
            "operations": [{"operation": "add", "statement": "x"}],
        },
        "promote_sandbox_changes": {
            "change_set_id": "sbx_" + "0" * 24,
            "paths": ["out.txt"],
        },
    }[name]


@pytest.mark.asyncio
async def test_actual_provider_wire_exposes_simple_bash_shape_runtime_accepts_extras():
    tool = _tool("bash", BashArguments)
    schema = await _captured_schema(tool)
    wire_validator = JsonSchemaArgumentsValidator(schema)
    runtime_validator = PydanticArgumentsValidator(
        BashArguments,
        provider_schema=BASH_PROVIDER_SCHEMA,
    )

    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate("{}")
    with pytest.raises(ToolArgumentsValidationError):
        runtime_validator.validate("{}")
    with pytest.raises(ToolArgumentsValidationError):
        runtime_validator.validate('{"command":["pwd"]}')
    assert wire_validator.validate('{"command":"pwd","unused":true}') == {
        "command": "pwd",
        "unused": True,
    }
    parsed = runtime_validator.validate('{"command":"pwd","unused":true}')
    assert parsed.command == "pwd"
    assert "unused" not in parsed.model_dump()


def test_provider_wire_exposes_simple_write_shape_without_revision_protocol():
    tool = _tool("write", WriteArguments)
    schema = serialize_tool(tool.definition)["function"]["parameters"]
    runtime_validator = PydanticArgumentsValidator(
        WriteArguments,
        provider_schema=WRITE_PROVIDER_SCHEMA,
    )
    wire_validator = JsonSchemaArgumentsValidator(schema)
    with pytest.raises(ToolArgumentsValidationError):
        wire_validator.validate('{"path":"new.txt"}')
    parsed = runtime_validator.validate(
        '{"path":"new.txt","content":"new","mode":"replace","expected_sha256":"ignored"}'
    )
    assert parsed.path == "new.txt"
    assert parsed.content == "new"
    assert parsed.model_dump() == {"path": "new.txt", "content": "new"}


@pytest.mark.parametrize(
    ("schema", "value"),
    [
        (
            PROMOTE_SANDBOX_PROVIDER_SCHEMA,
            {"change_set_id": "sbx_" + "0" * 24, "paths": ["out.txt", "out.txt"]},
        ),
        (
            SKILL_SCRIPT_PROVIDER_SCHEMA,
            {
                "selection_id": "ssel_selection",
                "skill_id": "demo",
                "version_id": "skv_12345678",
                "tree_digest": "0" * 64,
                "script_path": "scripts/check.py",
                "environment_names": ["HOME"],
            },
        ),
        (
            SKILL_SCRIPT_PROVIDER_SCHEMA,
            {
                "selection_id": "ssel_" + "x" * 127,
                "skill_id": "demo",
                "version_id": "skv_12345678",
                "tree_digest": "0" * 64,
                "script_path": "scripts/check.py",
            },
        ),
        (
            SKILL_SCRIPT_PROVIDER_SCHEMA,
            {
                "selection_id": "ssel_selection",
                "skill_id": "demo",
                "version_id": "skv_12345678",
                "tree_digest": "0" * 64,
                "script_path": "scripts/check.py",
                "output_paths": ["out", "out/file"],
            },
        ),
        (
            SKILL_SCRIPT_PROVIDER_SCHEMA,
            {
                "selection_id": "ssel_selection",
                "skill_id": "demo",
                "version_id": "skv_12345678",
                "tree_digest": "0" * 64,
                "script_path": "scripts/cafe\u0301.py",
            },
        ),
        (
            CONFIGURATION_PROVIDER_SCHEMA,
            {
                "scope": "workspace",
                "target": "profile",
                "operation": "set",
                "path": "summary",
                "value": "   ",
            },
        ),
        (
            PREFERENCE_MANAGEMENT_PROVIDER_SCHEMA,
            {"scope": "workspace", "operations": [{"operation": "add", "statement": "   "}]},
        ),
        (
            PREFERENCE_MANAGEMENT_PROVIDER_SCHEMA,
            {
                "scope": "workspace",
                "operations": [{"operation": "add", "statement": "keep\u200b"}],
            },
        ),
        (
            PREFERENCE_MANAGEMENT_PROVIDER_SCHEMA,
            {
                "scope": "workspace",
                "operations": [
                    {
                        "operation": "replace",
                        "preference_id": "pref_x",
                        "statement": "keep",
                        "evidence_ids": ["pev_" + "x" * 127],
                    }
                ],
            },
        ),
    ],
)
def test_provider_schema_rejects_runtime_contract_mismatches(schema, value):
    with pytest.raises(ToolArgumentsValidationError):
        JsonSchemaArgumentsValidator(schema).validate(json.dumps(value, ensure_ascii=False))


def test_provider_schema_bounds_are_conservative_for_raw_argument_budget():
    def raw_size(value):
        return tuple(
            len(json.dumps(value, ensure_ascii=ensure_ascii, separators=(",", ":")).encode("utf-8"))
            for ensure_ascii in (False, True)
        )

    skill_argv_max = SKILL_SCRIPT_PROVIDER_SCHEMA["properties"]["argv"]["items"]["maxLength"]
    assert 16 * skill_argv_max * 4 <= 16 * 1024
    skill = {
        "selection_id": "ssel_selection",
        "skill_id": "demo",
        "version_id": "skv_12345678",
        "tree_digest": "0" * 64,
        "script_path": "scripts/check.py",
        "argv": ["🧪" * skill_argv_max for _ in range(16)],
        "output_paths": ["🧪" * 512],
    }
    assert all(size <= MAX_ARGUMENT_BYTES for size in raw_size(skill))

    promote = {
        "change_set_id": "sbx_" + "0" * 24,
        "paths": ["🧪" * 512 for _ in range(16)],
    }
    assert all(size <= MAX_ARGUMENT_BYTES for size in raw_size(promote))


def test_preference_tool_is_included_in_the_direct_provider_contract_inventory():
    tool = make_preference_management_tool(SimpleNamespace())
    wire = serialize_tool(tool.definition)
    assert wire["function"]["parameters"] == normalize_tool_schema(
        PREFERENCE_MANAGEMENT_PROVIDER_SCHEMA
    )
    validator = JsonSchemaArgumentsValidator(wire["function"]["parameters"])
    valid = {"scope": "workspace", "operations": [{"operation": "add", "statement": "keep"}]}
    assert validator.validate(json.dumps(valid)) == valid
    audit_registered_tool(
        tool,
        require_runtime_contract=True,
        require_closed_schema=True,
        require_production_declaration=True,
        expected_process_isolation=ProcessIsolation.HOST,
    )


def test_contract_audit_uses_independent_run_isolation_and_policy_expectations():
    async def handler(_arguments):
        return {"ok": True}

    def intent(_arguments, _context):
        return OperationIntent(kind=OperationKind.PROCESS, effect=ToolEffect.NONE)

    wrong_isolation = make_tool(
        name="bash",
        description="audit",
        arguments_model=BashArguments,
        provider_schema=BASH_PROVIDER_SCHEMA,
        handler=handler,
        intent_resolver=intent,
        recovery_declaration=tool_declaration(
            "bash", process_isolation=ProcessIsolation.NATIVE_SANDBOX
        ),
    )
    with pytest.raises(ToolContractAuditError):
        audit_registered_tool(
            wrong_isolation,
            require_runtime_contract=True,
            require_production_declaration=True,
            expected_process_isolation=ProcessIsolation.HOST,
        )

    wrong_policy = replace(
        _tool("write", WriteArguments),
        execution_policy=ToolExecutionPolicy(
            effect=ToolEffect.SESSION_WRITE, approval=ToolApproval.REQUIRED
        ),
    )
    with pytest.raises(ToolContractAuditError):
        audit_registered_tool(
            wrong_policy,
            require_runtime_contract=True,
            require_production_declaration=True,
            expected_process_isolation=ProcessIsolation.HOST,
        )


@pytest.mark.asyncio
async def test_static_contract_rejects_capability_intent_drift_before_handler(tmp_path):
    class EmptyArguments(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

    called = False

    async def handler(_arguments):
        nonlocal called
        called = True
        return {"ok": True}

    def wrong_intent(_arguments, _context):
        return OperationIntent(kind=OperationKind.PROCESS, effect=ToolEffect.NONE)

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="read",
            description="read",
            arguments_model=EmptyArguments,
            handler=handler,
            intent_resolver=wrong_intent,
        )
    )
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(), WorkspaceCapability(workspace_id="w1", root=tmp_path)
        ),
    )
    outcome = await executor.execute(
        FunctionToolCall(id="call-contract", name="read", arguments="{}")
    )
    assert outcome.error_code is ToolErrorCode.PREFLIGHT_FAILED
    assert called is False


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
        if definition.function.parameters.get("additionalProperties") is False:
            with pytest.raises(ToolArgumentsValidationError):
                schema.validate(json.dumps(invalid))
            with pytest.raises(ToolArgumentsValidationError):
                registered.arguments_validator.validate(json.dumps(invalid))
        else:
            assert schema.validate(json.dumps(invalid)) == invalid
            assert (
                "unexpected_contract_field"
                not in registered.arguments_validator.validate(json.dumps(invalid)).model_dump()
            )


def test_tool_contract_audit_fails_closed_on_definition_drift_and_open_schema():
    async def handler(_arguments):
        return {"ok": True}

    tool = make_tool(
        name="bash",
        description="audit",
        arguments_model=BashArguments,
        provider_schema=BASH_PROVIDER_SCHEMA,
        handler=handler,
        recovery_declaration=tool_declaration("bash", process_isolation=ProcessIsolation.HOST),
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
    class BoundedArguments(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        content: str = Field(max_length=MAX_STRING_CHARS)

    schema = tool_parameters_from_model(BoundedArguments)
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
