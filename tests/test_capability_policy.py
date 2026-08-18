from pathlib import Path

import pytest

from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    OperationIntent,
    OperationKind,
    PermissionProfile,
    PolicyVerdict,
    ProcessIsolation,
    RiskFlag,
    WorkspaceCapability,
)
from morrow.core.models import ToolEffect
from morrow.runtime.capabilities import CapabilityPolicy, CapabilityReason


def _policy(
    *,
    approval_mode=ApprovalMode.MANUAL,
    process_isolation=ProcessIsolation.HOST,
    access_scope=AccessScope.WORKSPACE,
    read_only=False,
    sandbox_available=False,
):
    return CapabilityPolicy(
        PermissionProfile(
            access_scope=access_scope,
            approval_mode=approval_mode,
            process_isolation=process_isolation,
        ),
        WorkspaceCapability(workspace_id="w1", root=Path("/workspace"), read_only=read_only),
        sandbox_available=sandbox_available,
    )


def _intent(kind, **overrides):
    return OperationIntent(
        kind=kind,
        effect=overrides.pop("effect", ToolEffect.NONE),
        preview_summary=overrides.pop("preview_summary", ("safe preview",)),
        **overrides,
    )


@pytest.mark.parametrize(
    ("kind", "verdict"),
    [
        (OperationKind.INTERNAL_READ, PolicyVerdict.ALLOW),
        (OperationKind.WORKSPACE_READ, PolicyVerdict.ALLOW),
        (OperationKind.GIT_READ, PolicyVerdict.ALLOW),
        (OperationKind.WORKSPACE_WRITE, PolicyVerdict.REQUIRE_APPROVAL),
        (OperationKind.CONFIGURATION_WRITE, PolicyVerdict.REQUIRE_APPROVAL),
        (OperationKind.PROCESS, PolicyVerdict.REQUIRE_APPROVAL),
    ],
)
def test_manual_policy_truth_table(kind, verdict):
    decision = _policy().evaluate(_intent(kind))
    assert decision.verdict is verdict


def test_auto_safe_allows_structured_workspace_write_but_still_approves_host_process():
    policy = _policy(approval_mode=ApprovalMode.AUTO_SAFE)
    assert policy.evaluate(_intent(OperationKind.WORKSPACE_WRITE)).verdict is PolicyVerdict.ALLOW
    process = policy.evaluate(_intent(OperationKind.PROCESS, requires_host=True))
    assert process.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert process.reason_codes == (CapabilityReason.HOST_PROCESS_APPROVAL_REQUIRED,)


def test_auto_safe_mutation_threshold_and_replace_require_approval_but_read_only_denies():
    auto_safe = _policy(approval_mode=ApprovalMode.AUTO_SAFE)
    threshold = _intent(
        OperationKind.WORKSPACE_WRITE,
        risk_flags=(RiskFlag.MUTATION_APPROVAL_REQUIRED,),
    )
    decision = auto_safe.evaluate(threshold)
    assert decision.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert decision.reason_codes == (CapabilityReason.MUTATION_APPROVAL_REQUIRED,)

    read_only = _policy(approval_mode=ApprovalMode.AUTO_SAFE, read_only=True)
    denied = read_only.evaluate(threshold)
    assert denied.verdict is PolicyVerdict.DENY
    assert denied.reason_codes == (CapabilityReason.READ_ONLY_SESSION,)


def test_auto_sandboxed_process_fails_closed_until_backend_is_proven():
    intent = _intent(OperationKind.PROCESS, requires_sandbox=True)
    policy = _policy(
        approval_mode=ApprovalMode.AUTO,
        process_isolation=ProcessIsolation.NATIVE_SANDBOX,
    )
    decision = policy.evaluate(intent)
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.reason_codes == (CapabilityReason.SANDBOX_UNAVAILABLE,)


def test_forbidden_risks_are_denied_before_approval_for_all_workspace_modes():
    for policy in (
        _policy(),
        _policy(approval_mode=ApprovalMode.AUTO_SAFE),
        _policy(
            approval_mode=ApprovalMode.AUTO,
            process_isolation=ProcessIsolation.NATIVE_SANDBOX,
        ),
    ):
        decision = policy.evaluate(
            _intent(OperationKind.WORKSPACE_READ, risk_flags=(RiskFlag.NETWORK,))
        )
        assert decision.verdict is PolicyVerdict.DENY
        assert decision.reason_codes == (CapabilityReason.NETWORK_NOT_ENABLED,)


def test_full_access_and_read_only_intersection_fail_closed():
    full_access = _policy(access_scope=AccessScope.FULL_ACCESS).evaluate(
        _intent(OperationKind.INTERNAL_READ)
    )
    assert full_access.verdict is PolicyVerdict.DENY
    assert full_access.reason_codes == (CapabilityReason.FULL_ACCESS_UNSUPPORTED,)

    read_only = _policy(read_only=True).evaluate(_intent(OperationKind.WORKSPACE_WRITE))
    assert read_only.verdict is PolicyVerdict.DENY
    assert read_only.reason_codes == (CapabilityReason.READ_ONLY_SESSION,)
