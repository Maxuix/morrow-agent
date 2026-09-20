"""Planning OCC and ownership on the existing operational transaction backend."""

import json

from morrow.core.execution import StaleRowVersionError
from morrow.core.workflows.planning import (
    DraftVersion,
    PlanDecision,
    PlanningBinding,
    PlanningOperation,
    TaskPlanProvenance,
)

_PAUSE_FACT_KINDS = frozenset({"pause", "resume", "shutdown"})
_PAUSE_FACT_LIFECYCLES = frozenset({"requested", "quiescing", "suspended", "resumed"})
_PAUSE_FACT_REASONS = frozenset({"user_interrupt", "shutdown"})


class SqlitePlanningJournal:
    def __init__(self, workflows):
        self.workflows = workflows
        self.backend = workflows.backend

    def binding(self, workspace_id, session_id, identity=None):
        return self.workflows._load(
            PlanningBinding,
            "SELECT body_json FROM workflow_planning_bindings WHERE workspace_id=? AND session_id=? "
            + ("AND planning_binding_id=?" if identity else "AND status='active'"),
            (workspace_id, session_id, identity) if identity else (workspace_id, session_id),
        )

    def save_binding(self, value, *, expected=0):
        def work():
            session = self.backend.read_one(
                "SELECT workspace_id FROM sessions WHERE session_id=?", (value.session_id,)
            )
            if session is None or session[0] != value.workspace_id:
                raise ValueError("planning Session ownership mismatch")
            old = self.binding(value.workspace_id, value.session_id, value.planning_binding_id)
            if (old.row_version if old else 0) != expected or value.row_version != expected + 1:
                raise StaleRowVersionError("stale planning binding")
            if old and (
                old.status != "active"
                or any(
                    getattr(old, k) != getattr(value, k)
                    for k in (
                        "workspace_id",
                        "session_id",
                        "origin_interaction_id",
                        "mode",
                        "parent_run_id",
                        "parent_revision_id",
                        "created_at",
                        "context_ref",
                    )
                )
            ):
                raise ValueError("planning binding identity or terminal state is immutable")
            if value.parent_run_id:
                parent = self.workflows.get_run(value.workspace_id, value.parent_run_id)
                if parent is None or parent.workflow_revision_id != value.parent_revision_id:
                    raise ValueError("planning parent mismatch")
            if value.current_draft_id:
                other = self.backend.read_one(
                    "SELECT 1 FROM workflow_planning_bindings WHERE workspace_id=? "
                    "AND current_draft_id=? AND planning_binding_id<>?",
                    (value.workspace_id, value.current_draft_id, value.planning_binding_id),
                )
                if other:
                    raise ValueError("draft belongs to another plan")
            args = (
                value.workspace_id,
                value.session_id,
                value.current_draft_id,
                value.status,
                value.row_version,
                json.dumps(list(value.artifact_ids), ensure_ascii=False, separators=(",", ":")),
                value.model_dump_json(),
                value.planning_binding_id,
            )
            if old:
                self.backend.executor().execute(
                    "UPDATE workflow_planning_bindings SET workspace_id=?,session_id=?,"
                    "current_draft_id=?,status=?,row_version=?,artifact_ids_json=?,body_json=? "
                    "WHERE planning_binding_id=?",
                    args,
                )
            else:
                if value.status == "active":
                    current = self.binding(value.workspace_id, value.session_id)
                    if current is not None:
                        raise ValueError("active plan already exists for this session")
                self.backend.executor().execute(
                    "INSERT INTO workflow_planning_bindings(workspace_id,session_id,current_draft_id,"
                    "status,row_version,artifact_ids_json,body_json,planning_binding_id) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    args,
                )
            return value

        return self.backend.transact(work)

    def operation(self, workspace_id, session_id, identity):
        return self.workflows._load(
            PlanningOperation,
            "SELECT body_json FROM workflow_planning_operations WHERE workspace_id=? "
            "AND session_id=? AND planning_operation_id=?",
            (workspace_id, session_id, identity),
        )

    def command(self, workspace_id, command_id):
        return self.workflows._load(
            PlanningOperation,
            "SELECT body_json FROM workflow_planning_operations WHERE workspace_id=? AND command_id=?",
            (workspace_id, command_id),
        )

    def save_operation(self, value, *, expected=0):
        def work():
            old = self.operation(value.workspace_id, value.session_id, value.planning_operation_id)
            if (old.row_version if old else 0) != expected or value.row_version != expected + 1:
                raise StaleRowVersionError("stale planning operation")
            if old:
                immutable = (
                    "workspace_id",
                    "session_id",
                    "planning_binding_id",
                    "operation",
                    "command_id",
                    "request_digest",
                    "accepted_request_json",
                    "base_draft_id",
                    "base_draft_version",
                    "created_at",
                )
                if old.status not in {"queued", "running"} or any(
                    getattr(old, k) != getattr(value, k) for k in immutable
                ):
                    raise ValueError("planning operation is immutable")
            binding = self.binding(value.workspace_id, value.session_id, value.planning_binding_id)
            if binding is None:
                raise ValueError("planning operation ownership mismatch")
            if old is None and value.base_draft_id != binding.current_draft_id:
                raise ValueError("planning baseline belongs to another binding")
            if value.status == "succeeded" and value.result_draft_id != binding.current_draft_id:
                raise ValueError("planning result belongs to another binding")
            if value.base_draft_id and not self.version(
                value.workspace_id, value.base_draft_id, value.base_draft_version
            ):
                raise ValueError("planning baseline is missing")
            args = (
                value.workspace_id,
                value.session_id,
                value.planning_binding_id,
                value.command_id,
                value.status,
                value.row_version,
                value.result_draft_id,
                value.result_draft_version,
                value.model_dump_json(),
                value.planning_operation_id,
            )
            if old:
                self.backend.executor().execute(
                    "UPDATE workflow_planning_operations SET workspace_id=?,session_id=?,planning_binding_id=?,"
                    "command_id=?,status=?,row_version=?,result_draft_id=?,result_draft_version=?,body_json=? "
                    "WHERE planning_operation_id=?",
                    args,
                )
            else:
                self.backend.executor().execute(
                    "INSERT INTO workflow_planning_operations(workspace_id,session_id,planning_binding_id,"
                    "command_id,status,row_version,result_draft_id,result_draft_version,body_json,planning_operation_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    args,
                )
            self.event(
                value.workspace_id,
                value.session_id,
                {
                    "operation_id": value.planning_operation_id,
                    "status": value.status,
                    "row_version": value.row_version,
                    "error_code": value.error_code,
                },
            )
            return value

        return self.backend.transact(work)

    def append_pause_fact(
        self,
        workspace_id,
        planning_operation_id,
        *,
        command_id,
        fact_kind,
        lifecycle,
        reason,
        control_generation,
        body_json,
        rebuild_json=None,
        created_at,
    ):
        """Append one immutable pause/resume/shutdown lifecycle fact (P05.2)."""
        if fact_kind not in _PAUSE_FACT_KINDS:
            raise ValueError("unknown planning pause fact kind")
        if lifecycle not in _PAUSE_FACT_LIFECYCLES:
            raise ValueError("unknown planning pause fact lifecycle")
        if reason not in _PAUSE_FACT_REASONS:
            raise ValueError("unknown planning pause fact reason")
        self.backend.executor().execute(
            "INSERT INTO workflow_planning_pause_facts(workspace_id,planning_operation_id,"
            "command_id,fact_kind,lifecycle,reason,control_generation,rebuild_json,body_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                workspace_id,
                planning_operation_id,
                command_id,
                fact_kind,
                lifecycle,
                reason,
                control_generation,
                rebuild_json,
                body_json,
                created_at,
            ),
        )

    def pause_facts(self, workspace_id, planning_operation_id):
        return self.backend.read_all(
            "SELECT sequence,command_id,fact_kind,lifecycle,reason,control_generation,"
            "rebuild_json,body_json,created_at FROM workflow_planning_pause_facts "
            "WHERE workspace_id=? AND planning_operation_id=? ORDER BY sequence",
            (workspace_id, planning_operation_id),
        )

    def latest_pause_lifecycle(self, workspace_id, planning_operation_id):
        row = self.backend.read_one(
            "SELECT lifecycle FROM workflow_planning_pause_facts "
            "WHERE workspace_id=? AND planning_operation_id=? ORDER BY sequence DESC LIMIT 1",
            (workspace_id, planning_operation_id),
        )
        return row[0] if row else None

    def pause_pending(self, workspace_id, planning_operation_id):
        """True while a pause intent is accepted but the safe point not reached."""
        return self.latest_pause_lifecycle(workspace_id, planning_operation_id) in {
            "requested",
            "quiescing",
        }

    def paused(self, workspace_id, planning_operation_id):
        """True when the operation sits at a durable suspended safe point."""
        return self.latest_pause_lifecycle(workspace_id, planning_operation_id) == "suspended"

    def open_pause_command(self, workspace_id, planning_operation_id):
        """The pause command that is still pending suspension, if any."""
        row = self.backend.read_one(
            "SELECT command_id FROM workflow_planning_pause_facts "
            "WHERE workspace_id=? AND planning_operation_id=? AND lifecycle IN ('requested','quiescing') "
            "ORDER BY sequence DESC LIMIT 1",
            (workspace_id, planning_operation_id),
        )
        return row[0] if row else None

    def suspend_snapshot(self, workspace_id, planning_operation_id):
        """Latest suspended fact's rebuild snapshot, or None (never suspended)."""
        row = self.backend.read_one(
            "SELECT rebuild_json FROM workflow_planning_pause_facts "
            "WHERE workspace_id=? AND planning_operation_id=? AND lifecycle='suspended' "
            "ORDER BY sequence DESC LIMIT 1",
            (workspace_id, planning_operation_id),
        )
        if row is None or row[0] is None:
            return None
        return json.loads(row[0])

    def save_outcome(
        self,
        workspace_id,
        planning_operation_id,
        *,
        layer,
        attempt,
        request_sequence,
        outcome,
        started_at,
        ended_at,
        reason=None,
        revision=1,
        body_json=None,
    ):
        self.backend.executor().execute(
            "INSERT INTO workflow_planning_request_outcomes(workspace_id,planning_operation_id,"
            "layer,attempt,request_sequence,outcome,started_at,ended_at,reason,revision,body_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                workspace_id,
                planning_operation_id,
                layer,
                attempt,
                request_sequence,
                outcome,
                started_at,
                ended_at,
                reason,
                revision,
                body_json,
            ),
        )

    def outcomes(self, workspace_id, planning_operation_id, *, layer=None):
        if layer is None:
            return self.backend.read_all(
                "SELECT layer,attempt,request_sequence,outcome,started_at,ended_at,reason,"
                "revision,body_json FROM workflow_planning_request_outcomes "
                "WHERE workspace_id=? AND planning_operation_id=? ORDER BY rowid",
                (workspace_id, planning_operation_id),
            )
        return self.backend.read_all(
            "SELECT layer,attempt,request_sequence,outcome,started_at,ended_at,reason,"
            "revision,body_json FROM workflow_planning_request_outcomes "
            "WHERE workspace_id=? AND planning_operation_id=? AND layer=? ORDER BY rowid",
            (workspace_id, planning_operation_id, layer),
        )

    def latest_outcome_revision(self, workspace_id, planning_operation_id, layer):
        row = self.backend.read_one(
            "SELECT revision FROM workflow_planning_request_outcomes "
            "WHERE workspace_id=? AND planning_operation_id=? AND layer=? "
            "ORDER BY rowid DESC LIMIT 1",
            (workspace_id, planning_operation_id, layer),
        )
        return row[0] if row else 0

    def next_request_sequence(self, workspace_id, planning_operation_id):
        row = self.backend.read_one(
            "SELECT max(request_sequence) FROM workflow_planning_request_outcomes "
            "WHERE workspace_id=? AND planning_operation_id=? AND layer='model_request'",
            (workspace_id, planning_operation_id),
        )
        return (row[0] or 0) + 1

    def version(self, workspace_id, draft_id, version):
        return self.workflows._load(
            DraftVersion,
            "SELECT body_json FROM workflow_draft_versions WHERE workspace_id=? AND draft_id=? AND version=?",
            (workspace_id, draft_id, version),
        )

    def append_version(self, value):
        current = self.workflows.get_draft(value.workspace_id, value.draft_id)
        if (
            current is None
            or current.row_version != value.version
            or current.source != value.source
        ):
            raise ValueError("draft history must match current source")
        self.backend.executor().execute(
            "INSERT INTO workflow_draft_versions VALUES(?,?,?,?,?)",
            (
                value.workspace_id,
                value.draft_id,
                value.version,
                value.command_id,
                value.model_dump_json(),
            ),
        )
        return value

    def history(self, workspace_id, draft_id, *, after=0, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100 or after < 0:
            raise ValueError("invalid history page")
        return tuple(
            DraftVersion.model_validate_json(row[0])
            for row in self.backend.read_all(
                "SELECT body_json FROM workflow_draft_versions WHERE workspace_id=? AND draft_id=? "
                "AND version>? ORDER BY version LIMIT ?",
                (workspace_id, draft_id, after, limit),
            )
        )

    def operations(self, workspace_id, session_id, *, after="", limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid operation page")
        return tuple(
            PlanningOperation.model_validate_json(row[0])
            for row in self.backend.read_all(
                "SELECT body_json FROM workflow_planning_operations WHERE workspace_id=? AND session_id=? "
                "AND planning_operation_id>? ORDER BY planning_operation_id LIMIT ?",
                (workspace_id, session_id, after, limit),
            )
        )

    def event(self, workspace_id, session_id, payload):
        self.backend.executor().execute(
            "INSERT INTO workflow_planning_events(workspace_id,session_id,body_json) VALUES(?,?,?)",
            (workspace_id, session_id, json.dumps(payload)),
        )
        # Optional display-only hook (master plan P3.6): the durable fact above
        # stays the only owner; a listener failure never affects planning.
        listener = getattr(self, "activity_listener", None)
        if listener is not None:
            try:
                listener(workspace_id, session_id, payload)
            except Exception:
                pass

    def events(self, workspace_id, session_id, *, after=0, limit=100):
        if not 1 <= limit <= 100 or after < 0:
            raise ValueError("invalid event page")
        return tuple(
            {"sequence": row[0], **json.loads(row[1])}
            for row in self.backend.read_all(
                "SELECT sequence,body_json FROM workflow_planning_events WHERE workspace_id=? AND session_id=? "
                "AND sequence>? ORDER BY sequence LIMIT ?",
                (workspace_id, session_id, after, limit),
            )
        )

    def get_binding(self, workspace_id, binding_id):
        return self.workflows._load(
            PlanningBinding,
            "SELECT body_json FROM workflow_planning_bindings "
            "WHERE workspace_id=? AND planning_binding_id=?",
            (workspace_id, binding_id),
        )

    def decision(self, workspace_id, identity):
        return self.workflows._load(
            PlanDecision,
            "SELECT body_json FROM workflow_plan_decisions "
            "WHERE workspace_id=? AND plan_decision_id=?",
            (workspace_id, identity),
        )

    def decision_command(self, workspace_id, command_id):
        return self.workflows._load(
            PlanDecision,
            "SELECT body_json FROM workflow_plan_decisions WHERE workspace_id=? AND command_id=?",
            (workspace_id, command_id),
        )

    def save_decision(self, value: PlanDecision):
        def work():
            session = self.backend.read_one(
                "SELECT workspace_id FROM sessions WHERE session_id=?", (value.session_id,)
            )
            if session is None or session[0] != value.workspace_id:
                raise ValueError("plan decision Session ownership mismatch")
            existing = self.decision(value.workspace_id, value.plan_decision_id)
            if existing is not None:
                if existing != value:
                    raise ValueError("plan decision identifier conflict")
                return existing
            by_command = self.decision_command(value.workspace_id, value.command_id)
            if by_command is not None:
                if by_command != value:
                    raise ValueError("plan decision command conflict")
                return by_command
            self.backend.executor().execute(
                "INSERT INTO workflow_plan_decisions VALUES(?,?,?,?,?,?,?)",
                (
                    value.plan_decision_id,
                    value.workspace_id,
                    value.session_id,
                    value.command_id,
                    value.decision,
                    value.result_id,
                    value.model_dump_json(),
                ),
            )
            return value

        return self.backend.transact(work)

    def provenance(self, workspace_id, revision_id):
        return self.workflows._load(
            TaskPlanProvenance,
            "SELECT body_json FROM workflow_task_plan_provenance "
            "WHERE workspace_id=? AND workflow_revision_id=?",
            (workspace_id, revision_id),
        )

    def decisions(self, workspace_id, session_id):
        rows = self.backend.read_all(
            "SELECT body_json FROM workflow_plan_decisions "
            "WHERE workspace_id=? AND session_id=? ORDER BY plan_decision_id",
            (workspace_id, session_id),
        )
        values = tuple(PlanDecision.model_validate_json(row[0]) for row in rows)
        return tuple(sorted(values, key=lambda item: item.created_at, reverse=True))
