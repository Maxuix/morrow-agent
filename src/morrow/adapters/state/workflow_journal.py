"""Focused Workflow persistence on the existing shared transaction backend.

Only a compiled immutable value may be stored; this repository never compiles,
resolves a model, generates an identity or computes a content hash.
"""

from datetime import timedelta

from morrow.core.domain import TaskRunPurpose, TaskRunStatus
from morrow.core.store import StorageError, StorageErrorCode
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowRevision,
    WorkflowRevisionRevocation,
)
from morrow.core.workflows.runs import NodeRun, WorkflowRun, WorkflowStatus, validate_run_transition


class SqliteWorkflowJournal:
    def __init__(self, backend, *, get_task, get_artifact, get_agent_version, get_agent_run):
        self.backend = backend
        self.get_task = get_task
        self.get_artifact = get_artifact
        self.get_agent_version = get_agent_version
        self.get_agent_run = get_agent_run

    def _load(self, model, sql, parameters):
        row = self.backend.read_one(sql, parameters)
        if row is None:
            return None
        try:
            return model.model_validate_json(row[0])
        except ValueError:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Workflow record is corrupt"
            ) from None

    def get_revision(self, workspace_id, revision_id):
        value = self._load(
            WorkflowRevision,
            "SELECT body_json FROM workflow_revisions WHERE workspace_id=? AND workflow_revision_id=?",
            (workspace_id, revision_id),
        )
        if value is not None and (value.workspace_id, value.workflow_revision_id) != (
            workspace_id,
            revision_id,
        ):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Workflow revision identity mismatch")
        return value

    def list_revisions(self, workspace_id):
        rows = self.backend.read_all(
            "SELECT workflow_revision_id FROM workflow_revisions WHERE workspace_id=? ORDER BY workflow_definition_id, revision",
            (workspace_id,),
        )
        return tuple(self.get_revision(workspace_id, row[0]) for row in rows)

    def get_head(self, workspace_id, definition_id):
        return self._load(
            WorkflowDefinitionHead,
            "SELECT body_json FROM workflow_definition_heads WHERE workspace_id=? AND workflow_definition_id=?",
            (workspace_id, definition_id),
        )

    def store_compiled_revision(
        self, revision: WorkflowRevision, head: WorkflowDefinitionHead, *, expected_row_version
    ):
        """Atomic persistence seam reserved for WorkflowCompilationService (Subplan 3)."""

        def work():
            current = self.get_head(revision.workspace_id, revision.workflow_definition_id)
            if (current.row_version if current else 0) != expected_row_version:
                raise ValueError("Workflow head revision conflict")
            if (
                head.workspace_id,
                head.workflow_definition_id,
                head.workflow_revision_id,
                head.row_version,
            ) != (
                revision.workspace_id,
                revision.workflow_definition_id,
                revision.workflow_revision_id,
                expected_row_version + 1,
            ):
                raise ValueError("Workflow head does not match compiled revision")
            if (head.source_revision, head.source_hash) != (
                revision.source_revision,
                revision.source_hash,
            ):
                raise ValueError("Workflow head source evidence mismatch")
            prior = (
                self.get_revision(revision.workspace_id, current.workflow_revision_id)
                if current
                else None
            )
            if revision.revision != (
                prior.revision + 1 if prior else 1
            ) or revision.parent_workflow_revision_id != (
                prior.workflow_revision_id if prior else None
            ):
                raise ValueError("Workflow revision lineage mismatch")
            if current and head.enabled != current.enabled:
                raise ValueError("publication must preserve the enabled flag")
            for node in revision.nodes:
                ref = node.agent_definition_ref
                agent = self.get_agent_version(revision.workspace_id, ref.version_id)
                if agent is None or (agent.source.definition_id, agent.content_hash) != (
                    ref.definition_id,
                    ref.content_hash,
                ):
                    raise ValueError("Workflow Agent version reference mismatch")
            sql = self.backend.executor()
            sql.execute(
                "INSERT INTO workflow_revisions VALUES(?,?,?,?,?,?)",
                (
                    revision.workflow_revision_id,
                    revision.workspace_id,
                    revision.workflow_definition_id,
                    revision.revision,
                    revision.content_hash,
                    revision.model_dump_json(),
                ),
            )
            for node in revision.nodes:
                sql.execute(
                    "INSERT INTO workflow_revision_nodes VALUES(?,?,?)",
                    (
                        revision.workflow_revision_id,
                        node.node_id,
                        node.agent_definition_ref.version_id,
                    ),
                )
            self._put_head(head)
            return revision

        return self.backend.transact(work)

    def _put_head(self, head):
        self.backend.executor().execute(
            "INSERT INTO workflow_definition_heads VALUES(?,?,?,?) ON CONFLICT(workspace_id, workflow_definition_id) "
            "DO UPDATE SET workflow_revision_id=excluded.workflow_revision_id, body_json=excluded.body_json",
            (
                head.workspace_id,
                head.workflow_definition_id,
                head.workflow_revision_id,
                head.model_dump_json(),
            ),
        )

    def set_enabled(self, workspace_id, definition_id, *, enabled: bool, expected_row_version):
        def work():
            current = self.get_head(workspace_id, definition_id)
            if (
                current is None
                or current.row_version != expected_row_version
                or type(enabled) is not bool
            ):
                raise ValueError("Workflow head revision conflict")
            value = current.model_copy(
                update={"enabled": enabled, "row_version": current.row_version + 1}
            )
            self._put_head(value)
            return value

        return self.backend.transact(work)

    def get_revocation(self, workspace_id, revision_id):
        return self._load(
            WorkflowRevisionRevocation,
            "SELECT r.body_json FROM workflow_revision_revocations r JOIN workflow_revisions v USING(workflow_revision_id) "
            "WHERE v.workspace_id=? AND v.workflow_revision_id=?",
            (workspace_id, revision_id),
        )

    def put_revocation(self, value: WorkflowRevisionRevocation):
        def work():
            if self.get_revision(value.workspace_id, value.workflow_revision_id) is None:
                raise ValueError("Workflow revision missing")
            current = self.get_revocation(value.workspace_id, value.workflow_revision_id)
            if current == value:
                return current
            if current is not None:
                raise ValueError("Workflow revocation is one-way and immutable")
            self.backend.executor().execute(
                "INSERT INTO workflow_revision_revocations VALUES(?,?)",
                (
                    value.workflow_revision_id,
                    value.model_dump_json(),
                ),
            )
            return value

        return self.backend.transact(work)

    def publication(self, workspace_id, command_id):
        return self.backend.read_one(
            "SELECT request_hash, workflow_revision_id FROM workflow_publications WHERE workspace_id=? AND command_id=?",
            (workspace_id, command_id),
        )

    def put_publication(self, workspace_id, command_id, request_hash, revision_id):
        if self.get_revision(workspace_id, revision_id) is None:
            raise ValueError("Workflow publication scope mismatch")
        self.backend.executor().execute(
            "INSERT INTO workflow_publications VALUES(?,?,?,?)",
            (
                workspace_id,
                command_id,
                request_hash,
                revision_id,
            ),
        )

    def get_run(self, workspace_id, run_id):
        value = self._load(
            WorkflowRun,
            "SELECT body_json FROM workflow_runs WHERE workspace_id=? AND workflow_run_id=?",
            (workspace_id, run_id),
        )
        if value and (value.workspace_id, value.workflow_run_id) != (workspace_id, run_id):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Workflow run identity mismatch")
        return value

    def list_runs(self, workspace_id):
        rows = self.backend.read_all(
            "SELECT workflow_run_id FROM workflow_runs WHERE workspace_id=? ORDER BY workflow_run_id",
            (workspace_id,),
        )
        return tuple(self.get_run(workspace_id, row[0]) for row in rows)

    def get_leaf_ownership(self, workspace_id, node_run_id):
        del workspace_id
        row = self.backend.read_one(
            "SELECT session_id, task_run_id FROM workflow_leaf_ownership WHERE node_run_id=?",
            (node_run_id,),
        )
        return (str(row[0]), str(row[1])) if row else None

    def active_for_root(self, workspace_id, task_run_id):
        row = self.backend.read_one(
            "SELECT workflow_run_id FROM workflow_runs WHERE workspace_id=? AND root_task_run_id=? "
            "AND status IN ('queued','running','blocked','draining','paused')",
            (workspace_id, task_run_id),
        )
        return self.get_run(workspace_id, row[0]) if row else None

    def create_run(self, value: WorkflowRun, nodes: tuple[NodeRun, ...]):
        def work():
            revision = self.get_revision(value.workspace_id, value.workflow_revision_id)
            root = self.get_task(value.workspace_id, value.root_task_run_id)
            if (
                revision is None
                or root is None
                or root.purpose != TaskRunPurpose.USER
                or root.status != TaskRunStatus.OPEN
            ):
                raise ValueError("Workflow root or revision is invalid")
            if (
                value.status != WorkflowStatus.QUEUED
                or value.row_version != 1
                or value.budget_snapshot != revision.budget
            ):
                raise ValueError("Workflow admission must preserve the revision budget")
            direct = revision.nodes[0].conversation_scope == "invoking_session"
            if direct != (value.invoking_client_message_id is not None):
                raise ValueError("Workflow Direct binding does not match its revision scope")
            if direct and value.invoking_root_row_version != root.row_version:
                raise ValueError("Workflow Direct binding does not match the root revision")
            if (
                value.started_at is None
                or value.admission_deadline_at
                != value.started_at
                + timedelta(seconds=value.budget_snapshot.admission_timeout_seconds)
            ):
                raise ValueError("Workflow admission deadline does not match its frozen duration")
            if {n.node_id for n in nodes} != {n.node_id for n in revision.nodes} or len(
                nodes
            ) != len(revision.nodes):
                raise ValueError("Workflow must pre-create exactly one NodeRun per node")
            if (
                value.pause_requested
                or value.run_relation != "initial"
                or value.parent_run_id is not None
                or value.effective_lineage_budget_root_run_id != value.workflow_run_id
            ):
                raise ValueError("Workflow admission starts an initial lineage root")
            self.backend.executor().execute(
                "INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    value.workflow_run_id,
                    value.workspace_id,
                    value.workflow_revision_id,
                    value.root_task_run_id,
                    value.status.value,
                    1 if value.pause_requested else 0,
                    value.run_relation,
                    value.effective_lineage_budget_root_run_id,
                    value.parent_run_id,
                    value.model_dump_json(),
                ),
            )
            for node in nodes:
                if (node.workspace_id, node.workflow_run_id, node.status, node.row_version) != (
                    value.workspace_id,
                    value.workflow_run_id,
                    WorkflowStatus.QUEUED,
                    1,
                ):
                    raise ValueError("invalid initial NodeRun")
                self.backend.executor().execute(
                    "INSERT INTO workflow_node_runs VALUES(?,?,?,?,?,?,?)",
                    (
                        node.node_run_id,
                        node.workspace_id,
                        node.workflow_run_id,
                        node.node_id,
                        node.attempt,
                        node.status.value,
                        node.model_dump_json(),
                    ),
                )
            self.bind_artifact(value.workspace_id, value.workflow_run_id, value.input_artifacts[0])
            return value

        return self.backend.transact(work)

    def get_node(self, workspace_id, node_run_id):
        value = self._load(
            NodeRun,
            "SELECT body_json FROM workflow_node_runs WHERE workspace_id=? AND node_run_id=?",
            (workspace_id, node_run_id),
        )
        if value and (value.workspace_id, value.node_run_id) != (workspace_id, node_run_id):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Workflow node identity mismatch")
        return value

    def list_nodes(self, workspace_id, workflow_run_id):
        rows = self.backend.read_all(
            "SELECT node_run_id FROM workflow_node_runs WHERE workspace_id=? AND workflow_run_id=? ORDER BY node_id, attempt",
            (workspace_id, workflow_run_id),
        )
        return tuple(self.get_node(workspace_id, row[0]) for row in rows)

    def save_run(self, value: WorkflowRun, *, expected_row_version):
        return self._save(value, expected_row_version, node=False)

    def save_node(self, value: NodeRun, *, expected_row_version):
        return self._save(value, expected_row_version, node=True)

    def _save(self, value, expected, *, node):
        value = type(value).model_validate(value.model_dump())

        def work():
            current = (
                self.get_node(value.workspace_id, value.node_run_id)
                if node
                else self.get_run(value.workspace_id, value.workflow_run_id)
            )
            if current == value:
                return current
            if (
                current is None
                or current.row_version != expected
                or value.row_version != expected + 1
            ):
                raise ValueError("Workflow run revision conflict")
            mutable = {"status", "row_version", "completed_at"}
            if node and current.status == WorkflowStatus.QUEUED:
                mutable |= {
                    "started_at",
                    "conversation_session_id",
                    "leaf_task_run_id",
                    "agent_run_id",
                    "effective_node_generation_request_cap",
                }
            if not node:
                mutable |= {"result_status", "pending_terminal_intent", "pause_requested"}
            for field in type(value).model_fields.keys() - mutable:
                if getattr(value, field) != getattr(current, field):
                    raise ValueError("frozen Workflow evidence cannot change")
            if current.status != value.status:
                validate_run_transition(current.status, value.status)
            elif (
                current.status.terminal
                or node
                or (
                    current.pending_terminal_intent == value.pending_terminal_intent
                    and current.pause_requested == value.pause_requested
                )
            ):
                raise ValueError("Workflow state did not advance")
            if (
                not node
                and current.pending_terminal_intent == "user_cancel"
                and value.pending_terminal_intent is None
            ):
                raise ValueError("pending cancellation intent cannot be cleared")
            if node and value.agent_run_id is not None:
                leaf = self.get_task(value.workspace_id, value.leaf_task_run_id)
                agent = self.get_agent_run(value.workspace_id, value.agent_run_id)
                run = self.get_run(value.workspace_id, value.workflow_run_id)
                revision = self.get_revision(value.workspace_id, run.workflow_revision_id)
                definition = next(n for n in revision.nodes if n.node_id == value.node_id)
                direct = definition.conversation_scope == "invoking_session"
                owner = self.backend.read_one(
                    "SELECT session_id, task_run_id FROM workflow_leaf_ownership WHERE node_run_id=?",
                    (value.node_run_id,),
                )
                if direct:
                    if (
                        owner is not None
                        or leaf is None
                        or leaf.purpose != TaskRunPurpose.USER
                        or leaf.task_run_id != run.root_task_run_id
                        or leaf.session_id != value.conversation_session_id
                    ):
                        raise ValueError("Workflow invoking-session scope mismatch")
                elif owner != (value.conversation_session_id, value.leaf_task_run_id):
                    raise ValueError("Workflow node does not own this leaf")
                elif (
                    leaf is None
                    or leaf.purpose != TaskRunPurpose.WORKFLOW_NODE
                    or leaf.session_id != value.conversation_session_id
                ):
                    raise ValueError("Workflow leaf scope mismatch")
                if (
                    agent is None
                    or agent.session_id != leaf.session_id
                    or agent.snapshot.definition_ref != definition.agent_definition_ref
                    or agent.snapshot.model != definition.resolved_model_ref
                ):
                    raise ValueError("Workflow AgentRun attribution mismatch")
                if (
                    value.effective_node_generation_request_cap
                    > definition.declared_node_max_agent_generation_requests
                ):
                    raise ValueError("Workflow node request cap escalates the revision")
                if current.agent_run_id is None:
                    self.backend.executor().execute(
                        "INSERT INTO workflow_agent_run_refs VALUES(?,?)",
                        (value.agent_run_id, value.node_run_id),
                    )
            if value.status == WorkflowStatus.COMPLETED:
                if node:
                    available = {
                        binding.name
                        for nid, direction, binding in self.list_bindings(
                            value.workspace_id, value.workflow_run_id
                        )
                        if nid == value.node_run_id and direction == "output"
                    }
                    if (
                        not {
                            s.slot
                            for s in definition.output_contracts
                            if s.required_for_node_completion
                        }
                        <= available
                    ):
                        raise ValueError("completed Node lacks required outputs")
                elif any(
                    n.status != WorkflowStatus.COMPLETED
                    for n in self.list_nodes(value.workspace_id, value.workflow_run_id)
                ):
                    raise ValueError("completed Workflow requires every node to complete")
            table, key = (
                ("workflow_node_runs", "node_run_id")
                if node
                else ("workflow_runs", "workflow_run_id")
            )
            if node:
                self.backend.executor().execute(
                    "UPDATE workflow_node_runs SET status=?, body_json=? WHERE node_run_id=?",
                    (value.status.value, value.model_dump_json(), value.node_run_id),
                )
            else:
                self.backend.executor().execute(
                    "UPDATE workflow_runs SET status=?, pause_requested=?, body_json=? "
                    "WHERE workflow_run_id=?",
                    (
                        value.status.value,
                        1 if value.pause_requested else 0,
                        value.model_dump_json(),
                        value.workflow_run_id,
                    ),
                )
            return value

        return self.backend.transact(work)

    def bind_artifact(
        self, workspace_id, run_id, binding: ArtifactBinding, *, node_run_id="", direction="input"
    ):
        def work():
            run = self.get_run(workspace_id, run_id)
            artifact = self.get_artifact(workspace_id, binding.artifact_id)
            if (
                run is None
                or artifact is None
                or artifact.state.value != "available"
                or artifact.contract != binding.contract
            ):
                raise ValueError("Workflow Artifact reference is invalid")
            args = (run_id, node_run_id, direction, binding.name)
            old = self.backend.read_one(
                "SELECT body_json FROM workflow_artifact_bindings WHERE workflow_run_id=? AND node_run_id=? AND direction=? AND name=?",
                args,
            )
            if old:
                if ArtifactBinding.model_validate_json(old[0]) != binding:
                    raise ValueError("Workflow Artifact binding is immutable")
                return binding
            if run.status.terminal:
                raise ValueError("terminal Workflow bindings are immutable")
            if node_run_id:
                node = self.get_node(workspace_id, node_run_id)
                if node is None or node.workflow_run_id != run_id:
                    raise ValueError("Workflow binding node scope mismatch")
                revision = self.get_revision(workspace_id, run.workflow_revision_id)
                declared = next(n for n in revision.nodes if n.node_id == node.node_id)
                if direction == "output":
                    slot = next(
                        (s for s in declared.output_contracts if s.slot == binding.name), None
                    )
                    if slot is None or (slot.kind, slot.version) != (
                        binding.contract.kind,
                        binding.contract.version,
                    ):
                        raise ValueError("Workflow output slot contract mismatch")
                    owner = self.backend.read_one(
                        "SELECT session_id, task_run_id FROM workflow_leaf_ownership WHERE node_run_id=?",
                        (node_run_id,),
                    )
                    direct = declared.conversation_scope == "invoking_session"
                    root = self.get_task(workspace_id, run.root_task_run_id)
                    expected_owner = (
                        (root.session_id, root.task_run_id)
                        if direct and root is not None
                        else owner
                    )
                    if expected_owner != (artifact.session_id, artifact.task_run_id):
                        raise ValueError("Workflow output Artifact scope mismatch")
                elif direction == "input":
                    declared_input = next(
                        (b for b in declared.input_bindings if b.input_name == binding.name), None
                    )
                    if declared_input is None or (
                        declared_input.accepts.kind,
                        declared_input.accepts.version,
                    ) != (binding.contract.kind, binding.contract.version):
                        raise ValueError("Workflow input contract mismatch")
                    if declared_input.source == "workflow_input":
                        expected_artifact_id = run.input_artifacts[0].artifact_id
                    else:
                        ref = declared_input.node_output
                        producer = next(
                            n
                            for n in self.list_nodes(workspace_id, run_id)
                            if n.node_id == ref.node_id
                        )
                        linked = self.backend.read_one(
                            "SELECT artifact_id FROM workflow_artifact_bindings WHERE node_run_id=? AND direction='output' AND name=?",
                            (producer.node_run_id, ref.output_slot),
                        )
                        expected_artifact_id = linked[0] if linked else None
                    if binding.artifact_id != expected_artifact_id:
                        raise ValueError("Workflow input producer mismatch")
                else:
                    raise ValueError("Workflow binding direction is invalid")
                if direction == "output" and (
                    artifact.producer_node_run_id,
                    artifact.output_slot,
                ) != (node_run_id, binding.name):
                    raise ValueError("Workflow Artifact producer mismatch")
            elif direction != "input" or binding != run.input_artifacts[0]:
                raise ValueError("Workflow root only binds its frozen task input")
            else:
                root = self.get_task(workspace_id, run.root_task_run_id)
                if (artifact.session_id, artifact.task_run_id) != (
                    root.session_id,
                    root.task_run_id,
                ):
                    raise ValueError("Workflow input Artifact scope mismatch")
            self.backend.executor().execute(
                "INSERT INTO workflow_artifact_bindings VALUES(?,?,?,?,?,?)",
                (*args, binding.artifact_id, binding.model_dump_json()),
            )
            return binding

        return self.backend.transact(work)

    def list_bindings(self, workspace_id, run_id):
        if self.get_run(workspace_id, run_id) is None:
            return ()
        rows = self.backend.read_all(
            "SELECT node_run_id, direction, body_json FROM workflow_artifact_bindings WHERE workflow_run_id=? ORDER BY node_run_id, direction, name",
            (run_id,),
        )
        return tuple((row[0], row[1], ArtifactBinding.model_validate_json(row[2])) for row in rows)
