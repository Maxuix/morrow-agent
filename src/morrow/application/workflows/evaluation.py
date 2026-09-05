"""Read-only run metrics and explicit user-paired benefit evidence, without workers."""

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.workflows.feedback import WorkflowEvaluation, WorkflowFeedback
from morrow.core.workflows.runs import WorkflowStatus

from .feedback import identity, task_type
from .queries import WorkflowQueryService


def promotion_state(evaluations, task_class):
    pairs = [e for e in evaluations if e.task_type == task_class and e.kind == "paired"]
    # Two independent pairs, all beneficial. A no-benefit observation closes
    # eligibility immediately. This is product bookkeeping, not a runtime cap.
    promoted = len(pairs) >= 2 and all(e.benefit for e in pairs)
    return {
        "task_type": task_class,
        "paired_count": len(pairs),
        "beneficial_count": sum(e.benefit for e in pairs),
        "promoted": promoted,
        "reason": "paired_benefit"
        if promoted
        else "no_benefit"
        if any(not e.benefit for e in pairs)
        else "paired_evidence_missing",
        "evidence_ids": [e.evaluation_id for e in pairs],
    }


class WorkflowEvaluationService:
    def __init__(self, feedback):
        self.feedback = feedback
        self.journal = feedback.journal
        self.workspace_id = feedback.workspace_id
        self.records = feedback.records
        self.queries = WorkflowQueryService(self.journal, workspace_id=self.workspace_id)

    def record(self, request):
        multi = self.queries.get_run_view(request.multi_run_id)
        if (
            multi is None
            or len(multi.revision.nodes) < 2
            or multi.run.status != WorkflowStatus.COMPLETED
        ):
            raise ValueError("evaluation requires a completed Multi Workflow")
        task = self.feedback.run_task(multi.run)
        digest = sha256_digest(canonical_json_bytes(task.model_dump(mode="json")))
        direct_requests = request.direct_estimated_requests
        kind = "estimate"
        if request.direct_run_id is not None:
            kind = "paired"
            direct = self.queries.get_run_view(request.direct_run_id)
            if (
                direct is None
                or len(direct.revision.nodes) != 1
                or direct.run.status != WorkflowStatus.COMPLETED
                or direct.run.root_task_run_id == multi.run.root_task_run_id
                or direct.run.run_relation != "initial"
                or multi.run.run_relation != "initial"
                or self.feedback.run_task(direct.run) != task
            ):
                raise ValueError(
                    "pair needs independent completed Direct/Multi runs of the same task"
                )
            if request.direct_estimated_requests is not None:
                raise ValueError("a paired run cannot carry an estimate")
            direct_requests = direct.lineage_agent_generation_request_count
        elif direct_requests is None:
            raise ValueError("provide a Direct run or an explicit request estimate")
        evaluation_id = identity("weval", self.workspace_id, request.command_id)
        existing = self.records.get(WorkflowEvaluation, self.workspace_id, evaluation_id)
        value = WorkflowEvaluation(
            evaluation_id=evaluation_id,
            workspace_id=self.workspace_id,
            task_type=task_type(task),
            task_digest=digest,
            kind=kind,
            multi_run_id=request.multi_run_id,
            direct_run_id=request.direct_run_id,
            direct_requests=direct_requests,
            multi_requests=multi.lineage_agent_generation_request_count,
            direct_quality=request.direct_quality,
            multi_quality=request.multi_quality,
            created_at=existing.created_at if existing else self.journal.now(),
        )

        def work(_):
            # Reusing a run cannot inflate the number of independent evaluations.
            for prior in self.records.list(WorkflowEvaluation, self.workspace_id):
                if (
                    prior.evaluation_id == evaluation_id
                    or prior.kind != "paired"
                    or kind != "paired"
                ):
                    continue
                if {prior.multi_run_id, prior.direct_run_id} & {
                    value.multi_run_id,
                    value.direct_run_id,
                }:
                    raise ValueError("a run already belongs to a paired evaluation")
            return self.records.put(value)

        return self.journal.transact(work)

    def promotion(self, task_class):
        return promotion_state(self.records.list(WorkflowEvaluation, self.workspace_id), task_class)

    def summary(self, run):
        view = self.queries.get_run_view(run.workflow_run_id)
        # Select evidence anchored to this Run, never the latest outcome of a
        # root that might already have a different rerun/continuation result.
        outcomes = self.journal.list_task_outcomes(self.workspace_id, run.root_task_run_id)
        outcome = next(
            (
                o
                for o in reversed(outcomes)
                if any(
                    ref.kind.value == "workflow_run" and ref.reference_id == run.workflow_run_id
                    for ref in o.evidence_refs
                )
            ),
            None,
        )
        required = {ref.node_id for ref in view.revision.required_outputs}
        dependencies = {(e.from_node_id, e.to_node_id) for e in view.revision.edges}
        dependencies.update(
            (b.node_output.node_id, n.node_id)
            for n in view.revision.nodes
            for b in n.input_bindings
            if b.source == "node_output"
        )
        while True:
            expanded = required | {a for a, b in dependencies if b in required}
            if expanded == required:
                break
            required = expanded
        executed = {n.node.node_id for n in view.nodes if n.node.status == WorkflowStatus.COMPLETED}
        dead = [
            n.node_id
            for n in view.revision.nodes
            if n.node_id in executed - required and n.access_mode == "read"
        ]
        reviews = []
        for output in view.effective_outputs:
            node = next(n for n in view.revision.nodes if n.node_id == output.node_id)
            if self.feedback.role(node) != "reviewer":
                continue
            fact = {
                "node_id": node.node_id,
                "artifact_id": output.binding.artifact_id,
                "findings": [],
                "verdict": None,
                "status": "unavailable",
            }
            if output.artifact and output.artifact.byte_size <= 32768 and self.feedback.artifacts:
                from morrow.core.workflows.contracts import ReviewReport, TextResult

                model = {"ReviewReport": ReviewReport, "TextResult": TextResult}.get(
                    output.binding.contract.kind
                )
                if model:
                    try:
                        payload = model.model_validate_json(
                            self.feedback.artifacts.read(
                                output.binding.artifact_id, max_bytes=output.artifact.byte_size
                            ).content
                        )
                        fact.update(
                            findings=list(payload.findings)
                            if isinstance(payload, ReviewReport)
                            else [payload.excerpt],
                            verdict=getattr(payload, "verdict", None),
                            status="available",
                        )
                    except (ValueError, OSError):
                        pass
            reviews.append(fact)
        return {
            "workflow_run_id": run.workflow_run_id,
            "root_task_run_id": run.root_task_run_id,
            "lineage_root_run_id": run.effective_lineage_budget_root_run_id,
            "mode": "direct" if len(view.revision.nodes) == 1 else "multi",
            "task_type": task_type(self.feedback.run_task(run)),
            "status": run.status.value,
            "result_status": run.result_status,
            "node_count": len(view.revision.nodes),
            "requests": view.agent_generation_request_count,
            "lineage_requests": view.lineage_agent_generation_request_count,
            "usage_availability": view.usage_availability,
            "outcome": {
                "outcome_id": outcome.outcome_id,
                "summary": outcome.summary,
                "task_status": outcome.task_status.value,
            }
            if outcome
            else None,
            "verification_results": list(outcome.validation_facts) if outcome else [],
            "reviewer_findings": reviews,
            "dead_node_ids": dead,
            "nodes": [
                {
                    "node_id": n.node.node_id,
                    "status": n.node.status.value,
                    "requests": n.agent_generation_request_count,
                }
                for n in view.nodes
            ],
        }

    def dashboard(self, page=0):
        runs = self.journal.workflows.list_runs(self.workspace_id)
        feedback = self.records.list(WorkflowFeedback, self.workspace_id)
        edits = [f for f in feedback if f.kind == "graph_edit"]
        edited_samples = {f.sample_id for f in edits}
        drafts = self.journal.workflows.list_drafts(self.workspace_id)
        edited_revisions = {
            d.frozen_workflow_revision_id
            for d in drafts
            if d.draft_id in edited_samples and d.frozen_workflow_revision_id
        }
        modified_roots = {
            r.root_task_run_id
            for r in runs
            if r.root_task_run_id in edited_samples or r.workflow_revision_id in edited_revisions
        }
        judgments = {
            f.sample_id: f.kind
            for f in feedback
            if f.kind in {"reviewer_useful", "reviewer_not_useful"}
        }
        summaries = [
            {**self.summary(r), "user_modified": r.root_task_run_id in modified_roots}
            for r in runs[page * 50 : (page + 1) * 50]
        ]
        classes = ("implementation", "refactor", "research", "explanation", "diagnosis", "general")
        promotion = []
        for cls in classes:
            gate = self.promotion(cls)
            policy = self.feedback.policies.resolve(cls) if self.feedback.policies else None
            gate.update(
                auto_run_eligible=bool(
                    gate["promoted"]
                    and policy
                    and policy.source == "user"
                    and policy.auto_run_mode == "allow_promoted"
                ),
                task_class_replan_eligible=bool(
                    gate["promoted"]
                    and policy
                    and policy.source == "user"
                    and policy.auto_replan_mode == "allow_low_risk"
                ),
            )
            promotion.append(gate)
        roots = {r.root_task_run_id for r in runs}
        return {
            "runs": summaries,
            "next_cursor": str(page + 1)
            if max(
                len(runs),
                len(feedback),
                len(self.records.list(WorkflowEvaluation, self.workspace_id)),
            )
            > (page + 1) * 50
            else None,
            "metrics": {
                "root_task_count": len(roots),
                "edited_task_count": len(modified_roots),
                "edit_frequency": len(modified_roots) / len(roots) if roots else None,
                "draft_edit_count": sum(f.subject_kind == "draft" for f in edits),
                "run_edit_count": sum(f.subject_kind == "run" for f in edits),
                "reviewer_rated_tasks": len(judgments),
                "reviewer_useful_tasks": sum(v == "reviewer_useful" for v in judgments.values()),
                "reviewer_value": sum(v == "reviewer_useful" for v in judgments.values())
                / len(judgments)
                if judgments
                else None,
            },
            "feedback": [f.model_dump(mode="json") for f in feedback[page * 50 : (page + 1) * 50]],
            "evaluations": [
                {**e.model_dump(mode="json"), "benefit": e.benefit}
                for e in self.records.list(WorkflowEvaluation, self.workspace_id)[
                    page * 50 : (page + 1) * 50
                ]
            ],
            "promotion": promotion,
        }
