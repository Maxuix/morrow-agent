"""Compose frozen-grant desktop tools without activating a native session."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from morrow.application.computer_requests import (
    ComputerUseSelection,
    LocalComputerUseRequest,
    PendingComputerObservationService,
)
from morrow.application.computer_tools import make_computer_action_tool, make_computer_observe_tool
from morrow.application.computer_use import ComputerUseObservationService
from morrow.application.computer_visuals import ToolVisualHydrator
from morrow.core.capabilities import AccessScope, ApprovalMode, ProcessIsolation
from morrow.core.computer_use import (
    ComputerUseContractError,
    ComputerUseOperation,
    reject_untrusted_computer_use_authority,
)
from morrow.core.permissions import (
    COMPUTER_USE_HOST_WARNING_DIGEST,
    CapabilityName,
    assert_grant_snapshot_matches,
    workspace_root_digest,
)
from morrow.runtime.tools import RegisteredTool, ToolExecutor, ToolRegistry


@dataclass(frozen=True, slots=True, repr=False)
class PreparedComputerUseRun:
    observations: ComputerUseObservationService | PendingComputerObservationService
    tools: tuple[RegisteredTool, ...]
    visual_hydrator_factory: Callable | None = None
    activate_local: Callable | None = None

    def __repr__(self) -> str:
        return "PreparedComputerUseRun()"

    def close(self) -> None:
        self.observations.stop_admission()

    async def aclose(self) -> None:
        await self.observations.close()

    def bind_context(self, context, capabilities):
        if capabilities.tool_protocol != "openai_function":
            raise ComputerUseContractError("function_tools_required")
        context = copy(context)
        if self.visual_hydrator_factory is not None:
            context.tool_visual_hydrator = self.visual_hydrator_factory(capabilities)
        return context

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
    """Compose existing evidence or stage an explicit local selection for the grant API."""

    def __init__(
        self, *, lifecycle, journal, visuals, settings, clock, workspace_id, workspace_root
    ):
        self.lifecycle, self.journal, self.visuals = lifecycle, journal, visuals
        self.settings, self.clock = settings, clock
        self.workspace_id = workspace_id
        self.root_digest = workspace_root_digest(Path(workspace_root))
        self.grant_creator = None
        self._requests = {}

    def select(self, selection, session, *, authority):
        reject_untrusted_computer_use_authority(authority)
        if not isinstance(selection, ComputerUseSelection):
            raise ComputerUseContractError("execution_not_authorized")
        selection = ComputerUseSelection.model_validate(selection.model_dump(), strict=True)
        self._assert_local_session(session)
        token = object()
        request = LocalComputerUseRequest(selection, session, token)
        self._requests[token] = request
        return request

    def _assert_local_session(self, session):
        profile = session.permission_profile
        capability = session.workspace_capability
        if (
            not self.settings.enabled
            or session.read_only
            or capability is None
            or capability.read_only
            or workspace_root_digest(capability.root) != self.root_digest
            or profile.access_scope is not AccessScope.FULL_ACCESS
            or profile.approval_mode is not ApprovalMode.MANUAL
            or profile.process_isolation is not ProcessIsolation.HOST
            or session.pending_full_access_grant
        ):
            raise ComputerUseContractError("execution_not_authorized")

    def prepare_selected(self, agent_run_id, _policy, request):
        if (
            not isinstance(request, LocalComputerUseRequest)
            or self._requests.get(request.issuer) is not request
            or self.journal.get_agent_run(self.workspace_id, agent_run_id) is not None
            or self.grant_creator is None
        ):
            raise ComputerUseContractError("execution_not_authorized")
        self._assert_local_session(request.session)
        del self._requests[request.issuer]
        pending = PendingComputerObservationService()
        tools = (make_computer_observe_tool(pending, self.visuals),)
        if ComputerUseOperation.ACTION in request.selection.operations:
            tools += (make_computer_action_tool(pending, self.visuals),)

        def activate(session):
            pending.assert_unbound()
            self._assert_local_session(session)
            runtime = session.durable_runtime
            run = self.journal.get_agent_run(self.workspace_id, agent_run_id)
            turn = None if run is None else self.journal.get_turn(self.workspace_id, run.turn_id)
            if (
                session is not request.session
                or runtime is None
                or runtime.current_agent_run_id != agent_run_id
                or run is None
                or run.session_id != session.session_id
                or run.permission_snapshot_id is not None
                or turn is None
            ):
                raise ComputerUseContractError("execution_not_authorized")
            generation = 1 + max(
                (
                    grant.computer_use_scope.generation
                    for grant in self.journal.list_capability_grants(self.workspace_id)
                    if grant.computer_use_scope is not None
                ),
                default=0,
            )
            scope = request.selection.bind(
                workspace_id=self.workspace_id,
                task_run_id=turn.task_run_id,
                agent_run_id=agent_run_id,
                generation=generation,
            )
            # Only the trusted local Application API writes the grant. The
            # ordinary loop freezes its snapshot before any model request.
            self.grant_creator(
                task_run_id=turn.task_run_id,
                agent_run_id=agent_run_id,
                capabilities=(CapabilityName.COMPUTER_USE_HOST,),
                reason="local interface approved the selected controlled-window run",
                preview_digest=COMPUTER_USE_HOST_WARNING_DIGEST,
                computer_use_enabled=True,
                computer_use_scope=scope,
            )
            pending.bind(
                ComputerUseObservationService(
                    self.lifecycle, self.journal, scope, self.settings, self.clock
                )
            )

        def hydrator(capabilities):
            return ToolVisualHydrator(
                self.visuals,
                session_id=request.session.session_id,
                agent_run_id=agent_run_id,
                settings=self.settings,
                input_types=capabilities.input_types,
                tool_protocol=capabilities.tool_protocol,
            )

        return PreparedComputerUseRun(pending, tools, hydrator, activate)

    def rehydrate(self, agent_run_id, policy):
        binding = self(agent_run_id, policy)
        if binding is not None:
            binding.close()
            raise ComputerUseContractError("computer_use_rebind_required")
        return None

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

        def hydrator(capabilities):
            return ToolVisualHydrator(
                self.visuals,
                session_id=snapshot.session_id,
                agent_run_id=agent_run_id,
                settings=self.settings,
                input_types=capabilities.input_types,
                tool_protocol=capabilities.tool_protocol,
            )

        return PreparedComputerUseRun(observations, tools, hydrator)
