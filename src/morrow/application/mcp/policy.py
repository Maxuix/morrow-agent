"""MCP launch/tool policy composition over the ordinary capability policy."""

from __future__ import annotations

from collections.abc import Iterable

from morrow.core.capabilities import (
    OperationIntent,
    PolicyDecision,
    PolicyVerdict,
    RiskFlag,
)
from morrow.core.mcp import McpReviewEvidence, McpReviewRisk
from morrow.runtime.capabilities import CapabilityPolicy, CapabilityReason

_REVIEWABLE_REASONS = {
    CapabilityReason.NETWORK_NOT_ENABLED: McpReviewRisk.NETWORK,
    CapabilityReason.NETWORK_APPROVAL_REQUIRED: McpReviewRisk.NETWORK,
    CapabilityReason.LOOPBACK_NOT_ENABLED: McpReviewRisk.LOOPBACK,
    CapabilityReason.LOOPBACK_APPROVAL_REQUIRED: McpReviewRisk.LOOPBACK,
    CapabilityReason.CREDENTIAL_ACCESS_DENIED: McpReviewRisk.CREDENTIALS,
    CapabilityReason.EXTERNAL_EFFECT_NOT_ENABLED: McpReviewRisk.EXTERNAL_EFFECT,
    CapabilityReason.EXTERNAL_EFFECT_APPROVAL_REQUIRED: McpReviewRisk.EXTERNAL_EFFECT,
}

_REVIEW_DENIAL_REASONS = {
    McpReviewRisk.NETWORK: CapabilityReason.NETWORK_NOT_ENABLED,
    McpReviewRisk.LOOPBACK: CapabilityReason.LOOPBACK_NOT_ENABLED,
    McpReviewRisk.CREDENTIALS: CapabilityReason.CREDENTIAL_ACCESS_DENIED,
    McpReviewRisk.EXTERNAL_EFFECT: CapabilityReason.EXTERNAL_EFFECT_NOT_ENABLED,
}


def _required_review_risks(intents: Iterable[OperationIntent]) -> frozenset[McpReviewRisk]:
    risks: set[McpReviewRisk] = set()
    for intent in intents:
        if RiskFlag.NETWORK in intent.risk_flags:
            risks.add(McpReviewRisk.NETWORK)
        if RiskFlag.LOOPBACK in intent.risk_flags:
            risks.add(McpReviewRisk.LOOPBACK)
        if RiskFlag.CREDENTIAL_ACCESS in intent.risk_flags:
            risks.add(McpReviewRisk.CREDENTIALS)
        if intent.kind.value == "external_effect":
            risks.add(McpReviewRisk.EXTERNAL_EFFECT)
    return frozenset(risks)


def _unique(values: Iterable[str], *, limit: int) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return tuple(result)


def combine_policy_decisions(decisions: Iterable[PolicyDecision]) -> PolicyDecision:
    """Combine launch and tool decisions with deny-first precedence."""

    values = tuple(decisions)
    if not values:
        raise ValueError("MCP policy requires at least one decision")
    reasons = _unique((reason for decision in values for reason in decision.reason_codes), limit=8)
    preview = _unique((line for decision in values for line in decision.preview_summary), limit=16)
    if any(decision.verdict is PolicyVerdict.DENY for decision in values):
        return PolicyDecision(
            verdict=PolicyVerdict.DENY, reason_codes=reasons, preview_summary=preview
        )
    if any(decision.verdict is PolicyVerdict.REQUIRE_APPROVAL for decision in values):
        return PolicyDecision(
            verdict=PolicyVerdict.REQUIRE_APPROVAL,
            reason_codes=reasons,
            preview_summary=preview,
        )
    return PolicyDecision(
        verdict=PolicyVerdict.ALLOW, reason_codes=reasons, preview_summary=preview
    )


def evaluate_mcp_policy(
    capability_policy: CapabilityPolicy,
    *,
    launch_intent: OperationIntent,
    tool_intent: OperationIntent,
    review: McpReviewEvidence | None,
    workspace_id: str,
    agent_run_id: str,
    server_id: str,
    config_digest: str,
    catalog_digest: str,
    toolset_digest: str,
    allow_unconfined_host: bool = False,
) -> PolicyDecision:
    """Evaluate two MCP intents and optionally convert eligible denials.

    Review evidence is exact-run and exact-toolset bound.  It never relaxes
    outside-workspace, destructive, privilege, git, protected-resource, or
    host-process policy outcomes.
    """

    intents = (launch_intent, tool_intent)
    decisions = [
        capability_policy.evaluate(intent, allow_unconfined_host=allow_unconfined_host)
        for intent in intents
    ]
    required = _required_review_risks(intents)
    exact_review = review is not None and review.matches(
        workspace_id=workspace_id,
        agent_run_id=agent_run_id,
        server_id=server_id,
        config_digest=config_digest,
        catalog_digest=catalog_digest,
        toolset_digest=toolset_digest,
    )
    review_covers = exact_review and review.covers(set(required))
    if not any(decision.verdict is PolicyVerdict.DENY for decision in decisions):
        if not required:
            return combine_policy_decisions(decisions)
        if not review_covers:
            combined = combine_policy_decisions(decisions)
            return PolicyDecision(
                verdict=PolicyVerdict.DENY,
                reason_codes=_unique(
                    (_REVIEW_DENIAL_REASONS[risk] for risk in sorted(required, key=str)),
                    limit=8,
                ),
                preview_summary=combined.preview_summary,
            )
        reviewed: list[PolicyDecision] = []
        for decision in decisions:
            if decision.verdict is not PolicyVerdict.REQUIRE_APPROVAL:
                reviewed.append(decision)
                continue
            reviewed.append(
                decision.model_copy(
                    update={
                        "reason_codes": _unique(
                            (*decision.reason_codes, "mcp_review_required"), limit=8
                        )
                    }
                )
            )
        return combine_policy_decisions(reviewed)

    if review_covers:
        relaxed: list[PolicyDecision] = []
        for decision in decisions:
            if decision.verdict is not PolicyVerdict.DENY:
                relaxed.append(decision)
                continue
            if not decision.reason_codes or any(
                reason not in _REVIEWABLE_REASONS for reason in decision.reason_codes
            ):
                return combine_policy_decisions(decisions)
            relaxed.append(
                PolicyDecision(
                    verdict=PolicyVerdict.REQUIRE_APPROVAL,
                    reason_codes=_unique((*decision.reason_codes, "mcp_review_required"), limit=8),
                    preview_summary=decision.preview_summary,
                )
            )
        return combine_policy_decisions(relaxed)
    return combine_policy_decisions(decisions)


__all__ = ["combine_policy_decisions", "evaluate_mcp_policy"]
