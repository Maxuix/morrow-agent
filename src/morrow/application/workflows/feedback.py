"""Feedback-to-Learning review; never an automatic orchestration policy writer."""

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.orchestration import GraphPlanningRequest, OrchestrationPolicy
from morrow.core.workflows.feedback import WorkflowFeedback, WorkflowPolicyCandidate

from .planning_features import local_features


def identity(prefix, *parts):
    return prefix + "_" + sha256_digest(canonical_json_bytes(parts))[:48]


def task_type(task):
    return local_features(
        GraphPlanningRequest(
            draft_id="wdraft_feedback",
            workflow_definition_id="feedback",
            task=task,
            use_model=False,
        )
    ).task_type


class WorkflowFeedbackService:
    def __init__(self, journal, *, workspace_id, policies=None, artifacts=None, active_model=None):
        self.journal = journal
        self.workspace_id = workspace_id
        self.records = journal.workflow_feedback
        self.policies = policies
        self.artifacts = artifacts
        self.active_model = active_model

    def role(self, node):
        version = self.journal.agent_definitions.get_version(
            self.workspace_id, node.agent_definition_ref.version_id
        )
        if version is None:
            return node.node_id
        source = version.source
        origin = source.derived_from_definition_id or source.definition_id
        if origin.startswith("builtin_"):
            return origin.removeprefix("builtin_")
        return node.node_id

    def model(self, node):
        if hasattr(node, "resolved_model_ref"):
            return node.resolved_model_ref
        version = self.journal.agent_definitions.get_version(
            self.workspace_id, node.agent_definition_ref.version_id
        )
        if version is None:
            return None
        selected = version.source.model_selection
        return self.active_model if selected == "invoking_active" else selected

    def run_task(self, run):
        if self.artifacts is None:
            raise ValueError("Workflow evaluation needs the Artifact authority")
        from morrow.core.workflows.contracts import TaskContract

        artifact = self.artifacts.get(run.input_artifacts[0].artifact_id)
        if artifact is None or artifact.byte_size > 16384:
            raise ValueError("Workflow input is unavailable")
        return TaskContract.model_validate_json(
            self.artifacts.read(artifact.artifact_id, max_bytes=artifact.byte_size).content
        )

    def capture_edit(
        self, before, after, *, subject_kind, subject_id, sample_id, task_class, edit_id
    ):
        if before.content_hash == after.content_hash:
            return ()
        changes = [("graph_edit", None, None)]
        old = {n.node_id: n for n in before.nodes}
        new = {n.node_id: n for n in after.nodes}
        old_roles = {self.role(n) for n in old.values()}
        new_roles = {self.role(n) for n in new.values()}
        if "planner" in old_roles - new_roles:
            changes.append(("removed_planner", "planner", None))
        if "reviewer" in new_roles - old_roles:
            changes.append(("added_reviewer", "reviewer", None))
        for key in old.keys() & new.keys():
            selected = self.model(new[key])
            if selected is not None and selected != self.model(old[key]):
                changes.append(("model_changed", self.role(new[key]), selected))
        values = []
        for index, (kind, role, model) in enumerate(changes):
            values.append(
                self.record(
                    WorkflowFeedback(
                        feedback_id=identity("wfb", self.workspace_id, edit_id, index),
                        workspace_id=self.workspace_id,
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        sample_id=sample_id,
                        task_type=task_class,
                        kind=kind,
                        role=role,
                        model=model,
                        before_hash=before.content_hash,
                        after_hash=after.content_hash,
                        created_at=self.journal.now(),
                    )
                )
            )
        return tuple(values)

    def record(self, feedback):
        def work(_):
            saved = self.records.put(feedback)
            self._review(saved)
            return saved

        return self.journal.transact(work)

    @staticmethod
    def semantic(feedback):
        return sha256_digest(
            canonical_json_bytes(
                {
                    "task_type": feedback.task_type,
                    "kind": feedback.kind,
                    "role": feedback.role,
                    "model": feedback.model.model_dump(mode="json") if feedback.model else None,
                    "template": feedback.template,
                }
            )
        )

    def _review(self, feedback):
        if feedback.kind == "graph_edit" or self.policies is None:
            return
        semantic = self.semantic(feedback)
        documents = self.policies.view()
        document_revision = documents["workspace"]["revision"]
        global_revision = documents["global"]["revision"]
        candidate_id = identity(
            "wpc", self.workspace_id, semantic, document_revision, global_revision
        )
        if self.records.get(WorkflowPolicyCandidate, self.workspace_id, candidate_id):
            return
        # Repeated clicks, retries, edits within a Draft, and continuation children
        # contribute at most one independent sample to a review.
        samples = {}
        for item in self.records.list(WorkflowFeedback, self.workspace_id):
            if self.semantic(item) == semantic:
                samples.setdefault(item.sample_id, item)
        if len(samples) < 2:
            return
        evidence = tuple(item.feedback_id for item in list(samples.values())[:32])
        base = self.policies.resolve(feedback.task_type)
        body = base.model_dump() | {
            "policy_id": base.policy_id
            if base.scope == "workspace" and base.task_matcher == feedback.task_type
            else "learned_" + feedback.task_type,
            "scope": "workspace",
            "task_matcher": feedback.task_type,
            "source": "user",
            "evidence": evidence,
            "status": "active",
        }
        kind = feedback.kind
        if kind in {"too_complex", "model_expensive"}:
            body.update(
                multi_agent=False,
                preferred_template="direct",
                excluded_templates=(),
                required_roles=(),
                review_requirement="adaptive",
                excluded_roles=tuple(r for r in base.excluded_roles if r != "direct"),
            )
        elif kind == "removed_planner":
            body.update(
                required_roles=tuple(r for r in base.required_roles if r != "planner"),
                excluded_roles=tuple(sorted(set(base.excluded_roles) | {"planner"})),
            )
        elif kind in {"added_reviewer", "reviewer_useful", "missing_exploration"}:
            role = "explorer" if kind == "missing_exploration" else "reviewer"
            body.update(
                multi_agent=True,
                preferred_template=None,
                required_roles=tuple(sorted(set(base.required_roles) | {role})),
                excluded_roles=tuple(r for r in base.excluded_roles if r != role),
            )
            if role == "reviewer":
                body["review_requirement"] = "required"
        elif kind == "reviewer_not_useful":
            body.update(
                review_requirement="skip",
                required_roles=tuple(r for r in base.required_roles if r != "reviewer"),
            )
        elif kind == "model_changed":
            body["model_preferences_by_role"] = base.model_preferences_by_role | {
                feedback.role: feedback.model
            }
        elif kind == "prefer_template":
            body.update(
                preferred_template=feedback.template,
                excluded_templates=tuple(
                    t for t in base.excluded_templates if t != feedback.template
                ),
            )
        elif kind == "avoid_template":
            body.update(
                preferred_template=None
                if base.preferred_template == feedback.template
                else base.preferred_template,
                excluded_templates=tuple(
                    sorted(set(base.excluded_templates) | {feedback.template})
                ),
            )
        policy = OrchestrationPolicy.model_validate(body)
        self.records.put(
            WorkflowPolicyCandidate(
                candidate_id=candidate_id,
                workspace_id=self.workspace_id,
                semantic_key=semantic,
                task_type=feedback.task_type,
                feedback_kind=kind,
                evidence_ids=evidence,
                proposed_policy=policy,
                expected_document_revision=document_revision,
                expected_global_revision=global_revision,
                created_at=self.journal.now(),
            )
        )

    def submit(self, request):
        run = self.journal.workflows.get_run(self.workspace_id, request.workflow_run_id)
        if run is None or not run.status.terminal or run.status.value == "superseded":
            raise ValueError("post-run feedback requires a settled Workflow")
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        if request.kind.startswith("reviewer_") and not any(
            self.role(n) == "reviewer" for n in revision.nodes
        ):
            raise ValueError("Reviewer feedback requires a Reviewer")
        if (request.kind in {"prefer_template", "avoid_template"}) != (
            request.template is not None
        ):
            raise ValueError("template preference requires exactly one template")
        feedback_id = identity("wfb", self.workspace_id, request.command_id)
        existing = self.records.get(WorkflowFeedback, self.workspace_id, feedback_id)
        value = WorkflowFeedback(
            feedback_id=feedback_id,
            workspace_id=self.workspace_id,
            subject_kind="run",
            subject_id=run.workflow_run_id,
            sample_id=run.root_task_run_id,
            task_type=task_type(self.run_task(run)),
            kind=request.kind,
            template=request.template,
            created_at=existing.created_at if existing else self.journal.now(),
        )
        return self.record(value)

    def review_view(self, page=0):
        values = self.records.list(WorkflowPolicyCandidate, self.workspace_id)
        current = self.policies.view()
        return {
            "items": [
                {
                    **c.model_dump(mode="json"),
                    "stale": c.status == "proposed"
                    and (
                        current["workspace"]["revision"] != c.expected_document_revision
                        or current["global"]["revision"] != c.expected_global_revision
                    ),
                }
                for c in values[page * 50 : (page + 1) * 50]
            ],
            "next_cursor": str(page + 1) if len(values) > (page + 1) * 50 else None,
        }

    def decide(self, candidate_id, request):
        candidate = self.records.get(WorkflowPolicyCandidate, self.workspace_id, candidate_id)
        if candidate is None:
            raise ValueError("Workflow policy candidate is missing")
        if candidate.decision_command_id == request.command_id and candidate.status in {
            "accepted",
            "rejected",
        }:
            if candidate.status != ("accepted" if request.action == "accept" else "rejected"):
                raise ValueError("decision command conflict")
            return candidate
        recovering = (
            candidate.status == "applying"
            and candidate.decision_command_id == request.command_id
            and request.action == "accept"
        )
        if not recovering and (
            candidate.status != "proposed" or candidate.row_version != request.expected_row_version
        ):
            raise ApplicationError(
                ApplicationErrorCode.STALE, "Workflow candidate decision is stale"
            )
        if request.action == "accept":
            if not recovering:
                documents = self.policies.view()
                if (
                    documents["workspace"]["revision"] != candidate.expected_document_revision
                    or documents["global"]["revision"] != candidate.expected_global_revision
                ):
                    raise ApplicationError(
                        ApplicationErrorCode.STALE, "Policy changed; review a fresh candidate"
                    )
                candidate = self.records.put(
                    candidate.model_copy(
                        update={
                            "status": "applying",
                            "decision_command_id": request.command_id,
                            "row_version": candidate.row_version + 1,
                        }
                    ),
                    expected_row_version=candidate.row_version,
                )
            # Intent is committed before YAML. The existing OCC owner permits an
            # identical retry after interruption, never overwrites a later user edit.
            documents = self.policies.view()
            if (
                documents["workspace"]["revision"] == candidate.expected_document_revision
                and documents["global"]["revision"] != candidate.expected_global_revision
            ):
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Global policy changed before publication"
                )
            self.policies.put(
                candidate.proposed_policy, expected_revision=candidate.expected_document_revision
            )
        return self.records.put(
            candidate.model_copy(
                update={
                    "status": "accepted" if request.action == "accept" else "rejected",
                    "decision_command_id": request.command_id,
                    "row_version": candidate.row_version + 1,
                }
            ),
            expected_row_version=candidate.row_version,
        )
