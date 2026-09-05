"""User policy over the existing Extension YAML OCC/backup authority."""

from __future__ import annotations

from morrow.adapters.state.extension_yaml import (
    ExtensionYamlConflict,
    ExtensionYamlLoadStatus,
)
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.orchestration import OrchestrationPolicy


class OrchestrationPolicyService:
    def __init__(self, store, *, workspace_id: str):
        self.store = store
        self.workspace_id = workspace_id
        self.evaluation = None

    def promoted(self, task_type):
        return bool(self.evaluation and self.evaluation.promotion(task_type)["promoted"])

    def auto_run(self, policy, task_type):
        return (
            policy.source == "user"
            and policy.auto_run_mode == "allow_promoted"
            and self.promoted(task_type)
        )

    def auto_replan(self, policy):
        return (
            policy.source == "user"
            and policy.auto_replan_mode == "allow_low_risk"
            and (policy.task_matcher == "*" or self.promoted(policy.task_matcher))
        )

    def _document(self, scope):
        if scope not in {"global", "workspace"}:
            raise ValueError("unknown orchestration policy scope")
        loaded = (
            self.store.load_global()
            if scope == "global"
            else self.store.load_workspace(self.workspace_id)
        )
        if loaded.status is not ExtensionYamlLoadStatus.OK or loaded.value is None:
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Orchestration policy needs repair"
            )
        return loaded.value

    def view(self):
        classes = ("implementation", "refactor", "research", "explanation", "diagnosis", "general")
        eligible = (
            any(self.auto_run(self.resolve(c), c) for c in classes) if self.evaluation else False
        )
        return {
            scope: {
                "revision": (doc := self._document(scope)).revision,
                "policies": [policy.model_dump(mode="json") for policy in doc.orchestration],
            }
            for scope in ("global", "workspace")
        } | {
            "auto_run_eligible": eligible,
            "auto_run_reason": "paired_benefit" if eligible else "paired_evidence_missing",
        }

    def resolve(self, task_type: str) -> OrchestrationPolicy:
        # A workspace policy is a complete explicit override, not a mutable merge.
        for scope in ("workspace", "global"):
            policies = self._document(scope).orchestration
            for matcher in (task_type, "*"):
                for policy in policies:
                    if policy.status == "active" and policy.task_matcher == matcher:
                        return policy
        return OrchestrationPolicy(source="builtin")

    def put(self, policy: OrchestrationPolicy, *, expected_revision: int):
        if policy.source != "user":
            raise ValueError("only explicit user policies can be written")
        current = self._document(policy.scope)
        policies = {item.policy_id: item for item in current.orchestration}
        # The same document revision is shared with Skill/MCP edits. Retrying an
        # identical write is safe, but an intervening edit is never overwritten.
        saved = policy.model_copy(update={"revision": expected_revision + 1})
        if current.revision == expected_revision + 1 and policies.get(policy.policy_id) == saved:
            return self.view()
        policies[policy.policy_id] = saved
        updated = type(current).model_validate(
            current.model_dump() | {"orchestration": tuple(policies.values())}
        )
        try:
            if policy.scope == "global":
                self.store.write_global(updated, expected_revision=expected_revision)
            else:
                self.store.write_workspace(
                    self.workspace_id, updated, expected_revision=expected_revision
                )
        except ExtensionYamlConflict:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Orchestration policy revision conflict"
            ) from None
        return self.view()
