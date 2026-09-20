"""Sole automatic proposer; compilation, publication and handoff retain their owners."""

from dataclasses import asdict

from morrow.application.workflows.compiler import WorkflowCompilationError
from morrow.application.workflows.patch_preview import diff_compiled
from morrow.application.workflows.planning_features import local_features
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.orchestration import GraphPlanningRequest, OrchestrationPolicy
from morrow.core.workflows.contracts import TaskContract
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowDefinitionSource,
)
from morrow.core.workflows.patches import FutureGraphPatch
from morrow.core.workflows.planning import PlanWorkflowRequest, PrepareWorkflowChangeRequest
from morrow.core.workflows.replan import ReplanProposal
from morrow.core.workflows.runs import WorkflowStatus


def revision_source(revision):
    body = {
        key: getattr(revision, key)
        for key in WorkflowDefinitionSource.model_fields
        if key not in {"nodes", "default_budget"}
    }
    return WorkflowDefinitionSource(
        **body,
        default_budget=revision.budget,
        nodes=tuple(
            AgentNodeSource(**{key: getattr(node, key) for key in AgentNodeSource.model_fields})
            for node in revision.nodes
        ),
    )


class ReplanCoordinator:
    def __init__(self, patches, transitions, *, policies=None, active_model=None):
        self.patches = patches
        self.transitions = transitions
        self.journal = patches.journal
        self.workspace_id = patches.workspace_id
        self.policies = policies
        self.active_model = active_model
        self.on_applied = None
        self.planning = None
        self.changes = None
        self._pending_plan = None

    def _policy(self, run_id):
        if self.policies is None:
            return OrchestrationPolicy(source="builtin")
        run = self.journal.workflows.get_run(self.workspace_id, run_id)
        artifacts = self.patches.finalizer.artifacts
        if artifacts is None:
            return OrchestrationPolicy(source="builtin")
        # Reuse Subplan 8's no-model classifier on the immutable root input.
        # Resolving every run as "general" would ignore explicit task policies.
        artifact = artifacts.get(run.input_artifacts[0].artifact_id)
        if artifact is None or artifact.byte_size > 16384:
            return OrchestrationPolicy(source="builtin")
        task = TaskContract.model_validate_json(
            artifacts.read(artifact.artifact_id, max_bytes=artifact.byte_size).content
        )
        features = local_features(
            GraphPlanningRequest(
                draft_id="wdraft_replan_policy",
                workflow_definition_id="replan_policy",
                task=task,
                use_model=False,
            )
        )
        return self.policies.resolve(features.task_type)

    def _automation_allowed(self, policy):
        if self.policies and hasattr(self.policies, "auto_replan"):
            return self.policies.auto_replan(policy)
        # Scripted clients can supply only resolve(); absent benefit state stays closed.
        return (
            policy.source == "user"
            and policy.auto_replan_mode == "allow_low_risk"
            and policy.task_matcher == "*"
        )

    def process_signals(self, run_id):
        """Consume settled closure evidence only; admission remains closed until pause commits."""
        signals = self.journal.workflows.list_replan_signals(
            self.workspace_id, run_id, pending_only=True
        )
        if not signals:
            return ()
        nodes = {
            n.node_run_id: n for n in self.journal.workflows.list_nodes(self.workspace_id, run_id)
        }
        if any(nodes[s.node_run_id].status is WorkflowStatus.RUNNING for s in signals):
            return ()
        run = self.journal.workflows.get_run(self.workspace_id, run_id)
        if not run.status.terminal and run.pending_terminal_intent is None:
            run = self.transitions.request_pause(run_id)
        if run.status is WorkflowStatus.DRAINING:
            return ()
        # Chat task-plans always go through the current LLM candidate path.
        session_id = self._session_for_run(run)
        chat_plan = (
            self.planning is not None
            and self.changes is not None
            and session_id is not None
            and self.planning.repo.binding(self.workspace_id, session_id) is not None
        )
        results = []
        if chat_plan:
            prepared = self._begin_fact_plan(run, signals)
            if prepared is not None:
                self._pending_plan = prepared
                results.append(prepared)
            return tuple(results)
        return ()

    def _session_for_run(self, run):
        task = self.journal.get_task_run(self.workspace_id, run.root_task_run_id)
        return None if task is None else task.session_id

    def _fact_digest(self, run, signals):
        return sha256_digest(
            canonical_json_bytes(
                {
                    "run": run.workflow_run_id,
                    "revision": run.workflow_revision_id,
                    "row_version": run.row_version,
                    "signals": [signal.signal_id for signal in signals],
                }
            )
        )

    def _replan_command_id(self, digest):
        base = "cmd_replan_" + digest[:24]
        for suffix in ("", "_2", "_3", "_4"):
            command_id = base if not suffix else base + suffix
            existing = self.planning.repo.command(self.workspace_id, command_id)
            if existing is None or existing.status in {"queued", "running", "succeeded"}:
                return command_id
        return base + "_x"

    def _task_contract(self, run):
        artifacts = getattr(self.patches.finalizer, "artifacts", None)
        if artifacts is None:
            return None
        artifact = artifacts.get(run.input_artifacts[0].artifact_id)
        if artifact is None or artifact.byte_size > 16384:
            return None
        return TaskContract.model_validate_json(
            artifacts.read(artifact.artifact_id, max_bytes=artifact.byte_size).content
        )

    def _begin_fact_plan(self, run, signals):
        if self.planning is None or self.changes is None:
            return None
        if run.status is not WorkflowStatus.PAUSED:
            return None
        session_id = self._session_for_run(run)
        if session_id is None:
            return None
        if self.planning.repo.binding(self.workspace_id, session_id) is None:
            return None
        nodes = self.journal.workflows.list_nodes(self.workspace_id, run.workflow_run_id)
        past = tuple(sorted(self.patches._past_node_ids(run)))
        past_set = set(past)
        future = tuple(node.node_id for node in nodes if node.node_id not in past_set)
        revision = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
        completed = []
        if revision is not None:
            contracts = {item.node_id: item.task_contract.objective for item in revision.nodes}
            for node in nodes:
                if node.node_id in past_set:
                    completed.append(
                        {
                            "node_id": node.node_id,
                            "status": node.status.value,
                            "objective": contracts.get(node.node_id, ""),
                        }
                    )
        digest = self._fact_digest(run, signals)
        bind_id = "cmd_replanb_" + digest[:24]
        plan_id = self._replan_command_id(digest)
        existing = self.planning.repo.command(self.workspace_id, plan_id)
        if existing is not None and existing.status in {"queued", "running", "succeeded"}:
            return existing
        task = self._task_contract(run)
        if task is None:
            return None
        self.changes.prepare(
            PrepareWorkflowChangeRequest(
                command_id=bind_id,
                session_id=session_id,
                origin_interaction_id=run.invoking_client_message_id or "replan",
                action_source="button",
                expected_run_row_version=run.row_version,
            )
        )
        binding = self.planning.repo.binding(self.workspace_id, session_id)
        draft = self.planning.drafts.get(binding.current_draft_id)
        impacts, conflict = {}, False
        for signal in signals:
            for fact in signal.request.affected_facts:
                if not fact.node_id:
                    continue
                previous = impacts.get(fact.node_id)
                if previous is not None and previous != fact.impact:
                    conflict = True
                impacts[fact.node_id] = fact.impact
        facts = {
            "signals": [signal.request.model_dump(mode="json") for signal in signals],
            "past_node_ids": list(past),
            "future_node_ids": list(future),
            "completed": completed,
            "parent_run_id": run.workflow_run_id,
            "parent_revision_id": run.workflow_revision_id,
            "parent_row_version": run.row_version,
            "conflicting_signals": conflict,
            "review_blocking": any(
                ref.kind == "submitted_slot" and ref.slot == "review"
                for signal in signals
                for ref in signal.request.evidence_refs
            ),
        }
        self.journal.transact(
            lambda _: self.planning.repo.event(
                self.workspace_id, session_id, {"replan": True, **facts}
            )
        )
        return self.planning.begin_replan(
            PlanWorkflowRequest(
                command_id=plan_id,
                session_id=session_id,
                origin_interaction_id=run.invoking_client_message_id or "replan",
                task=task,
                planning_binding_id=binding.planning_binding_id,
                base_draft_version=draft.draft.row_version,
                operation="revise",
            ),
            parent_row_version=run.row_version,
            signal_ids=tuple(signal.signal_id for signal in signals),
            past_node_ids=past,
            facts=facts,
        )

    async def dispatch_pending(self):
        prepared = self._pending_plan
        self._pending_plan = None
        if prepared is None or self.planning is None:
            return None

        async def command(work):
            return work()

        try:
            return await self.planning.dispatch(prepared, command=command)
        except ApplicationError:
            return None

    def propose(self, run_id, source, *, signal_ids=(), invalid_reason=None):
        source = WorkflowDefinitionSource.model_validate(source.model_dump())
        run = self.journal.workflows.get_run(self.workspace_id, run_id)
        if run is None:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Replan parent is missing")
        if self.active_model is None:
            base = self.journal.workflows.get_revision(self.workspace_id, run.workflow_revision_id)
            self.active_model = base.nodes[0].resolved_model_ref
        policy = self._policy(run_id)
        # Explicit policy and task-class benefit are separate from patch risk.
        mode = policy.auto_replan_mode if self._automation_allowed(policy) else "approval_only"
        proposal_id = self.patches.id_source.new_id("rprop")
        patch = FutureGraphPatch(
            workflow_patch_id="wpatch_" + proposal_id.removeprefix("rprop_"),
            workspace_id=self.workspace_id,
            parent_run_id=run_id,
            base_workflow_revision_id=run.workflow_revision_id,
            expected_parent_row_version=run.row_version,
            source=source,
            requested_by="replan_coordinator",
            request_kind="approved_proposal",
        )
        risk = None
        reason = invalid_reason or mode
        status = "invalid" if invalid_reason else "pending"
        try:
            validation = self.patches.validate(patch, active_model=self.active_model)
            risk = validation.risk
            if validation.compilation.candidate is None:
                status, reason = "invalid", "compile_failed"
        except ApplicationError as exc:
            status = "conflict" if exc.code is ApplicationErrorCode.CONFLICT else "invalid"
            reason = "stale_base" if status == "conflict" else "future_only_validation_failed"
        proposal = ReplanProposal(
            proposal_id=proposal_id,
            workspace_id=self.workspace_id,
            patch=patch,
            signal_ids=signal_ids,
            status=status,
            risk_level=risk.level if risk else "elevated",
            risk_reasons=risk.reasons if risk else (reason,),
            disposition_reason=(
                "approval_required"
                if risk and risk.level != "low" and status == "pending"
                else reason
            ),
            policy_id=policy.policy_id,
            policy_revision=policy.revision,
            created_at=self.patches.clock(),
        )
        self.journal.workflows.create_replan_proposal(proposal)
        if (
            status == "pending"
            and mode == "allow_low_risk"
            and risk
            and risk.level == "low"
            and run.status is WorkflowStatus.PAUSED
        ):
            try:
                return self.decide(
                    proposal_id, approved=True, expected_row_version=1, automatic=True
                )
            except (WorkflowCompilationError, ApplicationError) as exc:
                conflict = (
                    isinstance(exc, ApplicationError) and exc.code is ApplicationErrorCode.CONFLICT
                )
                failed = proposal.model_copy(
                    update={
                        "status": "conflict" if conflict else "invalid",
                        "disposition_reason": "stale_base"
                        if conflict
                        else "auto_apply_validation_failed",
                        "decided_by": "replan_coordinator",
                        "decided_at": self.patches.clock(),
                        "row_version": 2,
                    }
                )
                return self.journal.workflows.decide_replan_proposal(failed, expected_row_version=1)
        return proposal

    def decide(self, proposal_id, *, approved, expected_row_version, automatic=False):
        def work(txn):
            proposal = txn.workflows.get_replan_proposal(self.workspace_id, proposal_id)
            if proposal is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Replan proposal is missing")
            if proposal.status != "pending" or proposal.row_version != expected_row_version:
                raise ApplicationError(ApplicationErrorCode.CONFLICT, "Replan decision is stale")
            if automatic:
                policy = self._policy(proposal.patch.parent_run_id)
                if (
                    not approved
                    or proposal.risk_level != "low"
                    or policy.source != "user"
                    or not self._automation_allowed(policy)
                    or policy.auto_replan_mode != "allow_low_risk"
                ):
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID, "Replan needs explicit approval"
                    )
            child_id = None
            status, reason = "rejected", "user_rejected"
            if approved:
                parent = txn.workflows.get_run(self.workspace_id, proposal.patch.parent_run_id)
                if (
                    parent.row_version != proposal.patch.expected_parent_row_version
                    or parent.workflow_revision_id != proposal.patch.base_workflow_revision_id
                ):
                    status, reason = "conflict", "stale_base"
                elif parent.status is not WorkflowStatus.PAUSED:
                    raise ApplicationError(
                        ApplicationErrorCode.NEEDS_RECOVERY
                        if parent.status is WorkflowStatus.BLOCKED
                        else ApplicationErrorCode.INVALID,
                        "Replan handoff requires a fully paused parent",
                    )
                else:
                    if automatic:
                        validation = self.patches.validate(
                            proposal.patch, active_model=self.active_model
                        )
                        if validation.risk is None or validation.risk.level != "low":
                            raise ApplicationError(
                                ApplicationErrorCode.INVALID,
                                "Replan classification changed; approval is required",
                            )
                    applied = self.patches.apply(proposal.patch, active_model=self.active_model)
                    child_id = applied.child.workflow_run_id
                    status, reason = "applied", "low_risk_policy" if automatic else "user_approved"
            updated = proposal.model_copy(
                update={
                    "status": status,
                    "disposition_reason": reason,
                    "child_run_id": child_id,
                    "auto_applied": automatic and status == "applied",
                    "decided_by": "replan_coordinator" if automatic else "user",
                    "decided_at": self.patches.clock(),
                    "row_version": proposal.row_version + 1,
                }
            )
            decided = txn.workflows.decide_replan_proposal(
                updated, expected_row_version=expected_row_version
            )
            if decided.child_run_id and self.on_applied is not None:
                txn.after_commit(lambda: self.on_applied(decided))
            return decided

        return self.journal.transact(work)

    def list(self, run_id):
        return tuple(
            self.view(p)
            for p in self.journal.workflows.list_replan_proposals(self.workspace_id, run_id)
        )

    def view(self, proposal):
        base = self.journal.workflows.get_revision(
            self.workspace_id, proposal.patch.base_workflow_revision_id
        )
        # Diff the exact source as well as its compact structural summary: review
        # never hides task, permission or guardrail changes behind node IDs.
        source = revision_source(base)
        return {
            "proposal": proposal.model_dump(mode="json"),
            "signals": [
                s.model_dump(mode="json")
                for s in self.journal.workflows.list_replan_signals(
                    self.workspace_id, proposal.patch.parent_run_id
                )
                if s.signal_id in proposal.signal_ids
            ],
            "before": source.model_dump(mode="json"),
            "after": proposal.patch.source.model_dump(mode="json"),
            "diff": asdict(diff_compiled(source, proposal.patch.source)),
        }
