from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    ChangeToolFact,
    CommandToolFact,
    PolicyVerdict,
    ToolRunContext,
    ValidationFact,
)
from morrow.core.completion import (
    CompletionBasis,
    CompletionOutcome,
    OutcomeContract,
    OutcomeContractCompiler,
    ValidationRequirement,
)
from morrow.core.local_tools import CommandRequest, CommandResult, CommandStatus
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    ToolApprovalDecision,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.services.completion import CompletionChecker, WorkspaceBaselineService
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import ProcessExecutionService
from morrow.testing import ScriptedModelProvider, make_context_builder


def _command(*, ordinal: int = 1, status: str = "exited", exit_code: int | None = 0):
    return CommandToolFact(
        call_id=f"call-{ordinal}",
        tool_name="run_command",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        command_class="project_command",
        status=status,
        exit_code=exit_code,
        duration_ms=1,
    )


def _call(call_id: str, name: str, arguments: dict) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


class _Approval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=True)


def _validation(
    *,
    ordinal: int = 1,
    validator_kind: str = "pytest",
    scope: str = ".",
    status: str = "passed",
):
    return ValidationFact(
        call_id=f"call-{ordinal}",
        tool_name="run_command",
        ordinal=ordinal,
        approval_verdict=PolicyVerdict.ALLOW,
        validator_kind=validator_kind,
        scope=scope,
        status=status,
        exit_code=0 if status == "passed" else 1,
        evidence_summary=status,
    )


def test_command_success_is_not_a_validation_fact_or_validation_outcome():
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record((_command(),))

    metrics = run.metrics("stop")

    assert not run.validation_facts
    assert metrics.validation_outcome == "not_run"


def test_latest_scoped_validation_fact_replaces_only_its_own_requirement():
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            _validation(ordinal=1, scope="tests"),
            _command(ordinal=2),
            _validation(ordinal=3, scope="tests", status="failed"),
            _validation(ordinal=4, scope="src"),
        )
    )

    assert {(fact.validator_kind, fact.scope): fact.status for fact in run.validation_facts} == {
        ("pytest", "tests"): "failed",
        ("pytest", "src"): "passed",
    }
    assert run.metrics("stop").validation_outcome == "failed"


def test_validator_recognition_is_strict_and_shell_control_flow_fails_closed(tmp_path):
    (tmp_path / "tests").mkdir()
    process = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))

    pytest_plan = process.preflight(CommandRequest(argv=(sys.executable, "-m", "pytest", "tests")))
    simple_shell_plan = process.preflight(CommandRequest(shell="pytest tests"))
    opaque_plan = process.preflight(CommandRequest(argv=("ls", "tests")))
    shell_plan = process.preflight(CommandRequest(shell="pytest tests && echo done"))
    newline_shell_plan = process.preflight(CommandRequest(shell="pytest tests\necho done"))

    assert pytest_plan.validation_kind == "pytest"
    assert pytest_plan.validation_scope == "tests"
    assert simple_shell_plan.validation_kind == "pytest"
    assert simple_shell_plan.validation_scope == "tests"
    assert opaque_plan.validation_kind is None
    assert shell_plan.validation_kind is None
    assert newline_shell_plan.validation_kind is None


def test_recognized_process_result_projects_only_a_validation_fact(tmp_path):
    (tmp_path / "tests").mkdir()
    process = ProcessExecutionService(WorkspaceFileService(WorkspacePathResolver(tmp_path)))
    result = CommandResult(
        status=CommandStatus.EXITED,
        exit_code=0,
        stdout="",
        stderr="",
        stdout_original_bytes=0,
        stdout_original_lines=0,
        stderr_original_bytes=0,
        stderr_original_lines=0,
        duration_ms=1,
        command_class="project_command",
        cwd=".",
    )
    recognized = process.preflight(CommandRequest(argv=("pytest", "tests")))
    opaque = process.preflight(CommandRequest(argv=("ls", "tests")))

    fact = process.validation_fact(
        recognized,
        result,
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
    )

    assert isinstance(fact, ValidationFact)
    assert fact.validator_kind == "pytest"
    assert fact.scope == "tests"
    assert (
        process.validation_fact(
            opaque,
            result,
            call_id="call-2",
            tool_name="run_command",
            ordinal=2,
            approval_verdict=PolicyVerdict.ALLOW,
        )
        is None
    )


def test_workspace_baseline_is_no_follow_bounded_and_non_git_aware(tmp_path):
    outside = tmp_path.parent / "s7p05-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    (tmp_path / ".git").symlink_to(outside)
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))

    baseline = WorkspaceBaselineService(files).prepare()
    entries = {entry.path: entry for entry in baseline.entries}

    assert baseline.repository_state == "unknown"
    assert entries["link.txt"].kind == "symlink"
    assert "s7p05-outside.txt" not in entries

    outside.write_text("changed", encoding="utf-8")
    assert WorkspaceBaselineService(files).prepare().entries == baseline.entries
    (tmp_path / "link.txt").unlink()
    (tmp_path / "link.txt").symlink_to(tmp_path / "tracked.txt")
    assert WorkspaceBaselineService(files).prepare().entries != baseline.entries


def test_workspace_baseline_reports_scan_truncation(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))

    baseline = WorkspaceBaselineService(files, max_entries=1).prepare()

    assert baseline.status.value == "truncated"
    assert len(baseline.entries) == 1


def test_change_completion_requires_net_change_and_declared_validation(tmp_path):
    target = tmp_path / "answer.txt"
    target.write_text("before", encoding="utf-8")
    before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    contract = OutcomeContract(
        mode="change",
        target_paths=("answer.txt",),
        required_validations=(ValidationRequirement(validator_kind="pytest", scope="."),),
    )
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    target.write_text("after", encoding="utf-8")
    after_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    run.record(
        (
            ChangeToolFact(
                call_id="edit",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("answer.txt",),
                operation="patch",
                status="modified",
                before_revision=before_sha256,
                after_revision=after_sha256,
                changed_lines=1,
                changed_bytes=5,
            ),
            ValidationFact(
                call_id="check",
                tool_name="run_command",
                ordinal=2,
                approval_verdict=PolicyVerdict.ALLOW,
                validator_kind="pytest",
                scope=".",
                status="passed",
                exit_code=0,
                evidence_summary="exit_zero",
            ),
        )
    )

    result = CompletionChecker(files).check(contract, baseline, run_context=run)

    assert result.outcome is CompletionOutcome.PASSED
    assert result.basis is CompletionBasis.RUNTIME_EVIDENCE_WITHOUT_VERIFIER
    assert result.changed_paths == ("answer.txt",)


def test_completion_rejects_validation_failure_and_forbidden_path(tmp_path):
    (tmp_path / "answer.txt").write_text("before", encoding="utf-8")
    before_sha256 = hashlib.sha256((tmp_path / "answer.txt").read_bytes()).hexdigest()
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    (tmp_path / "answer.txt").write_text("after", encoding="utf-8")
    after_sha256 = hashlib.sha256((tmp_path / "answer.txt").read_bytes()).hexdigest()
    (tmp_path / "secret.txt").write_text("unexpected", encoding="utf-8")
    secret_sha256 = hashlib.sha256((tmp_path / "secret.txt").read_bytes()).hexdigest()
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            ChangeToolFact(
                call_id="edit",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("answer.txt",),
                operation="patch",
                status="modified",
                before_revision=before_sha256,
                after_revision=after_sha256,
                changed_lines=2,
                changed_bytes=20,
            ),
            ChangeToolFact(
                call_id="forbidden-edit",
                tool_name="apply_patch",
                ordinal=2,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("secret.txt",),
                operation="create",
                status="created",
                after_revision=secret_sha256,
                changed_lines=1,
                changed_bytes=10,
            ),
            _validation(ordinal=3, status="failed"),
        )
    )
    contract = OutcomeContract(
        mode="change",
        target_paths=("answer.txt",),
        forbidden_paths=("secret.txt",),
        required_validations=(ValidationRequirement(validator_kind="pytest", scope="."),),
    )

    result = CompletionChecker(files).check(contract, baseline, run_context=run)

    assert result.outcome is CompletionOutcome.REJECTED
    assert "forbidden_workspace_change" in result.reason_codes
    assert "validation_failed" in result.reason_codes
    assert "secret.txt" in result.forbidden_paths


def test_completion_requires_exact_validation_scope_and_run_attribution(tmp_path):
    target = tmp_path / "answer.txt"
    target.write_text("before", encoding="utf-8")
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    target.write_text("after", encoding="utf-8")
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            ValidationFact(
                call_id="check",
                tool_name="run_command",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                validator_kind="pytest",
                scope="other",
                status="passed",
                exit_code=0,
                evidence_summary="exit_zero",
            ),
        )
    )
    contract = OutcomeContract(
        mode="change",
        target_paths=("answer.txt",),
        required_validations=(ValidationRequirement(validator_kind="pytest", scope="tests"),),
    )

    result = CompletionChecker(files).check(contract, baseline, run_context=run)

    assert result.outcome is CompletionOutcome.INCONCLUSIVE
    assert result.stop_code.value == "validation_missing"
    assert "baseline_drift" in result.reason_codes


def test_compiled_target_paths_are_exclusive_and_reject_unexpected_net_changes(tmp_path):
    target = tmp_path / "answer.txt"
    target.write_text("before", encoding="utf-8")
    before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    target.write_text("after", encoding="utf-8")
    after_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    (tmp_path / "extra.txt").write_text("unexpected", encoding="utf-8")
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            ChangeToolFact(
                call_id="edit",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("answer.txt",),
                operation="patch",
                status="modified",
                before_revision=before_sha256,
                after_revision=after_sha256,
                changed_lines=1,
                changed_bytes=5,
            ),
        )
    )

    result = CompletionChecker(files).check(
        OutcomeContractCompiler().compile("修复 `answer.txt`"),
        baseline,
        run_context=run,
    )

    assert result.outcome is CompletionOutcome.INCONCLUSIVE
    assert result.unexpected_paths == ("extra.txt",)
    assert result.stop_code.value == "unexpected_workspace_change"


def test_completion_does_not_trust_unknown_or_wildcard_change_facts(tmp_path):
    target = tmp_path / "answer.txt"
    target.write_text("before", encoding="utf-8")
    before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    target.write_text("after", encoding="utf-8")
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            ChangeToolFact(
                call_id="edit",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=(".",),
                operation="patch",
                status="outcome_unknown",
                before_revision=before_sha256,
                changed_lines=1,
                changed_bytes=5,
            ),
        )
    )

    result = CompletionChecker(files).check(
        OutcomeContract(mode="change", target_paths=("answer.txt",)),
        baseline,
        run_context=run,
    )

    assert result.outcome is CompletionOutcome.INCONCLUSIVE
    assert "baseline_drift" in result.reason_codes


def test_completion_does_not_trust_change_fact_after_external_mutation(tmp_path):
    target = tmp_path / "answer.txt"
    target.write_text("before", encoding="utf-8")
    before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    target.write_text("after", encoding="utf-8")
    after_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text("external", encoding="utf-8")
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record(
        (
            ChangeToolFact(
                call_id="edit",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("answer.txt",),
                operation="patch",
                status="modified",
                before_revision=before_sha256,
                after_revision=after_sha256,
                changed_lines=1,
                changed_bytes=5,
            ),
        )
    )

    result = CompletionChecker(files).check(
        OutcomeContract(mode="change", target_paths=("answer.txt",)),
        baseline,
        run_context=run,
    )

    assert result.outcome is CompletionOutcome.INCONCLUSIVE
    assert "baseline_drift" in result.reason_codes


@pytest.mark.parametrize(
    ("verifier", "outcome", "basis"),
    [
        (lambda result: True, CompletionOutcome.PASSED, "verified"),
        (lambda result: False, CompletionOutcome.REJECTED, "not_completed"),
        (lambda result: None, CompletionOutcome.INCONCLUSIVE, "inconclusive"),
    ],
)
def test_optional_verifier_is_authoritative_when_runtime_facts_pass(
    tmp_path, verifier, outcome, basis
):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    contract = OutcomeContract(mode="explanation", verifier_id="answer_verifier")

    result = CompletionChecker(files).check(contract, baseline, verifier=verifier)

    assert result.outcome is outcome
    assert result.basis.value == basis


@pytest.mark.asyncio
async def test_scripted_direct_agent_acceptance_requires_scoped_validation_and_reports_terminal_truth(
    tmp_path: Path,
):
    project = tmp_path / "project"
    project.mkdir()
    answer = project / "answer.txt"
    answer.write_text("old\n", encoding="utf-8")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_answer.py").write_text(
        "from pathlib import Path\n\n"
        "def test_answer():\n    assert Path('answer.txt').read_text() == 'new\\n'\n",
        encoding="utf-8",
    )
    expected_sha256 = hashlib.sha256(answer.read_bytes()).hexdigest()
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _call(
                        "edit",
                        "apply_patch",
                        {
                            "path": "answer.txt",
                            "expected_sha256": expected_sha256,
                            "edits": [{"old_text": "old", "new_text": "new"}],
                        },
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call(
                        "validate",
                        "run_command",
                        {"argv": [sys.executable, "-m", "pytest", "tests"]},
                    ),
                )
            ),
            AssistantMessage(content="已修改 answer.txt 并通过 pytest tests。"),
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

    items = [
        item
        async for item in session_app.orchestrator.stream("修复 `answer.txt` 并运行 pytest tests")
    ]
    events = [item for item in items if getattr(item, "type", None) is not None]

    assert answer.read_text(encoding="utf-8") == "new\n"
    assert [event.type for event in events].count("text.delta") == 1
    assert events[-1].type == "turn.completed"
    assert events[-1].payload["finish_reason"] == "stop"
    assert len(approval.requests) == 2
    assert session_app.session.latest_metrics is not None
    assert session_app.session.latest_metrics.validation_outcome == "passed"
    assert session_app.session.latest_completion_check is not None
    assert session_app.session.latest_completion_check.outcome is CompletionOutcome.PASSED
    assert (
        session_app.session.latest_completion_check.basis
        is CompletionBasis.RUNTIME_EVIDENCE_WITHOUT_VERIFIER
    )
    observation = session_app.persistence.get_agent_run_observation()
    assert observation is not None
    assert observation.terminal_metrics is not None
    assert observation.terminal_metrics.validation_outcome == "passed"
    assert observation.terminal_metrics.completion_outcome == "passed"
    assert observation.terminal_metrics.completion_basis == "runtime_evidence_without_verifier"
    assert "new\\n" not in json.dumps(
        [event.payload for event in events if event.type in {"tool.status", "turn.completed"}],
        ensure_ascii=False,
    )


def test_contract_compiler_is_conservative_about_explanations_and_targets():
    compiler = OutcomeContractCompiler()

    change = compiler.compile("修复 `src/app.py` 并运行 pytest tests")
    flagged_change = compiler.compile("prefix the explanation with one sentence")
    explanation = compiler.compile("请解释 `src/app.py` 的作用")
    unspecified = compiler.compile("请处理一下这个问题")
    configuration = compiler.compile("请更新工作空间简介")

    assert change.mode == "change"
    assert change.target_paths == ("src/app.py",)
    assert change.required_validations == (
        ValidationRequirement(validator_kind="pytest", scope="tests"),
    )
    assert flagged_change.mode.value == "unspecified"
    assert explanation.mode == "explanation"
    assert not explanation.requires_net_change
    assert unspecified.mode == "unspecified"
    assert not unspecified.requires_net_change
    assert configuration.mode == "unspecified"
    assert not configuration.requires_net_change


def test_failed_final_claim_is_buffered_and_gets_only_one_fact_feedback(tmp_path):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    provider = ScriptedModelProvider(["first claim", "second claim", "third claim"])
    loop = AgentLoop(
        provider,
        ModelRef(provider_id="demo", model_id="model"),
        make_context_builder(max_model_attempts=3),
        completion_checker=CompletionChecker(files),
    )
    contract = OutcomeContract(mode="change", target_paths=("answer.txt",))

    events = asyncio.run(
        _collect(
            loop.run_task(
                Session(session_id="session-1"),
                "fix answer.txt",
                outcome_contract=contract,
                workspace_baseline=baseline,
            )
        )
    )

    assert not [event for event in events if event.type == "text.delta"]
    assert events[-1].payload["finish_reason"] == "error"
    assert events[-1].payload["stop_code"] == "missing_required_change"
    assert len(provider.stream_calls) == 2
    assert "reason_codes=missing_required_change" in provider.stream_calls[1][0].content


def test_failed_final_claim_reports_missing_validation_without_chat_history_append(tmp_path):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    baseline = WorkspaceBaselineService(files).prepare()
    provider = ScriptedModelProvider(["claim one", "claim two", "claim three"])
    loop = AgentLoop(
        provider,
        ModelRef(provider_id="demo", model_id="model"),
        make_context_builder(max_model_attempts=3),
        completion_checker=CompletionChecker(files),
    )
    contract = OutcomeContract(
        mode="change",
        required_validations=(ValidationRequirement(validator_kind="pytest", scope="."),),
    )
    session = Session(session_id="session-1")

    events = asyncio.run(
        _collect(
            loop.run_task(
                session,
                "fix the project and run pytest",
                outcome_contract=contract,
                workspace_baseline=baseline,
            )
        )
    )

    assert not [event for event in events if event.type == "text.delta"]
    assert events[-1].payload["stop_code"] == "validation_missing"
    assert [message.role for message in session.messages] == ["user"]
    assert len(provider.stream_calls) == 2
    correction = provider.stream_calls[1][0].content
    assert "reason_codes=validation_missing,missing_required_change" in correction
    assert "claim one" not in correction


async def _collect(stream):
    return [item async for item in stream]
