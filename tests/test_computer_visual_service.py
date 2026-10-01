"""Controlled capture publication and source checks against an actual store."""

import io

import pytest
from PIL import Image

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.application.artifacts import ArtifactService
from morrow.application.computer_visuals import ComputerVisualService
from morrow.core.artifacts import ArtifactKind
from morrow.core.capabilities import AccessScope
from morrow.core.computer_use import (
    ComputerUseContractError,
    ComputerUseImageShare,
    CoordinateFrame,
    Observation,
    TransientCapture,
)
from morrow.core.domain import (
    ArtifactReference,
    DurableConversationRecord,
    DurableSession,
    sha256_digest,
)
from morrow.core.execution import (
    HandlerResultEnvelope,
    ToolExecutionDisposition,
    ToolExecutionState,
)
from morrow.core.permissions import (
    CapabilityIsolation,
    CapabilityName,
    IsolationLabel,
    capability_grant_digest,
    workspace_root_digest,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.testing import FixedClock, FixedIdSource
from test_computer_use_permissions import NOW, _grant, _scope
from test_stage4_permissions import _snapshot
from test_stage4_tool_journal import _execution, _intent, _open_journal, _seed_run


@pytest.fixture
def environment(tmp_path):
    store, handle, journal = _open_journal(tmp_path)
    _seed_run(journal)
    scope = _scope(workspace_id="ws_a", image_share=ComputerUseImageShare.CONTROLLED_WINDOW)
    grant = _grant(workspace_id="ws_a", computer_use_scope=scope)
    journal.put_capability_grant("ws_a", grant)
    snapshot = _snapshot(
        workspace_id="ws_a",
        workspace_root_digest=workspace_root_digest(tmp_path),
        source_revisions=journal.get_agent_run("ws_a", "arun_1").snapshot.source_revisions,
        permission_profile_digest=journal.get_agent_run(
            "ws_a", "arun_1"
        ).snapshot.permission_profile_digest,
        access_scope=AccessScope.FULL_ACCESS,
        schema_version=grant.schema_version,
        policy_version=grant.policy_version,
        computer_use_scope=scope,
        grant_id=grant.grant_id,
        grant_digest=capability_grant_digest(grant),
        granted_capabilities=grant.capabilities,
        capability_isolations=(
            CapabilityIsolation(
                capability=CapabilityName.COMPUTER_USE_HOST,
                isolation=IsolationLabel.COMPUTER_USE_HOST,
            ),
        ),
    )
    journal.put_permission_snapshot("ws_a", snapshot)
    journal.link_agent_run_permission_snapshot("ws_a", "arun_1", snapshot.permission_snapshot_id)
    execution = _execution(
        intent=_intent(tool_name="computer_observe"),
        state=ToolExecutionState.EXECUTING,
        permission_snapshot_id=snapshot.permission_snapshot_id,
        grant_id=grant.grant_id,
        isolation=IsolationLabel.COMPUTER_USE_HOST,
    )
    journal.put_execution("ws_a", execution)
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id="ws_a",
        id_source=FixedIdSource(),
        clock=FixedClock(NOW).now,
    )
    service = ComputerVisualService(artifacts, journal, clock=FixedClock(NOW).now)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(buffer, format="PNG")
    capture = TransientCapture(buffer.getvalue(), "image/png", 8, 6)
    observation = Observation(
        observation_id="cobs_1",
        target_ref="ctarget_1",
        agent_run_id="arun_1",
        generation=1,
        bundle_id="com.example.Notes",
        process_identity="cproc_1",
        window_identity="cwin_1",
        capture_digest=sha256_digest(capture.content),
        captured_at=NOW,
        frame=CoordinateFrame(width=8, height=6),
    )
    try:
        yield service, journal, scope, capture, observation, execution
    finally:
        handle.close()


def publish(environment, **changes):
    service, _, scope, capture, observation, _ = environment
    return service.publish(
        capture,
        observation,
        tool_execution_id="tex_1",
        scope=scope,
        settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        **changes,
    )


def complete(environment, reference, *, write_history=True):
    _, journal, _, _, _, execution = environment
    journal.save_execution(
        "ws_a",
        execution.model_copy(
            update={
                "state": ToolExecutionState.HANDLER_COMPLETED,
                "handler_completed_at": NOW,
                "disposition": ToolExecutionDisposition.SUCCEEDED,
                "row_version": 2,
                "artifact_refs": (
                    ArtifactReference(
                        artifact_id=reference.artifact_id, role="computer_observation"
                    ),
                ),
                "result_envelope": HandlerResultEnvelope(ok=True, visual_refs=(reference,)),
            }
        ),
        expected_row_version=1,
    )
    if write_history:
        journal.append_records(
            "ws_a",
            (
                DurableConversationRecord(
                    record_id="rec_1",
                    session_id="ses_1",
                    conversation_position=1,
                    kind="message",
                    payload={
                        "role": "tool",
                        "call_id": "call1",
                        "content": {"redacted": True},
                        "visual_refs": [reference.model_dump(mode="json")],
                    },
                ),
            ),
        )


def test_actual_bytes_readable_only_after_completion_for_exact_run_or_visible_history(environment):
    service, journal, _, _, _, _ = environment
    reference = publish(environment)
    metadata = service.artifacts.get(reference.artifact_id)
    assert metadata.kind is ArtifactKind.COMPUTER_OBSERVATION
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_1", agent_run_id="arun_1")
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read_preview(reference.artifact_id, session_id="ses_1")
    complete(environment, reference)
    assert (
        sha256_digest(service.read(reference, session_id="ses_1", agent_run_id="arun_1").content)
        == reference.sha256
    )
    assert service.read(reference, session_id="ses_1").width == 8
    resolved, preview = service.read_preview(reference.artifact_id, session_id="ses_1")
    assert resolved == reference
    assert preview.content == service.read(reference, session_id="ses_1").content
    for changes in [
        {"workspace_id": "ws_other"},
        {"tool_execution_id": "tex_other"},
        {"sha256": "b" * 64},
    ]:
        with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
            service.read(reference.model_copy(update=changes), session_id="ses_1")
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_1", agent_run_id="arun_other")
    journal.create_session(DurableSession(session_id="ses_other", workspace_id="ws_a"))
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_other")
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read_preview(reference.artifact_id, session_id="ses_other")


def test_publication_refuses_revoked_grant_and_unconfirmed_sensitive_regions(environment):
    service, journal, scope, capture, observation, _ = environment
    from morrow.core.computer_use import AxElement

    sensitive = observation.model_copy(
        update={
            "elements": (AxElement(element_ref="celem_1", depth=1, role="secure", sensitive=True),)
        }
    )
    with pytest.raises(ComputerUseContractError, match="image_safety_unconfirmed"):
        service.publish(
            capture,
            sensitive,
            tool_execution_id="tex_1",
            scope=scope,
            settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        )
    grant = journal.get_capability_grant("ws_a", "grt_1")
    journal.save_capability_grant(
        "ws_a",
        grant.model_copy(
            update={
                "revoked_at": NOW,
                "revocation_reason": "stop",
                "row_version": 2,
            }
        ),
        expected_row_version=1,
    )
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        publish(environment)
    assert journal.list_artifacts("ws_a") == ()


def test_corrupt_bytes_are_rejected_even_when_reference_metadata_matches(environment):
    service, _, _, _, _, _ = environment
    reference = publish(environment)
    complete(environment, reference)
    metadata = service.artifacts.get(reference.artifact_id)
    path = service.artifacts.filesystem.final_path(metadata.artifact_id)
    path.write_bytes(b"corrupt")
    from morrow.core.artifacts import ArtifactError

    with pytest.raises(ArtifactError):
        service.read(reference, session_id="ses_1", agent_run_id="arun_1")


def test_fork_preview_enforces_source_record_cut_and_provider_does_not_inherit_run(environment):
    service, journal, _, _, _, _ = environment
    # Fork before the visual tool record. The closed-turn cut is immutable.
    journal.append_records(
        "ws_a",
        (
            DurableConversationRecord(
                record_id="rec_before",
                session_id="ses_1",
                conversation_position=1,
                kind="terminal",
                payload={"finish_reason": "stop"},
            ),
        ),
    )
    journal.create_session(
        DurableSession(
            session_id="ses_before",
            workspace_id="ws_a",
            parent_session_id="ses_1",
            parent_cut_record_id="rec_before",
            parent_cut_position=1,
            conversation_position=1,
            fork_reason="continue",
        )
    )
    reference = publish(environment)
    # Persist completion first, then explicitly place the visual at the next position.
    service, journal, _, _, _, execution = environment
    journal.save_execution(
        "ws_a",
        execution.model_copy(
            update={
                "state": ToolExecutionState.HANDLER_COMPLETED,
                "handler_completed_at": NOW,
                "row_version": 2,
                "disposition": ToolExecutionDisposition.SUCCEEDED,
                "artifact_refs": (
                    ArtifactReference(
                        artifact_id=reference.artifact_id, role="computer_observation"
                    ),
                ),
                "result_envelope": HandlerResultEnvelope(ok=True, visual_refs=(reference,)),
            }
        ),
        expected_row_version=1,
    )
    journal.append_records(
        "ws_a",
        (
            DurableConversationRecord(
                record_id="rec_visual",
                session_id="ses_1",
                conversation_position=2,
                kind="message",
                payload={"role": "tool", "visual_refs": [reference.model_dump(mode="json")]},
            ),
            DurableConversationRecord(
                record_id="rec_after",
                session_id="ses_1",
                conversation_position=3,
                kind="terminal",
                payload={"finish_reason": "stop"},
            ),
        ),
    )
    journal.create_session(
        DurableSession(
            session_id="ses_after",
            workspace_id="ws_a",
            parent_session_id="ses_1",
            parent_cut_record_id="rec_after",
            parent_cut_position=3,
            conversation_position=3,
            fork_reason="continue",
        )
    )
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_before")
    assert service.read(reference, session_id="ses_after").width == 8
    assert service.read_preview(reference.artifact_id, session_id="ses_after")[1].width == 8
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read_preview(reference.artifact_id, session_id="ses_before")
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_after", agent_run_id="arun_1")


def test_preview_root_requires_durable_workflow_leaf_chain(environment):
    service, journal, _, _, _, _ = environment
    reference = publish(environment)
    complete(environment, reference)
    from morrow.core.domain import DurableTaskRun

    journal.create_session(
        DurableSession(session_id="ses_root", workspace_id="ws_a"),
        task=DurableTaskRun(task_run_id="task_root", session_id="ses_root", workspace_id="ws_a"),
    )
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_root")
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read_preview(reference.artifact_id, session_id="ses_root")

    # Match the actual root → run → node → leaf visibility query, using the
    # smallest rows needed by this read-only contract, as timeline tests do.
    def link(_):
        executor = journal._backend.executor()
        executor.execute(
            "INSERT INTO workflow_revisions (workflow_revision_id, workspace_id, "
            "workflow_definition_id, revision, content_hash, body_json) VALUES (?,?,?,?,?,'{}')",
            ("rev_visual", "ws_a", "wf_visual", 1, "a" * 64),
        )
        executor.execute(
            "INSERT INTO workflow_runs (workflow_run_id, workspace_id, workflow_revision_id, "
            "root_task_run_id, status, lineage_budget_root_run_id, body_json) "
            "VALUES (?,?,?,?,'running',?,'{}')",
            ("wfr_visual", "ws_a", "rev_visual", "task_root", "wfr_visual"),
        )
        executor.execute(
            "INSERT INTO workflow_node_runs (node_run_id, workspace_id, workflow_run_id, "
            "node_id, attempt, status, body_json) VALUES (?,?,?,?,'1','running',?)",
            (
                "noderun_visual",
                "ws_a",
                "wfr_visual",
                "observe",
                '{"conversation_session_id":"ses_1"}',
            ),
        )

    journal.transact(link)
    assert service.read(reference, session_id="ses_root").width == 8
    assert service.read_preview(reference.artifact_id, session_id="ses_root")[1].width == 8
    with pytest.raises(ComputerUseContractError, match="image_source_not_authorized"):
        service.read(reference, session_id="ses_root", agent_run_id="arun_1")


def test_run_capture_quota_prevents_partial_extra_publication(environment):
    service, journal, scope, capture, observation, _ = environment
    reference = publish(environment)
    with pytest.raises(ComputerUseContractError, match="image_budget"):
        service.publish(
            capture,
            observation,
            tool_execution_id="tex_1",
            scope=scope,
            settings=ComputerUseSettings(
                enabled=True, mode=ComputerUseMode.HYBRID, max_observation_bytes=reference.byte_size
            ),
        )
    assert len(journal.list_artifacts("ws_a")) == 1


def test_known_sensitive_element_requires_exact_mask_and_persists_actual_black_pixels(environment):
    from morrow.adapters.computer_use.images import CaptureMask
    from morrow.core.artifacts import ArtifactSensitivity
    from morrow.core.computer_use import AxElement

    service, _, scope, capture, observation, _ = environment
    sensitive = observation.model_copy(
        update={
            "elements": (
                AxElement(element_ref="celem_secret", depth=1, role="secure", sensitive=True),
            )
        }
    )
    settings = ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID)
    with pytest.raises(ComputerUseContractError, match="image_safety_unconfirmed"):
        service.publish(
            capture,
            sensitive,
            tool_execution_id="tex_1",
            scope=scope,
            masks={"celem_wrong": CaptureMask(0, 0, 2, 2)},
            settings=settings,
        )
    reference = service.publish(
        capture,
        sensitive,
        tool_execution_id="tex_1",
        scope=scope,
        masks={"celem_secret": CaptureMask(0, 0, 2, 2)},
        settings=settings,
    )
    complete(environment, reference)
    metadata = service.artifacts.get(reference.artifact_id)
    assert metadata.sensitivity is ArtifactSensitivity.REDACTED
    data = service.read(reference, session_id="ses_1", agent_run_id="arun_1").content
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.getpixel((0, 0)) == (0, 0, 0)
        assert decoded.getpixel((2, 2)) == (255, 0, 0)


def test_hydration_uses_latest_two_run_images_and_preserves_original_messages(environment):
    import base64

    from morrow.application.computer_visuals import ToolVisualHydrator
    from morrow.core.image_tokens import iter_image_parts, messages_without_image_payloads
    from morrow.core.models import ToolMessage

    service, _, _, _, _, _ = environment
    reference = publish(environment)
    complete(environment, reference)
    original = tuple(
        ToolMessage(tool_call_id=f"call{i}", content="{}", visual_refs=(reference,))
        for i in range(3)
    )
    hydrator = ToolVisualHydrator(
        service,
        session_id="ses_1",
        agent_run_id="arun_1",
        settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        input_types=("text", "image"),
        tool_protocol="openai_function",
    )
    projected = hydrator(original)
    hydrator.bind_history(original)
    assert not hydrator(original[:1])[0].input_parts
    assert not projected[0].input_parts
    assert "omitted" in projected[0].content
    assert len(tuple(iter_image_parts(projected))) == 2
    for message in projected[1:]:
        assert sha256_digest(base64.b64decode(message.input_parts[0].data)) == reference.sha256
        assert not message.model_dump().get("input_parts")
        assert message.input_parts[0].data not in repr(message)
    assert all(message.content == "{}" and not message.input_parts for message in original)
    assert all(
        part.data == "" for part in iter_image_parts(messages_without_image_payloads(projected))
    )
    prior = original[0].model_copy(
        update={"visual_refs": (reference.model_copy(update={"agent_run_id": "arun_prior"}),)}
    )
    assert not hydrator((prior,))[0].input_parts


@pytest.mark.parametrize(
    "inputs,protocol", [(("text",), "openai_function"), (("text", "image"), "none")]
)
def test_hybrid_requires_exact_model_image_and_function_tools(environment, inputs, protocol):
    from morrow.application.computer_visuals import ToolVisualHydrator

    service, _, _, _, _, _ = environment
    with pytest.raises(ComputerUseContractError, match="model_image_tools_required"):
        ToolVisualHydrator(
            service,
            session_id="ses_1",
            agent_run_id="arun_1",
            settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
            input_types=inputs,
            tool_protocol=protocol,
        )
    ToolVisualHydrator(
        service,
        session_id="ses_1",
        agent_run_id="arun_1",
        settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.SEMANTIC),
        input_types=inputs,
        tool_protocol=protocol,
    )


def test_context_hydrates_actual_tool_image_and_normalizes_missing_latest_image(environment):
    from morrow.application.computer_visuals import ToolVisualHydrator
    from morrow.application.context import ContextBudgetError
    from morrow.core.image_tokens import iter_image_parts
    from morrow.core.models import AssistantMessage, FunctionToolCall, UserMessage
    from morrow.runtime.session import Session
    from morrow.testing import make_context_builder

    service, _, _, _, _, _ = environment
    reference = publish(environment)
    complete(environment, reference)
    session = Session(session_id="ses_1")
    session.log.begin_turn(UserMessage(content="Inspect this controlled fixture"))
    session.log.append_assistant(
        AssistantMessage(
            tool_calls=(FunctionToolCall(id="call1", name="computer_observe", arguments="{}"),)
        )
    )
    session.log.append_tool_result("call1", "{}", visual_refs=(reference,))
    original = session.log.snapshot()
    builder = make_context_builder()
    builder.tool_visual_hydrator = ToolVisualHydrator(
        service,
        session_id="ses_1",
        agent_run_id="arun_1",
        settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        input_types=("text", "image"),
        tool_protocol="openai_function",
    )
    pack = builder.build(session)
    assert len(tuple(iter_image_parts(pack.messages))) == 1
    assert session.log.snapshot() == original
    service.artifacts.filesystem.final_path(reference.artifact_id).unlink()
    with pytest.raises(ContextBudgetError) as failed:
        builder.build(session)
    assert failed.value.cause_code == "tool_image_unavailable"
