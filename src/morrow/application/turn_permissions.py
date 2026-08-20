"""Permission evidence coordination for one durable foreground AgentRun."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from morrow.core.domain import AgentRunSnapshot, sha256_digest
from morrow.core.execution import (
    DurableToolExecution,
    EffectClass,
    assert_handler_may_enter,
)
from morrow.core.journal import RunPermissionJournalPort
from morrow.core.permissions import (
    PERMISSION_POLICY_VERSION,
    CapabilityGrant,
    CapabilityIsolation,
    IsolationLabel,
    PermissionEvidenceError,
    PermissionSnapshot,
    assert_grant_snapshot_matches,
    capability_grant_digest,
    workspace_root_digest,
)
from morrow.core.ports import IdSource
from morrow.runtime.session import Session


def build_permission_snapshot(
    session: Session,
    *,
    workspace_id: str,
    base_snapshot: AgentRunSnapshot,
    permission_snapshot_id: str,
    task_run_id: str,
    turn_id: str,
    agent_run_id: str,
    grant: CapabilityGrant | None = None,
    created_at: datetime,
) -> PermissionSnapshot:
    capability = session.workspace_capability
    root_digest = (
        workspace_root_digest(capability.root)
        if capability is not None
        else sha256_digest(f"workspace:{workspace_id}".encode())
    )
    if grant is not None:
        if not grant.is_active(created_at):
            raise RuntimeError("capability grant is expired or revoked")
        if (
            grant.workspace_id != workspace_id
            or grant.task_run_id != task_run_id
            or grant.agent_run_id != agent_run_id
        ):
            raise RuntimeError("capability grant does not match the AgentRun subjects")
        grant_digest = capability_grant_digest(grant)
        capabilities = grant.capabilities
        isolations = tuple(
            CapabilityIsolation(
                capability=item,
                isolation=IsolationLabel.UNCONFINED_HOST,
            )
            for item in capabilities
        )
    else:
        grant_digest = None
        capabilities = ()
        isolations = ()
    return PermissionSnapshot(
        permission_snapshot_id=permission_snapshot_id,
        workspace_id=workspace_id,
        session_id=session.session_id,
        task_run_id=task_run_id,
        turn_id=turn_id,
        agent_run_id=agent_run_id,
        access_scope=session.permission_profile.access_scope,
        approval_mode=session.permission_profile.approval_mode,
        process_isolation=session.permission_profile.process_isolation,
        workspace_root_digest=root_digest,
        workspace_read_only=session.read_only or bool(capability and capability.read_only),
        tool_schema_digest=base_snapshot.tool_schema_digest,
        run_policy_digest=base_snapshot.run_policy_digest,
        permission_profile_digest=base_snapshot.permission_profile_digest,
        policy_version=PERMISSION_POLICY_VERSION,
        source_revisions=base_snapshot.source_revisions,
        grant_id=grant.grant_id if grant is not None else None,
        grant_digest=grant_digest,
        granted_capabilities=capabilities,
        capability_isolations=isolations,
        created_at=created_at,
    )


class RunPermissionCoordinator:
    """Freeze and revalidate immutable permission evidence for one workspace."""

    def __init__(
        self,
        journal: RunPermissionJournalPort,
        *,
        workspace_id: str,
        id_source: IdSource,
        clock: Callable[[], datetime],
    ) -> None:
        self.journal = journal
        self.workspace_id = workspace_id
        self.id_source = id_source
        self.clock = clock

    def freeze(
        self,
        session: Session,
        *,
        agent_run_id: str | None,
        task_run_id: str | None,
        turn_id: str | None,
        now: datetime | None = None,
    ) -> PermissionSnapshot:
        if agent_run_id is None:
            raise RuntimeError("permission snapshot requires an open AgentRun")
        run = self.journal.get_agent_run(self.workspace_id, agent_run_id)
        if run is None:
            raise RuntimeError("durable AgentRun is missing")
        if run.permission_snapshot_id is not None:
            snapshot = self.journal.get_permission_snapshot(
                self.workspace_id, run.permission_snapshot_id
            )
            if snapshot is None:
                raise RuntimeError("durable PermissionSnapshot is missing")
            return snapshot
        if turn_id is None or task_run_id is None:
            raise RuntimeError("permission snapshot subjects are incomplete")
        # The durable AgentRun snapshot remains the authority for crash-resumed runs.
        stamp = now or self.clock()
        candidates = tuple(
            grant
            for grant in self.journal.list_capability_grants(
                self.workspace_id, agent_run_id=agent_run_id
            )
            if grant.is_active(stamp)
        )
        if len(candidates) > 1:
            raise RuntimeError("AgentRun has conflicting active capability grants")
        snapshot = build_permission_snapshot(
            session,
            workspace_id=self.workspace_id,
            base_snapshot=run.snapshot,
            permission_snapshot_id=self.id_source.new_id("psnap"),
            task_run_id=task_run_id,
            turn_id=turn_id,
            agent_run_id=agent_run_id,
            grant=candidates[0] if candidates else None,
            created_at=stamp,
        )
        self.journal.freeze_agent_run_permission_snapshot(self.workspace_id, agent_run_id, snapshot)
        return snapshot

    def assert_execution_permission(
        self, execution: DurableToolExecution, *, now: datetime
    ) -> None:
        if execution.permission_snapshot_id is None:
            if execution.grant_id is not None or execution.isolation is not None:
                raise PermissionEvidenceError("execution permission evidence is incomplete")
            return
        snapshot = self.journal.get_permission_snapshot(
            self.workspace_id, execution.permission_snapshot_id
        )
        if snapshot is None:
            raise PermissionEvidenceError("permission snapshot is missing")
        if (
            snapshot.session_id != execution.session_id
            or snapshot.turn_id != execution.turn_id
            or snapshot.task_run_id != execution.task_run_id
            or snapshot.agent_run_id != execution.agent_run_id
        ):
            raise PermissionEvidenceError("execution permission snapshot subjects are mismatched")
        if snapshot.grant_id is None:
            if execution.grant_id is not None or execution.isolation is not None:
                raise PermissionEvidenceError("execution cannot add elevated evidence")
            return
        if execution.grant_id is None:
            if execution.isolation is not None:
                raise PermissionEvidenceError("execution added an incomplete elevated label")
            if (
                execution.tool_name == "run_command"
                and execution.intent.effect_class is EffectClass.UNCONFINED_EXTERNAL_EFFECT
                and execution.intent.requires_approval
            ):
                raise PermissionEvidenceError("elevated Host execution dropped grant evidence")
            return
        if execution.isolation is not snapshot.isolation_label:
            raise PermissionEvidenceError("execution elevated evidence is mismatched")
        grant = self.journal.get_capability_grant(self.workspace_id, execution.grant_id)
        if grant is None:
            raise PermissionEvidenceError("capability grant is missing")
        assert_grant_snapshot_matches(
            snapshot,
            grant,
            now=now,
            workspace_id=self.workspace_id,
            task_run_id=execution.task_run_id,
            agent_run_id=execution.agent_run_id,
        )

    def assert_handler_may_enter(
        self, execution: DurableToolExecution, *, now: datetime
    ) -> DurableToolExecution:
        current = self.journal.get_execution(self.workspace_id, execution.tool_execution_id)
        if current is None:
            raise PermissionEvidenceError("tool execution is missing")
        approval = (
            self.journal.get_approval_for_execution(self.workspace_id, current.tool_execution_id)
            if current.intent.requires_approval
            else None
        )
        snapshot = (
            self.journal.get_permission_snapshot(self.workspace_id, current.permission_snapshot_id)
            if current.permission_snapshot_id is not None
            else None
        )
        grant = (
            self.journal.get_capability_grant(self.workspace_id, current.grant_id)
            if current.grant_id is not None
            else None
        )
        assert_handler_may_enter(
            current,
            approval,
            now=now,
            permission_snapshot=snapshot,
            grant=grant,
        )
        return current

    def has_active_unconfined_grant(
        self, execution: DurableToolExecution, *, now: datetime
    ) -> bool:
        if execution.grant_id is None:
            return False
        grant = self.journal.get_capability_grant(self.workspace_id, execution.grant_id)
        return grant is not None and grant.is_active(now)
