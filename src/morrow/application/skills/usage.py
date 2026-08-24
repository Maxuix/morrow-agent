"""Record-only Skill Usage facts and descriptive comparison queries."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.skills.usage import (
    SkillComparisonStatus,
    SkillUsage,
    SkillUsageComparison,
    SkillUsageMetrics,
    SkillUsageStatus,
)
from morrow.runtime.ids import RandomIdSource


class SkillUsageServiceError(RuntimeError):
    """A Skill Usage fact is invalid or cannot be persisted."""


def usage_digest(usage: SkillUsage) -> str:
    payload = usage.model_dump(mode="json")
    payload.pop("facts_digest", None)
    return sha256_digest(canonical_json_bytes(payload))


def _now(clock) -> datetime:
    value = clock()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SkillUsageService:
    """Observational Usage writer; it cannot modify a package or Binding."""

    def __init__(self, journal, *, workspace_id: str, id_source=None, clock=None) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source or RandomIdSource()
        self.clock = clock or (lambda: datetime.now(UTC))

    def record(
        self,
        *,
        agent_run_id: str,
        skill_id: str,
        version_id: str,
        activation_reason: str,
        status: SkillUsageStatus | str,
        task_run_id: str | None = None,
        selection_id: str | None = None,
        user_correction: bool = False,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
        tool_call_count: int = 0,
        artifact_refs=(),
        usage_id: str | None = None,
    ) -> SkillUsage:
        if selection_id is not None:
            selection = next(
                (
                    item
                    for item in self.journal.list_skill_selections(self.workspace_id, agent_run_id)
                    if item.selection_id == selection_id
                ),
                None,
            )
            if (
                selection is None
                or selection.skill_id != skill_id
                or selection.version_id != version_id
            ):
                raise SkillUsageServiceError("selection evidence does not match Skill usage")
            activation_reason = selection.activation_reason
        usage = SkillUsage(
            usage_id=usage_id or self.id_source.new_id("sug"),
            workspace_id=self.workspace_id,
            agent_run_id=agent_run_id,
            task_run_id=task_run_id,
            selection_id=selection_id,
            skill_id=skill_id,
            version_id=version_id,
            activation_reason=activation_reason,
            status=status,
            user_correction=user_correction,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            tool_call_count=tool_call_count,
            artifact_refs=tuple(artifact_refs),
            facts_digest="0" * 64,
            created_at=_now(self.clock),
        )
        usage = usage.model_copy(update={"facts_digest": usage_digest(usage)})
        existing = self.journal.get_skill_usage(self.workspace_id, usage.usage_id)
        if existing is not None:
            if existing.facts_digest != usage.facts_digest:
                raise SkillUsageServiceError("Skill usage ID was reused with different facts")
            return existing
        try:
            return self.journal.transact(lambda txn: txn.put_skill_usage(self.workspace_id, usage))
        except Exception as exc:
            raise SkillUsageServiceError("Skill usage could not be recorded") from exc

    def list(
        self,
        *,
        skill_id: str | None = None,
        version_id: str | None = None,
        agent_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SkillUsage, ...]:
        return self.journal.list_skill_usages(
            self.workspace_id,
            skill_id=skill_id,
            version_id=version_id,
            agent_run_id=agent_run_id,
            limit=limit,
        )

    def compare(
        self,
        *,
        skill_id: str,
        left_version_id: str,
        right_version_id: str,
        minimum_samples: int = 3,
    ) -> SkillUsageComparison:
        if not 1 <= minimum_samples <= 100:
            raise SkillUsageServiceError("minimum usage sample count is invalid")
        left = self.list(skill_id=skill_id, version_id=left_version_id, limit=1000)
        right = self.list(skill_id=skill_id, version_id=right_version_id, limit=1000)
        left_metrics = self._metrics(left)
        right_metrics = self._metrics(right)
        enough = (
            left_metrics.sample_count >= minimum_samples
            and right_metrics.sample_count >= minimum_samples
        )
        return SkillUsageComparison(
            skill_id=skill_id,
            left_version_id=left_version_id,
            right_version_id=right_version_id,
            status=SkillComparisonStatus.DESCRIPTIVE
            if enough
            else SkillComparisonStatus.INSUFFICIENT_DATA,
            reason=(
                "descriptive metrics only; no automatic superiority decision"
                if enough
                else f"at least {minimum_samples} terminal samples per version are required"
            ),
            left=left_metrics,
            right=right_metrics,
        )

    @staticmethod
    def _metrics(usages: tuple[SkillUsage, ...]) -> SkillUsageMetrics:
        success = sum(item.status is SkillUsageStatus.SUCCEEDED for item in usages)
        return SkillUsageMetrics(
            sample_count=len(usages),
            success_count=success,
            average_duration_ms=(
                round(sum(item.duration_ms for item in usages) / len(usages)) if usages else None
            ),
            average_tool_calls=(
                round(sum(item.tool_call_count for item in usages) / len(usages))
                if usages
                else None
            ),
        )


__all__ = ["SkillUsageService", "SkillUsageServiceError", "usage_digest"]
