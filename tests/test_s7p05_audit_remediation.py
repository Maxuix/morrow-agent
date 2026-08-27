"""Regressions retained after removing the runtime outcome gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.local_tools import make_apply_patch_tool
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    PermissionProfile,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    SystemMessage,
    ToolApprovalDecision,
)
from morrow.runtime.agent import _AgentRunState, _prompt_refresh_targets
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.testing import ScriptedModelProvider, make_run_policy


def _call(call_id: str, name: str, arguments: dict) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


class _Approval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=True)


class _RejectApproval:
    async def request(self, request):
        del request
        return ToolApprovalDecision(approved=False)


def test_prompt_refresh_keeps_all_recent_unique_directories_for_batched_resolution():
    state = _AgentRunState(
        turn_id="turn-1",
        run_context=ToolRunContext(run_id="run-1", session_id="session-1"),
        deadline=0,
    )
    state.touched_paths = [f"package-{index}/module.py" for index in range(12)]

    assert _prompt_refresh_targets(state) == tuple(
        f"package-{index}/module.py" for index in range(12)
    )


@pytest.mark.asyncio
async def test_production_model_stop_is_accepted_after_a_failed_change(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "answer.txt"
    target.write_text("old\n", encoding="utf-8")
    provider = ScriptedModelProvider(
        (
            AssistantMessage(
                tool_calls=(
                    _call(
                        "edit-failed",
                        "apply_patch",
                        {
                            "path": "answer.txt",
                            "expected_sha256": "0" * 64,
                            "edits": [{"old_text": "old", "new_text": "new"}],
                        },
                    ),
                )
            ),
            AssistantMessage(content="I am stopping without a successful change."),
        )
    )
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=_Approval(),
    )

    result = await session_app.orchestrator.dispatch("修复 answer.txt")

    assert result.events[-1].type == "turn.completed"
    assert result.events[-1].payload["finish_reason"] == "stop"
    assert target.read_text(encoding="utf-8") == "old\n"
    run = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )[0]
    observation = session_app.persistence.get_agent_run_observation(run.agent_run_id)
    assert observation is not None
    assert [request.purpose.value for request in observation.requests] == ["agent", "agent"]


@pytest.mark.asyncio
async def test_dynamic_nested_agents_instructions_loaded_on_touch(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Root Rules\n- Always be concise\n", encoding="utf-8")
    sub = project / "services" / "auth"
    sub.mkdir(parents=True)
    (sub / "AGENTS.md").write_text("# Auth Rules\n- Use bcrypt only\n", encoding="utf-8")
    (sub / "handler.py").write_text("def login(): pass\n", encoding="utf-8")
    provider = ScriptedModelProvider(
        (
            AssistantMessage(
                tool_calls=(_call("read", "read_file", {"path": "services/auth/handler.py"}),)
            ),
            AssistantMessage(content="Auth handler verified."),
        )
    )
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=_Approval(),
    )

    [event async for event in session_app.orchestrator.stream("检查认证模块")]

    assert len(provider.stream_calls) == 2
    second_system_text = "\n".join(
        message.content
        for message in provider.stream_calls[1]
        if isinstance(message, SystemMessage) and message.content
    )
    assert "Root Rules" in second_system_text
    assert "Use bcrypt only" in second_system_text
    run = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )[0]
    assert {source.path for source in run.snapshot.project_instruction_sources} == {"AGENTS.md"}
    observation = session_app.persistence.get_agent_run_observation(run.agent_run_id)
    assert observation is not None
    assert "services/auth/AGENTS.md" in {
        source.path
        for source in observation.requests[-1].prompt_evidence.project_instruction_sources
    }


@pytest.mark.asyncio
async def test_first_nested_write_is_deferred_until_scope_rules_are_loaded(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    nested = project / "pkg"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("# Nested Rule\n- Preserve the API\n", encoding="utf-8")
    target = nested / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    write_call = _call(
        "first-write",
        "apply_patch",
        {
            "path": "pkg/module.py",
            "expected_sha256": digest,
            "edits": [{"old_text": "1", "new_text": "2"}],
        },
    )
    provider = ScriptedModelProvider(
        (
            AssistantMessage(tool_calls=(write_call,)),
            AssistantMessage(tool_calls=(write_call.model_copy(update={"id": "retry-write"}),)),
            AssistantMessage(content="已按嵌套规则完成。"),
        )
    )
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    approval = _Approval()
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    [item async for item in session_app.orchestrator.stream("修改模块")]

    tool_messages = [message for message in session_app.session.messages if message.role == "tool"]
    assert json.loads(tool_messages[0].content)["error"]["code"] == "preflight_failed"
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert len(approval.requests) == 1
    second_prompt = "\n".join(
        message.content or ""
        for message in provider.stream_calls[1]
        if isinstance(message, SystemMessage)
    )
    assert "Preserve the API" in second_prompt


@pytest.mark.asyncio
async def test_persisted_request_size_matches_the_actual_dynamic_prompt(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    nested = project / "pkg"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("# Nested\n", encoding="utf-8")
    (nested / "module.py").write_text("value = 1\n", encoding="utf-8")
    provider = ScriptedModelProvider(
        (
            AssistantMessage(tool_calls=(_call("read", "read_file", {"path": "pkg/module.py"}),)),
            AssistantMessage(content="done"),
        )
    )
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    [item async for item in session_app.orchestrator.stream("inspect")]

    run = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )[0]
    observation = session_app.persistence.get_agent_run_observation(run.agent_run_id)
    assert observation is not None
    expected = session_app.context_builder.validate_request(
        provider.stream_calls[1], provider.stream_tools[1]
    )
    assert observation.requests[1].estimated_request_chars == expected


@pytest.mark.asyncio
async def test_tool_lifecycle_cleans_preview_when_approval_is_rejected(tmp_path: Path):
    target = tmp_path / "hello.txt"
    target.write_text("hello world\n", encoding="utf-8")
    expected_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    mutations = WorkspaceMutationService(files)
    registry = ToolRegistry()
    registry.register(make_apply_patch_tool(mutations, ChangeSetService()))
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=_RejectApproval(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="workspace-1", root=tmp_path),
        ),
    )
    run = ToolRunContext(run_id="run-100", session_id="session-1")

    outcome = await executor.execute_with_context(
        _call(
            "call-1",
            "apply_patch",
            {
                "path": "hello.txt",
                "expected_sha256": expected_sha256,
                "edits": [{"old_text": "world", "new_text": "morrow"}],
            },
        ),
        run_context=run,
        ordinal=1,
        total=1,
    )

    assert outcome.error_code.value == "approval_rejected"
    assert mutations.cached_plan("run-100", "call-1") is None
