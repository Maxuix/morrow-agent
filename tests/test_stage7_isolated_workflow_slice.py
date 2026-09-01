"""Stage 7 Subplan 4: isolated Workflow vertical slice end to end.

One compiled isolated node runs through WorkflowRun -> NodeRun -> AgentFactory
-> the existing AgentLoop -> Artifact/TaskOutcome on the single Scheduler /
transition / committer / finalizer path. All Providers are scripted; no Live
network access and no wall-clock synchronization assertions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ValidationError

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
from morrow.application.turns import SessionPersistence
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
    DurableTurn,
    TaskOutcomeEvidenceKind,
    TaskOutcomeTrigger,
    TaskRunStatus,
    TextSafetyProfile,
    TurnSubmitDisposition,
    TurnSubmitReceipt,
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
    NodeOutputRef,
    OutputContract,
    TaskContract,
    node_output_artifact_id,
    workflow_input_artifact_id,
)
from morrow.core.workflows.definitions import (
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
)
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.agent import AgentLoop
from morrow.runtime.durable_log import DurableConversationWriter, restore_conversation_log
from morrow.runtime.policy import load_runtime_policy
from morrow.runtime.session import Session
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
CONTRACT = TaskContract(objective="Inspect password_validation.py and its authorization tests")


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class ScriptBank:
    """One scripted Provider per preparation, in construction order."""

    def __init__(self, scripts=()) -> None:
        self.scripts = list(scripts)
        self.providers: list[ScriptedModelProvider] = []

    def __call__(self, config, credential) -> ScriptedModelProvider:
        script = self.scripts.pop(0) if self.scripts else [["done"]]
        provider = ScriptedModelProvider(script)
        self.providers.append(provider)
        return provider


class ReadArgs(BaseModel):
    path: str


async def _read_handler(arguments) -> str:
    return "safe contents"


class SliceFixture:
    def __init__(self, tmp_path, scripts=()) -> None:
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
        self.bank = ScriptBank(scripts)
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
    fixture = SliceFixture(tmp_path)
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


def node_source(ref, **changes):
    return AgentNodeSource(
        **{
            "node_id": "worker",
            "agent_definition_ref": ref,
            "task_contract": TaskContract(objective="Inspect authorization tests"),
            "output_contracts": (OutputContract(slot="result"),),
            "access_mode": "read",
            **changes,
        }
    )


def workflow_source(ref, **changes):
    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "pipeline",
            "name": "Authorization audit",
            "default_budget": BUDGET,
            "nodes": (node_source(ref),),
            "required_outputs": (NodeOutputRef(node_id="worker", output_slot="result"),),
            **changes,
        }
    )


def publish(fx, *, agent=None, workflow=None, active=MODEL, command="cmd_publish"):
    version = fx.agents.publish(
        agent or agent_source(),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_agent",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow or workflow_source(ref),
        source_revision=0,
        expected_head_revision=0,
        command_id=command,
        active_model=active,
    )
    return version, publication.revision


def start(
    fx,
    revision,
    *,
    command_id="cmd_start",
    contract=CONTRACT,
    root_version=None,
    session_id="ses_root",
    root_id="task_root",
):
    if root_version is None:
        root_version = fx.journal.get_task_run(WS, root_id).row_version
    return fx.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id="pipeline",
            workflow_revision_id=revision.workflow_revision_id,
            session_id=session_id,
            root_task_run_id=root_id,
            expected_root_row_version=root_version,
            contract=contract,
            command_id=command_id,
        )
    )


def root(fx):
    return fx.journal.get_task_run(WS, "task_root")


def outcomes(fx):
    return fx.journal.list_task_outcomes(WS, "task_root")


def only_node(fx, run_id):
    return fx.journal.workflows.list_nodes(WS, run_id)[0]


# Happy path --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolated_slice_completes_end_to_end(fx):
    fx.bank.scripts.append([["final ", "answer"]])
    _, revision = publish(fx)
    started = start(fx, revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)

    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    node = only_node(fx, run.workflow_run_id)
    assert node.status is WorkflowStatus.COMPLETED
    assert node.effective_node_generation_request_cap == 3
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE

    # The exported TextResult wraps the durable final Assistant message.
    artifact_id = node_output_artifact_id(node.node_run_id, "result")
    artifact = fx.artifacts.get(artifact_id)
    assert artifact is not None and artifact.state.value == "available"
    bindings = fx.journal.workflows.list_bindings(WS, run.workflow_run_id)
    assert (node.node_run_id, "output", artifact_id) in {
        (nid, direction, binding.artifact_id) for nid, direction, binding in bindings
    }

    # Root snapshot carries the markers, the goal and the exported result.
    snapshots = [o for o in outcomes(fx) if o.trigger is TaskOutcomeTrigger.SNAPSHOT]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.text_safety_profile == TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE
    roles = {(ref.kind, ref.role) for ref in snapshot.evidence_refs}
    assert (TaskOutcomeEvidenceKind.WORKFLOW_RUN, "workflow_result_snapshot") in roles
    assert ("task_transition", "workflow_ready_transition") in roles
    assert snapshot.goal_reference.reference_id == run.input_artifacts[0].artifact_id
    assert artifact_id in {ref.artifact_id for ref in snapshot.artifact_refs}
    assert "workflow_result=succeeded" in snapshot.completion_basis

    # One purpose=agent admission per model call; nothing else is counted.
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 1
    assert len(fx.bank.providers[0].stream_calls) == 1

    # Accepted outcome inherits only the marked snapshot's evidence and goal.
    accepted = fx.tasks.accept("task_root", command_id="cmd_accept")
    outcome = accepted.outcome
    assert outcome.trigger is TaskOutcomeTrigger.ACCEPTANCE
    assert outcome.text_safety_profile == TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE
    assert artifact_id in {ref.artifact_id for ref in outcome.artifact_refs}
    assert outcome.goal_reference.reference_id == run.input_artifacts[0].artifact_id

    # Completed work is never rerun.
    again = await fx.runtime.scheduler.run(run.workflow_run_id)
    assert again.status is WorkflowStatus.COMPLETED
    assert len(fx.bank.providers[0].stream_calls) == 1


@pytest.mark.asyncio
async def test_query_projection_reports_run_node_artifact_and_usage(fx):
    fx.bank.scripts.append([["observed"]])
    _, revision = publish(fx)
    started = start(fx, revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    view = fx.runtime.queries.get_run_view(run.workflow_run_id)
    assert view.run.workflow_run_id == run.workflow_run_id
    assert view.revision.workflow_revision_id == revision.workflow_revision_id
    assert view.nodes[0].node.status is WorkflowStatus.COMPLETED
    assert view.nodes[0].artifacts[0].state.value == "available"
    assert view.input_artifacts[0].contract.kind == "TaskContract"
    assert view.agent_generation_request_count == 1
    assert fx.runtime.queries.list_runs()[0].workflow_run_id == run.workflow_run_id


# Start admission ----------------------------------------------------------------


def test_start_command_replay_returns_same_run(fx):
    _, revision = publish(fx)
    first = start(fx, revision)
    replayed = start(fx, revision)
    assert replayed.replayed and not first.replayed
    assert replayed.run.workflow_run_id == first.run.workflow_run_id
    assert len(fx.runtime.queries.list_runs()) == 1


def test_start_conflicting_command_id_rejected(fx):
    _, revision = publish(fx)
    start(fx, revision)
    with pytest.raises(ApplicationError, match="reused"):
        start(fx, revision, contract=TaskContract(objective="different objective"))


def test_start_rejects_second_active_workflow_open_turn_and_agent_run(fx):
    _, revision = publish(fx)
    start(fx, revision)
    with pytest.raises(ApplicationError, match="active WorkflowRun"):
        start(fx, revision, command_id="cmd_second")


def test_start_rejects_pre_existing_open_turn(fx):
    _, revision = publish(fx)
    fx.journal.create_turn(
        WS,
        DurableTurn(
            turn_id="turn_open",
            session_id="ses_root",
            task_run_id="task_root",
            client_message_id="msg_open",
        ),
    )
    fx.journal.put_receipt(
        WS,
        TurnSubmitReceipt(
            session_id="ses_root",
            client_message_id="msg_open",
            request_digest="0" * 64,
            disposition=TurnSubmitDisposition.ACCEPTED_OPEN,
            turn_id="turn_open",
        ),
    )
    with pytest.raises(ApplicationError, match="work in progress"):
        start(fx, revision)


def test_start_rejects_disabled_heads_and_revoked_versions(fx):
    version, revision = publish(fx)
    fx.compiler.set_enabled("pipeline", enabled=False, expected_head_revision=1)
    with pytest.raises(ApplicationError, match="disabled"):
        start(fx, revision)
    fx.compiler.set_enabled("pipeline", enabled=True, expected_head_revision=2)

    fx.agents.set_enabled("helper", enabled=False, expected_head_revision=1)
    with pytest.raises(ApplicationError, match="disabled"):
        start(fx, revision)
    fx.agents.set_enabled("helper", enabled=True, expected_head_revision=2)

    fx.compiler.revoke(revision.workflow_revision_id, reason="policy", command_id="cmd_revoke_wf")
    with pytest.raises(ApplicationError, match="policy_revoked"):
        start(fx, revision)


def test_start_rejects_revoked_agent_version(fx):
    version, revision = publish(fx)
    fx.agents.revoke(version.version_id, reason="policy", command_id="cmd_revoke_agent")
    with pytest.raises(ApplicationError, match="policy_revoked"):
        start(fx, revision)


def test_start_rejects_stale_non_current_or_wrong_root(fx):
    _, revision = publish(fx)
    with pytest.raises(ApplicationError, match="stale"):
        start(fx, revision, root_version=99)
    # A task that is not the invoking Session's current task cannot be the root.
    fx.journal.create_session(
        DurableSession(session_id="ses_other", workspace_id=WS),
        task=DurableTaskRun(task_run_id="task_other", session_id="ses_other", workspace_id=WS),
    )
    fx.journal.transact(
        lambda txn: txn._task_journal._create(
            WS,
            DurableTaskRun(task_run_id="task_side", session_id="ses_other", workspace_id=WS),
            make_current=False,
        )
    )
    with pytest.raises(ApplicationError, match="current"):
        start(fx, revision, command_id="cmd_side", session_id="ses_other", root_id="task_side")
    # A leaf-purpose task can never be a root.
    fx.journal.create_session(DurableSession(session_id="ses_leafish", workspace_id=WS))
    leaf_task = DurableTaskRun(
        task_run_id="task_leaf_fake",
        session_id="ses_leafish",
        workspace_id=WS,
        purpose="workflow_node",
    )
    fx.journal.transact(lambda txn: txn._task_journal._create(WS, leaf_task, make_current=True))
    with pytest.raises(ApplicationError, match="purpose=user"):
        start(
            fx,
            revision,
            command_id="cmd_leaf",
            session_id="ses_leafish",
            root_id="task_leaf_fake",
        )


def test_start_rejects_unpublished_revision_and_membership_drift(fx):
    _, revision = publish(fx)
    with pytest.raises(ApplicationError, match="not published"):
        start(
            fx,
            revision.model_copy(update={"workflow_revision_id": "wrev_missing"}),
            command_id="cmd_missing",
        )
    # Definition drift: a newer published revision does not block the old one.
    version = fx.agents.publish(
        agent_source(), source_revision=0, expected_head_revision=1, command_id="cmd_agent2"
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    fx.compiler.publish(
        workflow_source(ref, name="Authorization audit v2"),
        source_revision=0,
        expected_head_revision=1,
        command_id="cmd_publish2",
        active_model=MODEL,
    )
    started = start(fx, revision, command_id="cmd_old_revision")
    assert started.run.workflow_revision_id == revision.workflow_revision_id


def test_secret_shaped_input_is_rejected_before_any_durable_row(fx):
    with pytest.raises(ValidationError):
        TaskContract(objective='use token = "sk-' + "x" * 24 + '" to probe the API')
    publish(fx)
    assert fx.runtime.queries.list_runs() == ()


# Execution mapping ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_after_start_never_blocks_admitted_run(fx):
    fx.bank.scripts.append([["still ", "runs"]])
    _, revision = publish(fx)
    started = start(fx, revision)
    fx.compiler.set_enabled("pipeline", enabled=False, expected_head_revision=1)
    fx.agents.set_enabled("helper", enabled=False, expected_head_revision=1)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE


@pytest.mark.asyncio
async def test_revocation_before_node_admission_uses_policy_revoked_mapping(fx):
    version, revision = publish(fx)
    started = start(fx, revision)
    fx.compiler.revoke(revision.workflow_revision_id, reason="policy", command_id="cmd_revoke_mid")
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.CANCELLED
    node = only_node(fx, run.workflow_run_id)
    assert node.status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.CANCELLED
    outcome = outcomes(fx)[-1]
    assert "workflow_terminal=policy_revoked" in outcome.completion_basis
    assert not fx.bank.providers

    # The agent-version revocation mapping shares the same path on a fresh run.
    fx2_revision = None
    del version, fx2_revision


@pytest.mark.asyncio
async def test_model_failure_fails_run_without_result_snapshot(fx):
    fx.bank.scripts.append([RuntimeError("boom")])
    _, revision = publish(fx)
    started = start(fx, revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.FAILED
    node = only_node(fx, run.workflow_run_id)
    assert node.status is WorkflowStatus.FAILED
    assert root(fx).status is TaskRunStatus.FAILED
    outcome = outcomes(fx)[-1]
    assert outcome.trigger is TaskOutcomeTrigger.TERMINAL_CLOSE
    assert "workflow_result_snapshot" not in {ref.role for ref in outcome.evidence_refs}


@pytest.mark.asyncio
async def test_node_request_cap_shrinks_and_exhaustion_fails(fx):
    fx.bank.scripts.append(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                )
            ),
            ["never ", "reached"],
        ]
    )
    version = fx.agents.publish(
        agent_source(tool_requirements=(ToolRequirement(name="read", requirement="required"),)),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_agent",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow_source(
            ref,
            default_budget=WorkflowBudget(
                max_agent_generation_requests=10,
                default_node_max_agent_generation_requests=3,
                admission_timeout_seconds=300,
                max_concurrency=1,
            ),
            nodes=(node_source(ref, max_agent_generation_requests=1),),
        ),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    started = start(fx, publication.revision)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    node = only_node(fx, run.workflow_run_id)
    assert node.effective_node_generation_request_cap == 1
    assert run.status is WorkflowStatus.FAILED
    assert node.status is WorkflowStatus.FAILED
    assert "workflow_terminal=budget_exhausted" in outcomes(fx)[-1].completion_basis
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 1


@pytest.mark.asyncio
async def test_workflow_remaining_budget_shrinks_effective_cap(fx):
    fx.bank.scripts.append([["ok"]])
    tight = WorkflowBudget(
        max_agent_generation_requests=2,
        default_node_max_agent_generation_requests=3,
        admission_timeout_seconds=300,
        max_concurrency=1,
    )
    _, revision = publish(fx, workflow=None)
    # Republish with a tighter budget as a separate definition.
    version = fx.agents.publish(
        agent_source(), source_revision=0, expected_head_revision=1, command_id="cmd_agent_tight"
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow_source(ref, workflow_definition_id="tight-pipe", default_budget=tight),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish_tight",
        active_model=MODEL,
    )
    started = fx.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id="tight-pipe",
            workflow_revision_id=publication.revision.workflow_revision_id,
            session_id="ses_root",
            root_task_run_id="task_root",
            expected_root_row_version=root(fx).row_version,
            contract=CONTRACT,
            command_id="cmd_start_tight",
        )
    )
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    node = only_node(fx, run.workflow_run_id)
    assert node.effective_node_generation_request_cap == 2
    assert run.status is WorkflowStatus.COMPLETED


@pytest.mark.asyncio
async def test_deadline_exceeded_before_admission_fails_run(fx):
    tight = WorkflowBudget(
        max_agent_generation_requests=10,
        default_node_max_agent_generation_requests=3,
        admission_timeout_seconds=10,
        max_concurrency=1,
    )
    version = fx.agents.publish(
        agent_source(), source_revision=0, expected_head_revision=0, command_id="cmd_agent"
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow_source(ref, default_budget=tight),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    started = start(fx, publication.revision)
    fx.clock.advance(20)
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.FAILED
    assert only_node(fx, run.workflow_run_id).status is WorkflowStatus.CANCELLED
    assert "workflow_terminal=deadline_exceeded" in outcomes(fx)[-1].completion_basis
    assert not fx.bank.providers


@pytest.mark.asyncio
async def test_deadline_expiry_after_settled_tool_fails_next_request(fx):
    fx.bank.scripts.append(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                )
            ),
            ["never ", "reached"],
        ]
    )
    version = fx.agents.publish(
        agent_source(tool_requirements=(ToolRequirement(name="read", requirement="required"),)),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_agent",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow_source(ref),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    started = start(fx, publication.revision)

    # Advance past the frozen deadline once the first request was admitted and
    # its Tool settled safely; the next generation admission must fail.
    original_admit = fx.journal.admit_model_request

    def admit_and_expire(workspace_id, **kwargs):
        observation = original_admit(workspace_id, **kwargs)
        fx.clock.advance(400)
        return observation

    fx.journal.admit_model_request = admit_and_expire
    try:
        run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    finally:
        fx.journal.admit_model_request = original_admit
    assert run.status is WorkflowStatus.FAILED
    assert "workflow_terminal=deadline_exceeded" in outcomes(fx)[-1].completion_basis


@pytest.mark.asyncio
async def test_user_cancel_maps_to_cancelled_run_and_root(fx):
    fx.bank.scripts.append(["cancel"])
    _, revision = publish(fx)
    started = start(fx, revision)
    task = asyncio.create_task(fx.runtime.scheduler.run(started.run.workflow_run_id))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    run = await task
    assert run.status is WorkflowStatus.CANCELLED
    assert only_node(fx, run.workflow_run_id).status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=user_cancelled" in outcomes(fx)[-1].completion_basis


# Crash and recovery ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [FaultPoint.ARTIFACT_AFTER_RESERVE, FaultPoint.ARTIFACT_BEFORE_MARK_AVAILABLE],
)
async def test_crash_at_artifact_boundaries_recovers_without_second_model_request(fx, point):
    fx.bank.scripts.append([["recovered ", "answer"]])
    _, revision = publish(fx)
    started = start(fx, revision)
    fx.artifacts.faults = OnceFaultInjector(point)
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.artifacts.faults = None

    node = only_node(fx, started.run.workflow_run_id)
    assert node.status is WorkflowStatus.RUNNING
    assert root(fx).status is TaskRunStatus.OPEN
    artifact = fx.artifacts.get(node_output_artifact_id(node.node_run_id, "result"))
    assert artifact is not None and artifact.state.value == "staging"

    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    artifact = fx.artifacts.get(node_output_artifact_id(node.node_run_id, "result"))
    assert artifact.state.value == "available"
    # The committer recovered from durable facts; the model ran exactly once.
    assert len(fx.bank.providers[0].stream_calls) == 1


@pytest.mark.asyncio
async def test_crash_after_leaf_terminal_finalize_from_durable_facts(fx):
    fx.bank.scripts.append([["committed"]])
    _, revision = publish(fx)
    started = start(fx, revision)

    finalizer = fx.runtime.scheduler.finalizer

    class ExplodingOnce:
        def __init__(self) -> None:
            self.fired = False

        def __getattr__(self, name):
            if name == "finalize_success" and not self.fired:
                self.fired = True
                raise InjectedFault(FaultPoint.TURN_AFTER_TERMINAL_COMMIT)
            return getattr(finalizer, name)

    fx.runtime.scheduler.finalizer = ExplodingOnce()
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.scheduler.finalizer = finalizer

    node = only_node(fx, started.run.workflow_run_id)
    assert node.status is WorkflowStatus.RUNNING
    leaf = fx.journal.get_task_run(WS, node.leaf_task_run_id)
    assert leaf.status is TaskRunStatus.READY_FOR_ACCEPTANCE
    assert root(fx).status is TaskRunStatus.OPEN

    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    assert len(fx.bank.providers[0].stream_calls) == 1
    snapshots = [o for o in outcomes(fx) if o.trigger is TaskOutcomeTrigger.SNAPSHOT]
    assert len(snapshots) == 1


@pytest.mark.asyncio
async def test_unknown_tool_outcome_blocks_then_resumes_after_resolution(fx):
    fx.bank.scripts.append(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                )
            ),
            ["after ", "recovery"],
        ]
    )
    version = fx.agents.publish(
        agent_source(tool_requirements=(ToolRequirement(name="read", requirement="required"),)),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_agent",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow_source(ref),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    started = start(fx, publication.revision)
    fx.runtime.scheduler.faults = OnceFaultInjector(
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
    )
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.scheduler.faults = None

    node = only_node(fx, started.run.workflow_run_id)
    # The crash left a non-closed Tool execution: only this Workflow blocks.
    blocked = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert blocked.status is WorkflowStatus.BLOCKED
    node = only_node(fx, started.run.workflow_run_id)
    assert node.status is WorkflowStatus.BLOCKED
    assert root(fx).status is TaskRunStatus.OPEN
    assert blocked.pending_terminal_intent is None

    # The existing Recovery owner reconciles the unknown outcome first.
    log = restore_conversation_log(fx.journal, WS, node.conversation_session_id)
    report = fx.runtime.scheduler.recovery.discover(node.conversation_session_id, log)
    assert report is not None and any(item.blocking for item in report.items)
    updated, receipt, planned = fx.runtime.scheduler.recovery.decide(
        report,
        command_id="cmd_ack",
        resolution=RecoveryResolution.ACKNOWLEDGE,
        item_id=report.items[0].item_id,
        log=log,
    )
    writer = DurableConversationWriter(
        log,
        fx.journal,
        workspace_id=WS,
        session_id=node.conversation_session_id,
        id_source=fx.ids,
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

    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    # The interrupted node resumed; its completed work was not rerun from scratch.
    assert fx.journal.count_workflow_agent_requests(WS, run.workflow_run_id) == 2


@pytest.mark.asyncio
async def test_blocked_run_with_cancel_intent_finishes_cancellation(fx):
    fx.bank.scripts.append(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(id="call_a", name="read", arguments='{"path": "a.py"}'),
                )
            ),
            ["unused"],
        ]
    )
    version = fx.agents.publish(
        agent_source(tool_requirements=(ToolRequirement(name="read", requirement="required"),)),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_agent",
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fx.compiler.publish(
        workflow_source(ref),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    started = start(fx, publication.revision)
    fx.runtime.scheduler.faults = OnceFaultInjector(
        FaultPoint.CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT
    )
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.runtime.scheduler.faults = None

    blocked = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert blocked.status is WorkflowStatus.BLOCKED
    # The user cancelled while the outcome was unknown: the intent persists and
    # can never be cleared.
    fx.runtime.transitions.set_pending_user_cancel(started.run.workflow_run_id)
    blocked = fx.runtime.transitions.get_run(started.run.workflow_run_id)
    assert blocked.pending_terminal_intent == "user_cancel"
    with pytest.raises(ValueError, match="never|cleared|advance|conflict"):
        fx.journal.workflows.save_run(
            blocked.model_copy(
                update={"pending_terminal_intent": None, "row_version": blocked.row_version + 1}
            ),
            expected_row_version=blocked.row_version,
        )

    node = only_node(fx, started.run.workflow_run_id)
    log = restore_conversation_log(fx.journal, WS, node.conversation_session_id)
    report = fx.runtime.scheduler.recovery.discover(node.conversation_session_id, log)
    updated, receipt, planned = fx.runtime.scheduler.recovery.decide(
        report,
        command_id="cmd_ack",
        resolution=RecoveryResolution.ACKNOWLEDGE,
        item_id=report.items[0].item_id,
        log=log,
    )
    writer = DurableConversationWriter(
        log,
        fx.journal,
        workspace_id=WS,
        session_id=node.conversation_session_id,
        id_source=fx.ids,
    )
    saved = fx.runtime.scheduler.recovery.commit_decision(
        updated, receipt, planned=planned, log=log, writer=writer, close_all=False
    )
    updated, receipt, planned = fx.runtime.scheduler.recovery.decide(
        saved,
        command_id="cmd_close_report",
        resolution=RecoveryResolution.RESUME,
        item_id=None,
        log=log,
    )
    fx.runtime.scheduler.recovery.commit_decision(
        updated, receipt, planned=planned, log=log, writer=writer, close_all=False
    )

    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.CANCELLED
    assert run.pending_terminal_intent == "user_cancel"
    assert only_node(fx, run.workflow_run_id).status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=user_cancelled" in outcomes(fx)[-1].completion_basis


# Evidence carry-forward -----------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_selects_only_the_current_ready_snapshot(fx):
    fx.bank.scripts.extend([[["first ", "answer"]], [["second ", "answer"]]])
    _, revision = publish(fx)
    run1 = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    artifact1 = node_output_artifact_id(only_node(fx, run1.workflow_run_id).node_run_id, "result")

    # An intervening ordinary snapshot carries no Workflow markers.
    fx.tasks.snapshot("task_root", command_id="cmd_snap", summary="interim")
    fx.tasks.resume("task_root", command_id="cmd_resume")

    run2 = await fx.runtime.scheduler.run(
        start(fx, revision, command_id="cmd_start2").run.workflow_run_id
    )
    artifact2 = node_output_artifact_id(only_node(fx, run2.workflow_run_id).node_run_id, "result")

    accepted = fx.tasks.accept("task_root", command_id="cmd_accept").outcome
    carried = {ref.artifact_id for ref in accepted.artifact_refs}
    assert artifact2 in carried
    assert artifact1 not in carried
    assert accepted.goal_reference.reference_id == run2.input_artifacts[0].artifact_id


@pytest.mark.asyncio
async def test_stale_workflow_refs_never_leak_after_ordinary_direct_turn(fx):
    fx.bank.scripts.extend([[["workflow ", "answer"]], [["direct ", "answer"]]])
    _, revision = publish(fx)
    run1 = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    artifact1 = node_output_artifact_id(only_node(fx, run1.workflow_run_id).node_run_id, "result")

    fx.tasks.resume("task_root", command_id="cmd_resume")
    # Ordinary Direct follow-up on the same root Session through the ordinary path.
    prepared = fx.preparation.prepare_new(model=MODEL)
    session = Session("ses_root")
    persistence = SessionPersistence(
        workspace_id=WS,
        journal=fx.journal,
        store_session=fx.handle,
        id_source=fx.ids,
        model=MODEL,
        run_policy=prepared.run_policy,
        runtime_instance_id="inst-test",
        artifacts=fx.artifacts,
        clock=fx.clock,
        prompt_assembler=fx.preparation.prompt_assembler,
    )
    persistence.restore_into(session)
    loop = AgentLoop(
        prepared.provider,
        prepared.model,
        prepared.context_builder,
        id_source=fx.ids,
        clock=fx.clock,
        tool_executor=prepared.tool_executor,
    )
    async for _event in loop.run_task(
        session, "ordinary follow-up", client_message_id="msg_direct", prepared=prepared
    ):
        pass
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE

    accepted = fx.tasks.accept("task_root", command_id="cmd_accept").outcome
    assert artifact1 not in {ref.artifact_id for ref in accepted.artifact_refs}
    assert accepted.text_safety_profile == TextSafetyProfile.LEGACY_STRICT
    assert accepted.goal_reference.kind.value == "turn"


# Frozen evidence and output safety -------------------------------------------------


@pytest.mark.asyncio
async def test_published_revision_keeps_its_frozen_model_after_active_switch(fx):
    fx.bank.scripts.append([["frozen ", "model"]])
    _, revision = publish(fx, active=MODEL)
    config = fx.app.global_store.load()
    fx.app.global_store.update(
        lambda value: value.model_copy(update={"active_model": OTHER}),
        expected_revision=config.revision,
    )
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    node = only_node(fx, run.workflow_run_id)
    agent_run = fx.journal.get_agent_run(WS, node.agent_run_id)
    assert agent_run.snapshot.model == MODEL


@pytest.mark.asyncio
async def test_secret_shaped_output_is_redacted_without_blocking_terminal(fx):
    secret = "sk-" + "x" * 24
    fx.bank.scripts.append([[f'the probe returned api_key = "{secret}"']])
    _, revision = publish(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    node = only_node(fx, run.workflow_run_id)
    artifact = fx.artifacts.get(node_output_artifact_id(node.node_run_id, "result"))
    stored = fx.artifacts.read(artifact.artifact_id, max_bytes=artifact.byte_size)
    assert secret.encode() not in stored.content
    assert b"<redacted>" in stored.content
    assert b'"content_complete":false' in stored.content


@pytest.mark.asyncio
async def test_workflow_leaf_runs_one_turn_without_steering(fx):
    fx.bank.scripts.append([["single ", "turn"]])
    _, revision = publish(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    node = only_node(fx, run.workflow_run_id)
    turns = fx.journal.list_session_turns(WS, node.conversation_session_id)
    assert len(turns) == 1
    leaf = fx.journal.get_task_run(WS, node.leaf_task_run_id)
    assert leaf.purpose.value == "workflow_node"
    # Leaf TaskRuns stay out of the ordinary user-facing Task surface.
    assert fx.tasks.list("ses_root") == (root(fx),)


@pytest.mark.asyncio
async def test_revoked_agent_version_before_admission_uses_policy_revoked_mapping(fx):
    version, revision = publish(fx)
    started = start(fx, revision)
    fx.agents.revoke(version.version_id, reason="policy", command_id="cmd_revoke_mid_agent")
    run = await fx.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.CANCELLED
    assert only_node(fx, run.workflow_run_id).status is WorkflowStatus.CANCELLED
    assert root(fx).status is TaskRunStatus.CANCELLED
    assert "workflow_terminal=policy_revoked" in outcomes(fx)[-1].completion_basis
    assert not fx.bank.providers


def test_head_disable_during_input_publication_leaves_no_runnable_partial_run(fx):
    _, revision = publish(fx)
    original = fx.artifacts.publish_workflow_payload

    def disabling_publish(*args, **kwargs):
        metadata = original(*args, **kwargs)
        head = fx.journal.workflows.get_head(WS, "pipeline")
        fx.compiler.set_enabled("pipeline", enabled=False, expected_head_revision=head.row_version)
        return metadata

    fx.artifacts.publish_workflow_payload = disabling_publish
    try:
        with pytest.raises(ApplicationError, match="disabled"):
            start(fx, revision)
    finally:
        fx.artifacts.publish_workflow_payload = original
    # Only an unbound immutable Artifact may remain; no Workflow row exists.
    assert fx.runtime.queries.list_runs() == ()
    orphan = fx.artifacts.get(workflow_input_artifact_id("cmd_start"))
    assert orphan is not None
    assert fx.journal.workflows.list_bindings(WS, "wrun_none") == ()


def test_ordinary_turn_admission_is_excluded_while_workflow_is_active(fx):
    from morrow.core.store import StorageError

    _, revision = publish(fx)
    started = start(fx, revision)
    assert started.run.status is WorkflowStatus.QUEUED
    with pytest.raises(StorageError, match="Workflow"):
        fx.journal.create_turn(
            WS,
            DurableTurn(
                turn_id="turn_race",
                session_id="ses_root",
                task_run_id="task_root",
                client_message_id="msg_race",
            ),
        )
