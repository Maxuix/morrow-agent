"""Read-only Workflow integrity shared by the current backup and doctor owners."""

import json
from datetime import timedelta

from morrow.core.agent_definitions import AgentDefinitionVersion
from morrow.core.domain import TaskOutcome, TaskOutcomeEvidenceKind
from morrow.core.workflows.contracts import ArtifactBinding, ContractRef
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowRevision,
    WorkflowRevisionRevocation,
)
from morrow.core.workflows.runs import NodeRun, WorkflowRun


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
                if (parent.workspace_id, parent.workflow_definition_id, parent.revision) != (
                    value.workspace_id,
                    value.workflow_definition_id,
                    value.revision - 1,
                ):
                    raise ValueError("revision lineage mismatch")
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
        for ws, did, rid, body in executor.execute("SELECT * FROM workflow_definition_heads"):
            value = WorkflowDefinitionHead.model_validate_json(body)
            revision = revisions[rid]
            if (ws, did, rid) != (
                value.workspace_id,
                value.workflow_definition_id,
                value.workflow_revision_id,
            ) or (ws, did) != (revision.workspace_id, revision.workflow_definition_id):
                raise ValueError("head mismatch")
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
            if run.started_at is None or run.admission_deadline_at != run.started_at + timedelta(
                seconds=run.budget_snapshot.admission_timeout_seconds
            ):
                raise ValueError("run deadline mismatch")
            runs[run_id] = run
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
            nodes[node_id] = node
        for run in runs.values():
            if {n.node_id for n in nodes.values() if n.workflow_run_id == run.workflow_run_id} != {
                n.node_id for n in revisions[run.workflow_revision_id].nodes
            }:
                raise ValueError("run node set mismatch")
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
            if (
                node.effective_node_generation_request_cap
                > declared.declared_node_max_agent_generation_requests
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
        return True, ()
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False, ("workflow_integrity",)
