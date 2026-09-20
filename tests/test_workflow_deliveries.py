"""Real Workflow delivery: registered files, readable answers and evidence.

Reproduces the three delivered gaps first -- a TextResult contract presented as
a delivered file, a ChangeCapture diff record presented as file content, and a
flattened result body -- then pins the v1/v2 submission protocol and the
ordinary-file delivery contract. Scripted Providers only; no wall-clock sleeps.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.local_tools import make_write_tool
from morrow.application.result_presentation import TaskResultProjector
from morrow.application.task_artifacts import TaskArtifactsService
from morrow.application.timeline_index import TimelineIndexService
from morrow.application.workflows.capture import ChangeArtifactCapture
from morrow.application.workflows.composition import build_workflow_runtime
from morrow.application.workflows.deliveries import (
    DeliveryError,
    WorkflowDeliveryReader,
    read_submission_marker,
    resolve_run_deliveries,
)
from morrow.application.workflows.start import StartWorkflowCommand
from morrow.application.workflows.submit import SubmitNodeResultArgumentsV2
from morrow.core.artifacts import ArtifactError, ArtifactErrorCode, ArtifactKind
from morrow.core.capabilities import PermissionProfile, WorkspaceCapability
from morrow.core.domain import DurableSession, DurableTaskRun, sha256_digest
from morrow.core.faults import FaultPoint, InjectedFault, OnceFaultInjector
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.core.workflows.contracts import (
    DeliverableRequest,
    ImplementationPatch,
    NodeOutputRef,
    OutputContract,
    TaskContract,
    node_output_artifact_id,
)
from morrow.core.workflows.definitions import AgentNodeSource, WorkflowDefinitionSource
from morrow.core.workflows.runs import WorkflowStatus
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import (
    ToolErrorCode,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
    make_tool,
)
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.testing import FixedIdSource
from test_stage7_isolated_workflow_slice import MODEL, WS, SliceFixture, publish, start
from test_stage7_multi_agent_pipeline import (
    BUDGET,
    AutoApprovalPort,
    PathArgs,
    PipelineFixture,
    _leaf_hooks,
    _ok_handler,
    _publish_agent,
    _stub_intent,
    _task_binding,
    coder_source,
)


def make_projector(fixture) -> TaskResultProjector:
    """The projector exactly as server composition wires it."""

    service = TaskArtifactsService(
        fixture.journal,
        artifacts=fixture.artifacts,
        workflow_queries=fixture.runtime.queries,
        workspace_id=WS,
    )
    return TaskResultProjector(
        fixture.journal, service, artifacts=fixture.artifacts, workspace_id=WS
    )


def result_of(fixture, session_id: str = "ses_root") -> dict:
    index = TimelineIndexService(fixture.journal, WS, result_projector=make_projector(fixture))
    items = [
        item
        for item in index.snapshot_page(session_id, limit=50)["items"]
        if item["kind"] == "result"
    ]
    assert len(items) == 1
    return items[0]["result"]


def artifact_bytes(fixture, artifact_id: str) -> bytes:
    stored = fixture.artifacts.get(artifact_id)
    assert stored is not None and stored.state.value == "available"
    return fixture.artifacts.read(artifact_id, max_bytes=stored.byte_size).content


def build_write_fixture(tmp_path, *, scripts) -> tuple[PipelineFixture, object]:
    """One PipelineFixture whose leaf really writes files and captures changes."""

    fixture = PipelineFixture(tmp_path, scripts=scripts)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    mutation = WorkspaceMutationService(files, artifact_capture=True)
    capture = ChangeArtifactCapture(fixture.artifacts, mutation)
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
    registry.register(make_write_tool(mutation, ChangeSetService()))

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

    fixture.preparation.tool_factory = factory
    fixture.runtime = build_workflow_runtime(
        fixture.journal,
        fixture.handle,
        workspace_id=WS,
        artifacts=fixture.artifacts,
        agent_publication=fixture.agents,
        preparation=fixture.preparation,
        id_source=fixture.ids,
        runtime_instance_id="inst-test",
        clock=fixture.clock.now,
        retry_sleep=fixture.runtime.scheduler.retry_sleep,
        mutation=mutation,
        change_capture=capture,
    )
    return fixture, workspace


def publish_coder_only(fixture: PipelineFixture):
    coder = _publish_agent(fixture, coder_source(), "cmd_coder")
    source = WorkflowDefinitionSource(
        workflow_definition_id="coder_only",
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
    return fixture.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    ).revision


def start_coder(fixture: PipelineFixture, revision):
    root = fixture.journal.get_task_run(WS, "task_root")
    return fixture.runtime.start.start(
        StartWorkflowCommand(
            workflow_definition_id="coder_only",
            workflow_revision_id=revision.workflow_revision_id,
            session_id="ses_root",
            root_task_run_id="task_root",
            expected_root_row_version=root.row_version,
            contract=TaskContract(objective="Implement the fix"),
            command_id="cmd_start",
        )
    )


def node_of(fixture, run, node_id: str):
    return next(
        node
        for node in fixture.journal.workflows.list_nodes(WS, run.workflow_run_id)
        if node.node_id == node_id
    )


# Reproduction of the delivered gaps ------------------------------------------------


@pytest.mark.asyncio
async def test_text_result_keeps_markdown_layout_and_adds_no_pseudo_file(tmp_path):
    """A1: the answer keeps its own layout and is not a file named after its slot."""

    answer = "# 标题\n\n第一行\n第二行\n"
    fixture = SliceFixture(tmp_path, scripts=[[[answer]]])
    try:
        _, revision = publish(fixture)
        run = await fixture.runtime.scheduler.run(start(fixture, revision).run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        result = result_of(fixture)
        assert result["body"]["text"] == answer
        assert result["body"]["truncated"] is False
        assert result["files"] == []
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_long_text_result_reads_its_own_record_not_the_slot_payload(tmp_path):
    """A1: a long answer is not presented as a bounded excerpt-only file."""

    answer = "".join(f"line {number:04d}\n" for number in range(600))  # 6600 chars
    fixture = SliceFixture(tmp_path, scripts=[[[answer]]])
    try:
        _, revision = publish(fixture)
        run = await fixture.runtime.scheduler.run(start(fixture, revision).run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        result = result_of(fixture)
        assert len(answer) > 4096
        assert result["body"]["text"] == answer
        assert result["files"] == []
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_change_capture_stays_evidence_not_a_delivered_file(tmp_path):
    """A2/A3: the diff record is evidence; only real snapshots are files."""

    fixture, workspace = build_write_fixture(
        tmp_path,
        scripts=[
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
            ]
        ],
    )
    try:
        revision = publish_coder_only(fixture)
        run = await fixture.runtime.scheduler.run(
            start_coder(fixture, revision).run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        assert (workspace / "hello.py").read_text() == "print('hello')\n"
        node = node_of(fixture, run, "coder")
        patch_id = node_output_artifact_id(node.node_run_id, "patch")
        patch = ImplementationPatch.model_validate_json(artifact_bytes(fixture, patch_id))
        assert patch.changed_paths == ("hello.py",)
        assert patch.change_refs, "the real write must be captured"

        result = result_of(fixture)
        capture_ids = set(patch.change_refs)
        assert not ({entry["artifact_id"] for entry in result["files"]} & capture_ids), (
            "a ChangeCapture record is not the delivered file"
        )
        evidence = [
            entry
            for entry in result["files"]
            if entry.get("path") == "hello.py" and entry["role"] == "changed"
        ]
        assert evidence, "the changed path must stay visible as execution evidence"
        assert evidence[0]["source"] == "task_evidence"
    finally:
        fixture.close()


# Explicit delivery registration (submission protocol v2) -----------------------------


def delivery_harness(tmp_path):
    """Artifacts plus one coder node's hooks over a real empty workspace."""

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
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    hooks = _leaf_hooks(artifacts)
    hooks.context = dataclasses.replace(hooks.context, submission_protocol_version=2)
    hooks.workspace_root = workspace
    return handle, artifacts, workspace, hooks, store


def register(hooks, path: str = "hello.py", slot: str = "patch", label: str | None = None):
    return hooks.submit_node_result(
        SubmitNodeResultArgumentsV2(
            deliverables={slot: (DeliverableRequest(path=path, label=label),)}
        ),
        call_id=None,
    )


def snapshots(artifacts) -> list:
    return [
        item
        for item in artifacts.journal.list_artifacts(WS, task_run_id="task_1")
        if item.kind is ArtifactKind.DELIVERABLE
    ]


def test_registration_publishes_a_byte_exact_delivery_snapshot(tmp_path):
    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "hello.py").write_text("print('hello')\n")
    try:
        receipt = register(hooks, label="实现文件")
        assert receipt["submitted"] is True and receipt["reused"] is False
        marker = read_submission_marker(artifacts, node_run_id="nrun_1")
        assert marker is not None and marker.schema_version == 2
        descriptor = marker.deliverables[0]
        assert (descriptor.slot, descriptor.path, descriptor.name) == (
            "patch",
            "hello.py",
            "hello.py",
        )
        assert descriptor.mime.startswith("text/x-python")
        assert descriptor.byte_size == len("print('hello')\n")
        assert descriptor.sha256 == sha256_digest(b"print('hello')\n")
        assert descriptor.resources == () and descriptor.limitations == ()
        stored = artifacts.get(descriptor.artifact_id)
        assert stored.kind is ArtifactKind.DELIVERABLE
        assert stored.producer_node_run_id is None and stored.output_slot is None
        assert stored.session_id == "ses_1" and stored.task_run_id == "task_1"
        assert (
            artifacts.read(descriptor.artifact_id, max_bytes=descriptor.byte_size).content
            == b"print('hello')\n"
        )
        # The tool receipt stays bounded: descriptors and refs, never content.
        [entry] = receipt["deliverables"]
        assert entry["artifact_id"] == descriptor.artifact_id
        assert set(entry) == {"slot", "path", "name", "mime", "artifact_id", "byte_size"}
    finally:
        handle.close()


def test_identical_retry_reuses_the_original_snapshot_without_reading_disk(tmp_path):
    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    target = workspace / "hello.py"
    target.write_text("one\n")
    try:
        first = register(hooks)
        target.write_text("two\n")
        second = register(hooks)
        assert second["reused"] is True
        assert second["deliverables"] == first["deliverables"]
        artifact_id = first["deliverables"][0]["artifact_id"]
        assert artifacts.read(artifact_id, max_bytes=8).content == b"one\n"
        target.unlink()
        third = register(hooks)
        assert third["reused"] is True
        assert third["deliverables"] == first["deliverables"]
        assert len(snapshots(artifacts)) == 1
    finally:
        handle.close()


def test_a_different_registration_is_a_conflict_not_an_overwrite(tmp_path):
    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "hello.py").write_text("one\n")
    (workspace / "other.py").write_text("two\n")
    try:
        register(hooks)
        with pytest.raises(ToolExecutionError) as exc:
            register(hooks, path="other.py")
        assert exc.value.code is ToolErrorCode.CONFLICT
        marker = read_submission_marker(artifacts, node_run_id="nrun_1")
        assert marker.deliverables[0].path == "hello.py"
    finally:
        handle.close()


def test_reader_rejects_escapes_directories_symlinks_and_missing_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sub").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (workspace / "link.txt").symlink_to(outside)
    reader = WorkflowDeliveryReader(workspace, workspace_id=WS)
    for path in ("link.txt", "sub", "missing.txt"):
        with pytest.raises(DeliveryError):
            reader.read_all({"patch": (DeliverableRequest(path=path),)})
    for path in ("../outside.txt", ".", "/etc/passwd", "a/../b"):
        with pytest.raises(ValueError):
            DeliverableRequest(path=path)


def test_secret_shaped_file_is_refused_without_a_half_delivery(tmp_path):
    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "leak.py").write_text('api_key = "sk-' + "x" * 24 + '"\n')
    try:
        with pytest.raises(ToolExecutionError) as exc:
            register(hooks, path="leak.py")
        assert exc.value.code is ToolErrorCode.PUBLISH_FAILED
        assert read_submission_marker(artifacts, node_run_id="nrun_1") is None
        assert snapshots(artifacts) == []
    finally:
        handle.close()


def test_interrupted_marker_write_exposes_no_half_delivery_and_retries(tmp_path, monkeypatch):
    import morrow.application.workflows.leaf as leaf_module

    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "hello.py").write_text("one\n")

    def fail(*_args, **_kwargs):
        raise DeliveryError("injected marker failure")

    try:
        monkeypatch.setattr(leaf_module, "write_submission_marker", fail)
        with pytest.raises(ToolExecutionError) as exc:
            register(hooks)
        assert exc.value.code is ToolErrorCode.PUBLISH_FAILED
        assert read_submission_marker(artifacts, node_run_id="nrun_1") is None
        monkeypatch.undo()
        receipt = register(hooks)
        assert receipt["reused"] is False
        assert read_submission_marker(artifacts, node_run_id="nrun_1") is not None
        # The recovered request reuses the snapshot published before the crash.
        assert len(snapshots(artifacts)) == 1
    finally:
        handle.close()


def test_html_delivery_captures_bounded_static_local_resources(tmp_path):
    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "index.html").write_text(
        '<link rel="stylesheet" href="style.css"><script src="app.js"></script>'
        '<img src="https://cdn.example.test/remote.png">'
    )
    (workspace / "style.css").write_text("body{color:red}")
    (workspace / "app.js").write_text("console.log('hi')")
    try:
        register(hooks, path="index.html")
        marker = read_submission_marker(artifacts, node_run_id="nrun_1")
        [entry] = marker.deliverables
        assert entry.path == "index.html"
        assert entry.mime.startswith("text/html")
        assert {item.path for item in entry.resources} == {"style.css", "app.js"}
        for resource in entry.resources:
            content = artifacts.read(resource.artifact_id, max_bytes=resource.byte_size).content
            assert sha256_digest(content) == resource.sha256
        assert entry.limitations == ()
    finally:
        handle.close()


@pytest.mark.asyncio
async def test_workflow_leaf_registers_a_delivery_through_the_submit_tool(tmp_path):
    """The registered file reaches the marker through the real tool call path."""

    fixture, workspace = build_write_fixture(
        tmp_path,
        scripts=[
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
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_s",
                            name="submit_node_result",
                            arguments=json.dumps(
                                {
                                    "deliverables": {
                                        "patch": [{"path": "hello.py", "label": "实现文件"}]
                                    }
                                }
                            ),
                        ),
                    )
                ),
                ["implemented"],
            ]
        ],
    )
    try:
        revision = publish_coder_only(fixture)
        run = await fixture.runtime.scheduler.run(
            start_coder(fixture, revision).run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        node = node_of(fixture, run, "coder")
        marker = read_submission_marker(fixture.artifacts, node_run_id=node.node_run_id)
        assert marker is not None and marker.schema_version == 2
        [descriptor] = marker.deliverables
        assert descriptor.path == "hello.py"
        assert descriptor.slot == "patch"
        # The model-provided label survives into the descriptor and the
        # chat entry, while the real file name keeps driving downloads.
        assert descriptor.label == "实现文件"
        assert result_of(fixture)["files"][0]["label"] == "实现文件"
        assert artifact_bytes(fixture, descriptor.artifact_id) == b"print('hello')\n"

        # The submitting ToolExecution durably owns the delivered bytes:
        # Doctor and Cleanup see a reference, never a JSON id alone.
        from morrow.application.cleanup import ArtifactCleanupService
        from morrow.application.doctor import OperationalDoctor

        referenced = {row[0] for row in fixture.journal.list_artifact_references(WS)}
        assert descriptor.artifact_id in referenced
        report = OperationalDoctor(fixture.store).inspect(WS)
        assert "artifact_managed_unreferenced" not in {issue.code for issue in report.issues}
        cleanup = ArtifactCleanupService(fixture.artifacts)
        assert cleanup.run(dry_run=True).eligible == 0
        assert cleanup.run(dry_run=False).removed == 0
        assert artifact_bytes(fixture, descriptor.artifact_id) == b"print('hello')\n"

        # The chat result shows the real registered file, not a contract payload.
        result = result_of(fixture)
        [entry] = result["files"]
        assert (entry["role"], entry["kind"], entry["source"]) == (
            "delivery",
            "deliverable",
            "workflow_delivery",
        )
        assert (entry["path"], entry["name"]) == ("hello.py", "hello.py")
        assert entry["mime"].startswith("text/x-python")
        assert entry["output_slot"] == "patch" and entry["inherited"] is False

        # Content and download describe exactly the delivered bytes and name.
        service = task_artifacts_for(fixture)
        content = service.read_content(
            "ses_root", descriptor.artifact_id, workflow_run_id=run.workflow_run_id
        )
        assert content["name"] == "hello.py" and content["path"] == "hello.py"
        assert content["preview"] == "text" and content["content_kind"] == "text"
        assert content["content"] == "print('hello')\n"
        data, name, mime = service.download(
            "ses_root", descriptor.artifact_id, workflow_run_id=run.workflow_run_id
        )
        assert (data, name, mime) == (b"print('hello')\n", "hello.py", "text/x-python")
    finally:
        fixture.close()


# Frozen protocol selection and failure windows ---------------------------------------


V1_TEXT_RESULT_TOOL_SCHEMA_DIGEST = (
    "60d23ff3ae6ca7a1b171773e9db3d58edcf2ee830588e269a59ee4813f673dd5"
)


def submit_schema_from(provider) -> dict:
    tools = provider.stream_tools[0]
    submit = next(tool for tool in tools if tool.function.name == "submit_node_result")
    return submit.function.parameters


@pytest.mark.asyncio
async def test_live_revision_advertises_registration_to_the_node(tmp_path):
    fixture = SliceFixture(tmp_path, scripts=[[["answer"]]])
    try:
        _, revision = publish(fixture)
        assert revision.submission_protocol_version == 2
        await fixture.runtime.scheduler.run(start(fixture, revision).run.workflow_run_id)
        schema = submit_schema_from(fixture.bank.providers[0])
        assert "deliverables" in schema["properties"]
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_legacy_revision_recovers_with_its_frozen_v1_submit_schema(tmp_path):
    """A revision stored before v2 keeps v1: recovery rebuilds the exact schema."""

    from morrow.application.agent_runs.preparation import tool_schema_digest

    fixture = SliceFixture(tmp_path, scripts=[[["recovered ", "answer"]]])
    try:
        _, revision = publish(fixture)

        def drop_protocol(_txn):
            from morrow.core.workflows.definitions import (
                WorkflowRevision,
                legacy_compiled_content_hash,
            )

            body = json.loads(revision.model_dump_json())
            body.pop("submission_protocol_version")
            # A pre-upgrade revision stored exactly this body and the hash of
            # the field-less content, so reconstruct both.
            body["content_hash"] = legacy_compiled_content_hash(
                WorkflowRevision.model_construct(**body)
            )
            fixture.journal._backend.executor().execute(
                "UPDATE workflow_revisions SET body_json=? WHERE workflow_revision_id=?",
                (json.dumps(body), revision.workflow_revision_id),
            )

        fixture.journal.transact(drop_protocol)
        legacy = fixture.journal.workflows.get_revision(WS, revision.workflow_revision_id)
        assert legacy.submission_protocol_version == 1

        started = start(fixture, revision)
        fixture.artifacts.faults = OnceFaultInjector(FaultPoint.ARTIFACT_AFTER_RESERVE)
        with pytest.raises(InjectedFault):
            await fixture.runtime.scheduler.run(started.run.workflow_run_id)
        fixture.artifacts.faults = None

        schema = submit_schema_from(fixture.bank.providers[0])
        assert "deliverables" not in schema["properties"]
        tools = fixture.bank.providers[0].stream_tools[0]
        submit = next(tool for tool in tools if tool.function.name == "submit_node_result")
        assert tool_schema_digest((submit,)) == V1_TEXT_RESULT_TOOL_SCHEMA_DIGEST

        run = await fixture.runtime.scheduler.recover(started.run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        assert len(fixture.bank.providers[0].stream_calls) == 1
    finally:
        fixture.close()


def test_partial_snapshot_failure_resumes_without_mixing_attempts(tmp_path):
    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "one.txt").write_text("one\n")
    (workspace / "two.txt").write_text("two\n")
    original = artifacts.publish_bytes
    calls = {"count": 0}

    def flaky(content, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise ArtifactError(ArtifactErrorCode.UNAVAILABLE, "injected snapshot failure")
        return original(content, **kwargs)

    arguments = SubmitNodeResultArgumentsV2(
        deliverables={
            "patch": (
                DeliverableRequest(path="one.txt"),
                DeliverableRequest(path="two.txt"),
            )
        }
    )
    try:
        artifacts.publish_bytes = flaky
        with pytest.raises(ToolExecutionError) as exc:
            hooks.submit_node_result(arguments, call_id=None)
        assert exc.value.code is ToolErrorCode.PUBLISH_FAILED
        assert read_submission_marker(artifacts, node_run_id="nrun_1") is None
        assert len(snapshots(artifacts)) == 1

        artifacts.publish_bytes = original
        receipt = hooks.submit_node_result(arguments, call_id=None)
        assert receipt["reused"] is False
        marker = read_submission_marker(artifacts, node_run_id="nrun_1")
        assert [item.path for item in marker.deliverables] == ["one.txt", "two.txt"]
        assert len(snapshots(artifacts)) == 2
        for item in marker.deliverables:
            content = artifacts.read(item.artifact_id, max_bytes=item.byte_size).content
            assert content == (workspace / item.path).read_bytes()
    finally:
        artifacts.publish_bytes = original
        handle.close()


# Exported slots, inherited deliveries and trust boundaries ---------------------------


def publish_two_slot_coder(fixture, *, export: str):
    """One coder node that declares a patch and a tests slot, exporting one."""

    coder = _publish_agent(fixture, coder_source(), "cmd_coder")
    source = WorkflowDefinitionSource(
        workflow_definition_id="coder_only",
        name="Coder only",
        default_budget=BUDGET,
        nodes=(
            AgentNodeSource(
                node_id="coder",
                agent_definition_ref=coder,
                task_contract=TaskContract(objective="Apply the change"),
                input_bindings=(_task_binding(),),
                output_contracts=(
                    OutputContract(kind="ImplementationPatch", slot="patch"),
                    OutputContract(kind="TestReport", slot="tests"),
                ),
                access_mode="write",
            ),
        ),
        required_outputs=(NodeOutputRef(node_id="coder", output_slot=export),),
    )
    return fixture.compiler.publish(
        source,
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    ).revision


def coder_delivery_script(*, deliver_slot: str, path: str = "hello.py"):
    return [
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(
                    id="call_w",
                    name="write",
                    arguments=json.dumps({"path": path, "content": "print('hello')\n"}),
                ),
            )
        ),
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(
                    id="call_s",
                    name="submit_node_result",
                    arguments=json.dumps({"deliverables": {deliver_slot: [{"path": path}]}}),
                ),
            )
        ),
        ["implemented"],
    ]


def task_artifacts_for(fixture) -> TaskArtifactsService:
    return TaskArtifactsService(
        fixture.journal,
        artifacts=fixture.artifacts,
        workflow_queries=fixture.runtime.queries,
        workspace_id=WS,
    )


@pytest.mark.asyncio
async def test_registered_files_outside_the_exported_slot_are_not_run_deliveries(tmp_path):
    fixture, workspace = build_write_fixture(
        tmp_path, scripts=[coder_delivery_script(deliver_slot="tests")]
    )
    try:
        revision = publish_two_slot_coder(fixture, export="patch")
        run = await fixture.runtime.scheduler.run(
            start_coder(fixture, revision).run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        node = node_of(fixture, run, "coder")
        marker = read_submission_marker(fixture.artifacts, node_run_id=node.node_run_id)
        assert [item.slot for item in marker.deliverables] == ["tests"]

        view = fixture.runtime.queries.get_run_view(run.workflow_run_id)
        assert resolve_run_deliveries(view, artifacts=fixture.artifacts) == ()

        # The registered file is still visible as authorized execution detail.
        detail = task_artifacts_for(fixture).view("ses_root", workflow_run_id=run.workflow_run_id)
        assert marker.deliverables[0].artifact_id in {item.artifact_id for item in detail.artifacts}
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_exported_slot_resolves_its_registered_delivery(tmp_path):
    fixture, workspace = build_write_fixture(
        tmp_path, scripts=[coder_delivery_script(deliver_slot="tests")]
    )
    try:
        revision = publish_two_slot_coder(fixture, export="tests")
        run = await fixture.runtime.scheduler.run(
            start_coder(fixture, revision).run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        view = fixture.runtime.queries.get_run_view(run.workflow_run_id)
        deliveries = resolve_run_deliveries(view, artifacts=fixture.artifacts)
        assert [
            (item.node_id, item.output_slot, item.inherited, item.descriptor.path)
            for item in deliveries
        ] == [("coder", "tests", False, "hello.py")]
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_inherited_delivery_stays_reachable_through_a_continuation(tmp_path):
    """A continuation exports the source node's registered file, not a rerun."""

    from morrow.core.workflows.contracts import NodeOutputRef
    from test_stage7_serial_scheduler import (
        DagFixture,
        ReadArgs,
        ScriptBank,
        WriteArgs,
        _read_handler,
        _write_handler,
        node_by_id,
        pair_source,
    )
    from test_stage7_serial_scheduler import publish as publish_dag
    from test_stage7_serial_scheduler import start as start_dag
    from test_stage8_patch_continuation import _patch

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cell = {}

    def pause_at_second(count):
        if count == 2:
            cell["fx"].runtime.transitions.request_pause(cell["run_id"])

    fixture = DagFixture(tmp_path / "fx", bank=ScriptBank(on_create=pause_at_second))
    cell["fx"] = fixture

    def factory(policy):
        # A capability policy requires audited tools; the shared DagFixture
        # stubs carry no intent resolver, so register equivalent ones here.
        registry = ToolRegistry()
        registry.register(
            make_tool(
                name="read",
                description="Read",
                arguments_model=ReadArgs,
                handler=_read_handler,
                intent_resolver=_stub_intent("read"),
            )
        )
        registry.register(
            make_tool(
                name="write",
                description="Write",
                arguments_model=WriteArgs,
                handler=_write_handler,
                intent_resolver=_stub_intent("write"),
            )
        )
        return ToolExecutor(
            registry.snapshot(),
            policy,
            approval_port=AutoApprovalPort(),
            capability_policy=CapabilityPolicy(
                PermissionProfile(),
                WorkspaceCapability(workspace_id=WS, root=workspace),
            ),
        )

    fixture.preparation.tool_factory = factory
    (workspace / "survey.md").write_text("# survey\n")
    fixture.bank.scripts.extend(
        [
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_g",
                            name="submit_node_result",
                            arguments=json.dumps(
                                {"deliverables": {"result": [{"path": "survey.md"}]}}
                            ),
                        ),
                    )
                ),
                ["phase one"],
            ],
            ["unused"],
            ["phase three concluded"],
        ]
    )
    try:
        _, publication = publish_dag(fixture, pair_source)
        started = start_dag(fixture, publication.revision)
        cell["run_id"] = started.run.workflow_run_id
        paused = await fixture.runtime.scheduler.run(cell["run_id"])
        assert paused.status is WorkflowStatus.PAUSED
        gamma = node_by_id(fixture, cell["run_id"], "gamma")
        parent_marker = read_submission_marker(fixture.artifacts, node_run_id=gamma.node_run_id)
        assert parent_marker is not None and len(parent_marker.deliverables) == 1

        base = publication.revision
        ref = base.nodes[0].agent_definition_ref
        source = pair_source(ref).model_copy(
            update={"required_outputs": (NodeOutputRef(node_id="gamma", output_slot="result"),)}
        )
        applied = fixture.runtime.patches.apply(
            _patch(paused, base, source=source), active_model=MODEL
        )
        child = applied.child
        assert child is not None
        completed = await fixture.runtime.scheduler.run(child.workflow_run_id)
        assert completed.status is WorkflowStatus.COMPLETED

        view = fixture.runtime.queries.get_run_view(child.workflow_run_id)
        deliveries = resolve_run_deliveries(view, artifacts=fixture.artifacts)
        assert [
            (item.node_id, item.output_slot, item.inherited, item.descriptor.path)
            for item in deliveries
        ] == [("gamma", "result", True, "survey.md")]

        artifact_id = deliveries[0].descriptor.artifact_id
        service = task_artifacts_for(fixture)
        child_view = service.view("ses_root", workflow_run_id=child.workflow_run_id)
        assert artifact_id in {item.artifact_id for item in child_view.artifacts}
        assert (
            service.read_content("ses_root", artifact_id, workflow_run_id=child.workflow_run_id)[
                "content"
            ]
            == "# survey\n"
        )
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_forged_marker_never_authorizes_a_foreign_snapshot(tmp_path):
    from morrow.core.application import ApplicationError
    from morrow.core.workflows.contracts import (
        DeliveryDescriptor,
        NodeSubmissionMarker,
        node_submission_artifact_id,
    )

    fixture, workspace = build_write_fixture(
        tmp_path,
        scripts=[
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
            ]
        ],
    )
    try:
        revision = publish_coder_only(fixture)
        run = await fixture.runtime.scheduler.run(
            start_coder(fixture, revision).run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        node = node_of(fixture, run, "coder")
        assert read_submission_marker(fixture.artifacts, node_run_id=node.node_run_id) is None

        fixture.journal.create_session(
            DurableSession(session_id="ses_other", workspace_id=WS),
            task=DurableTaskRun(task_run_id="task_other", session_id="ses_other", workspace_id=WS),
        )
        foreign = fixture.artifacts.publish_bytes(
            b"foreign bytes",
            kind=ArtifactKind.DELIVERABLE,
            session_id="ses_other",
            task_run_id="task_other",
        )
        forged = NodeSubmissionMarker(
            schema_version=2,
            digest="0" * 64,
            slots=(),
            deliverables=(
                DeliveryDescriptor(
                    slot="patch",
                    path="hello.py",
                    name="hello.py",
                    mime="text/x-python",
                    artifact_id=foreign.artifact_id,
                    sha256=foreign.sha256,
                    byte_size=foreign.byte_size,
                ),
            ),
        )
        fixture.artifacts.publish_bytes(
            forged.model_dump_json().encode("utf-8"),
            kind=ArtifactKind.TASK_SUMMARY,
            session_id=node.conversation_session_id,
            task_run_id=node.leaf_task_run_id,
            artifact_id=node_submission_artifact_id(node.node_run_id),
            excerpt="forged submission marker",
        )
        assert read_submission_marker(fixture.artifacts, node_run_id=node.node_run_id) is not None

        service = task_artifacts_for(fixture)
        view = service.view("ses_root", workflow_run_id=run.workflow_run_id)
        assert foreign.artifact_id not in {item.artifact_id for item in view.artifacts}
        with pytest.raises(ApplicationError):
            service.read_content(
                "ses_root", foreign.artifact_id, workflow_run_id=run.workflow_run_id
            )
    finally:
        fixture.close()


def test_backup_restore_keeps_the_delivery_marker_and_snapshot(tmp_path):
    from morrow.adapters.state.operational import OperationalStore as Store
    from morrow.application.backup import OperationalBackupService

    handle, artifacts, workspace, hooks, store = delivery_harness(tmp_path)
    (workspace / "index.html").write_text('<script src="app.js"></script>')
    (workspace / "app.js").write_text("console.log('hi')")
    try:
        register(hooks, path="index.html")
        before = read_submission_marker(artifacts, node_run_id="nrun_1")
        assert before is not None
        backup = OperationalBackupService(store, journal=artifacts.journal)
        created = backup.create("delivery")
        bundle = store.layout.backups_dir / created.bundle_name
        assert created.integrity_ok and backup.verify(bundle).ok

        target = tmp_path / "restored"
        restored = backup.restore(bundle, target)
        assert restored.restored

        restored_store = Store(target)
        handle2 = restored_store.initialize()
        try:
            journal2 = SqliteOperationalJournal(handle2)
            artifacts2 = ArtifactService(
                journal=journal2,
                filesystem=FilesystemArtifactStore(restored_store.layout),
                workspace_id=WS,
                id_source=FixedIdSource(),
            )
            after = read_submission_marker(artifacts2, node_run_id="nrun_1")
            assert after == before
            [descriptor] = after.deliverables
            assert descriptor.path == "index.html"
            assert (
                artifacts2.read(descriptor.artifact_id, max_bytes=descriptor.byte_size).content
                == b'<script src="app.js"></script>'
            )
            [resource] = descriptor.resources
            assert resource.path == "app.js"
            assert (
                artifacts2.read(resource.artifact_id, max_bytes=resource.byte_size).content
                == b"console.log('hi')"
            )
        finally:
            handle2.close()
    finally:
        handle.close()


def test_snapshot_identity_separates_producer_slot_and_bytes():
    """Same-named files in different slots or runs never share a snapshot."""

    from morrow.application.workflows.deliveries import delivery_artifact_id

    digest = "d" * 64
    identities = {
        delivery_artifact_id("nrun_1", "result", "a.py", digest),
        delivery_artifact_id("nrun_2", "result", "a.py", digest),
        delivery_artifact_id("nrun_1", "review", "a.py", digest),
        delivery_artifact_id("nrun_1", "result", "a.py", "e" * 64),
    }
    assert len(identities) == 4


@pytest.mark.asyncio
async def test_html_resource_content_and_download_use_the_real_names(tmp_path):
    """An HTML entry and its captured dependency each keep their own identity."""

    fixture, workspace = build_write_fixture(
        tmp_path,
        scripts=[
            [
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_h",
                            name="write",
                            arguments=json.dumps(
                                {
                                    "path": "index.html",
                                    "content": '<script src="app.js"></script>',
                                }
                            ),
                        ),
                    )
                ),
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_j",
                            name="write",
                            arguments=json.dumps(
                                {"path": "app.js", "content": "console.log('hi')"}
                            ),
                        ),
                    )
                ),
                AssistantMessage(
                    tool_calls=(
                        FunctionToolCall(
                            id="call_s",
                            name="submit_node_result",
                            arguments=json.dumps(
                                {
                                    "deliverables": {
                                        "patch": [{"path": "index.html", "label": "页面"}]
                                    }
                                }
                            ),
                        ),
                    )
                ),
                ["implemented"],
            ]
        ],
    )
    try:
        revision = publish_coder_only(fixture)
        run = await fixture.runtime.scheduler.run(
            start_coder(fixture, revision).run.workflow_run_id
        )
        assert run.status is WorkflowStatus.COMPLETED
        node = node_of(fixture, run, "coder")
        marker = read_submission_marker(fixture.artifacts, node_run_id=node.node_run_id)
        [entry] = marker.deliverables
        [resource] = entry.resources
        assert (entry.path, entry.name) == ("index.html", "index.html")
        assert resource.path == "app.js"

        service = task_artifacts_for(fixture)
        html = service.read_content(
            "ses_root", entry.artifact_id, workflow_run_id=run.workflow_run_id
        )
        assert html["preview"] == "html" and html["mime"].startswith("text/html")
        script = service.read_content(
            "ses_root", resource.artifact_id, workflow_run_id=run.workflow_run_id
        )
        assert (script["name"], script["path"]) == ("app.js", "app.js")
        assert script["content"] == "console.log('hi')"

        data, name, mime = service.download(
            "ses_root", resource.artifact_id, workflow_run_id=run.workflow_run_id
        )
        assert (data, name) == (b"console.log('hi')", "app.js")
        assert mime.startswith("text/javascript")
    finally:
        fixture.close()
