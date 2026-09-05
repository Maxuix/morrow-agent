"""Read-only Workflow integrity shared by the current backup and doctor owners."""

import json
from datetime import UTC, datetime, timedelta

from morrow.core.agent_definitions import AgentDefinitionVersion
from morrow.core.domain import TaskOutcome, TaskOutcomeEvidenceKind
from morrow.core.workflows.contracts import ArtifactBinding, ContractRef
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowRevision,
    WorkflowRevisionRevocation,
)
from morrow.core.workflows.drafts import WorkflowDraft, WorkflowDraftStatus
from morrow.core.workflows.replan import ReplanProposal, ReplanSignal
from morrow.core.workflows.runs import (
    NodeRun,
    WorkflowArtifactImport,
    WorkflowExecutionNode,
    WorkflowRun,
)


def _first(executor, sql, parameters):
    rows = tuple(executor.execute(sql, parameters))
    return rows[0] if rows else None


def verify_workflow_rows(executor):
    if not tuple(
        executor.execute("SELECT name FROM sqlite_master WHERE name='workflow_revisions'")
    ):
        return True, ()
    try:
        revisions = {}
        for rid, ws, did, number, digest, body in executor.execute(
            "SELECT * FROM workflow_revisions"
        ):
            value = WorkflowRevision.model_validate_json(body)
            if (rid, ws, did, number, digest) != (
                value.workflow_revision_id,
                value.workspace_id,
                value.workflow_definition_id,
                value.revision,
                value.content_hash,
            ):
                raise ValueError("revision identity mismatch")
            revisions[rid] = value
        for value in revisions.values():
            if value.parent_workflow_revision_id:
                parent = revisions[value.parent_workflow_revision_id]
                if (parent.workspace_id, parent.workflow_definition_id) != (
                    value.workspace_id,
                    value.workflow_definition_id,
                ):
                    raise ValueError("revision lineage mismatch")
                if value.revision > 0 and (
                    parent.revision <= 0 or parent.revision != value.revision - 1
                ):
                    raise ValueError("published revision lineage mismatch")
            expected = {(n.node_id, n.agent_definition_ref.version_id) for n in value.nodes}
            actual = set(
                executor.execute(
                    "SELECT node_id, agent_definition_version_id FROM workflow_revision_nodes WHERE workflow_revision_id=?",
                    (value.workflow_revision_id,),
                )
            )
            if actual != expected:
                raise ValueError("revision nodes mismatch")

            for node in value.nodes:
                row = _first(
                    executor,
                    "SELECT body_json FROM agent_definition_versions WHERE version_id=?",
                    (node.agent_definition_ref.version_id,),
                )
                agent = AgentDefinitionVersion.model_validate_json(row[0])
                if (agent.workspace_id, agent.source.definition_id, agent.content_hash) != (
                    value.workspace_id,
                    node.agent_definition_ref.definition_id,
                    node.agent_definition_ref.content_hash,
                ):
                    raise ValueError("Agent reference mismatch")

        for (
            draft_id,
            workspace_id,
            definition_id,
            status,
            row_version,
            body,
            *_timestamps,
        ) in executor.execute("SELECT * FROM workflow_drafts"):
            draft = WorkflowDraft.model_validate_json(body)
            if (
                draft.draft_id,
                draft.workspace_id,
                draft.source.workflow_definition_id,
                draft.status.value,
                draft.row_version,
            ) != (draft_id, workspace_id, definition_id, status, row_version):
                raise ValueError("Workflow Draft identity mismatch")
            if draft.status is WorkflowDraftStatus.FROZEN:
                revision = revisions.get(draft.frozen_workflow_revision_id)
                if (
                    revision is None
                    or revision.workflow_definition_id != draft.source.workflow_definition_id
                    or revision.source_hash != draft.source_hash
                ):
                    raise ValueError("frozen Workflow Draft Revision mismatch")
        for ws, did, rid, body in executor.execute("SELECT * FROM workflow_definition_heads"):
            value = WorkflowDefinitionHead.model_validate_json(body)
            revision = revisions[rid]
            if (ws, did, rid) != (
                value.workspace_id,
                value.workflow_definition_id,
                value.workflow_revision_id,
            ) or (ws, did) != (revision.workspace_id, revision.workflow_definition_id):
                raise ValueError("head mismatch")
            if revision.revision <= 0:
                raise ValueError("Definition head references a detached Revision")
            if (value.source_revision, value.source_hash) != (
                revision.source_revision,
                revision.source_hash,
            ):
                raise ValueError("head source evidence mismatch")
        for rid, body in executor.execute("SELECT * FROM workflow_revision_revocations"):
            value = WorkflowRevisionRevocation.model_validate_json(body)
            if (rid, revisions[rid].workspace_id) != (
                value.workflow_revision_id,
                value.workspace_id,
            ):
                raise ValueError("revocation mismatch")
        runs = {}
        for (
            run_id,
            ws,
            rid,
            root_id,
            status,
            pause_requested,
            run_relation,
            budget_root,
            parent_run_id,
            body,
        ) in executor.execute("SELECT * FROM workflow_runs"):
            run = WorkflowRun.model_validate_json(body)
            root = _first(
                executor,
                "SELECT workspace_id, purpose FROM task_runs WHERE task_run_id=?",
                (root_id,),
            )
            if (
                (run_id, ws, rid, root_id, status)
                != (
                    run.workflow_run_id,
                    run.workspace_id,
                    run.workflow_revision_id,
                    run.root_task_run_id,
                    run.status.value,
                )
                or (
                    pause_requested,
                    run_relation,
                    budget_root,
                    parent_run_id,
                )
                != (
                    1 if run.pause_requested else 0,
                    run.run_relation,
                    run.effective_lineage_budget_root_run_id,
                    run.parent_run_id,
                )
                or root != (ws, "user")
                or revisions[rid].workspace_id != ws
                or run.budget_snapshot != revisions[rid].budget
            ):
                raise ValueError("run ownership mismatch")
            runs[run_id] = run
        for run in runs.values():
            if run.started_at is None:
                raise ValueError("run admission timestamp is missing")
            if run.run_relation == "continuation":
                parent = runs[run.parent_run_id]
                if (
                    run.lineage_budget_root_run_id != parent.effective_lineage_budget_root_run_id
                    or run.admission_deadline_at != parent.admission_deadline_at
                ):
                    raise ValueError("continuation lineage facts mismatch")
            else:
                timeout = run.budget_snapshot.admission_timeout_seconds
                expected_deadline = (
                    run.started_at + timedelta(seconds=timeout) if timeout is not None else None
                )
                if run.admission_deadline_at != expected_deadline:
                    raise ValueError("run deadline mismatch")
        nodes = {}
        for node_id, ws, run_id, nid, attempt, status, body in executor.execute(
            "SELECT * FROM workflow_node_runs"
        ):
            node = NodeRun.model_validate_json(body)
            if (node_id, ws, run_id, nid, attempt, status) != (
                node.node_run_id,
                node.workspace_id,
                node.workflow_run_id,
                node.node_id,
                node.attempt,
                node.status.value,
            ) or runs[run_id].workspace_id != ws:
                raise ValueError("node identity mismatch")
            if node.leaf_task_run_id:
                leaf = _first(
                    executor,
                    "SELECT workspace_id, session_id, purpose FROM task_runs WHERE task_run_id=?",
                    (node.leaf_task_run_id,),
                )
                workflow_run = runs[run_id]
                declared = next(
                    candidate
                    for candidate in revisions[workflow_run.workflow_revision_id].nodes
                    if candidate.node_id == node.node_id
                )
                if declared.conversation_scope == "invoking_session":
                    valid_leaf = (
                        node.leaf_task_run_id == workflow_run.root_task_run_id
                        and leaf == (ws, node.conversation_session_id, "user")
                    )
                else:
                    valid_leaf = leaf == (ws, node.conversation_session_id, "workflow_node")
                if not valid_leaf:
                    raise ValueError("leaf ownership mismatch")
                if node.parallel_read_digest is not None:
                    permission = _first(
                        executor,
                        "SELECT p.workspace_read_only, p.access_scope, p.grant_id, "
                        "p.tool_schema_digest, r.snapshot_json FROM agent_runs r "
                        "JOIN permission_snapshots p ON p.permission_snapshot_id=r.permission_snapshot_id "
                        "WHERE r.agent_run_id=? AND p.agent_run_id=r.agent_run_id",
                        (node.agent_run_id,),
                    )
                    if (
                        declared.access_mode != "read"
                        or declared.conversation_scope != "isolated"
                        or permission is None
                        or permission[:3] != (1, "workspace", None)
                        or permission[3] != json.loads(permission[4])["tool_schema_digest"]
                    ):
                        raise ValueError("parallel read permission evidence mismatch")
            nodes[node_id] = node
        for run in runs.values():
            active = tuple(
                node
                for node in nodes.values()
                if node.workflow_run_id == run.workflow_run_id
                and node.status.value in {"running", "blocked"}
            )
            if len(active) > run.budget_snapshot.max_concurrency or (
                len(active) > 1 and any(node.parallel_read_digest is None for node in active)
            ):
                raise ValueError("Workflow concurrency admission mismatch")
        execution_sets = {}
        for run_id, node_id, ordinal, reason in executor.execute(
            "SELECT * FROM workflow_run_execution_nodes"
        ):
            item = WorkflowExecutionNode(
                workflow_run_id=run_id,
                node_id=node_id,
                topology_ordinal=ordinal,
                inclusion_reason=reason,
            )
            if node_id not in {
                node.node_id for node in revisions[runs[run_id].workflow_revision_id].nodes
            }:
                raise ValueError("execution-set node is outside the Revision")
            execution_sets.setdefault(run_id, []).append(item)
        for run in runs.values():
            execution_ids = {item.node_id for item in execution_sets.get(run.workflow_run_id, ())}
            actual_ids = {
                n.node_id for n in nodes.values() if n.workflow_run_id == run.workflow_run_id
            }
            if not execution_ids and run.run_relation == "initial":
                # Compatibility for stores opened on v26 before execution-set
                # population shipped; the legacy full NodeRun set is exact.
                execution_ids = actual_ids
            if actual_ids != execution_ids:
                raise ValueError("run node set mismatch")
            if run.run_relation == "initial" and execution_ids != {
                n.node_id for n in revisions[run.workflow_revision_id].nodes
            }:
                raise ValueError("initial run execution set is incomplete")
            root_binding = _first(
                executor,
                "SELECT body_json FROM workflow_artifact_bindings WHERE workflow_run_id=? AND node_run_id='' AND direction='input' AND name='task'",
                (run.workflow_run_id,),
            )
            if (
                root_binding is None
                or ArtifactBinding.model_validate_json(root_binding[0]) != run.input_artifacts[0]
            ):
                raise ValueError("Workflow input binding is missing")
        effective_outputs = {}
        for run_id, node_id, direction, name, artifact_id, body in executor.execute(
            "SELECT * FROM workflow_artifact_bindings"
        ):
            binding = ArtifactBinding.model_validate_json(body)
            artifact = _first(
                executor,
                "SELECT workspace_id, contract_json, producer_node_run_id, output_slot FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            )
            if (
                (name, artifact_id) != (binding.name, binding.artifact_id)
                or artifact[0] != runs[run_id].workspace_id
                or ContractRef.model_validate_json(artifact[1]) != binding.contract
            ):
                raise ValueError("Artifact binding mismatch")
            if node_id and nodes[node_id].workflow_run_id != run_id:
                raise ValueError("Artifact node scope mismatch")
            if direction == "output" and (node_id, name) != artifact[2:]:
                raise ValueError("Artifact producer mismatch")
            if direction == "output":
                effective_outputs[(run_id, nodes[node_id].node_id, name)] = binding
        for (
            run_id,
            source_run_id,
            source_node_run_id,
            source_node_id,
            output_slot,
            artifact_id,
            contract_json,
            inherited_at,
        ) in executor.execute("SELECT * FROM workflow_run_artifact_imports"):
            item = WorkflowArtifactImport(
                workflow_run_id=run_id,
                source_workflow_run_id=source_run_id,
                source_node_run_id=source_node_run_id,
                source_node_id=source_node_id,
                output_slot=output_slot,
                artifact_id=artifact_id,
                contract=ContractRef.model_validate_json(contract_json),
                inherited_at=datetime.fromtimestamp(inherited_at, UTC),
            )
            source_node = nodes[source_node_run_id]
            artifact = _first(
                executor,
                "SELECT workspace_id, contract_json FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            )
            if (
                item.source_workflow_run_id != source_node.workflow_run_id
                or item.source_node_id != source_node.node_id
                or artifact[0] != runs[run_id].workspace_id
                or ContractRef.model_validate_json(artifact[1]) != item.contract
            ):
                raise ValueError("Workflow Artifact import mismatch")
            key = (run_id, source_node_id, output_slot)
            if key in effective_outputs:
                raise ValueError("Workflow effective output is ambiguous")
            effective_outputs[key] = ArtifactBinding(
                name=output_slot, artifact_id=artifact_id, contract=item.contract
            )
        for run in runs.values():
            if run.status.value == "completed":
                revision = revisions[run.workflow_revision_id]
                if any(
                    (run.workflow_run_id, ref.node_id, ref.output_slot) not in effective_outputs
                    for ref in revision.required_outputs
                ):
                    raise ValueError("completed Workflow required output is missing")
        for node_id, session_id, task_id in executor.execute(
            "SELECT * FROM workflow_leaf_ownership"
        ):
            task = _first(
                executor,
                "SELECT workspace_id, session_id, purpose FROM task_runs WHERE task_run_id=?",
                (task_id,),
            )
            if task != (nodes[node_id].workspace_id, session_id, "workflow_node"):
                raise ValueError("leaf ownership mismatch")
        for agent_id, node_id in executor.execute("SELECT * FROM workflow_agent_run_refs"):
            if nodes[node_id].agent_run_id != agent_id:
                raise ValueError("AgentRun reference mismatch")
            node = nodes[node_id]
            revision = revisions[runs[node.workflow_run_id].workflow_revision_id]
            declared = next(n for n in revision.nodes if n.node_id == node.node_id)
            agent = _first(
                executor,
                "SELECT session_id, snapshot_json FROM agent_runs WHERE agent_run_id=?",
                (agent_id,),
            )
            snapshot = json.loads(agent[1])
            if (
                agent[0] != node.conversation_session_id
                or snapshot.get("definition_ref")
                != declared.agent_definition_ref.model_dump(mode="json")
                or snapshot.get("model") != declared.resolved_model_ref.model_dump(mode="json")
            ):
                raise ValueError("Workflow AgentRun frozen reference mismatch")
            effective_cap = node.effective_node_generation_request_cap
            declared_cap = declared.declared_node_max_agent_generation_requests
            if (
                effective_cap is not None
                and declared_cap is not None
                and effective_cap > declared_cap
            ):
                raise ValueError("Workflow node budget mismatch")
        for node in nodes.values():
            if node.agent_run_id is not None and _first(
                executor,
                "SELECT node_run_id FROM workflow_agent_run_refs WHERE agent_run_id=?",
                (node.agent_run_id,),
            ) != (node.node_run_id,):
                raise ValueError("Workflow AgentRun attribution is missing")
            if node.status.value == "completed":
                declared = next(
                    n
                    for n in revisions[runs[node.workflow_run_id].workflow_revision_id].nodes
                    if n.node_id == node.node_id
                )
                slots = {
                    row[0]
                    for row in executor.execute(
                        "SELECT name FROM workflow_artifact_bindings WHERE node_run_id=? AND direction='output'",
                        (node.node_run_id,),
                    )
                }
                if (
                    not {
                        s.slot for s in declared.output_contracts if s.required_for_node_completion
                    }
                    <= slots
                ):
                    raise ValueError("completed Node output is missing")
        for ws, _command_id, _request_hash, rid in executor.execute(
            "SELECT * FROM workflow_publications"
        ):
            if revisions[rid].workspace_id != ws:
                raise ValueError("Workflow publication scope mismatch")
        for (body,) in executor.execute("SELECT payload_json FROM task_outcomes"):
            outcome = TaskOutcome.model_validate_json(body)
            for ref in outcome.evidence_refs:
                if ref.kind == TaskOutcomeEvidenceKind.WORKFLOW_RUN:
                    run = runs[ref.reference_id]
                    if (run.workspace_id, run.root_task_run_id) != (
                        outcome.workspace_id,
                        outcome.task_run_id,
                    ):
                        raise ValueError("Outcome Workflow scope mismatch")
        if tuple(
            executor.execute("SELECT name FROM sqlite_master WHERE name='workflow_replan_signals'")
        ):
            proposals = {}
            for pid, ws, rid, version, body in executor.execute(
                "SELECT * FROM workflow_replan_proposals"
            ):
                proposal = ReplanProposal.model_validate_json(body)
                if (
                    proposal.proposal_id,
                    proposal.workspace_id,
                    proposal.patch.parent_run_id,
                    proposal.row_version,
                ) != (pid, ws, rid, version):
                    raise ValueError("Replan proposal identity mismatch")
                if (
                    runs[rid].workspace_id != ws
                    or revisions[proposal.patch.base_workflow_revision_id].workspace_id != ws
                ):
                    raise ValueError("Replan proposal scope mismatch")
                if proposal.child_run_id:
                    child = runs[proposal.child_run_id]
                    if (
                        child.parent_run_id != rid
                        or child.workspace_id != ws
                        or child.run_relation != "continuation"
                    ):
                        raise ValueError("Replan child lineage mismatch")
                proposals[pid] = proposal
            consumed_signals = {}
            for sid, ws, rid, nid, consumed, body in executor.execute(
                "SELECT * FROM workflow_replan_signals"
            ):
                signal = ReplanSignal.model_validate_json(body)
                if (
                    signal.signal_id,
                    signal.workspace_id,
                    signal.workflow_run_id,
                    signal.node_run_id,
                ) != (sid, ws, rid, nid):
                    raise ValueError("Replan signal identity mismatch")
                if (
                    nodes[nid].workspace_id != ws
                    or nodes[nid].workflow_run_id != rid
                    or nodes[nid].started_at is None
                ):
                    raise ValueError("Replan signal node ownership mismatch")
                if consumed:
                    proposal = proposals[consumed]
                    if (
                        proposal.workspace_id != ws
                        or proposal.patch.parent_run_id != rid
                        or sid not in proposal.signal_ids
                    ):
                        raise ValueError("Replan signal consumption mismatch")
                    consumed_signals[sid] = consumed
            for proposal in proposals.values():
                if any(
                    consumed_signals.get(sid) != proposal.proposal_id for sid in proposal.signal_ids
                ):
                    raise ValueError("Replan proposal signal evidence is missing")
        from morrow.application.workflows.feedback_integrity import verify_feedback_rows

        verify_feedback_rows(executor, runs, revisions)
        return True, ()
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False, ("workflow_integrity",)
