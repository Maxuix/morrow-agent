"""Definition-bound preparation for a caller-owned, isolated Session/Task pair."""

from __future__ import annotations

from dataclasses import replace

from morrow.application.agent_definitions.publication import resolve_definition_tools
from morrow.application.agent_runs.preparation import AgentRunPreparationError
from morrow.application.prompt import DirectCodingPromptAssembler
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.capabilities import OperationIntent, OperationKind, PolicyVerdict
from morrow.core.domain import TaskRunStatus, session_can_start_work
from morrow.core.models import ModelRef, ToolEffect
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
    ):
        self.preparation = preparation
        self.publication = publication
        self.version_id = version_id
        self.session = session
        self.task_run_id = task_run_id
        self.invoking_session_id = invoking_session_id
        self.diagnostics = ()

    def _scope(self, *, fresh):
        ws = self.publication.workspace_id
        journal = self.publication.journal
        stored = journal.get_session(ws, self.session.session_id)
        task = journal.get_task_run(ws, self.task_run_id)
        if (
            stored is None
            or task is None
            or task.session_id != stored.session_id
            or stored.current_task_run_id != task.task_run_id
            or stored.session_id == self.invoking_session_id
            or stored.parent_session_id is not None
            or not session_can_start_work(stored.lifecycle, stored.health)
            or task.status is not TaskRunStatus.OPEN
        ):
            raise AgentRunPreparationError(
                "isolated scope requires a distinct standalone Session/Task pair"
            )
        if fresh and (stored.conversation_position != 0 or self.session.log.snapshot().records):
            raise AgentRunPreparationError("isolated admission requires an empty ConversationLog")
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

    def _tools(self, version):
        validation = resolve_definition_tools(version.source, self.publication.catalog)
        selected = set(validation.tool_names)
        required = {
            item.name for item in version.source.tool_requirements if item.requirement == "required"
        }
        diagnostics = list(validation.diagnostics)

        def restrict(executor):
            available = executor.tool_set.tools if executor else {}
            chosen = {}
            for name in sorted(selected):
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
                        raise AgentRunPreparationError(
                            "required tool backend is unavailable or denied by the capability ceiling"
                        )
                    diagnostics.append(f"optional_removed:{name}")
                    continue
                chosen[name] = tool
            self.diagnostics = tuple(sorted(set(diagnostics)))
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

    def prepare_new(self, *, agent_run_id=None):
        self._scope(fresh=True)
        version = self.publication.admit(self.version_id)
        model = (
            version.source.model_selection
            if isinstance(version.source.model_selection, ModelRef)
            else None
        )
        runtime = self.preparation.prepare_new(
            agent_run_id=agent_run_id,
            model=model,
            prompt_assembler=self._assembler(version),
            tool_transform=self._tools(version),
        )
        spec = runtime.spec.model_copy(
            update={
                "definition_ref": AgentDefinitionRef(
                    definition_id=version.source.definition_id,
                    version_id=version.version_id,
                    content_hash=version.content_hash,
                ),
                "conversation_session_id": self.session.session_id,
                "max_agent_generation_requests": version.source.max_agent_generation_requests,
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
            raise AgentRunPreparationError("Agent definition frozen evidence mismatch")
        # No enabled-head check: recovery belongs to the already admitted run.
        return self.preparation.rehydrate(
            snapshot,
            agent_run_id=agent_run_id,
            prompt_assembler=self._assembler(version),
            tool_transform=self._tools(version),
        )
