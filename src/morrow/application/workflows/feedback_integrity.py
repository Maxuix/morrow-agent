"""Feedback references join the shared Doctor/Backup verification boundary."""

from morrow.core.workflows.drafts import WorkflowDraft
from morrow.core.workflows.feedback import (
    WorkflowEvaluation,
    WorkflowFeedback,
    WorkflowPolicyCandidate,
)


def verify_feedback_rows(executor, runs, revisions):
    if not tuple(executor.execute("SELECT name FROM sqlite_master WHERE name='workflow_feedback'")):
        return
    drafts = {
        d.draft_id: d
        for (body,) in executor.execute("SELECT body_json FROM workflow_drafts")
        for d in (WorkflowDraft.model_validate_json(body),)
    }
    feedback = {}
    for record_id, ws, body in executor.execute("SELECT * FROM workflow_feedback"):
        value = WorkflowFeedback.model_validate_json(body)
        if (value.feedback_id, value.workspace_id) != (record_id, ws):
            raise ValueError("feedback identity mismatch")
        subject = (
            drafts[value.subject_id] if value.subject_kind == "draft" else runs[value.subject_id]
        )
        sample = subject.draft_id if value.subject_kind == "draft" else subject.root_task_run_id
        if subject.workspace_id != ws or value.sample_id != sample:
            raise ValueError("feedback subject ownership mismatch")
        feedback[record_id] = value
    for record_id, ws, body in executor.execute("SELECT * FROM workflow_policy_candidates"):
        value = WorkflowPolicyCandidate.model_validate_json(body)
        evidence = [feedback[e] for e in value.evidence_ids]
        if (value.candidate_id, value.workspace_id) != (record_id, ws):
            raise ValueError("candidate identity mismatch")
        if any(
            e.workspace_id != ws or e.task_type != value.task_type or e.kind != value.feedback_kind
            for e in evidence
        ):
            raise ValueError("candidate evidence mismatch")
        if (
            len({e.sample_id for e in evidence}) < 2
            or value.proposed_policy.evidence != value.evidence_ids
        ):
            raise ValueError("candidate independent evidence missing")
        if (
            value.proposed_policy.scope != "workspace"
            or value.proposed_policy.task_matcher != value.task_type
        ):
            raise ValueError("candidate policy scope mismatch")
    paired_runs = set()
    for record_id, ws, body in executor.execute("SELECT * FROM workflow_evaluations"):
        value = WorkflowEvaluation.model_validate_json(body)
        if (value.evaluation_id, value.workspace_id) != (record_id, ws):
            raise ValueError("evaluation identity mismatch")
        multi = runs[value.multi_run_id]
        if multi.workspace_id != ws or len(revisions[multi.workflow_revision_id].nodes) < 2:
            raise ValueError("Multi evaluation reference mismatch")
        if value.kind == "paired":
            direct = runs[value.direct_run_id]
            pair = {value.multi_run_id, value.direct_run_id}
            if (
                direct.workspace_id != ws
                or direct.root_task_run_id == multi.root_task_run_id
                or paired_runs & pair
            ):
                raise ValueError("paired evaluation independence mismatch")
            if len(revisions[direct.workflow_revision_id].nodes) != 1:
                raise ValueError("Direct evaluation reference mismatch")
            paired_runs.update(pair)
