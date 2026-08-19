from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    ChangeToolFact,
    CommandToolFact,
    OperationIntent,
    OperationKind,
    PermissionPreset,
    PermissionProfile,
    PolicyDecision,
    PolicyVerdict,
    ProcessIsolation,
    RiskFlag,
    ToolCallContext,
    ToolFact,
    ToolHandlerOutcome,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.models import ToolDefinition, ToolEffect, ToolFunction


def test_permission_profile_and_workspace_capability_are_strict_and_frozen(tmp_path):
    profile = PermissionProfile(
        access_scope=AccessScope.WORKSPACE,
        approval_mode=ApprovalMode.AUTO_SAFE,
        process_isolation=ProcessIsolation.HOST,
    )
    capability = WorkspaceCapability(workspace_id="w1", root=tmp_path)

    assert profile.approval_mode is ApprovalMode.AUTO_SAFE
    assert capability.root == tmp_path
    with pytest.raises(ValidationError):
        PermissionProfile.model_validate({"approval_mode": "auto_safe", "extra": True}, strict=True)
    with pytest.raises(ValidationError):
        profile.approval_mode = ApprovalMode.MANUAL
    with pytest.raises(ValidationError):
        WorkspaceCapability(workspace_id="w1", root=Path("relative"))


def test_permission_presets_are_process_local_and_full_access_is_manual_only():
    assert PermissionProfile.from_preset(PermissionPreset.MANUAL) == PermissionProfile()
    auto_safe = PermissionProfile.from_preset(PermissionPreset.AUTO_SAFE)
    assert auto_safe.approval_mode is ApprovalMode.AUTO_SAFE
    auto_sandboxed = PermissionProfile.from_preset(PermissionPreset.AUTO_SANDBOXED)
    assert auto_sandboxed.process_isolation is ProcessIsolation.NATIVE_SANDBOX
    assert auto_sandboxed.approval_mode is ApprovalMode.AUTO
    full_access = PermissionProfile.from_preset(PermissionPreset.FULL_ACCESS_MANUAL)
    assert full_access.access_scope is AccessScope.FULL_ACCESS
    assert full_access.approval_mode is ApprovalMode.MANUAL
    assert full_access.process_isolation is ProcessIsolation.HOST


def test_operation_intent_and_policy_decision_bound_local_metadata():
    intent = OperationIntent(
        kind=OperationKind.WORKSPACE_READ,
        effect=ToolEffect.NONE,
        relative_paths=("src/morrow/core/models.py",),
        risk_flags=(RiskFlag.PROTECTED_RESOURCE,),
        preview_summary=("Read one workspace file",),
    )
    decision = PolicyDecision(
        verdict=PolicyVerdict.REQUIRE_APPROVAL,
        reason_codes=("protected_resource",),
        preview_summary=intent.preview_summary,
    )

    assert intent.model_dump() == {
        "kind": "workspace_read",
        "effect": "none",
        "relative_paths": ("src/morrow/core/models.py",),
        "command_class": None,
        "risk_flags": ("protected_resource",),
        "requires_host": False,
        "requires_sandbox": False,
        "preview_summary": ("Read one workspace file",),
    }
    assert decision.verdict is PolicyVerdict.REQUIRE_APPROVAL


def test_tool_facts_are_a_strict_tagged_union_with_no_raw_content_fields():
    fact = ChangeToolFact(
        call_id="call-1",
        tool_name="apply_patch",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=("src/example.py",),
        operation="patch",
        changed_lines=2,
        changed_bytes=10,
    )
    parsed = TypeAdapter(ToolFact).validate_python(fact.model_dump(), strict=True)
    assert isinstance(parsed, ChangeToolFact)
    assert "content" not in fact.model_dump()
    assert "arguments" not in fact.model_dump()
    with pytest.raises(ValidationError):
        TypeAdapter(ToolFact).validate_python(
            {**fact.model_dump(), "kind": "change", "content": "secret"}, strict=True
        )


def test_command_fact_and_run_context_keep_only_bounded_ordered_facts():
    fact = CommandToolFact(
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        command_class="test",
        status="failed",
        exit_code=1,
        duration_ms=12,
        output_truncated=True,
        redaction_flags=("secret",),
        redaction_count=1,
    )
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record((fact,))
    outcome = ToolHandlerOutcome(payload={"ok": True}, facts=[fact])
    context = ToolCallContext(
        run=run,
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        total=1,
        result_limit=100,
    )

    assert run.facts == (fact,)
    assert outcome.facts == (fact,)
    assert context.result_limit == 100


def test_run_metrics_are_local_json_safe_and_composition_disableable():
    from morrow.runtime.session import Session

    fact = CommandToolFact(
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        command_class="test",
        status="failed",
        exit_code=1,
        duration_ms=12,
    )
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    run.record((fact,))
    run.note_tool_outcome(ok=True)
    session = Session(session_id="session-1", metrics_enabled=True)
    session.retain_run_facts(run, finish_reason="stop")
    assert session.latest_metrics is not None
    assert session.latest_metrics.validation_outcome == "failed"
    assert session.latest_metrics.model_dump(mode="json")["run_id"] == "run-1"

    disabled = Session(session_id="session-2", metrics_enabled=False)
    disabled.retain_run_facts(run, finish_reason="stop")
    assert disabled.latest_metrics is None


def test_local_capabilities_do_not_appear_in_provider_tool_definition():
    definition = ToolDefinition(
        function=ToolFunction(
            name="read_like",
            description="A test-only definition",
            parameters={"type": "object", "properties": {}},
        )
    )
    serialized = definition.model_dump()
    assert "PermissionProfile" not in str(serialized)
    assert "PolicyDecision" not in str(serialized)
    assert "ToolFact" not in str(serialized)
