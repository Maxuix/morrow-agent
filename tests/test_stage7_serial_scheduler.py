"""Stage 7 Subplan 5: deterministic serial DAG Scheduler end to end.

Multi-node all-isolated graphs run on the same Scheduler / transition /
committer / finalizer path as the one-node slice: topological order derived
from the frozen Revision, per-node completion, aggregate request/deadline
budget, foreground cancellation, recovery and recovery-only abandon. All
Providers are scripted; no Live network access and no wall-clock sleeps.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.agent_runs.preparation import AgentRunPreparationService
from morrow.application.artifacts import ArtifactService
from morrow.application.prompt import DirectCodingPromptAssembler
from morrow.application.tasks import TaskService
from morrow.application.workflows.composition import build_workflow_runtime
from morrow.application.workflows.publication import WorkflowCompilationService
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.bootstrap import build_application
from morrow.core.agent_definitions import AgentDefinitionSource, ToolRequirement
from morrow.core.agent_runs import AgentDefinitionRef, ProviderCapabilities
from morrow.core.application import ApplicationError
from morrow.core.domain import (
    DurableSession,
    DurableTaskRun,
    TaskOutcomeEvidenceKind,
    TaskOutcomeTrigger,
    TaskRunStatus,
)
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import (
    AssistantMessage,
    CredentialRef,
    FunctionToolCall,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
)
from morrow.core.recovery import RecoveryResolution
from morrow.core.workflows.contracts import (
    ContractRef,
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    TaskContractRef,
    WorkflowInputBinding,
    node_output_artifact_id,
    workflow_input_artifact_id,
)
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
    WorkflowEdge,
)
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.policy import load_runtime_policy
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.testing import FixedIdSource, ScriptedModelProvider

WS = "ws_one"
MODEL = ModelRef(provider_id="fake-provider", model_id="m1")
OTHER = ModelRef(provider_id="fake-provider", model_id="m2")
CATALOG = DefinitionCatalog(
    models=(MODEL, OTHER),
    skill_version_ids=frozenset(),
    tool_access={"read": "read", "write": "write"},
    allowed_tools=frozenset({"read", "write"}),
)
AGENT_POLICY = load_runtime_policy().agent_run
BUDGET = WorkflowBudget(
    max_agent_generation_requests=10,
    default_node_max_agent_generation_requests=3,
    admission_timeout_seconds=300,
    max_concurrency=1,
)
CONTRACT = TaskContract(objective="Audit password validation and authorization tests")


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class ScriptBank:
    """One scripted Provider per preparation, in construction order."""

    def __init__(self, scripts=(), on_create=None) -> None:
        self.scripts = list(scripts)
        self.providers: list[ScriptedModelProvider] = []
        self.on_create = on_create

    def __call__(self, config, credential) -> ScriptedModelProvider:
        script = self.scripts.pop(0) if self.scripts else [["done"]]
        provider = ScriptedModelProvider(script)
        self.providers.append(provider)
        if self.on_create is not None:
            self.on_create(len(self.providers))
        return provider


class ReadArgs(BaseModel):
    path: str


class WriteArgs(BaseModel):
    path: str


async def _read_handler(arguments) -> str:
    return "safe contents"


async def _write_handler(arguments) -> str:
    return "written"


class DagFixture:
    def __init__(self, tmp_path, scripts=(), bank=None) -> None:
        self.clock = MutableClock()
        self.store = OperationalStore(tmp_path / "state", clock=self.clock)
        self.handle = self.store.initialize()
        self.journal = SqliteOperationalJournal(self.handle, clock=self.clock.now)
        self.journal.create_session(
            DurableSession(session_id="ses_root", workspace_id=WS),
            task=DurableTaskRun(task_run_id="task_root", session_id="ses_root", workspace_id=WS),
        )
        self.app = build_application(
            state_root=tmp_path / "app", credentials=MemoryCredentialStore()
        )
        self.bank = bank if bank is not None else ScriptBank(scripts)
        self.app.registry.register(
            "fake-adapter",
            self.bank,
            capabilities=ProviderCapabilities(
                tool_protocol="openai_function", multiple_tool_calls=True
            ),
        )
        credential_ref = CredentialRef(ref="provider:fake-provider:test", version=3)
        self.app.credentials.set(credential_ref.ref, "topsecret-value")
        config = self.app.global_store.load()
        self.app.global_store.update(
            lambda value: value.model_copy(
                update={
                    "providers": {
                        "fake-provider": ProviderConfig(
                            adapter="fake-adapter",
                            base_url="https://api.example.test/v1",
                            credential_ref=credential_ref,
                            models={
                                "m1": ProviderModelConfig(api_model_id="api-m1"),
                                "m2": ProviderModelConfig(api_model_id="api-m2"),
                            },
                        )
                    },
                    "active_model": MODEL,
                }
            ),
            expected_revision=config.revision,
        )
        registry = ToolRegistry()
        registry.register(
            make_tool(
                name="read",
                description="Read",
                arguments_model=ReadArgs,
                handler=_read_handler,
            )
        )
        registry.register(
            make_tool(
                name="write",
                description="Write",
                arguments_model=WriteArgs,
                handler=_write_handler,
            )
        )
        self.tool_registry = registry
        self.preparation = AgentRunPreparationService(
            global_store=self.app.global_store,
            registry=self.app.registry,
            agent_policy=AGENT_POLICY,
            credential_resolver=self.app.provider_service.credential_resolver,
            estimate_request_chars=estimate_request_chars,
            tool_factory=lambda policy: ToolExecutor(registry.snapshot(), policy),
        )
        self.preparation.prompt_assembler = DirectCodingPromptAssembler()
        self.ids = FixedIdSource()
        self.artifacts = ArtifactService(
            journal=self.journal,
            filesystem=FilesystemArtifactStore(self.store.layout),
            workspace_id=WS,
            id_source=self.ids,
            clock=self.clock.now,
        )
        self.agents = AgentDefinitionPublicationService(
            self.journal, workspace_id=WS, catalog=CATALOG, id_source=self.ids
        )
        self.compiler = WorkflowCompilationService(
            self.journal, workspace_id=WS, catalog=CATALOG, id_source=self.ids
        )

        async def _no_sleep(_delay) -> None:
            return None

        self.runtime = build_workflow_runtime(
            self.journal,
            self.handle,
            workspace_id=WS,
            artifacts=self.artifacts,
            agent_publication=self.agents,
            preparation=self.preparation,
            id_source=self.ids,
            runtime_instance_id="inst-test",
            clock=self.clock.now,
            retry_sleep=_no_sleep,
        )
        self.tasks = TaskService(
            journal=self.journal,
            workspace_id=WS,
            id_source=self.ids,
            clock=self.clock.now,
        )

    def close(self) -> None:
        self.handle.close()


@pytest.fixture
def fx(tmp_path):
    fixture = DagFixture(tmp_path)
    yield fixture
    fixture.close()


def agent_source(**kwargs):
    return AgentDefinitionSource(
        **{
            "definition_id": "helper",
            "name": "Helper",
            "role_prompt": "Inspect password validation and authorization tests.",
            **kwargs,
        }
    )


def reader_agent(**kwargs):
    return agent_source(
        tool_requirements=(ToolRequirement(name="read", requirement="required"),), **kwargs
    )


def chain_node(ref, node_id, objective, *, bindings=(), access_mode="read", overrides=None):
    return AgentNodeSource(
        **{
            "node_id": node_id,
            "agent_definition_ref": ref,
            "task_contract": TaskContract(objective=objective),
            "input_bindings": bindings,
            "output_contracts": (OutputContract(slot="result"),),
            "access_mode": access_mode,
            **(overrides or {}),
        }
    )


def chain_source(ref, **changes):
    """Three-node chain gamma -> beta -> alpha: lexical order is NOT topological."""

    nodes = (
        chain_node(ref, "gamma", "Phase one survey work"),
        chain_node(
            ref,
            "beta",
            "Phase two refine work",
            bindings=(
                NodeOutputBinding(
                    source="node_output",
                    input_name="prior",
                    accepts=ContractRef(kind="TextResult"),
                    node_output=NodeOutputRef(node_id="gamma", output_slot="result"),
                ),
            ),
        ),
        chain_node(
            ref,
            "alpha",
            "Phase three conclude work",
            bindings=(
                NodeOutputBinding(
                    source="node_output",
                    input_name="prior",
                    accepts=ContractRef(kind="TextResult"),
                    node_output=NodeOutputRef(node_id="beta", output_slot="result"),
                ),
            ),
        ),
    )
    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Three phase pipeline",
            "default_budget": BUDGET,
            "nodes": nodes,
            "edges": (
                WorkflowEdge(from_node_id="gamma", to_node_id="beta"),
                WorkflowEdge(from_node_id="beta", to_node_id="alpha"),
            ),
            "required_outputs": (NodeOutputRef(node_id="alpha", output_slot="result"),),
            **changes,
        }
    )


def pair_source(ref, *, binding=True, budget=BUDGET, gamma_overrides=None, **changes):
    """Two-node chain gamma -> alpha; the binding is optional (control edge otherwise)."""

    alpha_bindings = ()
    if binding:
        alpha_bindings = (
            NodeOutputBinding(
                source="node_output",
                input_name="prior",
                accepts=ContractRef(kind="TextResult"),
                node_output=NodeOutputRef(node_id="gamma", output_slot="result"),
            ),
        )
    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Two phase pipeline",
            "default_budget": budget,
            "nodes": (
                chain_node(ref, "gamma", "Phase one survey work", overrides=gamma_overrides),
                chain_node(ref, "alpha", "Phase three conclude work", bindings=alpha_bindings),
            ),
            "edges": (WorkflowEdge(from_node_id="gamma", to_node_id="alpha"),),
            "required_outputs": (NodeOutputRef(node_id="alpha", output_slot="result"),),
            **changes,
        }
    )


def publish(fx, make_source, *, agent=None, command="cmd_publish", active=MODEL):
    version = fx.agents.publish(
        agent or agent_source(),
        source_revision=0,
        expected_head_revision=0,
        command_id=f"{command}_agent",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        make_source(ref),
        source_revision=0,
        expected_head_revision=0,
        command_id=command,
        active_model=active,
    )
    return version, publication


def start(fx, revision, *, command_id="cmd_start", contract=CONTRACT, root_version=None):
    if root_version is None:
        root_version = fx.journal.get_task_run(WS, "task_root").row_version
    return fx.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id="pipeline",
            workflow_revision_id=revision.workflow_revision_id,
            session_id="ses_root",
            root_task_run_id="task_root",
            expected_root_row_version=root_version,
            contract=contract,
            command_id=command_id,
        )
    )


def root(fx):
    return fx.journal.get_task_run(WS, "task_root")


def outcomes(fx):
    return fx.journal.list_task_outcomes(WS, "task_root")


def node_by_id(fx, run_id, node_id):
    return next(
        node for node in fx.journal.workflows.list_nodes(WS, run_id) if node.node_id == node_id
    )


def call_text(provider, index=0) -> str:
    return "\n".join(
        str(getattr(message, "content", "") or "") for message in provider.stream_calls[index]
    )


async def wait_for(predicate) -> None:
    for _ in range(2000):
        await asyncio.sleep(0)
        if predicate():
            return
    raise AssertionError("condition was not reached")


def resolve_blocking(fx, node) -> None:
    """Drive the existing Recovery owner through acknowledge + resume."""

    log = restore_conversation_log(fx.journal, WS, node.conversation_session_id)
    report = fx.runtime.scheduler.recovery.discover(node.conversation_session_id, log)
    assert report is not None and any(item.blocking for item in report.items)
    writer = DurableConversationWriter(
        log,
        fx.journal,
        workspace_id=WS,
        session_id=node.conversation_session_id,
        id_source=fx.ids,
    )
    updated, receipt, planned = fx.runtime.scheduler.recovery.decide(
        report,
        command_id="cmd_ack",
        resolution=RecoveryResolution.ACKNOWLEDGE,
        item_id=report.items[0].item_id,
        log=log,
    )
    saved = fx.runtime.scheduler.recovery.commit_decision(
        updated, receipt, planned=planned, log=log, writer=writer, close_all=False
    )
    updated, receipt, planned = fx.runtime.scheduler.recovery.decide(
        saved,
        command_id="cmd_resume",
        resolution=RecoveryResolution.RESUME,
        item_id=None,
        log=log,
    )
    fx.runtime.scheduler.recovery.commit_decision(
        updated, receipt, planned=planned, log=log, writer=writer, close_all=False
    )


# Start: atomic precreation, replay and fault rollback -------------------------------


def test_start_precreates_one_queued_node_run_per_node_and_replays_atomically(fx):
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)

    nodes = fx.journal.workflows.list_nodes(WS, started.run.workflow_run_id)
    assert len(nodes) == 3
    assert {node.node_id for node in nodes} == {"gamma", "beta", "alpha"}
    assert all(node.status is WorkflowStatus.QUEUED for node in nodes)
    assert all(node.attempt == 1 for node in nodes)
    assert started.run.status is WorkflowStatus.QUEUED

    replayed = start(fx, publication.revision)
    assert replayed.replayed
    assert replayed.run.workflow_run_id == started.run.workflow_run_id
    assert len(fx.journal.workflows.list_nodes(WS, started.run.workflow_run_id)) == 3
    assert len(fx.runtime.queries.list_runs()) == 1


def test_start_transaction_fault_creates_neither_partial_run_nor_partial_nodes(fx):
    _, publication = publish(fx, chain_source)
    original = fx.journal.workflows.create_run

    def failing_create(run, nodes):
        raise ValueError("simulated Start fault")

    fx.journal.workflows.create_run = failing_create
    try:
        with pytest.raises(ValueError, match="simulated Start fault"):
            start(fx, publication.revision)
    finally:
        fx.journal.workflows.create_run = original
    assert fx.runtime.queries.list_runs() == ()
    assert fx.journal.get_application_command_receipt(WS, "cmd_start") is None
    # At most the unbound immutable TaskContract Artifact may remain.
    orphan = fx.artifacts.get(workflow_input_artifact_id("cmd_start"))
    assert orphan is not None


# Order, dependency binding and duplicate wake ----------------------------------------


@pytest.mark.asyncio
async def test_chain_runs_in_topological_order_with_typed_artifact_handoff(fx):
    fx.bank.scripts.extend(
        [
            [["gamma findings text"]],
            [["beta summary text"]],
            [["alpha verdict text"]],
        ]
    )

    def with_workflow_input(ref, **changes):
        source = chain_source(ref, **changes)
        gamma = next(node for node in source.nodes if node.node_id == "gamma")
        bound = gamma.model_copy(
            update={
                "input_bindings": (
                    WorkflowInputBinding(
                        source="workflow_input",
                        input_name="task",
                        accepts=TaskContractRef(),
                        workflow_input="task",
                    ),
                )
            }
        )
        nodes = tuple(bound if node.node_id == "gamma" else node for node in source.nodes)
        return source.model_copy(update={"nodes": nodes})

    _, publication = publish(fx, with_workflow_input)
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    # Lexical order would run alpha first; the frozen DAG forces gamma -> beta -> alpha.
    assert "Phase one survey work" in call_text(fx.bank.providers[0])
    assert CONTRACT.objective in call_text(fx.bank.providers[0])
    assert "Phase two refine work" in call_text(fx.bank.providers[1])
    assert "gamma findings text" in call_text(fx.bank.providers[1])
    assert "Phase three conclude work" in call_text(fx.bank.providers[2])
    assert "beta summary text" in call_text(fx.bank.providers[2])

    for node_id in ("gamma", "beta", "alpha"):
        assert node_by_id(fx, run.workflow_run_id, node_id).status is WorkflowStatus.COMPLETED
    # Input bindings are durable and scoped to the consuming node.
    bindings = fx.journal.workflows.list_bindings(WS, run.workflow_run_id)
    bound_inputs = {
        (nid, binding.name) for nid, direction, binding in bindings if direction == "input" and nid
    }
    assert (node_by_id(fx, run.workflow_run_id, "beta").node_run_id, "prior") in bound_inputs
    assert (node_by_id(fx, run.workflow_run_id, "alpha").node_run_id, "prior") in bound_inputs
    assert (node_by_id(fx, run.workflow_run_id, "gamma").node_run_id, "task") in bound_inputs

    # The root snapshot exports only alpha's result and carries both markers.
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    exported = node_output_artifact_id(alpha.node_run_id, "result")
    snapshots = [o for o in outcomes(fx) if o.trigger is TaskOutcomeTrigger.SNAPSHOT]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert {ref.artifact_id for ref in snapshot.artifact_refs} == {exported}
    assert "node_count=3" in snapshot.completion_basis
    assert "workflow_result=succeeded" in snapshot.completion_basis
    roles = {(ref.kind, ref.role) for ref in snapshot.evidence_refs}
    assert (TaskOutcomeEvidenceKind.WORKFLOW_RUN, "workflow_result_snapshot") in roles
    assert ("task_transition", "workflow_ready_transition") in roles
    # Atomic terminal visibility: run, root and snapshot always agree.
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    ready = next(ref for ref in snapshot.evidence_refs if ref.role == "workflow_ready_transition")
    transitions = fx.journal.list_task_transitions(WS, "task_root")
    latest_ready = [t for t in transitions if t.to_status is TaskRunStatus.READY_FOR_ACCEPTANCE]
    assert ready.reference_id == latest_ready[-1].transition_id

    # The accepted Outcome inherits the marked snapshot's goal and exported refs.
    accepted = fx.tasks.accept("task_root", command_id="cmd_accept").outcome
    assert accepted.goal_reference.reference_id == run.input_artifacts[0].artifact_id
    assert exported in {ref.artifact_id for ref in accepted.artifact_refs}

    # One purpose=agent admission per node; nothing else is counted.
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 3


@pytest.mark.asyncio
async def test_control_only_edge_orders_without_inventing_an_artifact(fx):
    fx.bank.scripts.extend([[["control first"]], [["control second"]]])
    _, publication = publish(fx, lambda ref: pair_source(ref, binding=False))
    assert any(d.code == "unconsumed_outputs" for d in publication.diagnostics)

    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert "Phase one survey work" in call_text(fx.bank.providers[0])
    assert "Phase three conclude work" in call_text(fx.bank.providers[1])
    # No binding exists, so no producer text leaks into the consumer.
    assert "control first" not in call_text(fx.bank.providers[1])
    bindings = fx.journal.workflows.list_bindings(WS, run.workflow_run_id)
    assert not [b for _, direction, b in bindings if direction == "input" and b.name == "prior"]


@pytest.mark.asyncio
async def test_duplicate_wake_never_reruns_or_duplicates_nodes(fx):
    fx.bank.scripts.extend([[["one"]], [["two"]], [["three"]]])
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    ids_before = {
        node.node_run_id for node in fx.journal.workflows.list_nodes(WS, run.workflow_run_id)
    }

    again = await fx.runtime.scheduler.run(run.workflow_run_id)
    assert again.status is WorkflowStatus.COMPLETED
    recovered = await fx.runtime.scheduler.recover(run.workflow_run_id)
    assert recovered.status is WorkflowStatus.COMPLETED
    nodes = fx.journal.workflows.list_nodes(WS, run.workflow_run_id)
    assert {node.node_run_id for node in nodes} == ids_before
    assert all(node.attempt == 1 for node in nodes)
    assert [len(provider.stream_calls) for provider in fx.bank.providers] == [1, 1, 1]
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 3


# Aggregate budget and deadline ---------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_remaining_budget_cancels_later_node_and_fails_run(fx):
    fx.bank.scripts.extend([[["only node one runs"]]])
    tight = WorkflowBudget(
        max_agent_generation_requests=1,
        default_node_max_agent_generation_requests=3,
        admission_timeout_seconds=300,
        max_concurrency=1,
    )
    _, publication = publish(fx, lambda ref: pair_source(ref, budget=tight))
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.FAILED
    gamma = node_by_id(fx, run.workflow_run_id, "gamma")
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert gamma.status is WorkflowStatus.COMPLETED
    assert alpha.status is WorkflowStatus.CANCELLED
    assert alpha.agent_run_id is None
    assert root(fx).status is TaskRunStatus.FAILED
    assert "workflow_terminal=budget_exhausted" in outcomes(fx)[-1].completion_basis
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 1
    assert len(fx.bank.providers) == 1


@pytest.mark.asyncio
async def test_positive_remainder_runs_later_node_under_shrunken_cap(fx):
    fx.bank.scripts.extend([[["one"]], [["two"]]])
    tight = WorkflowBudget(
        max_agent_generation_requests=2,
        default_node_max_agent_generation_requests=3,
        admission_timeout_seconds=300,
        max_concurrency=1,
    )
    _, publication = publish(fx, lambda ref: pair_source(ref, budget=tight))
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.COMPLETED
    gamma = node_by_id(fx, run.workflow_run_id, "gamma")
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert gamma.effective_node_generation_request_cap == 2
    assert alpha.effective_node_generation_request_cap == 1
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 2


@pytest.mark.asyncio
async def test_deadline_expiry_between_nodes_fails_at_next_admission(fx):
    fx.bank.scripts.extend([[["one"]]])
    tight = WorkflowBudget(
        max_agent_generation_requests=10,
        default_node_max_agent_generation_requests=3,
        admission_timeout_seconds=10,
        max_concurrency=1,
    )
    _, publication = publish(fx, lambda ref: pair_source(ref, budget=tight))
    started = start(fx, publication.revision)

    real_complete = fx.runtime.transitions.complete_node

    def complete_then_expire(node_run_id):
        result = real_complete(node_run_id)
        fx.clock.advance(20)
        return result

    fx.runtime.transitions.complete_node = complete_then_expire
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.transitions.complete_node = real_complete

    assert run.status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert alpha.status is WorkflowStatus.CANCELLED
    assert alpha.agent_run_id is None
    assert "workflow_terminal=deadline_exceeded" in outcomes(fx)[-1].completion_basis
    assert len(fx.bank.providers) == 1


@pytest.mark.asyncio
async def test_deadline_expiry_after_settled_tool_fails_the_next_request(fx):
    fx.bank.scripts.extend(
        [
            [["one"]],
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                    )
                ),
                ["never ", "reached"],
            ],
        ]
    )
    _, publication = publish(fx, pair_source, agent=reader_agent())
    started = start(fx, publication.revision)

    original_admit = fx.journal.admit_model_request
    admissions = 0

    def admit_and_expire(workspace_id, **kwargs):
        nonlocal admissions
        observation = original_admit(workspace_id, **kwargs)
        admissions += 1
        if admissions == 2:
            # gamma's request plus alpha's first request were admitted; alpha's
            # Tool settled safely, then the deadline passed.
            fx.clock.advance(400)
        return observation

    fx.journal.admit_model_request = admit_and_expire
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.journal.admit_model_request = original_admit

    assert run.status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run.workflow_run_id, "alpha").status is WorkflowStatus.FAILED
    assert "workflow_terminal=deadline_exceeded" in outcomes(fx)[-1].completion_basis


@pytest.mark.asyncio
async def test_compaction_and_non_agent_requests_never_charge_the_workflow_budget(fx):
    fx.bank.scripts.extend([[["one"]], [["two"]]])
    _, publication = publish(fx, pair_source)
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 2
    # Automatic compaction summaries never pass through the purpose=agent seam,
    # so they stay excluded instead of being misreported as counted.
    fx.journal.admit_model_request(
        WS,
        agent_run_id=node_by_id(fx, run.workflow_run_id, "gamma").agent_run_id,
        attempt_ordinal=99,
        estimated_request_chars=10,
        request_char_budget=1000,
        purpose="outcome_intent",
    )
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 2


# Failure propagation ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_failure_in_later_node_preserves_completed_work(fx):
    fx.bank.scripts.extend([[["one"]], [RuntimeError("boom")]])
    _, publication = publish(fx, chain_source)
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run.workflow_run_id, "beta").status is WorkflowStatus.FAILED
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert alpha.status is WorkflowStatus.CANCELLED
    assert alpha.agent_run_id is None
    assert root(fx).status is TaskRunStatus.FAILED
    outcome = outcomes(fx)[-1]
    assert outcome.trigger is TaskOutcomeTrigger.TERMINAL_CLOSE
    # Scripted Provider failures are retryable; the leaf exhausts its frozen
    # node request cap, which is the truthful recorded reason.
    assert "workflow_terminal=budget_exhausted" in outcome.completion_basis
    assert "workflow_result_snapshot" not in {ref.role for ref in outcome.evidence_refs}


@pytest.mark.asyncio
async def test_write_leaf_tool_evidence_survives_downstream_failure(fx):
    fx.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_w", name="write", arguments='{"path": "a.py"}'),
                    )
                ),
                ["write complete"],
            ],
            [RuntimeError("boom")],
        ]
    )
    writer = agent_source(
        definition_id="writer",
        name="Writer",
        access_mode_ceiling="write",
        tool_requirements=(ToolRequirement(name="write", requirement="required"),),
    )
    version = fx.agents.publish(
        writer, source_revision=0, expected_head_revision=0, command_id="cmd_writer_agent"
    )
    writer_ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    reader = fx.agents.publish(
        agent_source(), source_revision=0, expected_head_revision=0, command_id="cmd_reader_agent"
    )
    reader_ref = AgentDefinitionRef(
        definition_id=reader.source.definition_id,
        version_id=reader.version_id,
        content_hash=reader.content_hash,
    )
    source = WorkflowDefinitionSource(
        workflow_definition_id="pipeline",
        name="Write then fail",
        default_budget=BUDGET,
        nodes=(
            chain_node(writer_ref, "gamma", "Phase one write work", access_mode="write"),
            chain_node(reader_ref, "alpha", "Phase three conclude work"),
        ),
        edges=(WorkflowEdge(from_node_id="gamma", to_node_id="alpha"),),
        required_outputs=(NodeOutputRef(node_id="alpha", output_slot="result"),),
    )
    publication = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.FAILED
    gamma = node_by_id(fx, run.workflow_run_id, "gamma")
    assert gamma.status is WorkflowStatus.COMPLETED
    outcome = outcomes(fx)[-1]
    assert outcome.trigger is TaskOutcomeTrigger.TERMINAL_CLOSE
    # The root projection preserves the write leaf's durable Tool facts even
    # though a downstream node failed afterwards.
    kinds = {(ref.kind, ref.role) for ref in outcome.evidence_refs}
    assert (TaskOutcomeEvidenceKind.TOOL_EXECUTION, "tool_execution") in kinds
    assert (TaskOutcomeEvidenceKind.AGENT_RUN, "workflow_node_agent_run") in kinds
    assert any(entry.startswith("write:") for entry in outcome.side_effects)
    assert "tool_execution_count=1" in outcome.completion_basis


@pytest.mark.asyncio
async def test_unconsumed_warning_node_still_executes_and_can_fail_the_workflow(fx):
    fx.bank.scripts.extend([[RuntimeError("boom")]])
    _, publication = publish(fx, lambda ref: pair_source(ref, binding=False))
    warning = next(d for d in publication.diagnostics if d.code == "unconsumed_outputs")
    assert "gamma" in warning.message

    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
    # gamma's outputs feed nobody, yet it is execution-required: its failure
    # fails the whole Workflow under the fixed mapping.
    assert run.status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "alpha").status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.FAILED


@pytest.mark.asyncio
async def test_success_waits_for_every_declared_node_not_only_exporters(fx):
    fx.bank.scripts.extend([[["gamma ran"]], [["alpha ran"]]])
    # Only alpha exports; gamma is a control-ordered, unconsumed node.
    _, publication = publish(fx, lambda ref: pair_source(ref, binding=False))
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)

    assert run.status is WorkflowStatus.COMPLETED
    assert len(fx.bank.providers) == 2
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run.workflow_run_id, "alpha").status is WorkflowStatus.COMPLETED
    snapshots = [o for o in outcomes(fx) if o.trigger is TaskOutcomeTrigger.SNAPSHOT]
    assert len(snapshots) == 1
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    exported = node_output_artifact_id(alpha.node_run_id, "result")
    assert {ref.artifact_id for ref in snapshots[0].artifact_refs} == {exported}


# Cancellation ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_during_later_node_preserves_completed_nodes(fx):
    fx.bank.scripts.extend([[["one"]], ["cancel"], [["unused"]]])
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)
    task = asyncio.create_task(fx.runtime.scheduler.run(started.run.workflow_run_id))
    await wait_for(
        lambda: node_by_id(fx, started.run.workflow_run_id, "beta").status is WorkflowStatus.RUNNING
    )
    task.cancel()
    run = await task

    assert run.status is WorkflowStatus.CANCELLED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run.workflow_run_id, "beta").status is WorkflowStatus.CANCELLED
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert alpha.status is WorkflowStatus.CANCELLED
    assert alpha.agent_run_id is None
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=user_cancelled" in outcomes(fx)[-1].completion_basis


@pytest.mark.asyncio
async def test_cancel_between_nodes_stops_every_not_started_node(fx):
    fx.bank.scripts.extend([[["one"]], [["two"]], [["three"]]])
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)

    real_complete = fx.runtime.transitions.complete_node
    fired = False

    def complete_then_cancel(node_run_id):
        nonlocal fired
        result = real_complete(node_run_id)
        if not fired:
            fired = True
            asyncio.current_task().cancel()
        return result

    fx.runtime.transitions.complete_node = complete_then_cancel
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.transitions.complete_node = real_complete

    assert run.status is WorkflowStatus.CANCELLED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run.workflow_run_id, "beta").status is WorkflowStatus.CANCELLED
    assert node_by_id(fx, run.workflow_run_id, "alpha").status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_unknown_then_resolved_never_admits_queued_nodes(fx):
    fx.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                    )
                ),
                ["unused"],
            ],
            [["unused"]],
        ]
    )
    _, publication = publish(fx, pair_source, agent=reader_agent())
    started = start(fx, publication.revision)
    fx.runtime.scheduler.faults = OnceFaultInjector(
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
    )
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.scheduler.faults = None

    blocked = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert blocked.status is WorkflowStatus.BLOCKED
    fx.runtime.transitions.set_pending_user_cancel(started.run.workflow_run_id)

    gamma = node_by_id(fx, started.run.workflow_run_id, "gamma")
    resolve_blocking(fx, gamma)
    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)

    assert run.status is WorkflowStatus.CANCELLED
    assert run.pending_terminal_intent == "user_cancel"
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.CANCELLED
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert alpha.status is WorkflowStatus.CANCELLED
    assert alpha.agent_run_id is None
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=user_cancelled" in outcomes(fx)[-1].completion_basis
    assert len(fx.bank.providers) == 1


# Crash, recovery, revocation and disable ---------------------------------------------------


@pytest.mark.asyncio
async def test_crash_between_nodes_recovers_without_rerunning_completed_work(fx):
    fx.bank.scripts.extend([[["one"]], [["two"]], [["three"]]])
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)

    real_complete = fx.runtime.transitions.complete_node
    fired = False

    def complete_then_crash(node_run_id):
        nonlocal fired
        result = real_complete(node_run_id)
        if not fired:
            fired = True
            raise InjectedFault(FaultPoint.TURN_AFTER_TERMINAL_COMMIT)
        return result

    fx.runtime.transitions.complete_node = complete_then_crash
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.transitions.complete_node = real_complete

    gamma = node_by_id(fx, started.run.workflow_run_id, "gamma")
    assert gamma.status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, started.run.workflow_run_id, "beta").status is WorkflowStatus.QUEUED
    assert root(fx).status is TaskRunStatus.OPEN

    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert all(
        node_by_id(fx, run.workflow_run_id, node_id).status is WorkflowStatus.COMPLETED
        for node_id in ("gamma", "beta", "alpha")
    )
    # The completed node was never rerun; each provider saw exactly one call.
    assert [len(provider.stream_calls) for provider in fx.bank.providers] == [1, 1, 1]


@pytest.mark.asyncio
async def test_crash_unknown_tool_outcome_blocks_then_resumes_remaining_nodes(fx):
    fx.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                    )
                ),
            ],
            [["resumed answer"]],
            [["two"]],
        ]
    )
    _, publication = publish(fx, pair_source, agent=reader_agent())
    started = start(fx, publication.revision)
    fx.runtime.scheduler.faults = OnceFaultInjector(
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
    )
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.scheduler.faults = None

    blocked = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert blocked.status is WorkflowStatus.BLOCKED
    assert node_by_id(fx, started.run.workflow_run_id, "gamma").status is WorkflowStatus.BLOCKED
    assert node_by_id(fx, started.run.workflow_run_id, "alpha").status is WorkflowStatus.QUEUED
    assert root(fx).status is TaskRunStatus.OPEN
    with pytest.raises(ApplicationError, match="blocked"):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)

    gamma = node_by_id(fx, started.run.workflow_run_id, "gamma")
    resolve_blocking(fx, gamma)
    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)

    assert run.status is WorkflowStatus.COMPLETED
    assert all(
        node_by_id(fx, run.workflow_run_id, node_id).status is WorkflowStatus.COMPLETED
        for node_id in ("gamma", "alpha")
    )
    # The interrupted node resumed (two admitted requests) instead of restarting.
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 3


@pytest.mark.asyncio
async def test_revocation_before_later_admission_closes_policy_revoked(fx):
    fx.bank.scripts.extend([[["one"]], [["unused"]], [["unused"]]])
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)

    real_complete = fx.runtime.transitions.complete_node
    fired = False

    def complete_then_revoke(node_run_id):
        nonlocal fired
        result = real_complete(node_run_id)
        if not fired:
            fired = True
            fx.compiler.revoke(
                publication.revision.workflow_revision_id,
                reason="policy",
                command_id="cmd_revoke_mid",
            )
        return result

    fx.runtime.transitions.complete_node = complete_then_revoke
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.transitions.complete_node = real_complete

    assert run.status is WorkflowStatus.CANCELLED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    for node_id in ("beta", "alpha"):
        node = node_by_id(fx, run.workflow_run_id, node_id)
        assert node.status is WorkflowStatus.CANCELLED
        assert node.agent_run_id is None
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=policy_revoked" in outcomes(fx)[-1].completion_basis
    assert len(fx.bank.providers) == 1


@pytest.mark.asyncio
async def test_ordinary_disable_mid_run_never_reaches_the_admitted_run(fx):
    fx.bank.scripts.extend([[["one"]], [["two"]], [["three"]]])
    _, publication = publish(fx, chain_source)
    started = start(fx, publication.revision)

    real_complete = fx.runtime.transitions.complete_node
    fired = False

    def complete_then_disable(node_run_id):
        nonlocal fired
        result = real_complete(node_run_id)
        if not fired:
            fired = True
            fx.compiler.set_enabled("pipeline", enabled=False, expected_head_revision=1)
            fx.agents.set_enabled("helper", enabled=False, expected_head_revision=1)
        return result

    fx.runtime.transitions.complete_node = complete_then_disable
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.transitions.complete_node = real_complete

    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE


# Recovery-only abandon ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abandon_rejects_active_runs_stale_versions_and_live_handles(fx):
    _, publication = publish(fx, pair_source)
    started = start(fx, publication.revision)
    run = fx.runtime.transitions.get_run(started.run.workflow_run_id)
    with pytest.raises(ApplicationError, match="blocked"):
        fx.runtime.scheduler.abandon(run.workflow_run_id, expected_row_version=1)


@pytest.mark.asyncio
async def test_abandon_blocked_run_preserves_unknown_evidence(fx):
    fx.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                    )
                ),
            ],
            [["unused"]],
        ]
    )
    _, publication = publish(fx, pair_source, agent=reader_agent())
    started = start(fx, publication.revision)
    fx.runtime.scheduler.faults = OnceFaultInjector(
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
    )
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.scheduler.faults = None
    blocked = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert blocked.status is WorkflowStatus.BLOCKED

    gamma = node_by_id(fx, started.run.workflow_run_id, "gamma")
    current = fx.runtime.transitions.get_run(started.run.workflow_run_id)
    with pytest.raises(ApplicationError, match="stale"):
        fx.runtime.scheduler.abandon(
            started.run.workflow_run_id, expected_row_version=current.row_version + 1
        )
    # An exact live handle owned by this process rejects abandon outright.
    fx.runtime.scheduler._live_node_run_id = gamma.node_run_id
    with pytest.raises(ApplicationError, match="live handle"):
        fx.runtime.scheduler.abandon(
            started.run.workflow_run_id, expected_row_version=current.row_version
        )
    fx.runtime.scheduler._live_node_run_id = None

    run = fx.runtime.scheduler.abandon(
        started.run.workflow_run_id, expected_row_version=current.row_version
    )
    assert run.status is WorkflowStatus.CANCELLED
    # The blocked NodeRun and its unknown Tool evidence are preserved, not reconciled.
    gamma = node_by_id(fx, run.workflow_run_id, "gamma")
    assert gamma.status is WorkflowStatus.BLOCKED
    assert node_by_id(fx, run.workflow_run_id, "alpha").status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.ABANDONED
    outcome = outcomes(fx)[-1]
    assert "workflow_terminal=abandoned" in outcome.completion_basis
    assert outcome.unresolved_items
    # Abandon is idempotent on the terminal run.
    again = fx.runtime.scheduler.abandon(run.workflow_run_id, expected_row_version=run.row_version)
    assert again.status is WorkflowStatus.CANCELLED


# Rerun policy ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_after_failure_requires_explicit_root_resume_and_creates_a_new_run(fx):
    fx.bank.scripts.extend([[["one"]], [RuntimeError("boom")], [["one again"]], [["two"]]])
    _, publication = publish(fx, pair_source)
    first = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
    assert first.status is WorkflowStatus.FAILED

    # A terminal failed run never resumes; the root must be reopened explicitly.
    with pytest.raises(ApplicationError, match="explicit TaskService"):
        start(fx, publication.revision, command_id="cmd_start_early")
    again = await fx.runtime.scheduler.run(first.workflow_run_id)
    assert again.status is WorkflowStatus.FAILED
    assert len(fx.bank.providers) == 2

    fx.tasks.resume("task_root", command_id="cmd_resume_root")
    second = await fx.runtime.scheduler.run(
        start(fx, publication.revision, command_id="cmd_start_second").run.workflow_run_id
    )
    assert second.status is WorkflowStatus.COMPLETED
    assert second.workflow_run_id != first.workflow_run_id
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE


# Settlement-order and serial-admission regressions ------------------------------------


@pytest.mark.asyncio
async def test_cancel_after_middle_node_commit_keeps_the_committed_node_completed(fx):
    """A late cancel must not undo a leaf that already committed READY."""

    fx.bank.scripts.extend([[["one"]], [["unused"]]])
    _, publication = publish(fx, pair_source)
    started = start(fx, publication.revision)

    real_drive = fx.runtime.scheduler._drive
    calls = 0

    async def drive_then_cancel(session, text, *, client_message_id, prepared, resume=False):
        nonlocal calls
        result = await real_drive(
            session, text, client_message_id=client_message_id, prepared=prepared, resume=resume
        )
        calls += 1
        if calls == 1:
            # The cancel surfaces from drive cleanup after gamma's terminal commit.
            raise asyncio.CancelledError
        return result

    fx.runtime.scheduler._drive = drive_then_cancel
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.scheduler._drive = real_drive

    assert run.status is WorkflowStatus.CANCELLED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    alpha = node_by_id(fx, run.workflow_run_id, "alpha")
    assert alpha.status is WorkflowStatus.CANCELLED
    assert alpha.agent_run_id is None
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=user_cancelled" in outcomes(fx)[-1].completion_basis


@pytest.mark.asyncio
async def test_cancel_after_last_node_commit_still_finalizes_success(fx):
    """A cancel arriving after the final leaf committed cannot falsify success."""

    fx.bank.scripts.extend([[["one"]], [["two"]]])
    _, publication = publish(fx, pair_source)
    started = start(fx, publication.revision)

    real_drive = fx.runtime.scheduler._drive
    calls = 0

    async def drive_then_cancel(session, text, *, client_message_id, prepared, resume=False):
        nonlocal calls
        result = await real_drive(
            session, text, client_message_id=client_message_id, prepared=prepared, resume=resume
        )
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError
        return result

    fx.runtime.scheduler._drive = drive_then_cancel
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.scheduler._drive = real_drive

    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    for node_id in ("gamma", "alpha"):
        assert node_by_id(fx, run.workflow_run_id, node_id).status is WorkflowStatus.COMPLETED
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    snapshots = [o for o in outcomes(fx) if o.trigger is TaskOutcomeTrigger.SNAPSHOT]
    assert len(snapshots) == 1


def fork_source(ref, **changes):
    """Legal fork: gamma -> alpha and gamma -> beta; alpha and beta are siblings."""

    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Fork pipeline",
            "default_budget": BUDGET,
            "nodes": (
                chain_node(ref, "gamma", "Phase one survey work"),
                chain_node(ref, "alpha", "Branch alpha work"),
                chain_node(ref, "beta", "Branch beta work"),
            ),
            "edges": (
                WorkflowEdge(from_node_id="gamma", to_node_id="alpha"),
                WorkflowEdge(from_node_id="gamma", to_node_id="beta"),
            ),
            "required_outputs": (
                NodeOutputRef(node_id="alpha", output_slot="result"),
                NodeOutputRef(node_id="beta", output_slot="result"),
            ),
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_unsettled_node_never_allows_a_sibling_admission(fx):
    """A drive returning without a terminal fact fails the run; no sibling is admitted."""

    fx.bank.scripts.extend([[["root done"]]])
    _, publication = publish(fx, fork_source)
    started = start(fx, publication.revision)

    real_drive_node = fx.runtime.scheduler._drive_node

    async def stall_on_alpha(run, revision, node_def, node, cap):
        if node_def.node_id == "alpha":
            # Simulates a drive that returns with its node still nonterminal
            # (for example an exhausted resume loop).
            return
        await real_drive_node(run, revision, node_def, node, cap)

    fx.runtime.scheduler._drive_node = stall_on_alpha
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.runtime.scheduler._drive_node = real_drive_node

    assert run.status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "gamma").status is WorkflowStatus.COMPLETED
    assert node_by_id(fx, run.workflow_run_id, "alpha").status is WorkflowStatus.CANCELLED
    beta = node_by_id(fx, run.workflow_run_id, "beta")
    assert beta.status is WorkflowStatus.CANCELLED
    # The sibling was never admitted: no leaf, no AgentRun, no Provider.
    assert beta.agent_run_id is None
    assert beta.conversation_session_id is None
    assert len(fx.bank.providers) == 1
    assert "workflow_terminal=node_failed" in outcomes(fx)[-1].completion_basis
    assert root(fx).status is TaskRunStatus.FAILED
