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


def test_full_access_manual_requires_a_grant_and_keeps_structured_risks_denied():
    policy = _policy(access_scope=AccessScope.FULL_ACCESS)
    assert policy.evaluate(_intent(OperationKind.INTERNAL_READ)).verdict is PolicyVerdict.ALLOW
    no_grant = policy.evaluate(_intent(OperationKind.PROCESS, requires_host=True))
    assert no_grant.verdict is PolicyVerdict.DENY
    assert no_grant.reason_codes == (CapabilityReason.FULL_ACCESS_GRANT_REQUIRED,)

    outside = policy.evaluate(
        _intent(
            OperationKind.PROCESS,
            requires_host=True,
            risk_flags=(RiskFlag.OUTSIDE_WORKSPACE, RiskFlag.NETWORK),
        ),
        allow_unconfined_host=True,
    )
    assert outside.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert outside.reason_codes == (CapabilityReason.FULL_ACCESS_HOST_APPROVAL_REQUIRED,)

    destructive = policy.evaluate(
        _intent(
            OperationKind.PROCESS,
            requires_host=True,
            risk_flags=(RiskFlag.DESTRUCTIVE,),
        ),
        allow_unconfined_host=True,
    )
    assert destructive.verdict is PolicyVerdict.DENY
    assert destructive.reason_codes == (CapabilityReason.DESTRUCTIVE_NOT_ENABLED,)

    full_access_auto = _policy(
        access_scope=AccessScope.FULL_ACCESS,
        approval_mode=ApprovalMode.AUTO,
        process_isolation=ProcessIsolation.HOST,
    ).evaluate(_intent(OperationKind.INTERNAL_READ))
    assert full_access_auto.verdict is PolicyVerdict.DENY
    assert full_access_auto.reason_codes == (CapabilityReason.FULL_ACCESS_UNSUPPORTED,)


@pytest.mark.parametrize(
    ("risk", "reason"),
    [
        (RiskFlag.CREDENTIAL_ACCESS, CapabilityReason.CREDENTIAL_ACCESS_DENIED),
        (RiskFlag.GIT_WRITE, CapabilityReason.GIT_WRITE_NOT_ENABLED),
        (RiskFlag.PRIVILEGE_ESCALATION, CapabilityReason.PRIVILEGE_ESCALATION_NOT_ENABLED),
    ],
)
def test_full_access_host_does_not_grant_credential_git_or_privilege_risks(risk, reason):
    decision = _policy(access_scope=AccessScope.FULL_ACCESS).evaluate(
        _intent(
            OperationKind.PROCESS,
            requires_host=True,
            risk_flags=(RiskFlag.OUTSIDE_WORKSPACE, risk),
        ),
        allow_unconfined_host=True,
    )
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.reason_codes == (reason,)


def test_read_only_intersection_still_denies_workspace_writes():

    read_only = _policy(read_only=True).evaluate(_intent(OperationKind.WORKSPACE_WRITE))
    assert read_only.verdict is PolicyVerdict.DENY
    assert read_only.reason_codes == (CapabilityReason.READ_ONLY_SESSION,)
