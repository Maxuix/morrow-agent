"""Chat-result projection: readable Outcome bodies, files and source checks.

Reproduces the gap first: a finished Workflow already carries a durable
result timeline item, but that item held no body, no declared output and no
file entry, so the main conversation could not show what was actually
produced. These tests pin the projection built on the existing TaskOutcome and
TaskArtifacts authorities. No network, no wall-clock assertions.
"""

from __future__ import annotations

import json

import pytest

from morrow.application.result_presentation import TaskResultProjector
from morrow.application.task_artifacts import TaskArtifactsService
from morrow.application.timeline_index import TimelineIndexService
from morrow.core.agent_runs import AgentDefinitionRef
from morrow.core.artifacts import ArtifactKind
from morrow.core.domain import (
    ArtifactReference,
    DurableSession,
    DurableTaskRun,
    TaskOutcome,
    TaskOutcomeTrigger,
    TaskRunStatus,
)
from morrow.core.models import AssistantMessage, FunctionToolCall
from morrow.core.workflows.contracts import NodeOutputRef, OutputContract
from morrow.core.workflows.runs import WorkflowStatus
from test_stage7_isolated_workflow_slice import (
    MODEL,
    WS,
    SliceFixture,
    agent_source,
    node_source,
    publish,
    start,
    workflow_source,
)

ROOT_SESSION = "ses_root"
ROOT_TASK = "task_root"


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


def make_index(fixture, projector=None) -> TimelineIndexService:
    return TimelineIndexService(
        fixture.journal,
        WS,
        result_projector=projector if projector is not None else make_projector(fixture),
    )


def result_items(index, session_id: str = ROOT_SESSION) -> list[dict]:
    page = index.snapshot_page(session_id, limit=50)
    return [item for item in page["items"] if item["kind"] == "result"]


def submission(slot: str, payload: dict) -> AssistantMessage:
    arguments = json.dumps({"outputs": {slot: payload}}, ensure_ascii=False)
    return AssistantMessage(
        tool_calls=(
            FunctionToolCall(id="call_submit", name="submit_node_result", arguments=arguments),
        )
    )


async def run_text_workflow(fixture):
    _, revision = publish(fixture)
    started = start(fixture, revision)
    run = await fixture.runtime.scheduler.run(started.run.workflow_run_id)
    assert run.status is WorkflowStatus.COMPLETED
    return run


def publish_synthesis_workflow(fixture, slot: str = "report"):
    """One node whose declared final output is a SynthesisReport."""

    version = fixture.agents.publish(
        agent_source(), source_revision=0, expected_head_revision=0, command_id="cmd_agent"
    )
    ref = AgentDefinitionRef(
        definition_id=version.source.definition_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
    )
    publication = fixture.compiler.publish(
        workflow_source(
            ref,
            nodes=(
                node_source(
                    ref,
                    output_contracts=(OutputContract(kind="SynthesisReport", slot=slot),),
                ),
            ),
            required_outputs=(NodeOutputRef(node_id="worker", output_slot=slot),),
        ),
        source_revision=0,
        expected_head_revision=0,
        command_id="cmd_publish",
        active_model=MODEL,
    )
    return publication.revision


def seed_other_task(fixture, session_id: str = "ses_other", task_id: str = "task_other"):
    fixture.journal.create_session(
        DurableSession(session_id=session_id, workspace_id=WS),
        task=DurableTaskRun(task_run_id=task_id, session_id=session_id, workspace_id=WS),
    )


def forged_outcome(artifact_ids, **changes) -> TaskOutcome:
    fields = {
        "outcome_id": "out_forged1",
        "workspace_id": WS,
        "session_id": ROOT_SESSION,
        "task_run_id": ROOT_TASK,
        "version": 99,
        "trigger": TaskOutcomeTrigger.SNAPSHOT,
        "task_status": TaskRunStatus.READY_FOR_ACCEPTANCE,
        "summary": "外部结果仅用于边界测试",
        "artifact_refs": tuple(
            ArtifactReference(artifact_id=value, role="workflow_result") for value in artifact_ids
        ),
    }
    fields.update(changes)
    return TaskOutcome(**fields)


@pytest.mark.asyncio
async def test_text_result_workflow_shows_the_real_answer_in_the_conversation(tmp_path):
    """A1: the transcript carries the answer text, not a bare contract payload."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        run = await run_text_workflow(fixture)
        items = result_items(make_index(fixture))
        assert len(items) == 1
        item = items[0]
        result = item["result"]
        assert result["outcome_id"] == item["source"]["source_id"]
        assert result["task_run_id"] == ROOT_TASK
        assert result["workflow_run_id"] == run.workflow_run_id
        assert result["result_status"] == "succeeded"
        assert result["body"]["text"] == "final answer"
        assert result["body"]["content_complete"] is True
        assert result["body_ref"]["record_id"].startswith("rec_")
        assert result["sections"] == []
        assert result["notes"] == []
        # A TextResult is the answer, never a file named after its slot; only
        # explicitly registered delivery snapshots become result files.
        assert result["files"] == []
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_structured_result_renders_its_own_fields(tmp_path):
    """A2: a SynthesisReport shows conclusion, findings, sources and limits."""

    fixture = SliceFixture(
        tmp_path,
        scripts=[
            [
                submission(
                    "report",
                    {
                        "summary": "授权测试覆盖不足",
                        "findings": ["缺少越权用例"],
                        "source_refs": ["tests/test_auth.py"],
                        "uncertainties": ["未验证并发路径"],
                    },
                ),
                "调查完成",
            ]
        ],
    )
    try:
        revision = publish_synthesis_workflow(fixture)
        started = start(fixture, revision)
        run = await fixture.runtime.scheduler.run(started.run.workflow_run_id)
        assert run.status is WorkflowStatus.COMPLETED
        result = result_items(make_index(fixture))[0]["result"]
        sections = {section["label"]: section["items"] for section in result["sections"]}
        assert sections["综合结论"] == ["授权测试覆盖不足"]
        assert sections["发现"] == ["缺少越权用例"]
        assert sections["来源"] == ["tests/test_auth.py"]
        assert sections["不确定项"] == ["未验证并发路径"]
        assert result["body"] is None
        assert result["files"] == []
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_result_item_identity_and_body_survive_replay_and_a_fresh_index(tmp_path):
    """A5: replay and a rebuilt index agree; identity and position stay put."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        index = make_index(fixture)
        first = result_items(index)[0]
        replayed = result_items(index)[0]
        rebuilt = result_items(make_index(fixture))[0]
        assert first["item_id"] == rebuilt["item_id"]
        assert first["timeline_position"] == rebuilt["timeline_position"]
        assert first["result"] == replayed["result"] == rebuilt["result"]
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_index_without_a_projector_keeps_the_plain_result_item(tmp_path):
    """An index wired without the Artifact store serves the entry unchanged."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        item = result_items(TimelineIndexService(fixture.journal, WS))[0]
        assert item["kind"] == "result"
        assert "result" not in item
        assert item["content"] is None

    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_result_projection_ignores_artifacts_outside_the_outcome_scope(tmp_path):
    """A7: an Artifact ref the Outcome scope does not own never becomes a file."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        seed_other_task(fixture)
        foreign = fixture.artifacts.publish_bytes(
            b"foreign bytes",
            kind=ArtifactKind.DIFF,
            session_id="ses_other",
            task_run_id="task_other",
        )
        projected = make_projector(fixture).project(forged_outcome((foreign.artifact_id,)))
        assert projected["files"] == []
        assert projected["body"] is None
        assert projected["summary"] == "外部结果仅用于边界测试"
        assert any("可读范围" in note for note in projected["notes"])
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_result_projection_refuses_a_foreign_session_scope(tmp_path):
    """A historical result is read through its own scope, never a newer one."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        projected = make_projector(fixture).project(
            forged_outcome(("art_any1",), session_id="ses_absent")
        )
        assert projected["files"] == []
        assert "结果来源已不属于当前会话范围，仅显示已登记摘要。" in projected["notes"]
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_unreadable_result_body_degrades_without_blocking_other_results(tmp_path):
    """A damaged result keeps its entry, summary and files; others stay intact."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        first = fixture.journal.list_task_outcomes(WS, ROOT_TASK)[0]
        second_artifact = fixture.artifacts.publish_bytes(
            b"second result body",
            kind=ArtifactKind.TASK_SUMMARY,
            session_id=ROOT_SESSION,
            task_run_id=ROOT_TASK,
        )
        second = forged_outcome(
            (second_artifact.artifact_id,),
            outcome_id="out_second1",
            version=2,
            summary="第二个结果",
        )
        fixture.journal.transact(lambda txn: txn.put_task_outcome(WS, second))
        damaged = first.artifact_refs[0].artifact_id

        def damage(_):
            fixture.journal._backend.executor().execute(
                "UPDATE artifacts SET sha256=? WHERE artifact_id=?", ("0" * 64, damaged)
            )

        fixture.journal.transact(damage)
        items = result_items(make_index(fixture))
        assert len(items) == 2
        broken = next(item for item in items if item["source"]["source_id"] == first.outcome_id)
        intact = next(item for item in items if item["source"]["source_id"] == "out_second1")
        assert broken["result"]["body"] is None
        assert broken["result"]["files"] == []
        assert broken["result"]["summary"].startswith("Workflow ")
        assert broken["result"]["notes"]
        # A contract-less Artifact from another outcome never becomes a file.
        assert intact["result"]["notes"] == []
        assert intact["result"]["files"] == []
        assert intact["result"]["summary"] == "第二个结果"
    finally:
        fixture.close()


def _stored_outcome(fixture):
    return fixture.journal.list_task_outcomes(WS, ROOT_TASK)[0]


@pytest.mark.asyncio
async def test_root_view_reads_the_full_leaf_answer_by_record_id(tmp_path):
    """A1: a long leaf answer is truncated inline but fully readable by record."""

    answer = "".join(f"paragraph {number:04d} " for number in range(1200))  # > 16 KiB
    fixture = SliceFixture(tmp_path, scripts=[[[answer]]])
    try:
        await run_text_workflow(fixture)
        index = make_index(fixture)
        result = result_items(index)[0]["result"]
        assert len(answer.encode("utf-8")) > 16384
        assert result["body"]["truncated"] is True
        assert result["body"]["text"] != answer
        record_id = result["body_ref"]["record_id"]
        assert index.read_record_content_authorized(ROOT_SESSION, record_id)["content"] == answer
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_corrupt_text_record_is_shown_but_never_called_complete(tmp_path):
    """A body that no longer matches its registered hash degrades honestly."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        outcome = _stored_outcome(fixture)
        record_id = result_items(make_index(fixture))[0]["result"]["body_ref"]["record_id"]

        def tamper(_txn):
            fixture.journal._backend.executor().execute(
                "UPDATE conversation_records SET payload_json = json_set(payload_json, '$.content', ?)"
                " WHERE record_id = ?",
                ("tampered answer", record_id),
            )

        fixture.journal.transact(tamper)
        projected = make_projector(fixture).project(outcome)
        assert projected["body"]["text"] == "tampered answer"
        assert projected["body"]["content_complete"] is False
        assert projected["body"]["truncated"] is True
        assert any("摘要不一致" in note for note in projected["notes"])
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_missing_text_record_falls_back_to_the_registered_excerpt(tmp_path):
    """A deleted record keeps the entry, the excerpt and an explicit note."""

    fixture = SliceFixture(tmp_path, scripts=[[["final ", "answer"]]])
    try:
        await run_text_workflow(fixture)
        outcome = _stored_outcome(fixture)
        record_id = result_items(make_index(fixture))[0]["result"]["body_ref"]["record_id"]

        def drop(_txn):
            fixture.journal._backend.executor().execute(
                "DELETE FROM conversation_records WHERE record_id = ?", (record_id,)
            )

        fixture.journal.transact(drop)
        projected = make_projector(fixture).project(outcome)
        assert projected["body"]["text"] == "final answer"
        assert projected["body"]["content_complete"] is False
        assert projected["body"]["truncated"] is True
        assert any("原记录" in note for note in projected["notes"])
        assert projected["files"] == []
    finally:
        fixture.close()
