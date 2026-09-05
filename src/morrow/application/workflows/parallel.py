"""Bounded frontier coordination, with no Provider retries or durable state owner.

Only Workflow output publication is ordered. Leaf request/response journals and
candidate Artifacts are persisted immediately and remain crash-recoverable.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import asdict, replace

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.capabilities import AccessScope, OperationKind
from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.models import ToolEffect
from morrow.runtime.tools import ToolExecutor, ToolRegistry

READ_KINDS = frozenset(
    {OperationKind.INTERNAL_READ, OperationKind.WORKSPACE_READ, OperationKind.GIT_READ}
)


def read_contract_error() -> ApplicationError:
    return ApplicationError(ApplicationErrorCode.INVALID, "read_contract_drift: read proof changed")


def read_tool_evidence(tool):
    """Unknown contracts cannot prove safety; known writes contradict a read declaration."""
    contract = tool.runtime_contract
    if contract is None:
        return None
    if (
        contract.intent_kind not in READ_KINDS
        or contract.intent_effect is not ToolEffect.NONE
        or contract.policy_effect is not ToolEffect.NONE
        or contract.requires_host is not False
        or contract.requires_sandbox is not False
        or tool.execution_policy.effect is not ToolEffect.NONE
    ):
        raise read_contract_error()
    if (
        tool.policy_resolver is not None
        or tool.intent_resolver is None
        or inspect.iscoroutinefunction(tool.intent_resolver)
    ):
        return None
    return asdict(contract)


def prove_read_runtime(prepared, session, *, source_proven: bool) -> str | None:
    executor = prepared.tool_executor
    if (
        not source_proven
        or session.workspace_capability is None
        or session.permission_profile.access_scope is not AccessScope.WORKSPACE
    ):
        return None
    if executor is None:
        tools = {}
    else:
        if executor.capability_policy is None:
            return None
        policy = executor.capability_policy
        if (
            policy.profile != session.permission_profile
            or policy.workspace.workspace_id != session.workspace_capability.workspace_id
            or policy.workspace.root != session.workspace_capability.root
        ):
            return None
        tools = executor.tool_set.tools
    contracts = {}
    for name, tool in sorted(tools.items()):
        evidence = read_tool_evidence(tool)
        if evidence is None:
            return None
        contracts[name] = evidence
    return sha256_digest(canonical_json_bytes(contracts))


def guard_read_executor(executor, hooks):
    """Recheck the actual per-call intent before any handler; drift is terminal to the node."""
    if executor is None:
        return None
    registry = ToolRegistry()
    for tool in executor.tool_set.tools.values():

        def resolve(arguments, context, *, original=tool):
            intent = original.intent_resolver(arguments, context)
            if inspect.isawaitable(intent):
                if inspect.iscoroutine(intent):
                    intent.close()
                hooks.read_contract_drift = True
                raise read_contract_error()
            try:
                ToolExecutor._validate_runtime_contract(original, intent)
            except Exception:
                hooks.read_contract_drift = True
                raise read_contract_error() from None
            return intent

        registry.register(replace(tool, intent_resolver=resolve))
    return ToolExecutor(
        registry.snapshot(),
        executor.run_policy,
        capability_policy=executor.capability_policy,
        approval_port=executor.approval_port,
        expected_process_isolation=executor.expected_process_isolation,
    )


class ReadFrontier:
    """All preparations finish before the first admission; unavailable proof serializes.

    Events coordinate scheduling only. Slot ownership and request accounting are
    durable journal facts, never these in-memory coordination objects.
    """

    def __init__(self, node_ids: tuple[str, ...]):
        self.node_ids = node_ids
        self.proofs: dict[str, str | None] = {}
        self.ready = asyncio.Event()
        self.finished = {node_id: asyncio.Event() for node_id in node_ids}
        self.started: set[str] = set()
        self.admitted = asyncio.Event()
        self.crashed = False
        self.controlled_cancel = False

    def closes_on_cancel(self, user_cancel):
        """Sibling failure closes leaves; actual driver loss retains recovery facts."""
        return not self.crashed and (user_cancel or self.controlled_cancel)

    @property
    def parallel(self):
        return self.ready.is_set() and all(self.proofs.values())

    async def wait_started(self, node_id):
        if not self.parallel:
            return
        self.started.add(node_id)
        if len(self.started) == len(self.node_ids):
            self.admitted.set()
        await self.admitted.wait()

    async def prepare(self, node_id, prepared, session, hooks, *, source_proven):
        proof = prove_read_runtime(prepared, session, source_proven=source_proven)
        self.proofs[node_id] = proof
        if len(self.proofs) == len(self.node_ids):
            self.ready.set()
        await self.ready.wait()
        if all(self.proofs.values()):
            hooks.parallel_read_digest = proof
            if session.workspace_capability is not None:
                session.workspace_capability = session.workspace_capability.model_copy(
                    update={"read_only": True}
                )
            return replace(
                prepared, tool_executor=guard_read_executor(prepared.tool_executor, hooks)
            )
        index = self.node_ids.index(node_id)
        if index:
            await self.finished[self.node_ids[index - 1]].wait()
        return prepared
