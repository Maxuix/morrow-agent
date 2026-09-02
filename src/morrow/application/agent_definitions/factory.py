"""Definition-bound preparation for a caller-owned, isolated Session/Task pair."""

from __future__ import annotations

from dataclasses import replace

from morrow.application.agent_definitions.errors import (
    AgentDefinitionAdmissionError,
    DefinitionFailure,
)
from morrow.application.agent_definitions.publication import resolve_definition_tools
from morrow.application.prompt import DirectCodingPromptAssembler
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.capabilities import OperationIntent, OperationKind, PolicyVerdict
from morrow.core.domain import TaskRunPurpose, TaskRunStatus, session_can_start_work
from morrow.core.models import ModelRef, ToolEffect
from morrow.core.workflows.contracts import MECHANISM_TOOL_NAMES
from morrow.runtime.tools import ToolExecutor, ToolRegistry


class AgentFactory:
    """Implements the same preparation seam used by SessionOrchestrator.

    The caller creates an empty standalone Session with a matching current TaskRun.
    The factory never forks, restores, copies or writes a ConversationLog. Input is
    passed by the caller through run_task; Artifact links use the existing reader.
    """

    def __init__(
        self,
        preparation,
        publication,
        *,
        version_id,
        session,
        task_run_id,
        invoking_session_id,
        conversation_scope="isolated",
        resolved_tool_requirements=None,
    ):
        self.preparation = preparation
        self.publication = publication
        self.version_id = version_id
        self.session = session
        self.task_run_id = task_run_id
        self.invoking_session_id = invoking_session_id
        self.conversation_scope = conversation_scope
        self.resolved_tool_requirements = resolved_tool_requirements
        self.diagnostics = ()

    def _scope(self, *, fresh):
        ws = self.publication.workspace_id
        journal = self.publication.journal
        stored = journal.get_session(ws, self.session.session_id)
        task = journal.get_task_run(ws, self.task_run_id)
        common_invalid = (
            stored is None
            or task is None
            or task.session_id != stored.session_id
            or stored.current_task_run_id != task.task_run_id
            or not session_can_start_work(stored.lifecycle, stored.health)
            or task.status is not TaskRunStatus.OPEN
        )
        if common_invalid:
            failure = (
                DefinitionFailure.DIRECT_SCOPE
                if self.conversation_scope == "invoking_session"
                else DefinitionFailure.SCOPE
            )
            raise AgentDefinitionAdmissionError(failure)
        if self.conversation_scope == "invoking_session":
            if (
                stored.session_id != self.invoking_session_id
                or task.purpose is not TaskRunPurpose.USER
            ):
                raise AgentDefinitionAdmissionError(DefinitionFailure.DIRECT_SCOPE)
            return stored
        if stored.session_id == self.invoking_session_id or stored.parent_session_id is not None:
            raise AgentDefinitionAdmissionError(DefinitionFailure.SCOPE)
        if fresh and (stored.conversation_position != 0 or self.session.log.snapshot().records):
            raise AgentDefinitionAdmissionError(DefinitionFailure.NONEMPTY)
        return stored

    def _assembler(self, version):
        base = self.preparation.prompt_assembler
        assembler = DirectCodingPromptAssembler(
            profile=base.profile if base else None,
            resolver=base.resolver if base else None,
            role_prompt=version.source.role_prompt,
        )
        if self.session.durable_runtime is not None:
            self.session.durable_runtime.bind_prompt_assembler(assembler)
        return assembler

    def _tools(self, version, *, preflight_skills=False):
        try:
            validation = resolve_definition_tools(version.source, self.publication.catalog)
        except ValueError:
            raise AgentDefinitionAdmissionError(DefinitionFailure.TOOLS) from None
        selected = set(validation.tool_names)
        required = {
            item.name for item in version.source.tool_requirements if item.requirement == "required"
        }
        if self.resolved_tool_requirements is not None:
            allowed = {
                item.name
                for item in self.resolved_tool_requirements
                if item.requirement != "forbidden"
            }
            required = {
                item.name
                for item in self.resolved_tool_requirements
                if item.requirement == "required"
            }
            selected &= allowed
        diagnostics = list(validation.diagnostics)

        def restrict(executor):
            available = executor.tool_set.tools if executor else {}
            chosen = {}
            for name in sorted(selected):
                if name in MECHANISM_TOOL_NAMES:
                    if name in required:
                        raise AgentDefinitionAdmissionError(DefinitionFailure.TOOLS)
                    diagnostics.append(f"optional_removed:{name}")
                    continue
                tool = available.get(name)
                safe = tool is not None
                if safe and version.source.access_mode_ceiling == "read":
                    contract = tool.runtime_contract
                    safe = (
                        contract is not None
                        and contract.intent_effect is ToolEffect.NONE
                        and contract.intent_kind
                        in {
                            OperationKind.INTERNAL_READ,
                            OperationKind.WORKSPACE_READ,
                            OperationKind.GIT_READ,
                        }
                        and not contract.requires_host
                        and not contract.requires_sandbox
                    )
                if (
                    safe
                    and executor.capability_policy is not None
                    and tool.runtime_contract is not None
                    and tool.policy_resolver is None
                ):
                    contract = tool.runtime_contract
                    decision = executor.capability_policy.evaluate(
                        OperationIntent(
                            kind=contract.intent_kind,
                            effect=contract.intent_effect,
                            requires_host=bool(contract.requires_host),
                            requires_sandbox=bool(contract.requires_sandbox),
                        )
                    )
                    safe = decision.verdict is not PolicyVerdict.DENY
                if not safe:
                    if name in required:
                        raise AgentDefinitionAdmissionError(DefinitionFailure.TOOLS)
                    diagnostics.append(f"optional_removed:{name}")
                    continue
                chosen[name] = tool
            if self.resolved_tool_requirements is not None and not required <= set(chosen):
                raise AgentDefinitionAdmissionError(DefinitionFailure.TOOLS)
            self.diagnostics = tuple(sorted(set(diagnostics)))
            if preflight_skills and version.source.skill_version_ids:
                if self.session.durable_runtime is None:
                    raise AgentDefinitionAdmissionError(DefinitionFailure.SKILLS)
                self.session.durable_runtime.preflight_definition_skills(
                    version.source.skill_version_ids, available_tools=tuple(chosen)
                )
            if executor is None:
                return None
            registry = ToolRegistry()
            for tool in chosen.values():
                registry.register(tool)
            return ToolExecutor(
                registry.snapshot(),
                executor.run_policy,
                approval_port=executor.approval_port,
                capability_policy=executor.capability_policy,
                expected_process_isolation=executor.expected_process_isolation,
            )

        return restrict

    def prepare_new(
        self,
        *,
        agent_run_id=None,
        model: ModelRef | None = None,
        max_agent_generation_requests=None,
        require_enabled: bool = True,
    ):
        """Prepare one new AgentRun for this factory's caller-owned leaf.

        ``require_enabled=False`` is the Workflow path: an admitted Run checks
        only one-way revocation, never the mutable head. ``model`` pins the
        Compiler-frozen ``resolved_model_ref``; without it the Definition's own
        selector applies (the standalone admission-time ``invoking_active``
        resolution). ``max_agent_generation_requests`` overrides the Definition
        ceiling with the Workflow's frozen effective node cap.
        """

        self._scope(fresh=True)
        if require_enabled:
            version = self.publication.admit(self.version_id)
        else:
            version = self.publication.require_unrevoked(
                self.publication.journal.agent_definitions.get_version(
                    self.publication.workspace_id,
                    self.version_id,
                )
            )
        if model is None:
            model = (
                version.source.model_selection
                if isinstance(version.source.model_selection, ModelRef)
                else None
            )
        runtime = self.preparation.prepare_new(
            agent_run_id=agent_run_id,
            model=model,
            prompt_assembler=self._assembler(version),
            tool_transform=self._tools(version, preflight_skills=True),
        )
        spec = runtime.spec.model_copy(
            update={
                "definition_ref": AgentDefinitionRef(
                    definition_id=version.source.definition_id,
                    version_id=version.version_id,
                    content_hash=version.content_hash,
                ),
                "conversation_session_id": self.session.session_id,
                "max_agent_generation_requests": (
                    max_agent_generation_requests
                    if max_agent_generation_requests is not None
                    else version.source.max_agent_generation_requests
                ),
            }
        )
        return replace(runtime, spec=spec)

    def rehydrate(self, snapshot, *, agent_run_id=None):
        self._scope(fresh=False)
        version = self.publication.require_unrevoked(
            self.publication.journal.agent_definitions.get_version(
                self.publication.workspace_id,
                self.version_id,
            )
        )
        ref = snapshot.definition_ref
        if (
            ref is None
            or ref.version_id != version.version_id
            or ref.definition_id != version.source.definition_id
            or ref.content_hash != version.content_hash
            or snapshot.conversation_session_id != self.session.session_id
        ):
            raise AgentDefinitionAdmissionError(DefinitionFailure.EVIDENCE)
        # Ordinary disable must not block recovery of an admitted run.
        return self.preparation.rehydrate(
            snapshot,
            agent_run_id=agent_run_id,
            prompt_assembler=self._assembler(version),
            tool_transform=self._tools(version),
        )
