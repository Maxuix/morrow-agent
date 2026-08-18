"""Deterministic local capability-policy evaluation."""

from __future__ import annotations

from enum import StrEnum

from morrow.core.capabilities import (
    AccessScope,
    ApprovalMode,
    OperationIntent,
    OperationKind,
    PermissionProfile,
    PolicyDecision,
    PolicyVerdict,
    ProcessIsolation,
    RiskFlag,
    WorkspaceCapability,
)


class CapabilityReason(StrEnum):
    ALLOWED = "allowed"
    FULL_ACCESS_UNSUPPORTED = "full_access_unsupported"
    INVALID_PROFILE = "invalid_profile"
    READ_ONLY_SESSION = "read_only_session"
    PROTECTED_RESOURCE = "protected_resource"
    OUTSIDE_WORKSPACE = "outside_workspace"
    NETWORK_NOT_ENABLED = "network_not_enabled"
    LOOPBACK_NOT_ENABLED = "loopback_not_enabled"
    CREDENTIAL_ACCESS_DENIED = "credential_access_denied"
    DESTRUCTIVE_NOT_ENABLED = "destructive_not_enabled"
    GIT_WRITE_NOT_ENABLED = "git_write_not_enabled"
    PRIVILEGE_ESCALATION_NOT_ENABLED = "privilege_escalation_not_enabled"
    EXTERNAL_EFFECT_NOT_ENABLED = "external_effect_not_enabled"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    HOST_PROCESS_NOT_ALLOWED = "host_process_not_allowed"
    HOST_PROCESS_APPROVAL_REQUIRED = "host_process_approval_required"
    WORKSPACE_WRITE_APPROVAL_REQUIRED = "workspace_write_approval_required"
    CONFIGURATION_APPROVAL_REQUIRED = "configuration_approval_required"
    MUTATION_APPROVAL_REQUIRED = "mutation_approval_required"


_DENIED_RISKS = {
    RiskFlag.OUTSIDE_WORKSPACE: CapabilityReason.OUTSIDE_WORKSPACE,
    RiskFlag.NETWORK: CapabilityReason.NETWORK_NOT_ENABLED,
    RiskFlag.LOOPBACK: CapabilityReason.LOOPBACK_NOT_ENABLED,
    RiskFlag.CREDENTIAL_ACCESS: CapabilityReason.CREDENTIAL_ACCESS_DENIED,
    RiskFlag.PROTECTED_RESOURCE: CapabilityReason.PROTECTED_RESOURCE,
    RiskFlag.DESTRUCTIVE: CapabilityReason.DESTRUCTIVE_NOT_ENABLED,
    RiskFlag.GIT_WRITE: CapabilityReason.GIT_WRITE_NOT_ENABLED,
    RiskFlag.PRIVILEGE_ESCALATION: CapabilityReason.PRIVILEGE_ESCALATION_NOT_ENABLED,
}


class CapabilityPolicy:
    """Pure policy for the currently frozen Session capability profile.

    The policy intentionally knows only operation categories and sanitized intent
    metadata.  It never parses Provider arguments and never calls a handler.
    """

    def __init__(
        self,
        profile: PermissionProfile,
        workspace: WorkspaceCapability,
        *,
        sandbox_available: bool = False,
    ) -> None:
        self.profile = profile
        self.workspace = workspace
        self.sandbox_available = sandbox_available

    def evaluate(self, intent: OperationIntent) -> PolicyDecision:
        if self.profile.access_scope is AccessScope.FULL_ACCESS:
            return self._deny(CapabilityReason.FULL_ACCESS_UNSUPPORTED)
        if not self._profile_supported():
            return self._deny(CapabilityReason.INVALID_PROFILE)
        for flag, reason in _DENIED_RISKS.items():
            if flag in intent.risk_flags:
                return self._deny(reason)
        if intent.kind is OperationKind.EXTERNAL_EFFECT:
            return self._deny(CapabilityReason.EXTERNAL_EFFECT_NOT_ENABLED)
        if intent.kind is OperationKind.DESTRUCTIVE:
            return self._deny(CapabilityReason.DESTRUCTIVE_NOT_ENABLED)
        if self.workspace.read_only and self._mutates_or_runs(intent):
            return self._deny(CapabilityReason.READ_ONLY_SESSION)
        if RiskFlag.MUTATION_APPROVAL_REQUIRED in intent.risk_flags:
            return self._approval(CapabilityReason.MUTATION_APPROVAL_REQUIRED, intent)
        if (
            intent.requires_sandbox
            and self.profile.process_isolation is not ProcessIsolation.NATIVE_SANDBOX
        ):
            return self._deny(CapabilityReason.SANDBOX_UNAVAILABLE)

        if intent.kind is OperationKind.PROCESS:
            if self.profile.process_isolation is ProcessIsolation.NATIVE_SANDBOX:
                if intent.requires_host:
                    return self._deny(CapabilityReason.HOST_PROCESS_NOT_ALLOWED)
                if not self.sandbox_available:
                    return self._deny(CapabilityReason.SANDBOX_UNAVAILABLE)
                return self._allow()
            return self._approval(
                CapabilityReason.HOST_PROCESS_APPROVAL_REQUIRED,
                intent,
            )
        if intent.kind is OperationKind.CONFIGURATION_WRITE:
            return self._approval(CapabilityReason.CONFIGURATION_APPROVAL_REQUIRED, intent)
        if intent.kind is OperationKind.WORKSPACE_WRITE:
            if self.profile.approval_mode is ApprovalMode.MANUAL:
                return self._approval(CapabilityReason.WORKSPACE_WRITE_APPROVAL_REQUIRED, intent)
            return self._allow()
        if intent.kind in {
            OperationKind.INTERNAL_READ,
            OperationKind.WORKSPACE_READ,
            OperationKind.GIT_READ,
        }:
            return self._allow()
        return self._deny(CapabilityReason.INVALID_PROFILE)

    def _profile_supported(self) -> bool:
        if self.profile.approval_mode is ApprovalMode.AUTO:
            return self.profile.process_isolation is ProcessIsolation.NATIVE_SANDBOX
        return self.profile.process_isolation is ProcessIsolation.HOST

    @staticmethod
    def _mutates_or_runs(intent: OperationIntent) -> bool:
        return intent.kind in {
            OperationKind.WORKSPACE_WRITE,
            OperationKind.CONFIGURATION_WRITE,
            OperationKind.PROCESS,
            OperationKind.DESTRUCTIVE,
            OperationKind.EXTERNAL_EFFECT,
        }

    @staticmethod
    def _allow() -> PolicyDecision:
        return PolicyDecision(verdict=PolicyVerdict.ALLOW, reason_codes=(CapabilityReason.ALLOWED,))

    @staticmethod
    def _deny(reason: CapabilityReason) -> PolicyDecision:
        return PolicyDecision(verdict=PolicyVerdict.DENY, reason_codes=(reason,))

    @staticmethod
    def _approval(reason: CapabilityReason, intent: OperationIntent) -> PolicyDecision:
        return PolicyDecision(
            verdict=PolicyVerdict.REQUIRE_APPROVAL,
            reason_codes=(reason,),
            preview_summary=intent.preview_summary,
        )
