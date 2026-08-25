"""Foreground/manual orchestration for a single Preference Review attempt.

S59 owns durable leases and scheduling.  This runner deliberately performs no job claim or status
mutation; it composes the frozen context, one no-tool Reviewer call, and the deterministic Inbox
pipeline for offline/manual execution.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

from morrow.application.preferences.context import PreferenceReviewContextBuilder
from morrow.application.preferences.proposals import (
    PreferenceProposalPipeline,
    PreferenceProposalPipelineResult,
)
from morrow.core.models import ModelRef
from morrow.core.ports import IdSource
from morrow.core.preference_persistence_models import (
    PreferenceEvidence,
    PreferenceReviewJob,
)
from morrow.core.preference_review import PreferenceReviewContext, PreferenceReviewOutput
from morrow.core.runtime_policy import REVIEW_MAX_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PreferenceReviewRunResult:
    job: PreferenceReviewJob
    evidence: PreferenceEvidence
    context: PreferenceReviewContext
    output: PreferenceReviewOutput
    pipeline: PreferenceProposalPipelineResult

    @property
    def proposals(self):
        return self.pipeline.proposals


class DeterministicPreferenceReviewer:
    """Offline-safe fallback which returns no semantic operations."""

    prompt_version = "preference-v2"
    schema_version = "preference-operations-v2"

    async def review(self, context, *, model: ModelRef, timeout_seconds: float):
        del context, model, timeout_seconds
        return PreferenceReviewOutput()


class PreferenceReviewRunner:
    """Run one explicit/manual Review without holding a transaction across the await."""

    def __init__(
        self,
        *,
        journal,
        workspace_id: str,
        id_source: IdSource,
        clock,
        reviewer=None,
        model: ModelRef | None = None,
        timeout_seconds: float = REVIEW_MAX_TIMEOUT_SECONDS / 2,
        context_builder: PreferenceReviewContextBuilder | None = None,
        pipeline: PreferenceProposalPipeline | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
        ):
            raise ValueError("Preference Review timeout is invalid")
        if timeout_seconds <= 0 or timeout_seconds > REVIEW_MAX_TIMEOUT_SECONDS:
            raise ValueError("Preference Review timeout is outside the supported range")
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock
        self.reviewer = reviewer or DeterministicPreferenceReviewer()
        self.model = model or ModelRef(provider_id="deterministic", model_id="preference-v2")
        self.timeout_seconds = timeout_seconds
        self.contexts = context_builder or PreferenceReviewContextBuilder(
            journal=journal,
            workspace_id=workspace_id,
        )
        self.proposals = pipeline or PreferenceProposalPipeline(
            journal=journal,
            workspace_id=workspace_id,
            id_source=id_source,
            clock=clock,
        )

    async def run(
        self,
        job_id: str,
        *,
        current_user_record=None,
        current_user_message: str | None = None,
        current_user_record_id: str | None = None,
        recent_dialogue=(),
    ) -> PreferenceReviewRunResult:
        repository = getattr(self.journal, "preference_journal", self.journal)
        job = repository.get_preference_review_job(self.workspace_id, job_id)
        if job is None:
            raise ValueError("Preference Review job is missing")
        evidence_rows = repository.list_preference_evidence(
            self.workspace_id, job_id=job.job_id, limit=2
        )
        if len(evidence_rows) != 1:
            raise ValueError("Preference Review requires exactly one current-user Evidence row")
        evidence = evidence_rows[0]
        context = self.contexts.build(
            job=job,
            evidence=evidence,
            current_user_record=current_user_record,
            current_user_message=current_user_message,
            current_user_record_id=current_user_record_id,
            recent_dialogue=recent_dialogue,
        )
        response = await asyncio.wait_for(
            self.reviewer.review(
                context,
                model=self.model,
                timeout_seconds=self.timeout_seconds,
            ),
            timeout=self.timeout_seconds,
        )
        output = PreferenceReviewOutput.model_validate(response, strict=True)
        pipeline = self.proposals.persist(job, evidence, output)
        return PreferenceReviewRunResult(job, evidence, context, output, pipeline)


__all__ = [
    "DeterministicPreferenceReviewer",
    "PreferenceReviewRunResult",
    "PreferenceReviewRunner",
]
