"""Application-only commands for run-bound capability grants."""

from __future__ import annotations

from datetime import UTC, datetime

from morrow.core.application import ApplicationErrorCode
from morrow.core.journal import CapabilityGrantJournalPort
from morrow.core.permissions import CapabilityGrant, CapabilityName, GrantSource


class CapabilityGrantError(ValueError):
    """A grant command violated the explicit Stage 4 authority boundary."""

    def __init__(
        self, message: str, *, code: ApplicationErrorCode = ApplicationErrorCode.INVALID
    ) -> None:
        super().__init__(message)
        self.code = code


class CapabilityGrantService:
    """Validate grant lifecycle rules before delegating to the journal.

    This service intentionally has no method accepting Provider output, tool
    arguments, project content, or a permission-profile mutation.  Its create
    path is called only by the local Application API command adapter.
    """

    def __init__(self, journal: CapabilityGrantJournalPort, *, workspace_id: str) -> None:
        self.journal = journal
        self.workspace_id = workspace_id

    def create(self, grant: CapabilityGrant, *, now: datetime) -> CapabilityGrant:
        if grant.workspace_id != self.workspace_id:
            raise CapabilityGrantError(
                "capability grant is outside the workspace",
                code=ApplicationErrorCode.CROSS_WORKSPACE,
            )
        if grant.granted_by is not GrantSource.LOCAL_INTERFACE_COMMAND:
            raise CapabilityGrantError("capability grant requires a local interface command")
        if not grant.is_active(now):
            raise CapabilityGrantError("capability grant must be active at creation")
        run = self.journal.get_agent_run(self.workspace_id, grant.agent_run_id)
        if run is None:
            raise CapabilityGrantError(
                "capability grant AgentRun is missing", code=ApplicationErrorCode.NOT_FOUND
            )
        if run.permission_snapshot_id is not None:
            raise CapabilityGrantError(
                "capability grant cannot be added after the AgentRun permission snapshot is frozen",
                code=ApplicationErrorCode.CONFLICT,
            )
        active = tuple(
            value
            for value in self.journal.list_capability_grants(
                self.workspace_id, agent_run_id=grant.agent_run_id
            )
            if value.is_active(now)
        )
        if active:
            raise CapabilityGrantError(
                "AgentRun already has an active capability grant",
                code=ApplicationErrorCode.CONFLICT,
            )
        return self.journal.put_capability_grant(self.workspace_id, grant)

    def get(self, grant_id: str) -> CapabilityGrant | None:
        return self.journal.get_capability_grant(self.workspace_id, grant_id)

    def list(self, *, agent_run_id: str | None = None) -> tuple[CapabilityGrant, ...]:
        return self.journal.list_capability_grants(self.workspace_id, agent_run_id=agent_run_id)

    def revoke(
        self,
        grant: CapabilityGrant,
        *,
        reason: str,
        now: datetime,
        expected_row_version: int,
    ) -> CapabilityGrant:
        if grant.workspace_id != self.workspace_id:
            raise CapabilityGrantError(
                "capability grant is outside the workspace",
                code=ApplicationErrorCode.CROSS_WORKSPACE,
            )
        if grant.row_version != expected_row_version:
            raise CapabilityGrantError(
                "capability grant row version is stale", code=ApplicationErrorCode.STALE
            )
        if grant.revoked_at is not None:
            return grant
        revoked = grant.model_copy(
            update={
                "revoked_at": _utc(now),
                "revocation_reason": reason,
                "row_version": grant.row_version + 1,
            }
        )
        revoked = CapabilityGrant.model_validate(revoked.model_dump(), strict=True)
        return self.journal.save_capability_grant(
            self.workspace_id,
            revoked,
            expected_row_version=expected_row_version,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_capability_subset(
    capabilities: tuple[CapabilityName, ...],
) -> tuple[CapabilityName, ...]:
    """Keep the UI command's requested subset explicit and deterministic."""

    if capabilities != (CapabilityName.UNCONFINED_HOST_PROCESS,):
        raise CapabilityGrantError("Stage 4 only grants unconfined_host_process")
    return capabilities
