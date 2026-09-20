"""Focused Workflow persistence on the existing shared transaction backend.

Only a compiled immutable value may be stored; this repository never compiles,
resolves a model, generates an identity or computes a content hash.
"""

from datetime import UTC, datetime, timedelta
from typing import get_args

from morrow.core.contracts import PauseReason
from morrow.core.domain import TaskRunPurpose, TaskRunStatus
from morrow.core.execution import StaleRowVersionError
from morrow.core.store import StorageError, StorageErrorCode
from morrow.core.workflows.contracts import ArtifactBinding
from morrow.core.workflows.definitions import (
    WorkflowDefinitionHead,
    WorkflowRevision,
    WorkflowRevisionRevocation,
)
from morrow.core.workflows.drafts import WorkflowDraft
from morrow.core.workflows.replan import ReplanProposal, ReplanSignal
from morrow.core.workflows.runs import (
    NodeRun,
    WorkflowArtifactImport,
    WorkflowExecutionNode,
    WorkflowRun,
    WorkflowStatus,
    validate_run_transition,
)

# Every status that still owns its root TaskRun and node conversations.
_ACTIVE_RUN_STATUSES = "('queued','running','blocked','draining','paused')"


class SqliteWorkflowJournal:
    def __init__(
        self,
        backend,
        *,
        get_task,
        get_artifact,
        get_agent_version,
        get_agent_run,
        get_permission_snapshot,
    ):
        self.backend = backend
        from morrow.adapters.state.planning_journal import SqlitePlanningJournal

        self.planning = SqlitePlanningJournal(self)
        from morrow.adapters.state.execution_pause_journal import SqliteExecutionPauseJournal

        self.execution_pause = SqliteExecutionPauseJournal(self.backend)
        self.get_task = get_task
        self.get_artifact = get_artifact
        self.get_agent_version = get_agent_version
        self.get_agent_run = get_agent_run
        self.get_permission_snapshot = get_permission_snapshot

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

    def list_replan_signals(self, workspace_id, run_id, *, pending_only=False):
        rows = self.backend.read_all(
            "SELECT body_json FROM workflow_replan_signals "
            "WHERE workspace_id=? AND workflow_run_id=? "
            + ("AND consumed_by IS NULL " if pending_only else "")
            + "ORDER BY signal_id",
            (workspace_id, run_id),
        )
        return tuple(ReplanSignal.model_validate_json(row[0]) for row in rows)

    def put_replan_signal(self, value: ReplanSignal):
        def work():
            node = self.get_node(value.workspace_id, value.node_run_id)
            leaf = self.get_task(value.workspace_id, node.leaf_task_run_id) if node else None
            if (
                node is None
                or node.workflow_run_id != value.workflow_run_id
                or node.started_at is None
                or leaf is None
                or leaf.status
                not in {
                    TaskRunStatus.READY_FOR_ACCEPTANCE,
                    TaskRunStatus.FAILED,
                    TaskRunStatus.CANCELLED,
                }
            ):
                raise ValueError("ReplanSignal requires its own durable leaf closure")
            existing = self._load(
                ReplanSignal,
                "SELECT body_json FROM workflow_replan_signals WHERE node_run_id=?",
                (value.node_run_id,),
            )
            if existing:
                if existing.model_copy(update={"created_at": value.created_at}) != value:
                    raise ValueError("ReplanSignal closure evidence is immutable")
                return existing
            self.backend.executor().execute(
                "INSERT INTO workflow_replan_signals VALUES(?,?,?,?,NULL,?)",
                (
                    value.signal_id,
                    value.workspace_id,
                    value.workflow_run_id,
                    value.node_run_id,
                    value.model_dump_json(),
                ),
            )
            return value

        return self.backend.transact(work)

    def get_replan_proposal(self, workspace_id, proposal_id):
        return self._load(
            ReplanProposal,
            "SELECT body_json FROM workflow_replan_proposals WHERE workspace_id=? AND proposal_id=?",
            (workspace_id, proposal_id),
        )

    def list_replan_proposals(self, workspace_id, run_id):
        rows = self.backend.read_all(
            "SELECT body_json FROM workflow_replan_proposals "
            "WHERE workspace_id=? AND workflow_run_id=? ORDER BY proposal_id",
            (workspace_id, run_id),
        )
        return tuple(ReplanProposal.model_validate_json(row[0]) for row in rows)

    def create_replan_proposal(self, value: ReplanProposal):
        value = ReplanProposal.model_validate_json(value.model_dump_json())

        def work():
            if value.row_version != 1 or self.get_replan_proposal(
                value.workspace_id, value.proposal_id
            ):
                raise ValueError("Replan proposal already exists")
            run = self.get_run(value.workspace_id, value.patch.parent_run_id)
            if run is None or value.patch.workspace_id != value.workspace_id:
                raise ValueError("Replan proposal workspace mismatch")
            for signal_id in value.signal_ids:
                cursor = self.backend.executor().execute(
                    "UPDATE workflow_replan_signals SET consumed_by=? "
                    "WHERE workspace_id=? AND workflow_run_id=? AND signal_id=? AND consumed_by IS NULL RETURNING signal_id",
                    (value.proposal_id, value.workspace_id, run.workflow_run_id, signal_id),
                )
                if len(cursor) != 1:
                    raise ValueError("Replan signal consumption conflict")
            self.backend.executor().execute(
                "INSERT INTO workflow_replan_proposals VALUES(?,?,?,?,?)",
                (
                    value.proposal_id,
                    value.workspace_id,
                    run.workflow_run_id,
                    value.row_version,
                    value.model_dump_json(),
                ),
            )
            return value

        return self.backend.transact(work)

    def decide_replan_proposal(self, value: ReplanProposal, *, expected_row_version):
        value = ReplanProposal.model_validate_json(value.model_dump_json())

        def work():
            old = self.get_replan_proposal(value.workspace_id, value.proposal_id)
            if old is None or old.status != "pending" or old.row_version != expected_row_version:
                raise ValueError("Replan proposal decision conflict")
            mutable = {
                "status",
                "disposition_reason",
                "auto_applied",
                "child_run_id",
                "decided_by",
                "decided_at",
                "row_version",
            }
            if (
                value.row_version != old.row_version + 1
                or value.status == "pending"
                or any(
                    getattr(old, name) != getattr(value, name)
                    for name in ReplanProposal.model_fields.keys() - mutable
                )
            ):
                raise ValueError("Replan proposal facts are immutable")
            self.backend.executor().execute(
                "UPDATE workflow_replan_proposals SET row_version=?, body_json=? WHERE proposal_id=?",
                (value.row_version, value.model_dump_json(), value.proposal_id),
            )
            return value

        return self.backend.transact(work)

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

    def get_draft(self, workspace_id, draft_id):
        value = self._load(
            WorkflowDraft,
            "SELECT body_json FROM workflow_drafts WHERE workspace_id=? AND draft_id=?",
            (workspace_id, draft_id),
        )
        if value is not None and (value.workspace_id, value.draft_id) != (
            workspace_id,
            draft_id,
        ):
            raise StorageError(StorageErrorCode.NEEDS_REPAIR, "Workflow Draft identity mismatch")
        return value

    def list_drafts(self, workspace_id):
        rows = self.backend.read_all(
            "SELECT draft_id FROM workflow_drafts WHERE workspace_id=? "
            "ORDER BY updated_at_unix DESC, draft_id",
            (workspace_id,),
        )
        return tuple(self.get_draft(workspace_id, str(row[0])) for row in rows)

    def draft_page(self, workspace_id, *, after=None, limit=100):
        rows = self.backend.read_all(
            "SELECT draft_id FROM workflow_drafts WHERE workspace_id=? AND draft_id>? ORDER BY draft_id LIMIT ?",
            (workspace_id, after or "", limit),
        )
        return tuple(self.get_draft(workspace_id, row[0]) for row in rows)

    def _draft_history(self, value):
        from morrow.core.domain import canonical_json_bytes, sha256_digest
        from morrow.core.workflows.planning import DraftVersion

        previous = self.planning.version(value.workspace_id, value.draft_id, value.row_version - 1)
        return DraftVersion(
            workspace_id=value.workspace_id,
            draft_id=value.draft_id,
            version=value.row_version,
            source=value.source,
            source_hash=value.source_hash,
            node_metadata=previous.node_metadata if previous else {},
            execution_selections=previous.execution_selections if previous else {},
            created_from="manual_edit",
            command_id=f"cmd_{value.draft_id}_{value.row_version}",
            validation_digest=sha256_digest(
                canonical_json_bytes([(d.severity, d.code, d.node_id) for d in value.diagnostics])
            ),
            created_at=value.updated_at,
        )

    def create_draft(self, value: WorkflowDraft, *, history=None):
        def work():
            if value.row_version != 1 or self.get_draft(value.workspace_id, value.draft_id):
                raise ValueError("Workflow Draft already exists")
            self.backend.executor().execute(
                "INSERT INTO workflow_drafts VALUES(?,?,?,?,?,?,?,?)",
                (
                    value.draft_id,
                    value.workspace_id,
                    value.source.workflow_definition_id,
                    value.status.value,
                    value.row_version,
                    value.model_dump_json(),
                    int(value.created_at.timestamp()),
                    int(value.updated_at.timestamp()),
                ),
            )
            self.planning.append_version(history or self._draft_history(value))
            return value

        return self.backend.transact(work)

    def save_draft(self, value: WorkflowDraft, *, expected_row_version: int, history=None):
        def work():
            current = self.get_draft(value.workspace_id, value.draft_id)
            if current is None:
                raise ValueError("Workflow Draft is missing")
            if current.row_version != expected_row_version:
                raise StaleRowVersionError("stale Workflow Draft row version")
            if value.row_version != expected_row_version + 1:
                raise ValueError("Workflow Draft row version must advance once")
            if (
                value.workspace_id != current.workspace_id
                or value.draft_id != current.draft_id
                or value.created_at != current.created_at
            ):
                raise ValueError("Workflow Draft identity is immutable")
            self.backend.executor().execute(
                "UPDATE workflow_drafts SET workflow_definition_id=?, status=?, row_version=?, "
                "body_json=?, updated_at_unix=? WHERE workspace_id=? AND draft_id=?",
                (
                    value.source.workflow_definition_id,
                    value.status.value,
                    value.row_version,
                    value.model_dump_json(),
                    int(value.updated_at.timestamp()),
                    value.workspace_id,
                    value.draft_id,
                ),
            )
            self.planning.append_version(history or self._draft_history(value))
            return value

        return self.backend.transact(work)

    def list_revisions(self, workspace_id):
        rows = self.backend.read_all(
            "SELECT workflow_revision_id FROM workflow_revisions "
            "WHERE workspace_id=? AND revision > 0 "
            "ORDER BY workflow_definition_id, revision",
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

    def get_task_plan_provenance(self, workspace_id, revision_id):
        return self.planning.provenance(workspace_id, revision_id)

    def store_detached_revision(self, revision: WorkflowRevision, *, provenance=None):
        """Store one run-local immutable Revision without moving a Definition head.

        Parentless Revisions are task-plan initials: they require verified
        provenance in the same transaction. Continuations keep the existing
        parent-lineage check and never carry provenance.
        """

        def work():
            candidate = revision
            existing = self.get_revision(candidate.workspace_id, candidate.workflow_revision_id)
            if existing is not None:
                if existing != candidate.model_copy(update={"revision": existing.revision}):
                    raise ValueError("detached Workflow revision identifier conflict")
                stored_origin = self.get_task_plan_provenance(
                    existing.workspace_id, existing.workflow_revision_id
                )
                if provenance is not None:
                    if stored_origin is None or stored_origin != provenance:
                        raise ValueError("detached Workflow revision identifier conflict")
                elif stored_origin is not None or existing.parent_workflow_revision_id is None:
                    raise ValueError("detached Workflow revision lineage mismatch")
                return existing
            parent = (
                self.get_revision(candidate.workspace_id, candidate.parent_workflow_revision_id)
                if candidate.parent_workflow_revision_id
                else None
            )
            if candidate.parent_workflow_revision_id:
                if (
                    provenance is not None
                    or parent is None
                    or parent.workflow_definition_id != candidate.workflow_definition_id
                    or candidate.revision >= 0
                ):
                    raise ValueError("detached Workflow revision lineage mismatch")
            else:
                self._require_task_plan_origin(candidate, provenance)
            for node in candidate.nodes:
                ref = node.agent_definition_ref
                agent = self.get_agent_version(candidate.workspace_id, ref.version_id)
                if agent is None or (agent.source.definition_id, agent.content_hash) != (
                    ref.definition_id,
                    ref.content_hash,
                ):
                    raise ValueError("Workflow Agent version reference mismatch")
            sql = self.backend.executor()
            row = self.backend.read_one(
                "SELECT MIN(revision) FROM workflow_revisions "
                "WHERE workspace_id=? AND workflow_definition_id=? AND revision < 0",
                (candidate.workspace_id, candidate.workflow_definition_id),
            )
            assigned_revision = int(row[0]) - 1 if row is not None and row[0] is not None else -1
            stored = candidate.model_copy(update={"revision": assigned_revision})
            sql.execute(
                "INSERT INTO workflow_revisions VALUES(?,?,?,?,?,?)",
                (
                    stored.workflow_revision_id,
                    stored.workspace_id,
                    stored.workflow_definition_id,
                    stored.revision,
                    stored.content_hash,
                    stored.model_dump_json(),
                ),
            )
            for node in stored.nodes:
                sql.execute(
                    "INSERT INTO workflow_revision_nodes VALUES(?,?,?)",
                    (
                        stored.workflow_revision_id,
                        node.node_id,
                        node.agent_definition_ref.version_id,
                    ),
                )
            if provenance is not None:
                sql.execute(
                    "INSERT INTO workflow_task_plan_provenance VALUES(?,?,?,?,?,?)",
                    (
                        provenance.workspace_id,
                        provenance.workflow_revision_id,
                        provenance.planning_binding_id,
                        provenance.plan_decision_id,
                        provenance.origin,
                        provenance.model_dump_json(),
                    ),
                )
            return stored

        return self.backend.transact(work)

    def _require_task_plan_origin(self, candidate, provenance):
        from morrow.core.workflows.planning import TASK_PLAN_DEFINITION_PREFIX

        if (
            provenance is None
            or candidate.revision >= 0
            or not candidate.workflow_definition_id.startswith(TASK_PLAN_DEFINITION_PREFIX)
            or provenance.workspace_id != candidate.workspace_id
            or provenance.workflow_revision_id != candidate.workflow_revision_id
        ):
            raise ValueError("detached Workflow revision lineage mismatch")
        if self.get_head(candidate.workspace_id, candidate.workflow_definition_id) is not None:
            raise ValueError("task-plan revision must not occupy a Definition head")
        binding = self.planning.get_binding(candidate.workspace_id, provenance.planning_binding_id)
        decision = self.planning.decision(candidate.workspace_id, provenance.plan_decision_id)
        version = self.planning.version(
            candidate.workspace_id, provenance.draft_id, provenance.draft_version
        )
        root = self.get_task(candidate.workspace_id, provenance.root_task_run_id)
        expected_mode = "repair" if provenance.origin == "repair" else "initial"
        if (
            binding is None
            or binding.status != "active"
            or binding.mode != expected_mode
            or binding.current_draft_id != provenance.draft_id
            or decision is None
            or decision.decision != "start"
            or decision.command_id != provenance.command_id
            or decision.session_id != binding.session_id
            or decision.draft_id != provenance.draft_id
            or decision.draft_version != provenance.draft_version
            or version is None
            or {item.node_id for item in provenance.frozen_selections}
            != {node.node_id for node in candidate.nodes}
            or root is None
            or root.purpose is not TaskRunPurpose.USER
            or root.session_id != binding.session_id
        ):
            raise ValueError("detached Workflow revision lineage mismatch")

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
        node = self.get_node(workspace_id, node_run_id)
        if node is None:
            return None
        run = self.get_run(workspace_id, node.workflow_run_id)
        if run is None:
            return None
        revision = self.get_revision(workspace_id, run.workflow_revision_id)
        definition = (
            next((item for item in revision.nodes if item.node_id == node.node_id), None)
            if revision is not None
            else None
        )
        # Invoking-session nodes use the workflow root directly; only isolated
        # nodes have a separately owned leaf conversation.
        if definition is not None and definition.conversation_scope == "invoking_session":
            return None
        row = self.backend.read_one(
            "SELECT leaf_session_id, leaf_task_run_id FROM workflow_node_runs "
            "WHERE workspace_id=? AND node_run_id=?",
            (workspace_id, node_run_id),
        )
        return (str(row[0]), str(row[1])) if row and row[0] and row[1] else None

    def active_for_root(self, workspace_id, task_run_id):
        row = self.backend.read_one(
            "SELECT workflow_run_id FROM workflow_runs WHERE workspace_id=? AND root_task_run_id=? "
            "AND status IN " + _ACTIVE_RUN_STATUSES,
            (workspace_id, task_run_id),
        )
        return self.get_run(workspace_id, row[0]) if row else None

    def active_runs_for_root_session(self, workspace_id, session_id):
        """Non-terminal runs whose root TaskRun belongs to this Session."""
        rows = self.backend.read_all(
            "SELECT r.workflow_run_id FROM workflow_runs r "
            "JOIN task_runs t ON t.workspace_id=r.workspace_id AND t.task_run_id=r.root_task_run_id "
            "WHERE r.workspace_id=? AND t.session_id=? AND r.status IN "
            + _ACTIVE_RUN_STATUSES
            + " ORDER BY r.workflow_run_id",
            (workspace_id, session_id),
        )
        return tuple(self.get_run(workspace_id, row[0]) for row in rows)

    def active_runs_for_leaf_session(self, workspace_id, session_id):
        """Non-terminal runs owning this Session as an isolated node conversation."""
        rows = self.backend.read_all(
            "SELECT DISTINCT r.workflow_run_id FROM workflow_runs r "
            "JOIN workflow_node_runs n ON n.workspace_id=r.workspace_id "
            "AND n.workflow_run_id=r.workflow_run_id "
            "WHERE r.workspace_id=? AND r.status IN "
            + _ACTIVE_RUN_STATUSES
            + " AND json_extract(n.body_json,'$.conversation_session_id')=? "
            "ORDER BY r.workflow_run_id",
            (workspace_id, session_id),
        )
        return tuple(self.get_run(workspace_id, row[0]) for row in rows)

    def runs_for_root_session(self, workspace_id, session_id):
        """All runs whose root TaskRun belongs to this Session, terminal included."""
        rows = self.backend.read_all(
            "SELECT r.workflow_run_id FROM workflow_runs r "
            "JOIN task_runs t ON t.workspace_id=r.workspace_id AND t.task_run_id=r.root_task_run_id "
            "WHERE r.workspace_id=? AND t.session_id=? ORDER BY r.workflow_run_id",
            (workspace_id, session_id),
        )
        return tuple(self.get_run(workspace_id, row[0]) for row in rows)

    def nodes_for_conversation_session(self, workspace_id, session_id):
        """Every NodeRun admitted onto this Session as its conversation, any status."""
        rows = self.backend.read_all(
            "SELECT n.node_run_id FROM workflow_node_runs n "
            "WHERE n.workspace_id=? "
            "AND json_extract(n.body_json,'$.conversation_session_id')=? "
            "ORDER BY n.node_run_id",
            (workspace_id, session_id),
        )
        nodes = []
        for row in rows:
            node = self.get_node(workspace_id, str(row[0]))
            if node is not None:
                nodes.append(node)
        return tuple(nodes)

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
            timeout = value.budget_snapshot.admission_timeout_seconds
            expected_deadline = (
                value.started_at + timedelta(seconds=timeout)
                if value.started_at is not None and timeout is not None
                else None
            )
            if value.started_at is None or value.admission_deadline_at != expected_deadline:
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
                    "INSERT INTO workflow_node_runs "
                    "(node_run_id,workspace_id,workflow_run_id,node_id,attempt,status,body_json,"
                    "agent_run_id,leaf_session_id,leaf_task_run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        node.node_run_id,
                        node.workspace_id,
                        node.workflow_run_id,
                        node.node_id,
                        node.attempt,
                        node.status.value,
                        node.model_dump_json(),
                        node.agent_run_id,
                        node.conversation_session_id,
                        node.leaf_task_run_id,
                    ),
                )
            for ordinal, node_id in enumerate(self._stable_order(revision)):
                self.backend.executor().execute(
                    "INSERT INTO workflow_run_execution_nodes VALUES(?,?,?,?)",
                    (value.workflow_run_id, node_id, ordinal, "initial"),
                )
            self.bind_artifact(value.workspace_id, value.workflow_run_id, value.input_artifacts[0])
            return value

        return self.backend.transact(work)

    def create_continuation_run(
        self,
        parent: WorkflowRun,
        value: WorkflowRun,
        nodes: tuple[NodeRun, ...],
        execution_nodes: tuple[WorkflowExecutionNode, ...],
        imports: tuple[WorkflowArtifactImport, ...],
        *,
        expected_parent_row_version: int,
    ):
        """Atomically supersede a paused parent and transfer root ownership."""

        def work():
            current = self.get_run(parent.workspace_id, parent.workflow_run_id)
            revision = self.get_revision(value.workspace_id, value.workflow_revision_id)
            root = self.get_task(value.workspace_id, value.root_task_run_id)
            if (
                current is None
                or current != parent
                or current.row_version != expected_parent_row_version
                or current.status is not WorkflowStatus.PAUSED
                or not current.pause_requested
            ):
                raise ValueError("continuation parent revision conflict or parent is not paused")
            parent_nodes = self.list_nodes(parent.workspace_id, parent.workflow_run_id)
            if any(
                item.status in (WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED)
                for item in parent_nodes
            ):
                raise ValueError("continuation requires a drained parent with no Active nodes")
            if (
                revision is None
                or root is None
                or root.purpose != TaskRunPurpose.USER
                or root.status != TaskRunStatus.OPEN
                or value.workspace_id != parent.workspace_id
                or value.root_task_run_id != parent.root_task_run_id
                or value.parent_run_id != parent.workflow_run_id
                or value.run_relation not in {"continuation", "rerun"}
                or value.status is not WorkflowStatus.RUNNING
                or value.pause_requested
                or value.row_version != 1
            ):
                raise ValueError("continuation child facts are invalid")
            if value.run_relation == "continuation":
                if (
                    value.effective_lineage_budget_root_run_id
                    != parent.effective_lineage_budget_root_run_id
                    or value.admission_deadline_at != parent.admission_deadline_at
                ):
                    raise ValueError("continuation must inherit budget-root and deadline facts")
            elif value.effective_lineage_budget_root_run_id != value.workflow_run_id:
                raise ValueError("rerun must start a new lineage budget root")
            execution_ids = {item.node_id for item in execution_nodes}
            if (
                len(execution_ids) != len(execution_nodes)
                or execution_ids != {item.node_id for item in nodes}
                or any(item.workflow_run_id != value.workflow_run_id for item in execution_nodes)
            ):
                raise ValueError("continuation execution set does not match its NodeRuns")
            if execution_ids - {item.node_id for item in revision.nodes}:
                raise ValueError("continuation execution set references an unknown node")
            for node in nodes:
                if (node.workspace_id, node.workflow_run_id, node.status, node.row_version) != (
                    value.workspace_id,
                    value.workflow_run_id,
                    WorkflowStatus.QUEUED,
                    1,
                ):
                    raise ValueError("invalid continuation NodeRun")
            for item in parent_nodes:
                if item.status is WorkflowStatus.QUEUED:
                    self.save_node(
                        item.model_copy(
                            update={
                                "status": WorkflowStatus.CANCELLED,
                                "completed_at": self.backend.now(),
                                "row_version": item.row_version + 1,
                            }
                        ),
                        expected_row_version=item.row_version,
                    )
            superseded = current.model_copy(
                update={
                    "status": WorkflowStatus.SUPERSEDED,
                    "superseded_reason": "continued_by_patch",
                    "completed_at": self.backend.now(),
                    "row_version": current.row_version + 1,
                }
            )
            self.save_run(superseded, expected_row_version=current.row_version)
            sql = self.backend.executor()
            sql.execute(
                "INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    value.workflow_run_id,
                    value.workspace_id,
                    value.workflow_revision_id,
                    value.root_task_run_id,
                    value.status.value,
                    0,
                    value.run_relation,
                    value.effective_lineage_budget_root_run_id,
                    value.parent_run_id,
                    value.model_dump_json(),
                ),
            )
            for node in nodes:
                sql.execute(
                    "INSERT INTO workflow_node_runs "
                    "(node_run_id,workspace_id,workflow_run_id,node_id,attempt,status,body_json,"
                    "agent_run_id,leaf_session_id,leaf_task_run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        node.node_run_id,
                        node.workspace_id,
                        node.workflow_run_id,
                        node.node_id,
                        node.attempt,
                        node.status.value,
                        node.model_dump_json(),
                        node.agent_run_id,
                        node.conversation_session_id,
                        node.leaf_task_run_id,
                    ),
                )
            for item in sorted(execution_nodes, key=lambda row: row.topology_ordinal):
                sql.execute(
                    "INSERT INTO workflow_run_execution_nodes VALUES(?,?,?,?)",
                    (
                        item.workflow_run_id,
                        item.node_id,
                        item.topology_ordinal,
                        item.inclusion_reason,
                    ),
                )
            for item in imports:
                if item.workflow_run_id != value.workflow_run_id:
                    raise ValueError("continuation Artifact import has the wrong child")
                expected = self.get_effective_output(
                    value.workspace_id,
                    item.source_workflow_run_id,
                    item.source_node_id,
                    item.output_slot,
                )
                if expected is None or (
                    expected.artifact_id,
                    expected.contract,
                ) != (item.artifact_id, item.contract):
                    raise ValueError("continuation Artifact import is not exact lineage evidence")
                sql.execute(
                    "INSERT INTO workflow_run_artifact_imports VALUES(?,?,?,?,?,?,?,?)",
                    (
                        item.workflow_run_id,
                        item.source_workflow_run_id,
                        item.source_node_run_id,
                        item.source_node_id,
                        item.output_slot,
                        item.artifact_id,
                        item.contract.model_dump_json(),
                        int(item.inherited_at.timestamp()),
                    ),
                )
            self.bind_artifact(value.workspace_id, value.workflow_run_id, value.input_artifacts[0])
            return value

        return self.backend.transact(work)

    def list_execution_nodes(self, workspace_id, workflow_run_id):
        run = self.get_run(workspace_id, workflow_run_id)
        if run is None:
            return ()
        rows = self.backend.read_all(
            "SELECT node_id, topology_ordinal, inclusion_reason "
            "FROM workflow_run_execution_nodes WHERE workflow_run_id=? "
            "ORDER BY topology_ordinal, node_id",
            (workflow_run_id,),
        )
        return tuple(
            WorkflowExecutionNode(
                workflow_run_id=workflow_run_id,
                node_id=str(row[0]),
                topology_ordinal=int(row[1]),
                inclusion_reason=str(row[2]),
            )
            for row in rows
        )

    def create_rerun(
        self,
        parent: WorkflowRun,
        value: WorkflowRun,
        nodes: tuple[NodeRun, ...],
        execution_nodes: tuple[WorkflowExecutionNode, ...],
        imports: tuple[WorkflowArtifactImport, ...],
    ):
        """Create a post-terminal rerun as a new lineage budget root."""

        def work():
            current = self.get_run(parent.workspace_id, parent.workflow_run_id)
            revision = self.get_revision(value.workspace_id, value.workflow_revision_id)
            root = self.get_task(value.workspace_id, value.root_task_run_id)
            if current != parent or current is None or not current.status.terminal:
                raise ValueError("rerun requires an exact terminal parent")
            if (
                revision is None
                or root is None
                or root.purpose != TaskRunPurpose.USER
                or root.status is not TaskRunStatus.OPEN
                or self.active_for_root(value.workspace_id, value.root_task_run_id) is not None
                or value.run_relation != "rerun"
                or value.parent_run_id != parent.workflow_run_id
                or value.status is not WorkflowStatus.RUNNING
                or value.pause_requested
                or value.effective_lineage_budget_root_run_id != value.workflow_run_id
            ):
                raise ValueError("rerun child facts are invalid; resume the failed root first")
            execution_ids = {item.node_id for item in execution_nodes}
            if execution_ids != {item.node_id for item in nodes} or execution_ids - {
                item.node_id for item in revision.nodes
            }:
                raise ValueError("rerun execution set does not match its NodeRuns")
            sql = self.backend.executor()
            sql.execute(
                "INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    value.workflow_run_id,
                    value.workspace_id,
                    value.workflow_revision_id,
                    value.root_task_run_id,
                    value.status.value,
                    0,
                    value.run_relation,
                    value.workflow_run_id,
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
                    raise ValueError("invalid rerun NodeRun")
                sql.execute(
                    "INSERT INTO workflow_node_runs "
                    "(node_run_id,workspace_id,workflow_run_id,node_id,attempt,status,body_json,"
                    "agent_run_id,leaf_session_id,leaf_task_run_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        node.node_run_id,
                        node.workspace_id,
                        node.workflow_run_id,
                        node.node_id,
                        node.attempt,
                        node.status.value,
                        node.model_dump_json(),
                        node.agent_run_id,
                        node.conversation_session_id,
                        node.leaf_task_run_id,
                    ),
                )
            for item in execution_nodes:
                sql.execute(
                    "INSERT INTO workflow_run_execution_nodes VALUES(?,?,?,?)",
                    (
                        item.workflow_run_id,
                        item.node_id,
                        item.topology_ordinal,
                        item.inclusion_reason,
                    ),
                )
            for item in imports:
                expected = self.get_effective_output(
                    value.workspace_id,
                    item.source_workflow_run_id,
                    item.source_node_id,
                    item.output_slot,
                )
                if expected is None or (expected.artifact_id, expected.contract) != (
                    item.artifact_id,
                    item.contract,
                ):
                    raise ValueError("rerun Artifact import is not exact lineage evidence")
                sql.execute(
                    "INSERT INTO workflow_run_artifact_imports VALUES(?,?,?,?,?,?,?,?)",
                    (
                        item.workflow_run_id,
                        item.source_workflow_run_id,
                        item.source_node_run_id,
                        item.source_node_id,
                        item.output_slot,
                        item.artifact_id,
                        item.contract.model_dump_json(),
                        int(item.inherited_at.timestamp()),
                    ),
                )
            self.bind_artifact(value.workspace_id, value.workflow_run_id, value.input_artifacts[0])
            return value

        return self.backend.transact(work)

    def list_artifact_imports(self, workspace_id, workflow_run_id):
        run = self.get_run(workspace_id, workflow_run_id)
        if run is None:
            return ()
        rows = self.backend.read_all(
            "SELECT source_workflow_run_id, source_node_run_id, source_node_id, output_slot, "
            "artifact_id, contract_json, inherited_at_unix "
            "FROM workflow_run_artifact_imports WHERE workflow_run_id=? "
            "ORDER BY source_node_id, output_slot",
            (workflow_run_id,),
        )
        from morrow.core.workflows.contracts import ContractRef

        return tuple(
            WorkflowArtifactImport(
                workflow_run_id=workflow_run_id,
                source_workflow_run_id=str(row[0]),
                source_node_run_id=str(row[1]),
                source_node_id=str(row[2]),
                output_slot=str(row[3]),
                artifact_id=str(row[4]),
                contract=ContractRef.model_validate_json(row[5]),
                inherited_at=datetime.fromtimestamp(int(row[6]), UTC),
            )
            for row in rows
        )

    def get_effective_output(self, workspace_id, workflow_run_id, node_id, output_slot):
        row = self.backend.read_one(
            "SELECT b.body_json FROM workflow_artifact_bindings b "
            "JOIN workflow_node_runs n ON n.node_run_id=b.node_run_id "
            "WHERE b.workflow_run_id=? AND n.workspace_id=? AND n.node_id=? "
            "AND b.direction='output' AND b.name=?",
            (workflow_run_id, workspace_id, node_id, output_slot),
        )
        if row is not None:
            return ArtifactBinding.model_validate_json(row[0])
        row = self.backend.read_one(
            "SELECT artifact_id, contract_json FROM workflow_run_artifact_imports "
            "WHERE workflow_run_id=? AND source_node_id=? AND output_slot=?",
            (workflow_run_id, node_id, output_slot),
        )
        if row is None:
            return None
        from morrow.core.workflows.contracts import ContractRef

        return ArtifactBinding(
            name=output_slot,
            artifact_id=str(row[0]),
            contract=ContractRef.model_validate_json(row[1]),
        )

    @staticmethod
    def _stable_order(revision: WorkflowRevision) -> tuple[str, ...]:
        incoming = {item.node_id: 0 for item in revision.nodes}
        consumers = {item.node_id: [] for item in revision.nodes}
        for edge in revision.edges:
            incoming[edge.to_node_id] += 1
            consumers[edge.from_node_id].append(edge.to_node_id)
        ready = sorted(key for key, value in incoming.items() if value == 0)
        result = []
        while ready:
            current = ready.pop(0)
            result.append(current)
            for target in sorted(consumers[current]):
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
            ready.sort()
        if len(result) != len(incoming):
            raise ValueError("Workflow revision is not a DAG")
        return tuple(result)

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

    # Node execution segments (P02) -------------------------------------------

    def append_segment(self, value):
        """Append one execution segment under the repository's identity rules.

        Rejects unknown nodes, workspace mismatch, leaf-ownership drift, a
        predecessor that is not the node's current tail, and a second active
        segment. Identity columns are immutable once written (trigger guard).
        """
        from morrow.core.workflows.segments import WorkflowNodeSegment

        if not isinstance(value, WorkflowNodeSegment):
            raise ValueError("append_segment requires a WorkflowNodeSegment")

        def work():
            node = self.get_node(value.workspace_id, value.node_run_id)
            if node is None or node.workflow_run_id != value.workflow_run_id:
                raise ValueError("segment node does not belong to the workflow run")
            ownership = self.get_leaf_ownership(value.workspace_id, value.node_run_id)
            refs = (value.leaf_session_id, value.leaf_task_run_id)
            if ownership is not None and refs != ownership:
                raise ValueError("segment leaf references must match the node's leaf ownership")
            if ownership is None and value.leaf_session_id is not None:
                # An invoking_session node owns the root conversation and has
                # no leaf-ownership row: accept the segment only when the refs
                # match the node's own admission binding. A truly unadmitted
                # node binds nothing and stays rejected.
                admitted_refs = (node.conversation_session_id, node.leaf_task_run_id)
                if refs != admitted_refs:
                    raise ValueError("an unadmitted node cannot own a segment conversation")
            if value.previous_segment_id is not None:
                previous = self.get_segment(value.workspace_id, value.previous_segment_id)
                if (
                    previous is None
                    or previous.node_run_id != value.node_run_id
                    or previous.ordinal != value.ordinal - 1
                ):
                    raise ValueError("segment predecessor must be the node's previous tail")
                if previous.status == "active":
                    raise ValueError("a node cannot open a second active segment")
            elif value.ordinal != 1 or self.segments_for_node(
                value.workspace_id, value.node_run_id
            ):
                raise ValueError("only the first segment of a node lacks a predecessor")
            self.backend.executor().execute(
                "INSERT INTO workflow_node_segments (segment_id, workspace_id, "
                "workflow_run_id, node_run_id, ordinal, leaf_session_id, leaf_task_run_id, "
                "agent_run_id, turn_id, status, pause_reason, previous_segment_id, "
                "accepted_command_id, row_version, created_at_unix, updated_at_unix, "
                "body_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value.segment_id,
                    value.workspace_id,
                    value.workflow_run_id,
                    value.node_run_id,
                    value.ordinal,
                    value.leaf_session_id,
                    value.leaf_task_run_id,
                    value.agent_run_id,
                    value.turn_id,
                    value.status,
                    value.pause_reason,
                    value.previous_segment_id,
                    value.accepted_command_id,
                    value.row_version,
                    int(value.created_at.timestamp()),
                    int(value.updated_at.timestamp()),
                    value.model_dump_json(),
                ),
            )
            return value

        return self.backend.transact(work)

    def close_segment(
        self,
        workspace_id,
        segment_id,
        *,
        status,
        pause_reason=None,
        agent_run_id=None,
        turn_id=None,
        expected_row_version,
    ):
        """Settle an active segment (interrupted/completed) under OCC."""

        if status not in ("interrupted", "completed"):
            raise ValueError("close_segment settles to interrupted or completed")
        if pause_reason is not None and pause_reason not in get_args(PauseReason):
            raise ValueError("unknown Workflow node pause reason")

        def work():
            current = self.get_segment(workspace_id, segment_id)
            if current is None:
                raise ValueError("WorkflowNodeSegment is missing")
            if current.status != "active":
                if current.status == status:
                    return current
                raise ValueError("WorkflowNodeSegment revision conflict")
            update = {
                "status": status,
                "row_version": current.row_version + 1,
                "updated_at": self.backend.now(),
            }
            if pause_reason is not None:
                update["pause_reason"] = pause_reason
            if agent_run_id is not None:
                update["agent_run_id"] = agent_run_id
            if turn_id is not None:
                update["turn_id"] = turn_id
            value = current.model_copy(update=update)
            self.backend.executor().execute(
                "UPDATE workflow_node_segments SET status=?, pause_reason=?, agent_run_id=?, "
                "turn_id=?, row_version=?, updated_at_unix=?, body_json=? "
                "WHERE segment_id=? AND row_version=? AND status='active'",
                (
                    value.status,
                    value.pause_reason,
                    value.agent_run_id,
                    value.turn_id,
                    value.row_version,
                    int(value.updated_at.timestamp()),
                    value.model_dump_json(),
                    segment_id,
                    expected_row_version,
                ),
            )
            changed = self.backend.executor().execute("SELECT changes()")
            if not changed or int(changed[0][0]) != 1:
                raise ValueError("WorkflowNodeSegment revision conflict")
            return value

        return self.backend.transact(work)

    _SEGMENT_COLUMNS = (
        "segment_id, workspace_id, workflow_run_id, node_run_id, ordinal, "
        "leaf_session_id, leaf_task_run_id, agent_run_id, turn_id, status, "
        "pause_reason, previous_segment_id, accepted_command_id, row_version, "
        "created_at_unix, updated_at_unix"
    )

    def _segment(self, row):
        """Column-authoritative mapping; body_json only carries audit metadata."""
        from datetime import UTC, datetime

        from morrow.core.workflows.segments import WorkflowNodeSegment

        if row is None:
            return None
        try:
            return WorkflowNodeSegment(
                segment_id=str(row[0]),
                workspace_id=str(row[1]),
                workflow_run_id=str(row[2]),
                node_run_id=str(row[3]),
                ordinal=int(row[4]),
                leaf_session_id=row[5] if row[5] is None else str(row[5]),
                leaf_task_run_id=row[6] if row[6] is None else str(row[6]),
                agent_run_id=row[7] if row[7] is None else str(row[7]),
                turn_id=row[8] if row[8] is None else str(row[8]),
                status=str(row[9]),
                pause_reason=row[10] if row[10] is None else str(row[10]),
                previous_segment_id=row[11] if row[11] is None else str(row[11]),
                accepted_command_id=row[12] if row[12] is None else str(row[12]),
                row_version=int(row[13]),
                created_at=datetime.fromtimestamp(int(row[14]), UTC),
                updated_at=datetime.fromtimestamp(int(row[15]), UTC),
            )
        except (ValueError, TypeError, IndexError):
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "Workflow node segment record is corrupt"
            ) from None

    def get_segment(self, workspace_id, segment_id):
        row = self.backend.read_one(
            f"SELECT {self._SEGMENT_COLUMNS} FROM workflow_node_segments "
            "WHERE segment_id=? AND workspace_id=?",
            (segment_id, workspace_id),
        )
        return self._segment(row)

    def segments_for_node(self, workspace_id, node_run_id):
        rows = self.backend.read_all(
            f"SELECT {self._SEGMENT_COLUMNS} FROM workflow_node_segments "
            "WHERE workspace_id=? AND node_run_id=? ORDER BY ordinal",
            (workspace_id, node_run_id),
        )
        return tuple(segment for row in rows if (segment := self._segment(row)) is not None)

    def current_segment(self, node_run_id):
        """Port view: the node's one active segment, workspace-agnostic."""
        row = self.backend.read_one(
            f"SELECT {self._SEGMENT_COLUMNS} FROM workflow_node_segments "
            "WHERE node_run_id=? AND status='active'",
            (node_run_id,),
        )
        return self._segment(row)

    def first_segment(self, workspace_id, node_run_id):
        row = self.backend.read_one(
            f"SELECT {self._SEGMENT_COLUMNS} FROM workflow_node_segments "
            "WHERE workspace_id=? AND node_run_id=? ORDER BY ordinal LIMIT 1",
            (workspace_id, node_run_id),
        )
        return self._segment(row)

    def segment_directory(self):
        """Frozen ``SegmentDirectoryPort`` view over this repository."""
        return SqliteSegmentDirectory(self)

    def save_run(self, value: WorkflowRun, *, expected_row_version):
        return self._save(value, expected_row_version, node=False)

    def save_node(self, value: NodeRun, *, expected_row_version):
        return self._save(value, expected_row_version, node=True)

    def save_recovered_run(self, value: WorkflowRun, *, expected_row_version):
        """Restricted historical-recovery write: the only sanctioned
        ``failed -> paused`` run transition. Ordinary ``save_run`` keeps
        refusing every terminal transition; the recovery service owns this
        path and re-validates its eligibility inside its own transaction."""

        return self._save(value, expected_row_version, node=False, recovery=True)

    def save_recovered_node(self, value: NodeRun, *, expected_row_version):
        """Restricted historical-recovery write: only ``failed -> running``
        and ``cancelled -> queued`` node transitions, re-verified against the
        frozen evidence like any other node save."""

        return self._save(value, expected_row_version, node=True, recovery=True)

    def _save(self, value, expected, *, node, recovery=False):
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
            if (
                node
                and current.status is WorkflowStatus.QUEUED
                and value.status is WorkflowStatus.RUNNING
                and self.list_replan_signals(
                    value.workspace_id, value.workflow_run_id, pending_only=True
                )
            ):
                raise ValueError("unconsumed ReplanSignal closes node admission")
            mutable = {"status", "row_version", "completed_at"}
            if node and current.status == WorkflowStatus.QUEUED:
                mutable |= {
                    "started_at",
                    "conversation_session_id",
                    "leaf_task_run_id",
                    "agent_run_id",
                    "effective_node_generation_request_cap",
                    "parallel_read_digest",
                }
            if not node:
                mutable |= {
                    "result_status",
                    "pending_terminal_intent",
                    "pause_requested",
                    "superseded_reason",
                }
            for field in type(value).model_fields.keys() - mutable:
                if getattr(value, field) != getattr(current, field):
                    raise ValueError("frozen Workflow evidence cannot change")
            if current.status != value.status:
                sanctioned = recovery and (
                    (
                        not node
                        and current.status is WorkflowStatus.FAILED
                        and value.status is WorkflowStatus.PAUSED
                    )
                    or (
                        node
                        and current.status is WorkflowStatus.FAILED
                        and value.status is WorkflowStatus.RUNNING
                    )
                    or (
                        node
                        and current.status is WorkflowStatus.CANCELLED
                        and value.status is WorkflowStatus.QUEUED
                    )
                )
                if not sanctioned:
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
                if current.status is WorkflowStatus.QUEUED:
                    active = tuple(
                        n
                        for n in self.list_nodes(value.workspace_id, value.workflow_run_id)
                        if n.status in {WorkflowStatus.RUNNING, WorkflowStatus.BLOCKED}
                    )
                    if len(active) >= run.budget_snapshot.max_concurrency:
                        raise ValueError("Workflow concurrency slots are exhausted")
                    if active and (
                        value.parallel_read_digest is None
                        or any(n.parallel_read_digest is None for n in active)
                    ):
                        raise ValueError(
                            "Workflow Writers and unproven nodes require serial admission"
                        )
                    if value.parallel_read_digest is not None:
                        from morrow.core.capabilities import AccessScope

                        permission = (
                            self.get_permission_snapshot(
                                value.workspace_id, agent.permission_snapshot_id
                            )
                            if agent and agent.permission_snapshot_id
                            else None
                        )
                        if (
                            definition.access_mode != "read"
                            or definition.conversation_scope != "isolated"
                            or permission is None
                            or not permission.workspace_read_only
                            or permission.access_scope is not AccessScope.WORKSPACE
                            or permission.grant_id is not None
                            or permission.agent_run_id != value.agent_run_id
                            or permission.tool_schema_digest != agent.snapshot.tool_schema_digest
                        ):
                            raise ValueError(
                                "parallel read admission requires frozen permission evidence"
                            )
                direct = definition.conversation_scope == "invoking_session"
                owner = self.get_leaf_ownership(value.workspace_id, value.node_run_id)
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
                effective_cap = value.effective_node_generation_request_cap
                declared_cap = definition.declared_node_max_agent_generation_requests
                if (
                    effective_cap is not None
                    and declared_cap is not None
                    and effective_cap > declared_cap
                ):
                    raise ValueError("Workflow node request cap escalates the revision")
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
                    "UPDATE workflow_node_runs SET status=?, body_json=?, agent_run_id=?, "
                    "leaf_session_id=?, leaf_task_run_id=? WHERE node_run_id=?",
                    (
                        value.status.value,
                        value.model_dump_json(),
                        value.agent_run_id,
                        value.conversation_session_id,
                        value.leaf_task_run_id,
                        value.node_run_id,
                    ),
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
                    owner = self.get_leaf_ownership(workspace_id, node_run_id)
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
                        linked = self.get_effective_output(
                            workspace_id, run_id, ref.node_id, ref.output_slot
                        )
                        expected_artifact_id = linked.artifact_id if linked else None
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


class SqliteSegmentDirectory:
    """Frozen ``SegmentDirectoryPort`` implementation over the workflow journal.

    Lane C/D and tests may consume this port directly; node identity is
    globally unique so the frozen port methods take no workspace argument.
    """

    def __init__(self, journal: SqliteWorkflowJournal) -> None:
        self._journal = journal

    def current_segment(self, node_run_id: str):
        return self._journal.current_segment(node_run_id)

    def segments(self, workflow_run_id: str, node_run_id: str):
        rows = self._journal.backend.read_all(
            f"SELECT {self._journal._SEGMENT_COLUMNS} FROM workflow_node_segments "
            "WHERE workflow_run_id=? AND node_run_id=? ORDER BY ordinal",
            (workflow_run_id, node_run_id),
        )
        return tuple(
            segment for row in rows if (segment := self._journal._segment(row)) is not None
        )
