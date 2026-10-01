"""Compose frozen-grant desktop tools without activating a native session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from morrow.application.computer_tools import make_computer_action_tool, make_computer_observe_tool
from morrow.application.computer_use import ComputerUseObservationService
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.computer_use import ComputerUseContractError, ComputerUseOperation
from morrow.core.permissions import (
    CapabilityName,
    assert_grant_snapshot_matches,
    workspace_root_digest,
)
from morrow.runtime.tools import RegisteredTool, ToolExecutor, ToolRegistry


@dataclass(frozen=True, slots=True, repr=False)
class PreparedComputerUseRun:
    observations: ComputerUseObservationService
    tools: tuple[RegisteredTool, ...]

    def __repr__(self) -> str:
        return "PreparedComputerUseRun()"

    def close(self) -> None:
        self.observations.stop_admission()

    async def aclose(self) -> None:
        await self.observations.close()

    def extend(self, executor: ToolExecutor | None) -> ToolExecutor:
        if executor is None:
            raise ComputerUseContractError("function_tools_required")
        # Audit the new tools independently; ordinary tools retain their
        # existing schema contracts and exact execution/approval policy.
        desktop = ToolRegistry()
        for tool in self.tools:
            desktop.register(tool)
        desktop.snapshot(
            require_runtime_contract=True,
            require_closed_schema=True,
            require_production_declaration=True,
        )
        registry = ToolRegistry()
        for tool in (*executor.tool_set.tools.values(), *self.tools):
            registry.register(tool)
        return ToolExecutor(
            registry.snapshot(
                require_runtime_contract=True,
                require_production_declaration=True,
                expected_process_isolation=executor.expected_process_isolation,
            ),
            executor.run_policy,
            approval_port=executor.approval_port,
            capability_policy=executor.capability_policy,
            expected_process_isolation=executor.expected_process_isolation,
        )


class ComputerUseRunFactory:
    """A grant is read, never inferred from a tool request or created here."""

    def __init__(
        self, *, lifecycle, journal, visuals, settings, clock, workspace_id, workspace_root
    ):
        self.lifecycle, self.journal, self.visuals = lifecycle, journal, visuals
        self.settings, self.clock = settings, clock
        self.workspace_id = workspace_id
        self.root_digest = workspace_root_digest(Path(workspace_root))

    def __call__(self, agent_run_id: str, _policy) -> PreparedComputerUseRun | None:
        if not self.settings.enabled:
            return None
        snapshot = self.journal.get_permission_snapshot_for_run(self.workspace_id, agent_run_id)
        if (
            snapshot is None
            or CapabilityName.COMPUTER_USE_HOST not in snapshot.granted_capabilities
        ):
            return None
        scope = snapshot.computer_use_scope
        run = self.journal.get_agent_run(self.workspace_id, agent_run_id)
        grant = self.journal.get_capability_grant(self.workspace_id, snapshot.grant_id)
        if (
            scope is None
            or run is None
            or grant is None
            or scope.workspace_id != self.workspace_id
            or scope.agent_run_id != agent_run_id
            or scope.task_run_id != snapshot.task_run_id
            or run.permission_snapshot_id != snapshot.permission_snapshot_id
            or run.session_id != snapshot.session_id
            or run.turn_id != snapshot.turn_id
            or snapshot.workspace_read_only
            or snapshot.workspace_root_digest != self.root_digest
            or snapshot.access_scope is not AccessScope.FULL_ACCESS
            or snapshot.approval_mode is not ApprovalMode.MANUAL
            or snapshot.process_isolation is not ProcessIsolation.HOST
            or ComputerUseOperation.OBSERVE not in scope.operations
        ):
            raise ComputerUseContractError("execution_not_authorized")
        try:
            assert_grant_snapshot_matches(
                snapshot,
                grant,
                now=self.clock.now(),
                workspace_id=self.workspace_id,
                task_run_id=scope.task_run_id,
                agent_run_id=agent_run_id,
            )
        except ValueError:
            raise ComputerUseContractError("grant_inactive") from None
        observations = ComputerUseObservationService(
            self.lifecycle,
            self.journal,
            scope,
            self.settings,
            self.clock,
        )
        tools = (make_computer_observe_tool(observations, self.visuals),)
        if ComputerUseOperation.ACTION in scope.operations:
            tools += (make_computer_action_tool(observations, self.visuals),)
        return PreparedComputerUseRun(observations, tools)
