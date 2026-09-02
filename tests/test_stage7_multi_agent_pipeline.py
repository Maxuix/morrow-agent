"""Stage 7 Subplan 6: serial Explorer -> Coder -> Reviewer Artifact pipeline.

Scripted Providers only; no Live network access and no wall-clock sleeps.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import estimate_request_chars
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.agent_definitions.builtins import builtin_definitions
from morrow.application.agent_definitions.publication import (
    AgentDefinitionPublicationService,
    DefinitionCatalog,
)
from morrow.application.agent_runs.preparation import AgentRunPreparationService
from morrow.application.artifacts import ArtifactService
from morrow.application.prompt import DirectCodingPromptAssembler
from morrow.application.workflows.capture import (
    CHANGE_CAPTURE_ROLE,
    VALIDATION_REPORT_ROLE,
    ChangeArtifactCapture,
)
from morrow.application.workflows.compiler import compile_workflow
from morrow.application.workflows.composition import build_workflow_runtime
from morrow.application.workflows.publication import WorkflowCompilationService
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.bootstrap import build_application
from morrow.core.agent_definitions import AgentDefinitionSource, ToolRequirement
from morrow.core.agent_runs import AgentDefinitionRef, ProviderCapabilities
from morrow.core.application import ApplicationError
from morrow.core.capabilities import (
    OperationIntent,
    OperationKind,
    PolicyVerdict,
    ToolRunContext,
    ValidationFact,
)
from morrow.core.domain import (
    DurableSession,
    DurableTaskRun,
    TaskOutcomeTrigger,
    TaskRunStatus,
)
from morrow.core.execution import (
    DurableToolExecution,
    DurableToolFacts,
    EffectClass,
    FileMutationEvidence,
    PreparedIntent,
)
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.local_tools import MutationOperation
from morrow.core.models import (
    AssistantMessage,
    CredentialRef,
    FunctionToolCall,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    ToolApprovalDecision,
    ToolEffect,
)
from morrow.core.workflows.contracts import (
    SUBMIT_NODE_RESULT_NAME,
    ArtifactBinding,
    ContractRef,
    EvidenceBundle,
    ImplementationPatch,
    NodeOutputBinding,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    TaskContractRef,
    WorkflowInputBinding,
    capture_artifact_id,
    node_output_artifact_id,
    parse_workflow_payload,
)
from morrow.core.workflows.contracts import (
    TestReport as WorkflowTestReport,
)
from morrow.core.workflows.definitions import (
    AgentNode,
    AgentNodeSource,
    WorkflowBudget,
    WorkflowDefinitionSource,
    WorkflowEdge,
)
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.policy import load_runtime_policy
from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.testing import FixedIdSource, ScriptedModelProvider

WS = "ws_one"
MODEL = ModelRef(provider_id="fake-provider", model_id="m1")
OTHER = ModelRef(provider_id="fake-provider", model_id="m2")
TOOLS = (
    "read",
    "ls",
    "find",
    "grep",
    "write",
    "edit",
    "bash",
    "promote_sandbox_changes",
)
CATALOG = DefinitionCatalog(
    models=(MODEL, OTHER),
    skill_version_ids=frozenset(),
    tool_access={
        "read": "read",
        "ls": "read",
        "find": "read",
        "grep": "read",
        "write": "write",
        "edit": "write",
        "bash": "write",
        "promote_sandbox_changes": "write",
    },
    allowed_tools=frozenset(TOOLS),
)
HOST_BASH_CATALOG = DefinitionCatalog(
    models=(MODEL, OTHER),
    skill_version_ids=frozenset(),
    tool_access={**dict(CATALOG.tool_access), "bash": "read"},
    allowed_tools=frozenset(TOOLS),
)
AGENT_POLICY = load_runtime_policy().agent_run
BUDGET = WorkflowBudget(
    max_agent_generation_requests=20,
    default_node_max_agent_generation_requests=6,
    admission_timeout_seconds=300,
    max_concurrency=1,
)
CONTRACT = TaskContract(objective="Inspect password_validation.py and implement the fix")


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class ScriptBank:
    def __init__(self, scripts=()) -> None:
        self.scripts = list(scripts)
        self.providers: list[ScriptedModelProvider] = []

    def __call__(self, config, credential) -> ScriptedModelProvider:
        script = self.scripts.pop(0) if self.scripts else [["done"]]
        provider = ScriptedModelProvider(script)
        self.providers.append(provider)
        return provider


class AutoApprovalPort:
    async def request(self, _request) -> ToolApprovalDecision:
        return ToolApprovalDecision(approved=True)


class PathArgs(BaseModel):
    path: str = "a.py"
    content: str = "ok"


async def _ok_handler(_arguments) -> str:
    return "ok"


def _stub_intent(name: str):
    def resolve(_arguments, _context) -> OperationIntent:
        if name in {"write", "edit", "promote_sandbox_changes"}:
            return OperationIntent(
                kind=OperationKind.WORKSPACE_WRITE, effect=ToolEffect.PERSISTENT_WRITE
            )
        if name == "bash":
            return OperationIntent(kind=OperationKind.PROCESS, requires_sandbox=True)
        return OperationIntent(kind=OperationKind.WORKSPACE_READ)

    return resolve


def _stub_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in TOOLS:
        registry.register(
            make_tool(
                name=name,
                description=name,
                arguments_model=PathArgs,
                handler=_ok_handler,
                intent_resolver=_stub_intent(name),
            )
        )
    return registry


def _digest(label: str = "x") -> str:
    from morrow.core.domain import sha256_digest

    return sha256_digest(label)


def _intent(**overrides) -> PreparedIntent:
    values = {
        "tool_name": "write",
        "call_id": "call-1",
        "ordinal": 1,
        "arguments_digest": _digest("args"),
        "schema_digest": _digest("schema"),
        "permission_context_digest": _digest("perms"),
        "effect_class": EffectClass.RECONCILEABLE_FILE_WRITE,
    }
    values.update(overrides)
    return PreparedIntent(**values)


def _execution(**overrides) -> DurableToolExecution:
    intent = overrides.pop("intent", None) or _intent()
    values = {
        "tool_execution_id": "tex_1",
        "workspace_id": WS,
        "session_id": "ses_1",
        "task_run_id": "task_1",
        "turn_id": "turn_1",
        "agent_run_id": "arun_1",
        "call_id": intent.call_id,
        "ordinal": intent.ordinal,
        "tool_name": intent.tool_name,
        "intent": intent,
    }
    values.update(overrides)
    return DurableToolExecution(**values)


def _file_fact(path: str = "hello.py") -> FileMutationEvidence:
    return FileMutationEvidence(
        relative_path=path,
        operation="create",
        existed_before=False,
        policy_version="files-v1",
        conflict_input_digest=_digest("conflict"),
    )


def _coder_node(**changes) -> AgentNode:
    return AgentNode(
        **{
            "node_id": "coder",
            "agent_definition_ref": AgentDefinitionRef(
                definition_id="coder", version_id="adev_one", content_hash="a" * 64
            ),
            "task_contract": TaskContract(objective="Apply the change"),
            "output_contracts": (OutputContract(kind="ImplementationPatch", slot="patch"),),
            "access_mode": "write",
            "resolved_model_ref": MODEL,
            "declared_node_max_agent_generation_requests": 6,
            **changes,
        }
    )


def _explorer_node() -> AgentNode:
    return AgentNode(
        node_id="explorer",
        agent_definition_ref=AgentDefinitionRef(
            definition_id="explorer", version_id="adev_one", content_hash="a" * 64
        ),
        task_contract=TaskContract(objective="Look"),
        output_contracts=(OutputContract(kind="EvidenceBundle", slot="evidence"),),
        access_mode="read",
        resolved_model_ref=MODEL,
        declared_node_max_agent_generation_requests=6,
    )


def _leaf_hooks(artifacts, *, node=None, executions=(), records=(), node_run_id="nrun_1"):
    from types import SimpleNamespace

    from morrow.application.workflows.leaf import WorkflowLeafContext, WorkflowLeafHooks

    journal = SimpleNamespace(
        list_task_executions=lambda _ws, _task: executions,
        load_effective_records=lambda _ws, _ses: records,
    )
    return WorkflowLeafHooks(
        journal,
        workspace_id=WS,
        context=WorkflowLeafContext(
            workflow_run_id="wrun_1",
            workflow_revision_id="wrev_1",
            node_run_id=node_run_id,
            node=node or _coder_node(),
            leaf_session_id="ses_1",
            leaf_task_run_id="task_1",
            effective_node_generation_request_cap=6,
        ),
        artifacts=artifacts,
        transitions=None,
        id_source=None,
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )


def _artifact_harness(tmp_path):
    store = OperationalStore(tmp_path / "state")
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id=WS),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id=WS),
    )
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id=WS,
        id_source=FixedIdSource(),
    )
    return handle, artifacts


class PipelineFixture:
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
        self.preparation = AgentRunPreparationService(
            global_store=self.app.global_store,
            registry=self.app.registry,
            agent_policy=AGENT_POLICY,
            credential_resolver=self.app.provider_service.credential_resolver,
            estimate_request_chars=estimate_request_chars,
            tool_factory=lambda policy: ToolExecutor(_stub_registry().snapshot(), policy),
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

    def close(self) -> None:
        self.handle.close()


@pytest.fixture
def fx(tmp_path):
    fixture = PipelineFixture(tmp_path)
    yield fixture
    fixture.close()


def explorer_source(**kwargs):
    return AgentDefinitionSource(
        **{
            "definition_id": "explorer",
            "name": "Explorer",
            "role_prompt": "Inspect password validation. Do not modify the workspace.",
            "access_mode_ceiling": "read",
            "tool_requirements": (
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="write", requirement="forbidden"),
            ),
            **kwargs,
        }
    )


def coder_source(**kwargs):
    return AgentDefinitionSource(
        **{
            "definition_id": "coder",
            "name": "Coder",
            "role_prompt": "Implement the password_validation change.",
            "access_mode_ceiling": "write",
            "tool_requirements": (
                ToolRequirement(name="write", requirement="required"),
                ToolRequirement(name="read", requirement="optional"),
            ),
            **kwargs,
        }
    )


def reviewer_source(**kwargs):
    return AgentDefinitionSource(
        **{
            "definition_id": "reviewer",
            "name": "Reviewer",
            "role_prompt": "Review authorization tests. Do not modify the workspace.",
            "access_mode_ceiling": "read",
            "tool_requirements": (
                ToolRequirement(name="read", requirement="required"),
                ToolRequirement(name="write", requirement="forbidden"),
            ),
            **kwargs,
        }
    )


def _publish_agent(fx, source, command):
    version = fx.agents.publish(
        source, source_revision=0, expected_head_revision=0, command_id=command
    )
    return AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )


def _task_binding():
    return WorkflowInputBinding(
        source="workflow_input",
        input_name="task",
        accepts=TaskContractRef(),
        workflow_input="task",
    )


def pipeline_source(explorer, coder, reviewer, **changes):
    nodes = (
        AgentNodeSource(
            node_id="explorer",
            agent_definition_ref=explorer,
            task_contract=TaskContract(objective="Gather evidence"),
            input_bindings=(_task_binding(),),
            output_contracts=(OutputContract(kind="EvidenceBundle", slot="evidence"),),
            access_mode="read",
        ),
        AgentNodeSource(
            node_id="coder",
            agent_definition_ref=coder,
            task_contract=TaskContract(objective="Apply the change"),
            input_bindings=(
                _task_binding(),
                NodeOutputBinding(
                    source="node_output",
                    input_name="evidence",
                    accepts=ContractRef(kind="EvidenceBundle"),
                    node_output=NodeOutputRef(node_id="explorer", output_slot="evidence"),
                ),
            ),
            output_contracts=(
                OutputContract(kind="ImplementationPatch", slot="patch"),
                OutputContract(kind="TestReport", slot="tests"),
            ),
            access_mode="write",
        ),
        AgentNodeSource(
            node_id="reviewer",
            agent_definition_ref=reviewer,
            task_contract=TaskContract(objective="Review the change"),
            input_bindings=(
                _task_binding(),
                NodeOutputBinding(
                    source="node_output",
                    input_name="patch",
                    accepts=ContractRef(kind="ImplementationPatch"),
                    node_output=NodeOutputRef(node_id="coder", output_slot="patch"),
                ),
                NodeOutputBinding(
                    source="node_output",
                    input_name="tests",
                    accepts=ContractRef(kind="TestReport"),
                    node_output=NodeOutputRef(node_id="coder", output_slot="tests"),
                ),
            ),
            output_contracts=(OutputContract(kind="ReviewReport", slot="review"),),
            access_mode="read",
        ),
    )
    return WorkflowDefinitionSource(
        **{
            "workflow_definition_id": "explore_implement_verify",
            "name": "Explore implement verify",
            "default_budget": BUDGET,
            "nodes": nodes,
            "edges": (
                WorkflowEdge(from_node_id="explorer", to_node_id="coder"),
                WorkflowEdge(from_node_id="coder", to_node_id="reviewer"),
            ),
            "required_outputs": (
                NodeOutputRef(node_id="coder", output_slot="patch"),
                NodeOutputRef(node_id="reviewer", output_slot="review"),
            ),
            **changes,
        }
    )


def publish_pipeline(fx, source=None):
    explorer = _publish_agent(fx, explorer_source(), "cmd_explorer")
    coder = _publish_agent(fx, coder_source(), "cmd_coder")
    reviewer = _publish_agent(fx, reviewer_source(), "cmd_reviewer")
    publication = fx.compiler.publish(
        source or pipeline_source(explorer, coder, reviewer),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    return explorer, coder, reviewer, publication.revision


def start(fx, revision, *, command_id="cmd_start", contract=CONTRACT):
    root = fx.journal.get_task_run(WS, "task_root")
    return fx.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id="explore_implement_verify",
            workflow_revision_id=revision.workflow_revision_id,
            session_id="ses_root",
            root_task_run_id="task_root",
            expected_root_row_version=root.row_version,
            contract=contract,
            command_id=command_id,
        )
    )


def node_by_id(fx, run_id, node_id):
    return next(
        node for node in fx.journal.workflows.list_nodes(WS, run_id) if node.node_id == node_id
    )


def root(fx):
    return fx.journal.get_task_run(WS, "task_root")


def outcomes(fx):
    return fx.journal.list_task_outcomes(WS, "task_root")


def _submit_call(call_id, slot, payload, *, name=SUBMIT_NODE_RESULT_NAME):
    return FunctionToolCall(
        id=call_id,
        name=name,
        arguments=json.dumps(
            {"schema_version": 1, "outputs": {slot: payload}, "summary": "submitted"}
        ),
    )


def _script_submit_then_stop(call_id, slot, payload, text="done"):
    return [
        AssistantMessage(tool_calls=(_submit_call(call_id, slot, payload),)),
        [text],
    ]


# Capture gate -----------------------------------------------------------------


def test_write_capture_publishes_complete_diff_and_replays(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    store = OperationalStore(tmp_path / "state")
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id=WS),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id=WS),
    )
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id=WS,
        id_source=FixedIdSource(),
    )
    capture = ChangeArtifactCapture(artifacts, mutation)
    plan = mutation.preflight_write("hello.py", content="print('hi')\n", mode="create")
    run = ToolRunContext(run_id="arun_1", session_id="ses_1")
    result, _fact = mutation.apply(
        plan,
        call_id="call-1",
        tool_name="write",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    assert result.diff_truncated is False
    from morrow.runtime.tools import ToolExecutionOutcome

    execution = _execution()
    refs = capture.capture(
        execution,
        ToolExecutionOutcome(call_id="call-1", name="write", ok=True, envelope="{}", facts=()),
        [],
    )
    assert len(refs) == 1
    assert refs[0].role == CHANGE_CAPTURE_ROLE
    stored = artifacts.get(refs[0].artifact_id)
    from morrow.core.workflows.contracts import ChangeCapture

    captured = ChangeCapture.model_validate_json(
        artifacts.read(refs[0].artifact_id, max_bytes=stored.byte_size).content
    )
    assert captured.content_complete is True
    assert "print('hi')" in (captured.unified_diff or "")
    replay_id = capture_artifact_id("tex_1", CHANGE_CAPTURE_ROLE, path="hello.py")
    again = capture.capture(
        execution,
        ToolExecutionOutcome(call_id="call-1", name="write", ok=True, envelope="{}", facts=()),
        [],
    )
    assert again == ()
    assert artifacts.get(replay_id) is not None
    handle.close()


def test_capture_degrades_secret_diff_and_skips_non_workflow_mutation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    captured = WorkspaceMutationService(files, artifact_capture=True)
    ordinary = WorkspaceMutationService(files, artifact_capture=False)
    plan = captured.preflight_write(
        "secret.py",
        content='token = "sk-abcdefghijklmnopqrstuvwx"\n',
        mode="create",
    )
    run = ToolRunContext(run_id="arun_1", session_id="ses_1")
    captured.apply(
        plan,
        call_id="call-1",
        tool_name="write",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    draft = captured.take_captures("arun_1", "call-1")[0]
    from morrow.application.workflows.capture import _change_payload

    payload = _change_payload(draft)
    assert payload.content_complete is False
    assert payload.unified_diff is None
    ordinary_plan = ordinary.preflight_write("plain.py", content="ok\n", mode="create")
    ordinary.apply(
        ordinary_plan,
        call_id="call-2",
        tool_name="write",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    assert ordinary.take_captures("arun_1", "call-2") == ()
    assert ordinary_plan.diff_truncated is False
    assert MutationOperation.CREATE is ordinary_plan.operation


def test_validation_capture_without_command_output_is_truthful(tmp_path):
    store = OperationalStore(tmp_path / "state")
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(
        DurableSession(session_id="ses_1", workspace_id=WS),
        task=DurableTaskRun(task_run_id="task_1", session_id="ses_1", workspace_id=WS),
    )
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id=WS,
        id_source=FixedIdSource(),
    )
    capture = ChangeArtifactCapture(artifacts)
    fact = ValidationFact(
        call_id="call-1",
        tool_name="bash",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        relative_paths=(".",),
        validator_kind="pytest",
        scope=".",
        status="passed",
        exit_code=0,
        evidence_summary="passed",
    )
    from morrow.runtime.tools import ToolExecutionOutcome

    refs = capture.capture(
        _execution(intent=_intent(tool_name="bash", call_id="call-1")),
        ToolExecutionOutcome(call_id="call-1", name="bash", ok=True, envelope="{}", facts=(fact,)),
        [],
    )
    assert refs[0].role == VALIDATION_REPORT_ROLE
    stored = artifacts.get(refs[0].artifact_id)
    report = WorkflowTestReport.model_validate_json(
        artifacts.read(refs[0].artifact_id, max_bytes=stored.byte_size).content
    )
    assert report.items[0].output_ref is None
    assert report.content_complete is False
    assert report.omission_reason == "command_output_unavailable"
    handle.close()


def test_two_file_promote_publishes_distinct_capture_ids(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    handle, artifacts = _artifact_harness(tmp_path)
    capture = ChangeArtifactCapture(artifacts, mutation)
    run = ToolRunContext(run_id="arun_1", session_id="ses_1")
    for name, body in (("a.py", "a = 1\n"), ("b.py", "b = 2\n")):
        plan = mutation.preflight_write(name, content=body, mode="create")
        mutation.apply(
            plan,
            call_id="call-1",
            tool_name="promote_sandbox_changes",
            ordinal=1,
            approval_verdict=PolicyVerdict.ALLOW,
            run=run,
        )
    from morrow.runtime.tools import ToolExecutionOutcome

    refs = capture.capture(
        _execution(),
        ToolExecutionOutcome(
            call_id="call-1", name="promote_sandbox_changes", ok=True, envelope="{}", facts=()
        ),
        [],
    )
    assert {ref.artifact_id for ref in refs} == {
        capture_artifact_id("tex_1", CHANGE_CAPTURE_ROLE, path="a.py"),
        capture_artifact_id("tex_1", CHANGE_CAPTURE_ROLE, path="b.py"),
    }
    handle.close()


def test_same_path_capture_conflict_when_bytes_differ(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    handle, artifacts = _artifact_harness(tmp_path)
    capture = ChangeArtifactCapture(artifacts, mutation)
    run = ToolRunContext(run_id="arun_1", session_id="ses_1")
    plan = mutation.preflight_write("hello.py", content="print('hi')\n", mode="create")
    result, _fact = mutation.apply(
        plan,
        call_id="call-1",
        tool_name="write",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    from morrow.core.artifacts import ArtifactError, ArtifactErrorCode
    from morrow.runtime.tools import ToolExecutionOutcome

    outcome = ToolExecutionOutcome(call_id="call-1", name="write", ok=True, envelope="{}", facts=())
    execution = _execution()
    capture.capture(execution, outcome, [])
    replacement = mutation.preflight_write(
        "hello.py",
        content="print('bye')\n",
        mode="replace",
        expected_sha256=result.after_revision.sha256,
    )
    mutation.apply(
        replacement,
        call_id="call-1",
        tool_name="write",
        ordinal=2,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    with pytest.raises(ArtifactError) as exc:
        capture.capture(execution, outcome, [])
    assert exc.value.code is ArtifactErrorCode.CONFLICT
    handle.close()


def test_take_captures_matches_durable_call_id_without_leftover_sweep(tmp_path):
    from morrow.core.domain import sha256_digest

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    plan = mutation.preflight_write("hello.py", content="print('hi')\n", mode="create")
    run = ToolRunContext(run_id="arun_1", session_id="ses_1")
    mutation.apply(
        plan,
        call_id="call_w",
        tool_name="write",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    hashed = f"call_{sha256_digest('call_w')}"
    assert mutation.take_captures("arun_1", "call_other") == ()
    drafts = mutation.take_captures("arun_1", hashed)
    assert len(drafts) == 1
    assert drafts[0].path == "hello.py"
    assert mutation.take_captures("arun_1", "call_w") == ()


def test_implementation_patch_relinks_available_capture(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    handle, artifacts = _artifact_harness(tmp_path)
    capture = ChangeArtifactCapture(artifacts, mutation)
    plan = mutation.preflight_write("hello.py", content="print('hi')\n", mode="create")
    run = ToolRunContext(run_id="arun_1", session_id="ses_1")
    mutation.apply(
        plan,
        call_id="call-1",
        tool_name="write",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    from morrow.runtime.tools import ToolExecutionOutcome

    execution = _execution(facts=DurableToolFacts(files=(_file_fact(),)))
    capture.capture(
        execution,
        ToolExecutionOutcome(call_id="call-1", name="write", ok=True, envelope="{}", facts=()),
        [],
    )
    hooks = _leaf_hooks(artifacts, executions=(execution,))
    patch = hooks._implementation_patch((execution,))
    expected = capture_artifact_id("tex_1", CHANGE_CAPTURE_ROLE, path="hello.py")
    assert patch.change_refs == (expected,)
    assert patch.changed_paths == ("hello.py",)
    assert patch.content_complete is True
    handle.close()


def test_known_write_without_capture_is_incomplete_empty_patch(tmp_path):
    handle, artifacts = _artifact_harness(tmp_path)
    execution = _execution(facts=DurableToolFacts(files=(_file_fact(),)))
    hooks = _leaf_hooks(artifacts, executions=(execution,))
    patch = hooks._implementation_patch((execution,))
    assert patch.change_refs == ()
    assert patch.content_complete is False
    assert patch.omission_reason == "change_capture_missing"
    handle.close()


def test_stop_without_writes_is_complete_empty_patch(tmp_path):
    handle, artifacts = _artifact_harness(tmp_path)
    hooks = _leaf_hooks(artifacts)
    patch = hooks._implementation_patch(())
    assert patch.change_refs == ()
    assert patch.changed_paths == ()
    assert patch.content_complete is True
    handle.close()


def test_secret_assistant_rationale_is_omitted_from_patch(tmp_path):
    from types import SimpleNamespace

    handle, artifacts = _artifact_harness(tmp_path)
    record = SimpleNamespace(
        kind="message",
        record_id="rec_1",
        payload={
            "role": "assistant",
            "content": 'token = "sk-abcdefghijklmnopqrstuvwx"',
            "tool_calls": None,
        },
    )
    hooks = _leaf_hooks(artifacts, records=(record,))
    patch = hooks._implementation_patch(())
    assert patch.rationale == ""
    assert patch.content_complete is True
    handle.close()


def test_submit_maps_slot_conflict_when_marker_is_missing():
    from morrow.application.workflows.submit import SubmitNodeResultArguments
    from morrow.core.artifacts import ArtifactError, ArtifactErrorCode
    from morrow.runtime.tools import ToolErrorCode, ToolExecutionError

    class _ConflictingArtifacts:
        def get(self, _artifact_id):
            return None

        def publish_workflow_payload(self, *_args, **_kwargs):
            raise ArtifactError(
                ArtifactErrorCode.CONFLICT, "Workflow output already has different content"
            )

        def publish_bytes(self, *_args, **_kwargs):
            raise AssertionError("marker must not publish after a slot conflict")

    hooks = _leaf_hooks(_ConflictingArtifacts(), node=_explorer_node())
    arguments = SubmitNodeResultArguments(
        outputs={"evidence": {"findings": ["hello.py exists"]}},
        summary="submitted",
    )
    with pytest.raises(ToolExecutionError) as exc:
        hooks.submit_node_result(arguments)
    assert exc.value.code is ToolErrorCode.CONFLICT


def test_missing_exported_review_report_does_not_succeed():
    from types import SimpleNamespace

    from morrow.application.workflows.finalizer import compute_workflow_result

    slot = OutputContract(kind="ReviewReport", slot="review")
    revision = SimpleNamespace(
        nodes=(SimpleNamespace(node_id="reviewer", output_contracts=(slot,)),),
        required_outputs=(NodeOutputRef(node_id="reviewer", output_slot="review"),),
    )
    nodes = (SimpleNamespace(node_id="reviewer", node_run_id="nrun_reviewer1"),)
    with pytest.raises(ApplicationError, match="ReviewReport"):
        compute_workflow_result(revision, nodes, (), artifacts=None)
    binding = ArtifactBinding(
        name="review",
        artifact_id="art_" + "a" * 32,
        contract=ContractRef(kind="ReviewReport"),
    )
    artifacts = SimpleNamespace(get=lambda _id: None)
    with pytest.raises(ApplicationError, match="ReviewReport"):
        compute_workflow_result(
            revision,
            nodes,
            (("nrun_reviewer1", "output", binding),),
            artifacts=artifacts,
        )
    patch_slot = OutputContract(kind="ImplementationPatch", slot="patch")
    patch_revision = SimpleNamespace(
        nodes=(SimpleNamespace(node_id="coder", output_contracts=(patch_slot,)),),
        required_outputs=(NodeOutputRef(node_id="coder", output_slot="patch"),),
    )
    patch_nodes = (SimpleNamespace(node_id="coder", node_run_id="nrun_coder1"),)
    assert compute_workflow_result(patch_revision, patch_nodes, (), artifacts=None) == "succeeded"


def test_complete_patch_sandbox_pairing_and_host_bash_rejection():
    explorer = AgentDefinitionSource(
        definition_id="explorer",
        name="Explorer",
        role_prompt="Inspect.",
        access_mode_ceiling="read",
        tool_requirements=(ToolRequirement(name="read", requirement="required"),),
        model_selection=MODEL,
    )
    coder = AgentDefinitionSource(
        definition_id="coder",
        name="Coder",
        role_prompt="Implement.",
        access_mode_ceiling="write",
        tool_requirements=(
            ToolRequirement(name="write", requirement="required"),
            ToolRequirement(name="bash", requirement="required"),
        ),
        model_selection=MODEL,
    )
    from morrow.core.agent_definitions import AgentDefinitionVersion
    from morrow.core.models import utc_now

    def version(source, vid):
        return AgentDefinitionVersion(
            version_id=vid,
            workspace_id=WS,
            version=1,
            source=source,
            content_hash=source.content_hash,
            origin="user",
            source_revision=0,
            created_at=utc_now(),
        )

    e_ver = version(explorer, "adev_explorer1")
    c_ver = version(coder, "adev_coder1")
    e_ref = AgentDefinitionRef(
        definition_id="explorer", version_id=e_ver.version_id, content_hash=e_ver.content_hash
    )
    c_ref = AgentDefinitionRef(
        definition_id="coder", version_id=c_ver.version_id, content_hash=c_ver.content_hash
    )
    source = WorkflowDefinitionSource(
        workflow_definition_id="pair",
        name="Pair",
        default_budget=BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="explorer",
                agent_definition_ref=e_ref,
                task_contract=TaskContract(objective="Look"),
                output_contracts=(OutputContract(kind="EvidenceBundle", slot="evidence"),),
                access_mode="read",
            ),
            AgentNodeSource(
                node_id="coder",
                agent_definition_ref=c_ref,
                task_contract=TaskContract(objective="Change"),
                input_bindings=(
                    NodeOutputBinding(
                        source="node_output",
                        input_name="evidence",
                        accepts=ContractRef(kind="EvidenceBundle"),
                        node_output=NodeOutputRef(node_id="explorer", output_slot="evidence"),
                    ),
                ),
                output_contracts=(OutputContract(kind="ImplementationPatch", slot="patch"),),
                access_mode="write",
            ),
        ),
        edges=(WorkflowEdge(from_node_id="explorer", to_node_id="coder"),),
        required_outputs=(NodeOutputRef(node_id="coder", output_slot="patch"),),
    )
    legal = compile_workflow(
        source,
        agent_versions={e_ver.version_id: e_ver, c_ver.version_id: c_ver},
        catalog=CATALOG,
        active_model=MODEL,
    )
    assert legal.candidate is not None
    host = compile_workflow(
        source,
        agent_versions={e_ver.version_id: e_ver, c_ver.version_id: c_ver},
        catalog=HOST_BASH_CATALOG,
        active_model=MODEL,
    )
    assert host.candidate is None
    assert any(item.code == "uncapturable_host_bash" for item in host.errors)


def test_compose_leaf_runtime_rejects_host_bash_for_complete_patch(fx):
    from dataclasses import dataclass
    from types import SimpleNamespace

    from morrow.core.capabilities import ProcessIsolation

    @dataclass(frozen=True)
    class _Prepared:
        tool_executor: object

    prepared = _Prepared(
        tool_executor=SimpleNamespace(
            expected_process_isolation=ProcessIsolation.HOST,
            tool_set=SimpleNamespace(tools={"bash": object(), "write": object()}),
        )
    )
    hooks = SimpleNamespace(
        context=SimpleNamespace(
            node=SimpleNamespace(
                output_contracts=(OutputContract(kind="ImplementationPatch", slot="patch"),)
            )
        )
    )
    with pytest.raises(ApplicationError, match="uncapturable_host_bash"):
        fx.runtime.scheduler._compose_leaf_runtime(prepared, hooks)


def _offered_tool_names(fx) -> set[str]:
    return {tool.function.name for tool in fx.bank.providers[0].stream_tools[0]}


def _start_named(fx, definition_id, revision, command_id):
    root_task = fx.journal.get_task_run(WS, "task_root")
    return fx.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id=definition_id,
            workflow_revision_id=revision.workflow_revision_id,
            session_id="ses_root",
            root_task_run_id="task_root",
            expected_root_row_version=root_task.row_version,
            contract=CONTRACT,
            command_id=command_id,
        )
    )


@pytest.mark.asyncio
async def test_read_node_on_write_definition_does_not_receive_write_tools(fx):
    writer = AgentDefinitionSource(
        definition_id="writer",
        name="Writer",
        role_prompt="Inspect or edit password validation.",
        access_mode_ceiling="write",
        tool_requirements=(
            ToolRequirement(name="read", requirement="required"),
            ToolRequirement(name="write", requirement="optional"),
        ),
        model_selection=MODEL,
    )
    ref = _publish_agent(fx, writer, "cmd_writer")
    source = WorkflowDefinitionSource(
        workflow_definition_id="narrow_read",
        name="Narrow read",
        default_budget=BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="reader",
                agent_definition_ref=ref,
                task_contract=TaskContract(objective="Read only"),
                input_bindings=(_task_binding(),),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="read",
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="reader", output_slot="result"),),
    )
    revision = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish_narrow",
        active_model=MODEL,
    ).revision
    allowed = {
        item.name
        for item in revision.nodes[0].resolved_tool_requirements
        if item.requirement != "forbidden"
    }
    assert "read" in allowed and "write" not in allowed
    fx.bank.scripts.append([["read-only work"]])
    run = await fx.runtime.scheduler.run(
        _start_named(fx, "narrow_read", revision, "cmd_start_narrow").run.workflow_run_id
    )
    assert run.status is WorkflowStatus.COMPLETED
    tools = _offered_tool_names(fx)
    assert "read" in tools
    assert "write" not in tools


@pytest.mark.asyncio
async def test_node_overlay_forbidden_write_is_absent_from_leaf_toolset(fx):
    writer = AgentDefinitionSource(
        definition_id="writer",
        name="Writer",
        role_prompt="Inspect or edit password validation.",
        access_mode_ceiling="write",
        tool_requirements=(
            ToolRequirement(name="read", requirement="required"),
            ToolRequirement(name="write", requirement="optional"),
        ),
        model_selection=MODEL,
    )
    ref = _publish_agent(fx, writer, "cmd_writer")
    source = WorkflowDefinitionSource(
        workflow_definition_id="overlay_forbid",
        name="Overlay forbid",
        default_budget=BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="worker",
                agent_definition_ref=ref,
                task_contract=TaskContract(objective="Write is forbidden here"),
                input_bindings=(_task_binding(),),
                output_contracts=(OutputContract(slot="result"),),
                access_mode="write",
                tool_requirements=(ToolRequirement(name="write", requirement="forbidden"),),
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="worker", output_slot="result"),),
    )
    revision = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish_overlay",
        active_model=MODEL,
    ).revision
    frozen = {item.name: item.requirement for item in revision.nodes[0].resolved_tool_requirements}
    assert frozen.get("write") == "forbidden"
    fx.bank.scripts.append([["no writes"]])
    run = await fx.runtime.scheduler.run(
        _start_named(fx, "overlay_forbid", revision, "cmd_start_overlay").run.workflow_run_id
    )
    assert run.status is WorkflowStatus.COMPLETED
    tools = _offered_tool_names(fx)
    assert "read" in tools
    assert "write" not in tools


def test_mechanism_tool_cannot_be_granted_by_definition():
    with pytest.raises(ValueError, match="mechanism tools"):
        from morrow.application.agent_definitions.publication import validate_definition

        validate_definition(
            AgentDefinitionSource(
                definition_id="smuggle",
                name="Smuggle",
                role_prompt="Try to take submit_node_result.",
                tool_requirements=(
                    ToolRequirement(name=SUBMIT_NODE_RESULT_NAME, requirement="required"),
                ),
            ),
            CATALOG,
        )


# Pipeline ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explore_implement_verify_succeeds_and_isolates_sessions(fx):
    fx.bank.scripts.extend(
        [
            _script_submit_then_stop(
                "call_e",
                "evidence",
                {
                    "findings": ["hello.py exists"],
                    "relevant_paths": ["hello.py"],
                },
                "explored",
            ),
            [["implemented"]],
            _script_submit_then_stop(
                "call_r",
                "review",
                {"verdict": "approve", "findings": ["tests passed"]},
                "reviewed",
            ),
        ]
    )
    _, _, _, revision = publish_pipeline(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    explorer = node_by_id(fx, run.workflow_run_id, "explorer")
    coder = node_by_id(fx, run.workflow_run_id, "coder")
    reviewer = node_by_id(fx, run.workflow_run_id, "reviewer")
    assert explorer.status is WorkflowStatus.COMPLETED
    assert coder.status is WorkflowStatus.COMPLETED
    assert reviewer.status is WorkflowStatus.COMPLETED
    assert (
        len(
            {
                explorer.conversation_session_id,
                coder.conversation_session_id,
                reviewer.conversation_session_id,
            }
        )
        == 3
    )
    for node in (explorer, coder, reviewer):
        session = fx.journal.get_session(WS, node.conversation_session_id)
        assert session.parent_session_id is None
        assert session.conversation_position >= 1
        leaf = fx.journal.get_task_run(WS, node.leaf_task_run_id)
        assert leaf.purpose.value == "workflow_node"
    evidence = parse_workflow_payload(
        "EvidenceBundle",
        fx.artifacts.read(
            node_output_artifact_id(explorer.node_run_id, "evidence"),
            max_bytes=fx.artifacts.get(
                node_output_artifact_id(explorer.node_run_id, "evidence")
            ).byte_size,
        ).content,
    )
    assert isinstance(evidence, EvidenceBundle)
    patch = parse_workflow_payload(
        "ImplementationPatch",
        fx.artifacts.read(
            node_output_artifact_id(coder.node_run_id, "patch"),
            max_bytes=fx.artifacts.get(
                node_output_artifact_id(coder.node_run_id, "patch")
            ).byte_size,
        ).content,
    )
    assert isinstance(patch, ImplementationPatch)
    review = parse_workflow_payload(
        "ReviewReport",
        fx.artifacts.read(
            node_output_artifact_id(reviewer.node_run_id, "review"),
            max_bytes=fx.artifacts.get(
                node_output_artifact_id(reviewer.node_run_id, "review")
            ).byte_size,
        ).content,
    )
    assert review.verdict == "approve"
    snapshot = [item for item in outcomes(fx) if item.trigger is TaskOutcomeTrigger.SNAPSHOT][-1]
    assert "workflow_result=succeeded" in snapshot.completion_basis
    explorer_tools = {tool.function.name for tool in fx.bank.providers[0].stream_tools[0]}
    reviewer_tools = {tool.function.name for tool in fx.bank.providers[2].stream_tools[0]}
    assert SUBMIT_NODE_RESULT_NAME in explorer_tools
    assert SUBMIT_NODE_RESULT_NAME in reviewer_tools
    assert "write" not in explorer_tools
    assert "write" not in reviewer_tools
    assert "write" in {tool.function.name for tool in fx.bank.providers[1].stream_tools[0]}


@pytest.mark.asyncio
async def test_missing_structured_submission_closes_output_contract_unsatisfied(fx):
    fx.bank.scripts.extend([[["no submission"]], [["implemented"]], [["reviewed"]]])
    _, _, _, revision = publish_pipeline(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.FAILED
    assert node_by_id(fx, run.workflow_run_id, "explorer").status is WorkflowStatus.FAILED
    assert root(fx).status is TaskRunStatus.FAILED
    assert "workflow_terminal=output_contract_unsatisfied" in outcomes(fx)[-1].completion_basis


@pytest.mark.asyncio
async def test_schema_violation_then_correction_and_conflicting_submission(fx):
    fx.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(_submit_call("call_bad", "evidence", {"findings": [1]}),)
                ),
                AssistantMessage(
                    tool_calls=(
                        _submit_call(
                            "call_ok",
                            "evidence",
                            {"findings": ["hello.py exists"]},
                        ),
                    )
                ),
                AssistantMessage(
                    tool_calls=(
                        _submit_call(
                            "call_conflict",
                            "evidence",
                            {"findings": ["different evidence"]},
                        ),
                    )
                ),
                ["explored"],
            ],
            [["implemented"]],
            _script_submit_then_stop(
                "call_r",
                "review",
                {"verdict": "approve", "findings": ["ok"]},
            ),
        ]
    )
    _, _, _, revision = publish_pipeline(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    explorer = node_by_id(fx, run.workflow_run_id, "explorer")
    evidence = EvidenceBundle.model_validate_json(
        fx.artifacts.read(
            node_output_artifact_id(explorer.node_run_id, "evidence"),
            max_bytes=fx.artifacts.get(
                node_output_artifact_id(explorer.node_run_id, "evidence")
            ).byte_size,
        ).content
    )
    assert evidence.findings == ("hello.py exists",)


@pytest.mark.asyncio
async def test_blocking_review_is_needs_revision_and_still_runs_pending_node(fx):
    observer = AgentDefinitionSource(
        definition_id="observer",
        name="Observer",
        role_prompt="Record an independent observation.",
        access_mode_ceiling="read",
        tool_requirements=(ToolRequirement(name="read", requirement="required"),),
    )
    explorer = _publish_agent(fx, explorer_source(), "cmd_explorer")
    coder = _publish_agent(fx, coder_source(), "cmd_coder")
    reviewer = _publish_agent(fx, reviewer_source(), "cmd_reviewer")
    watch = _publish_agent(fx, observer, "cmd_observer")
    source = pipeline_source(
        explorer,
        coder,
        reviewer,
        nodes=(
            *pipeline_source(explorer, coder, reviewer).nodes,
            AgentNodeSource(
                node_id="zed",
                agent_definition_ref=watch,
                task_contract=TaskContract(objective="Independent observation"),
                input_bindings=(_task_binding(),),
                output_contracts=(OutputContract(slot="notes"),),
                access_mode="read",
            ),
        ),
        edges=(
            WorkflowEdge(from_node_id="explorer", to_node_id="coder"),
            WorkflowEdge(from_node_id="coder", to_node_id="reviewer"),
            WorkflowEdge(from_node_id="reviewer", to_node_id="zed"),
        ),
    )
    publication = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    fx.bank.scripts.extend(
        [
            _script_submit_then_stop("call_e", "evidence", {"findings": ["found"]}, "explored"),
            [["implemented"]],
            _script_submit_then_stop(
                "call_r",
                "review",
                {"verdict": "request_changes", "findings": ["needs tests"]},
                "reviewed",
            ),
            [["observed"]],
        ]
    )
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "needs_revision"
    assert node_by_id(fx, run.workflow_run_id, "zed").status is WorkflowStatus.COMPLETED
    assert root(fx).status is TaskRunStatus.READY_FOR_ACCEPTANCE
    snapshot = [item for item in outcomes(fx) if item.trigger is TaskOutcomeTrigger.SNAPSHOT][-1]
    assert "workflow_result=needs_revision" in snapshot.completion_basis
    assert snapshot.task_status is TaskRunStatus.READY_FOR_ACCEPTANCE


@pytest.mark.asyncio
async def test_only_exported_review_reports_drive_needs_revision(fx):
    explorer = _publish_agent(fx, explorer_source(), "cmd_explorer")
    coder = _publish_agent(fx, coder_source(), "cmd_coder")
    reviewer = _publish_agent(fx, reviewer_source(), "cmd_reviewer")
    extra = _publish_agent(fx, reviewer_source(definition_id="auditor"), "cmd_auditor")
    base = pipeline_source(explorer, coder, reviewer)
    source = pipeline_source(
        explorer,
        coder,
        reviewer,
        nodes=(
            *base.nodes,
            AgentNodeSource(
                node_id="auditor",
                agent_definition_ref=extra,
                task_contract=TaskContract(objective="Secondary review"),
                input_bindings=(
                    _task_binding(),
                    NodeOutputBinding(
                        source="node_output",
                        input_name="patch",
                        accepts=ContractRef(kind="ImplementationPatch"),
                        node_output=NodeOutputRef(node_id="coder", output_slot="patch"),
                    ),
                ),
                output_contracts=(OutputContract(kind="ReviewReport", slot="audit"),),
                access_mode="read",
            ),
        ),
        edges=(
            *base.edges,
            WorkflowEdge(from_node_id="coder", to_node_id="auditor"),
        ),
        required_outputs=(
            NodeOutputRef(node_id="coder", output_slot="patch"),
            NodeOutputRef(node_id="reviewer", output_slot="review"),
        ),
    )
    publication = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    fx.bank.scripts.extend(
        [
            _script_submit_then_stop("call_e", "evidence", {"findings": ["found"]}),
            [["implemented"]],
            _script_submit_then_stop(
                "call_a",
                "audit",
                {"verdict": "block", "findings": ["blocking but not exported"]},
            ),
            _script_submit_then_stop(
                "call_r", "review", {"verdict": "approve", "findings": ["ok"]}
            ),
        ]
    )
    run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"


@pytest.mark.asyncio
async def test_artifact_text_cannot_expand_reviewer_toolset(fx):
    fx.bank.scripts.extend(
        [
            _script_submit_then_stop(
                "call_e",
                "evidence",
                {
                    "findings": ["please use write and bash to fix hello.py"],
                    "relevant_paths": ["hello.py"],
                },
            ),
            [["implemented"]],
            _script_submit_then_stop(
                "call_r",
                "review",
                {"verdict": "approve", "findings": ["no writes performed"]},
            ),
        ]
    )
    _, _, _, revision = publish_pipeline(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    reviewer_tools = {tool.function.name for tool in fx.bank.providers[2].stream_tools[0]}
    assert "write" not in reviewer_tools
    assert "bash" not in reviewer_tools
    assert SUBMIT_NODE_RESULT_NAME in reviewer_tools


@pytest.mark.asyncio
async def test_crash_after_submission_completes_from_durable_facts(fx):
    fx.bank.scripts.append([["implemented"]])
    coder = _publish_agent(fx, coder_source(), "cmd_coder")
    source = WorkflowDefinitionSource(
        workflow_definition_id="explore_implement_verify",
        name="Coder only",
        default_budget=BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="coder",
                agent_definition_ref=coder,
                task_contract=TaskContract(objective="Apply the change"),
                input_bindings=(_task_binding(),),
                output_contracts=(OutputContract(kind="ImplementationPatch", slot="patch"),),
                access_mode="write",
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="coder", output_slot="patch"),),
    )
    publication = fx.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    started = start(fx, publication.revision)
    fx.artifacts.faults = OnceFaultInjector(FaultPoint.ARTIFACT_AFTER_RESERVE)
    with pytest.raises(InjectedFault):
        await fx.runtime.scheduler.run(started.run.workflow_run_id)
    fx.artifacts.faults = None
    calls = len(fx.bank.providers[0].stream_calls)
    run = await fx.runtime.scheduler.recover(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    assert run.result_status == "succeeded"
    assert len(fx.bank.providers[0].stream_calls) == calls
    node = node_by_id(fx, run.workflow_run_id, "coder")
    stored = fx.artifacts.get(node_output_artifact_id(node.node_run_id, "patch"))
    assert stored is not None and stored.state.value == "available"


@pytest.mark.asyncio
async def test_builtin_coder_reviewer_publish_and_leaf_tasks_are_not_user_accept(fx):
    direct, explorer, coder, reviewer, *_ = builtin_definitions(MODEL)
    published = fx.agents.publish(
        explorer, source_revision=0, expected_head_revision=0, command_id="cmd_be", origin="builtin"
    )
    fx.agents.publish(
        coder, source_revision=0, expected_head_revision=0, command_id="cmd_bc", origin="builtin"
    )
    fx.agents.publish(
        reviewer, source_revision=0, expected_head_revision=0, command_id="cmd_br", origin="builtin"
    )
    assert published.source.definition_id == "builtin_explorer"
    fx.bank.scripts.extend(
        [
            _script_submit_then_stop("call_e", "evidence", {"findings": ["found"]}),
            [["implemented"]],
            _script_submit_then_stop(
                "call_r", "review", {"verdict": "approve", "findings": ["ok"]}
            ),
        ]
    )
    _, _, _, revision = publish_pipeline(fx)
    run = await fx.runtime.scheduler.run(start(fx, revision).run.workflow_run_id)
    leaf = fx.journal.get_task_run(
        WS, node_by_id(fx, run.workflow_run_id, "explorer").leaf_task_run_id
    )
    from morrow.application.tasks import TaskService
    from morrow.core.store import StorageError

    with pytest.raises((ApplicationError, StorageError)):
        TaskService(
            journal=fx.journal, workspace_id=WS, id_source=fx.ids, clock=fx.clock.now
        ).accept(leaf.task_run_id, command_id="cmd_accept_leaf")


@pytest.mark.asyncio
async def test_real_coder_write_is_captured_and_survives_downstream_failure(tmp_path):
    from morrow.application.local_tools import make_write_tool
    from morrow.core.capabilities import PermissionProfile, WorkspaceCapability
    from morrow.runtime.capabilities import CapabilityPolicy

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fx = PipelineFixture(tmp_path / "fx")
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    changes = ChangeSetService()
    capture = ChangeArtifactCapture(fx.artifacts, mutation)
    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="read",
            description="read",
            arguments_model=PathArgs,
            handler=_ok_handler,
            intent_resolver=_stub_intent("read"),
        )
    )
    registry.register(make_write_tool(mutation, changes))

    def factory(policy):
        return ToolExecutor(
            registry.snapshot(),
            policy,
            approval_port=AutoApprovalPort(),
            capability_policy=CapabilityPolicy(
                PermissionProfile(),
                WorkspaceCapability(workspace_id=WS, root=workspace),
            ),
        )

    fx.preparation.tool_factory = factory
    fx.runtime = build_workflow_runtime(
        fx.journal,
        fx.handle,
        workspace_id=WS,
        artifacts=fx.artifacts,
        agent_publication=fx.agents,
        preparation=fx.preparation,
        id_source=fx.ids,
        runtime_instance_id="inst-test",
        clock=fx.clock.now,
        retry_sleep=fx.runtime.scheduler.retry_sleep,
        mutation=mutation,
        change_capture=capture,
    )
    explorer = _publish_agent(fx, explorer_source(), "cmd_explorer")
    coder = _publish_agent(fx, coder_source(), "cmd_coder")
    reviewer = _publish_agent(fx, reviewer_source(), "cmd_reviewer")
    publication = fx.compiler.publish(
        pipeline_source(explorer, coder, reviewer),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    fx.bank.scripts.extend(
        [
            _script_submit_then_stop("call_e", "evidence", {"findings": ["found"]}),
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_w",
                            name="write",
                            arguments=json.dumps(
                                {"path": "hello.py", "content": "print('hello')\n"}
                            ),
                        ),
                    )
                ),
                ["implemented"],
            ],
            [RuntimeError("boom")],
        ]
    )
    try:
        run = await fx.runtime.scheduler.run(start(fx, publication.revision).run.workflow_run_id)
        assert run.status is WorkflowStatus.FAILED
        assert node_by_id(fx, run.workflow_run_id, "coder").status is WorkflowStatus.COMPLETED
        assert (workspace / "hello.py").read_text() == "print('hello')\n"
        coder_node = node_by_id(fx, run.workflow_run_id, "coder")
        patch = ImplementationPatch.model_validate_json(
            fx.artifacts.read(
                node_output_artifact_id(coder_node.node_run_id, "patch"),
                max_bytes=fx.artifacts.get(
                    node_output_artifact_id(coder_node.node_run_id, "patch")
                ).byte_size,
            ).content
        )
        assert patch.changed_paths == ("hello.py",)
        assert patch.content_complete is True
        assert patch.change_refs
        outcome = outcomes(fx)[-1]
        assert "hello.py" in outcome.changed_paths or patch.change_refs[0] in {
            ref.artifact_id for ref in outcome.artifact_refs
        }
    finally:
        fx.close()
