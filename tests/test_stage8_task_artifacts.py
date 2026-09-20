"""Subplan 8 TaskArtifacts/CommandOutput authorization and availability tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from morrow.application.task_artifacts import TaskArtifactsService
from morrow.core.application import ApplicationError
from morrow.core.artifacts import ArtifactKind
from morrow.core.execution import DurableCommandFacts, DurableToolFacts
from morrow.core.task_artifacts import CommandOutputWire, TaskArtifactsWire, TaskArtifactWire
from test_stage7_isolated_workflow_slice import (
    SliceFixture,
    only_node,
    publish,
    start,
)
from test_stage7_serial_scheduler import MODEL, WS, pair_source
from test_stage8_core_api import ServerFixture, create_session_and_task
from test_stage8_patch_continuation import _patch, _paused_after_first_node


@pytest.mark.asyncio
async def test_task_artifact_route_reads_only_the_selected_session_task(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        session_id, task_id, _ = await create_session_and_task(fixture.client)

        def publish_artifact():
            return fixture.host.context.api.artifacts.publish_bytes(
                b"@@ -1 +1 @@\n-old\n+new\n",
                kind=ArtifactKind.DIFF,
                session_id=session_id,
                task_run_id=task_id,
            )

        artifact = await fixture.on_core(publish_artifact)
        path = (
            f"/v1/workspaces/{fixture.workspace_id}/sessions/{session_id}/task-artifacts"
            f"?task_run_id={task_id}"
        )
        listing = await fixture.client.get(path)
        assert listing.status == 200, listing.body
        body = listing.json()
        assert body["artifacts"][0]["artifact_id"] == artifact.artifact_id
        assert body["artifacts"][0]["retention"] == "standard"
        assert body["artifacts"][0]["row_version"] == artifact.row_version
        assert body["artifacts"][0]["diff"].startswith("@@")

        content = await fixture.client.get(
            f"/v1/workspaces/{fixture.workspace_id}/sessions/{session_id}/task-artifacts/"
            f"{artifact.artifact_id}/content?task_run_id={task_id}"
        )
        assert content.status == 200
        assert content.json()["content"].startswith("@@")

        other_session, other_task, _ = await create_session_and_task(fixture.client)
        assert other_session != session_id
        assert (
            await fixture.client.get(
                f"/v1/workspaces/{fixture.workspace_id}/sessions/{other_session}/task-artifacts/"
                f"{artifact.artifact_id}/content?task_run_id={other_task}"
            )
        ).status == 404
        assert (
            await fixture.client.get(
                f"/v1/workspaces/ws_missing/sessions/{session_id}/task-artifacts"
            )
        ).status == 403
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_root_workflow_task_artifacts_follow_run_node_session_authority(tmp_path):
    fixture = SliceFixture(tmp_path, scripts=[["node result"]])
    try:
        _agent, revision = publish(fixture)
        started = start(fixture, revision)
        run = await fixture.runtime.scheduler.run(started.run.workflow_run_id)
        node = only_node(fixture, run.workflow_run_id)
        assert node.conversation_session_id is not None

        service = TaskArtifactsService(
            fixture.journal,
            artifacts=fixture.artifacts,
            workflow_queries=fixture.runtime.queries,
            workspace_id="ws_one",
        )
        root_view = service.view("ses_root", workflow_run_id=run.workflow_run_id)
        output = next(item for item in root_view.artifacts if item.node_run_id == node.node_run_id)
        assert output.source == "workflow_output"
        assert service.read_content(
            "ses_root",
            output.artifact_id,
            workflow_run_id=run.workflow_run_id,
        )["content"]

        leaf_view = service.view(
            node.conversation_session_id,
            node_run_id=node.node_run_id,
            task_run_id=node.leaf_task_run_id,
        )
        assert [item.artifact_id for item in leaf_view.artifacts] == [output.artifact_id]
        with pytest.raises(ApplicationError):
            service.view("ses_root", node_run_id=node.node_run_id, workflow_run_id="wf_other")
        with pytest.raises(ApplicationError):
            service.read_content("ses_root", output.artifact_id, node_run_id="nrun_other")
    finally:
        fixture.close()


@pytest.mark.asyncio
async def test_root_task_view_includes_authorized_inherited_workflow_output(tmp_path):
    fixture, base, parent = await _paused_after_first_node(tmp_path)
    try:
        source = pair_source(base.nodes[0].agent_definition_ref)
        child = fixture.runtime.patches.apply(
            _patch(parent, base, source=source), active_model=MODEL
        ).child
        assert child is not None
        imported = fixture.journal.workflows.list_artifact_imports(WS, child.workflow_run_id)
        assert imported

        service = TaskArtifactsService(
            fixture.journal,
            artifacts=fixture.artifacts,
            workflow_queries=fixture.runtime.queries,
            workspace_id=WS,
        )
        view = service.view("ses_root", workflow_run_id=child.workflow_run_id)
        artifact = next(
            item for item in view.artifacts if item.artifact_id == imported[0].artifact_id
        )
        assert artifact.source == "workflow_output"
        assert service.read_content(
            "ses_root",
            artifact.artifact_id,
            workflow_run_id=child.workflow_run_id,
        )["content"]
    finally:
        fixture.close()


def test_command_facts_are_value_free_and_reconnectable():
    facts = DurableToolFacts(
        commands=(
            DurableCommandFacts(
                command_class="workspace_read",
                status="succeeded",
                cwd=".",
                exit_code=0,
                duration_ms=42,
            ),
        )
    )
    assert facts.commands[0].cwd == "."
    assert "password" not in facts.model_dump_json().lower()


def test_task_artifact_contracts_reject_forged_identities():
    now = datetime.now(UTC)
    base = {
        "kind": ArtifactKind.DIFF,
        "name": "example.py",
        "source": "task_evidence",
        "availability": "available",
        "byte_size": 1,
        "retention": "standard",
        "row_version": 1,
        "created_at": now,
        "updated_at": now,
    }
    with pytest.raises(ValidationError):
        TaskArtifactWire(artifact_id="not_an_artifact", **base)
    with pytest.raises(ValidationError):
        TaskArtifactWire(artifact_id="art_ok", session_id="ses_other/escape", **base)
    with pytest.raises(ValidationError):
        TaskArtifactsWire(session_id="ses_ok", workflow_run_ids=("workflow_forged",))
    with pytest.raises(ValidationError):
        CommandOutputWire(
            tool_execution_id="execution_forged",
            tool_name="bash",
            ordinal=1,
            state="succeeded",
            disposition="succeeded",
            started_at=now,
            output_availability="not_persisted",
        )


@pytest.mark.asyncio
async def test_artifact_content_and_download_describe_the_actual_bytes(tmp_path):
    """Content metadata and the download response agree on the real bytes."""

    fixture = ServerFixture(tmp_path)
    try:
        session_id, task_id, _ = await create_session_and_task(fixture.client)

        def publish_text():
            return fixture.host.context.api.artifacts.publish_bytes(
                "你好，world\n".encode(),
                kind=ArtifactKind.DELIVERABLE,
                session_id=session_id,
                task_run_id=task_id,
            )

        def publish_png():
            return fixture.host.context.api.artifacts.publish_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
                kind=ArtifactKind.DELIVERABLE,
                session_id=session_id,
                task_run_id=task_id,
            )

        text_artifact = await fixture.on_core(publish_text)
        png_artifact = await fixture.on_core(publish_png)
        base = (
            f"/v1/workspaces/{fixture.workspace_id}/sessions/{session_id}"
            f"/task-artifacts/{text_artifact.artifact_id}"
        )

        content = await fixture.client.get(f"{base}/content?task_run_id={task_id}")
        assert content.status == 200, content.body
        body = content.json()
        assert body["content"] == "你好，world\n"
        assert body["content_kind"] == "text" and body["preview"] == "text"
        assert body["name"] == text_artifact.artifact_id
        assert body["byte_size"] == len("你好，world\n".encode())

        png = await fixture.client.get(
            f"/v1/workspaces/{fixture.workspace_id}/sessions/{session_id}"
            f"/task-artifacts/{png_artifact.artifact_id}/content?task_run_id={task_id}"
        )
        assert png.status == 200, png.body
        png_body = png.json()
        # A markerless delivery never claims a decodable text body.
        assert png_body["content"] is None
        assert png_body["content_kind"] == "binary" and png_body["preview"] == "binary"

        download = await fixture.client.get(
            f"/v1/workspaces/{fixture.workspace_id}/sessions/{session_id}"
            f"/task-artifacts/{png_artifact.artifact_id}/download?task_run_id={task_id}"
        )
        assert download.status == 200
        assert download.body == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        headers = dict(download.headers)
        assert headers["content-type"].startswith("application/octet-stream")
        assert headers["content-disposition"].startswith("attachment")
        assert png_artifact.artifact_id in headers["content-disposition"]
    finally:
        fixture.close()


def test_preview_family_classifies_known_names():
    from morrow.application.task_artifacts import _preview_family

    assert _preview_family("a.png", "application/octet-stream") == "image"
    assert _preview_family("a.pdf", "application/octet-stream") == "pdf"
    assert _preview_family("a.html", "text/html") == "html"
    assert _preview_family("a.py", "text/x-python") == "text"
    assert _preview_family("a.bin", "application/octet-stream") == "binary"
