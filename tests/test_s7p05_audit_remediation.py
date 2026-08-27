"""Targeted regression tests for S7P-00~S7P-05 audit findings remediation."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.local_tools import make_apply_patch_tool
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    ChangeToolFact,
    PermissionProfile,
    PolicyVerdict,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.completion import (
    CompletionOutcome,
    OutcomeContract,
    OutcomeMode,
    ValidationRequirement,
    WorkspaceBaseline,
    WorkspaceBaselineEntry,
    WorkspaceBaselineStatus,
)
from morrow.core.domain import AGENT_RUN_SNAPSHOT_MAX_BYTES, AgentRunSnapshot, canonical_json_bytes
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    SystemMessage,
    ToolApprovalDecision,
)
from morrow.runtime.agent import (
    AgentLoop,
    _AgentRunState,
    _prompt_refresh_targets,
    _tool_obligation_keys,
)
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.outcome_intent import OutcomeIntentResolver
from morrow.runtime.session import Session
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.completion import CompletionChecker, WorkspaceBaselineService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.testing import ScriptedModelProvider, make_context_builder, make_run_policy


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


def _commit_all(root: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=root, check=True)
    subprocess.run(("git", "add", "."), cwd=root, check=True)
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Morrow Tests",
            "-c",
            "user.email=morrow@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ),
        cwd=root,
        check=True,
    )


# ==============================================================================
# P0: Workspace Baseline Scale & Decoupled Truncation
# ==============================================================================


def test_git_baseline_represents_large_clean_workspace_without_manifest_growth(tmp_path: Path):
    """A large clean Git tree is proven by Git state, not a larger snapshot manifest."""
    for dir_idx in range(30):
        sub_dir = tmp_path / f"pkg_{dir_idx}"
        sub_dir.mkdir()
        for file_idx in range(10):
            (sub_dir / f"module_{file_idx}.py").write_text("# code\n", encoding="utf-8")
    _commit_all(tmp_path)

    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()

    assert baseline.status is WorkspaceBaselineStatus.COMPLETE
    assert baseline.repository_state == "git"
    assert baseline.entries == ()
    assert baseline.directory_paths == ()
    assert baseline.reason_code is None


def test_truncated_baseline_blocks_attributed_task_completion(tmp_path: Path):
    """An incomplete baseline cannot prove completion even for an attributed change."""
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()

    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    target.write_text("after\n", encoding="utf-8")
    after_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()

    # Simulate a truncated baseline from an enormous workspace
    baseline = WorkspaceBaseline(
        status=WorkspaceBaselineStatus.TRUNCATED,
        entries=(
            WorkspaceBaselineEntry(
                path="app.py",
                kind="file",
                sha256=before_sha256,
                size=7,
            ),
        ),
        directory_paths=(),
        repository_state="filesystem",
        reason_code="baseline_scan_limit",
    )

    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            ChangeToolFact(
                call_id="edit",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("app.py",),
                operation="patch",
                status="modified",
                before_revision=before_sha256,
                after_revision=after_sha256,
                changed_lines=1,
                changed_bytes=6,
            ),
        )
    )

    contract = OutcomeContract(mode="change", target_paths=("app.py",))
    checker = CompletionChecker(files)
    result = checker.check(contract, baseline, run_context=run)

    assert result.outcome is CompletionOutcome.INCONCLUSIVE
    assert "baseline_inconclusive" in result.reason_codes
    assert "workspace_scan_inconclusive" not in result.reason_codes


# ==============================================================================
# P1-1: OutcomeContract Natural Language Inference Robustness
# ==============================================================================


@pytest.mark.asyncio
async def test_outcome_contract_uses_strict_semantic_model_intent():
    provider = ScriptedModelProvider(
        intent_responses=(
            '{"mode":"explanation","certainty":"clear","target_paths":[],'
            '"allowed_paths":null,"forbidden_paths":[],"required_validations":[],'
            '"no_change_allowed":true}',
            '{"mode":"change","certainty":"clear","target_paths":[],'
            '"allowed_paths":null,"forbidden_paths":[],'
            '"required_validations":[{"validator_kind":"cargo_test","scope":"."}],'
            '"no_change_allowed":false}',
        )
    )
    resolver = OutcomeIntentResolver(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
    )

    c1 = await resolver.resolve("请审计之前的修复，不要修改代码")
    assert c1.mode == OutcomeMode.EXPLANATION
    assert not c1.requires_net_change
    assert c1.no_change_allowed is True

    c2 = await resolver.resolve("请分析并修复这个 bug，然后运行 cargo test")
    assert c2.mode == OutcomeMode.CHANGE
    assert c2.target_paths == ()
    assert c2.allowed_paths is None
    assert c2.required_validations == (
        ValidationRequirement(validator_kind="cargo_test", scope="."),
    )
    assert len(provider.intent_calls) == 2


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


def test_failure_obligations_are_family_wide_and_independently_path_scoped():
    executor = ToolExecutor(ToolRegistry().snapshot(), make_run_policy())

    assert _tool_obligation_keys(
        _call(
            "move",
            "move_path",
            {"source_path": "src/a.py", "destination_path": "src/b.py"},
        ),
        executor,
    ) == (
        ("move_path", ()),
        ("move_path", ("src/a.py",)),
        ("move_path", ("src/b.py",)),
    )
    assert _tool_obligation_keys(
        _call("invalid", "read_file", {"path": "../outside.py"}), executor
    ) == (("read_file", ()),)


# ==============================================================================
# P1-2: Recovered Tool Errors Do Not Poison Completion
# ==============================================================================


@pytest.mark.asyncio
async def test_agent_loop_recovers_from_transient_tool_error(tmp_path: Path):
    """Tool failure in turn 1 corrected in turn 2 allows task to complete PASSED."""
    project = tmp_path / "project"
    project.mkdir()
    target = project / "answer.txt"
    target.write_text("old\n", encoding="utf-8")
    before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()

    provider = ScriptedModelProvider(
        [
            # Turn 1: Model calls patch with invalid revision
            AssistantMessage(
                tool_calls=(
                    _call(
                        "edit_fail",
                        "apply_patch",
                        {
                            "path": "answer.txt",
                            "expected_sha256": "0" * 64,  # wrong revision -> failure
                            "edits": [{"old_text": "old", "new_text": "new"}],
                        },
                    ),
                )
            ),
            # Turn 2: Model sees error and corrects arguments
            AssistantMessage(
                tool_calls=(
                    _call(
                        "edit_ok",
                        "apply_patch",
                        {
                            "path": "answer.txt",
                            "expected_sha256": before_sha256,
                            "edits": [{"old_text": "old", "new_text": "new"}],
                        },
                    ),
                )
            ),
            # Turn 3: Final confirmation
            AssistantMessage(content="已成功修复 answer.txt。"),
        ]
    )

    approval = _Approval()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    items = [item async for item in session_app.orchestrator.stream("修复 `answer.txt`")]
    events = [item for item in items if getattr(item, "type", None) is not None]

    assert target.read_text(encoding="utf-8") == "new\n"
    assert session_app.session.latest_completion_check is not None
    assert session_app.session.latest_completion_check.outcome is CompletionOutcome.PASSED
    assert "known_failure" not in session_app.session.latest_completion_check.reason_codes
    assert events[-1].type == "turn.completed"
    assert events[-1].payload["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_unrelated_success_does_not_clear_failed_change_obligation(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "answer.txt"
    target.write_text("old\n", encoding="utf-8")
    (project / "notes.txt").write_text("context\n", encoding="utf-8")
    provider = ScriptedModelProvider(
        [
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
            AssistantMessage(tool_calls=(_call("read-ok", "read_file", {"path": "notes.txt"}),)),
            AssistantMessage(content="已完成。"),
        ]
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

    [item async for item in session_app.orchestrator.stream("修复 answer.txt")]

    completion = session_app.session.latest_completion_check
    assert completion is not None
    assert "known_failure" in completion.reason_codes
    assert "conflict" in completion.known_failure_codes
    assert target.read_text(encoding="utf-8") == "old\n"


# ==============================================================================
# P1-3: Dynamic Nested AGENTS.md Scope Discovery
# ==============================================================================


@pytest.mark.asyncio
async def test_dynamic_nested_agents_instructions_loaded_on_touch(tmp_path: Path):
    """When agent touches a subdirectory, its nested AGENTS.md instructions are loaded."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Root Rules\n- Always be concise\n", encoding="utf-8")
    sub = project / "services" / "auth"
    sub.mkdir(parents=True)
    (sub / "AGENTS.md").write_text("# Auth Rules\n- Use bcrypt only\n", encoding="utf-8")
    (sub / "handler.py").write_text("def login(): pass\n", encoding="utf-8")

    provider = ScriptedModelProvider(
        [
            # Turn 1: Inspect handler in subpackage
            AssistantMessage(
                tool_calls=(
                    _call(
                        "read",
                        "read_file",
                        {"path": "services/auth/handler.py"},
                    ),
                )
            ),
            # Turn 2: Finish
            AssistantMessage(content="Auth handler verified."),
        ]
    )

    approval = _Approval()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    events = [event async for event in session_app.orchestrator.stream("检查认证模块")]
    assert len(events) > 0

    # Turn 2 model call should receive prompt containing nested Auth Rules
    assert len(provider.stream_calls) == 2
    turn_2_messages = provider.stream_calls[1]
    turn_2_system_text = "\n".join(
        msg.content for msg in turn_2_messages if isinstance(msg, SystemMessage) and msg.content
    )

    assert "Root Rules" in turn_2_system_text
    assert "Auth Rules" in turn_2_system_text
    assert "Use bcrypt only" in turn_2_system_text
    run = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )[0]
    assert {source.path for source in run.snapshot.project_instruction_sources} == {"AGENTS.md"}
    observation = session_app.persistence.get_agent_run_observation(run.agent_run_id)
    agent_requests = [item for item in observation.requests if item.purpose.value == "agent"]
    assert "services/auth/AGENTS.md" in {
        source.path for source in agent_requests[-1].prompt_evidence.project_instruction_sources
    }
    assert observation.requests[0].resolved_outcome_contract is not None


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
    retry_call = write_call.model_copy(update={"id": "retry-write"})
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(write_call,)),
            AssistantMessage(tool_calls=(retry_call,)),
            AssistantMessage(content="已按嵌套规则完成。"),
        ]
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


def test_baseline_at_persistable_entry_cap_fits_agent_run_snapshot(tmp_path: Path):
    for index in range(4096):
        (tmp_path / f"file-{index:04d}.py").write_text("", encoding="utf-8")
    _commit_all(tmp_path)
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))

    baseline = WorkspaceBaselineService(files).prepare()
    assert baseline.status is WorkspaceBaselineStatus.COMPLETE
    assert baseline.entries == ()
    snapshot = AgentRunSnapshot(
        model=ModelRef(provider_id="p", model_id="m"),
        provider_id="p",
        run_policy_digest="a" * 64,
        tool_schema_digest="b" * 64,
        permission_profile_digest="c" * 64,
        runtime_instance_id="instance-1",
        workspace_baseline=baseline,
    )

    assert (
        len(canonical_json_bytes(snapshot.model_dump(mode="json"))) <= AGENT_RUN_SNAPSHOT_MAX_BYTES
    )


# ==============================================================================
# P2-1: Completion Feedback Ordering & Telemetry Accuracy
# ==============================================================================


@pytest.mark.asyncio
async def test_completion_feedback_ordering_preserves_system_boundary(tmp_path: Path):
    """Completion feedback is inserted after the system boundary and estimated chars are accurate."""
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    context_builder = make_context_builder(max_model_attempts=3)

    provider = ScriptedModelProvider(["claim one", "claim two"])
    loop = AgentLoop(
        provider,
        ModelRef(provider_id="demo", model_id="model"),
        context_builder,
        completion_checker=CompletionChecker(files),
    )

    contract = OutcomeContract(mode="change", target_paths=("answer.txt",))
    session = Session(session_id="session-1")

    events = [
        event
        async for event in loop.run_task(
            session,
            "fix answer.txt",
            outcome_contract=contract,
            workspace_baseline=baseline,
        )
    ]
    assert len(events) > 0

    assert len(provider.stream_calls) == 2
    turn_2_messages = provider.stream_calls[1]

    # System boundary MUST be strictly index 0
    assert isinstance(turn_2_messages[0], SystemMessage)
    assert "你是 Morrow（承序）" in turn_2_messages[0].content

    # Feedback message must be among subsequent system messages
    feedback_msgs = [
        msg
        for msg in turn_2_messages
        if isinstance(msg, SystemMessage)
        and "reason_codes=missing_required_change" in (msg.content or "")
    ]
    assert len(feedback_msgs) == 1


@pytest.mark.asyncio
async def test_persisted_request_size_includes_completion_feedback_exactly(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    provider = ScriptedModelProvider(
        ["claim one", "claim two"],
        intent_responses=(
            '{"mode":"change","certainty":"clear","target_paths":["answer.txt"],'
            '"allowed_paths":null,"forbidden_paths":[],"required_validations":[],'
            '"no_change_allowed":false}',
        ),
    )
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
    )

    [item async for item in session_app.orchestrator.stream("修改 answer.txt")]

    run = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )[0]
    observation = session_app.persistence.get_agent_run_observation(run.agent_run_id)
    agent_requests = [item for item in observation.requests if item.purpose.value == "agent"]
    expected = session_app.context_builder.validate_request(
        provider.stream_calls[1], provider.stream_tools[1]
    )
    assert agent_requests[1].estimated_request_chars == expected


# ==============================================================================
# P2-2: WorkspaceMutationService Previews Cleanup
# ==============================================================================


@pytest.mark.asyncio
async def test_tool_lifecycle_cleans_preview_when_approval_is_rejected(tmp_path: Path):
    """Prepared plans are released on a terminal path that never enters the handler."""
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
