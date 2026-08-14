from __future__ import annotations

import asyncio

import pytest

from morrow.application.context import ContextBuilder
from morrow.application.structured import StructuredCompletionError, complete_structured
from morrow.core.models import Handoff, ModelRef
from morrow.runtime.session import Session
from morrow.services.handoff import HandoffService
from morrow.testing import ScriptedModelProvider


@pytest.mark.asyncio
async def test_structured_completion_repairs_once_through_context_builder():
    provider = ScriptedModelProvider(["not json", '{"current_goal":"done"}'])
    value, repaired = await complete_structured(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(),
        Session(session_id="s"),
        Handoff,
        "return handoff",
    )
    assert value.current_goal == "done"
    assert repaired is True
    assert len(provider.complete_calls) == 2
    assert all(
        any(message.role == "system" for message in call) for call in provider.complete_calls
    )


@pytest.mark.asyncio
async def test_structured_completion_stops_after_one_repair():
    provider = ScriptedModelProvider(["bad", "still bad"])
    with pytest.raises(StructuredCompletionError):
        await complete_structured(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            ContextBuilder(),
            Session(session_id="s"),
            Handoff,
            "return handoff",
        )
    assert len(provider.complete_calls) == 2


@pytest.mark.asyncio
async def test_handoff_service_uses_deterministic_fallback_on_model_failure(tmp_path):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider([RuntimeError("provider down")])
    session = Session(session_id="s")
    session.accept_user("current request")
    service = HandoffService(
        app.project_store,
        provider,
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(),
        identity.workspace_id,
    )
    value, degraded = await service.generate(session)
    assert degraded is True
    assert value.current_goal == "current request"
    assert "摘要生成失败" in (value.recovery_note or "")


@pytest.mark.asyncio
async def test_handoff_fallback_publishes_valid_state_and_marks_continuation(tmp_path):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    existing = Handoff(current_goal="old", decisions=[])
    saved = app.project_store.write_handoff(identity.workspace_id, existing, expected_revision=0)
    session = Session(
        session_id="s", loaded_handoff=existing, handoff_source_revision=saved.revision
    )
    session.accept_user("new request")
    service = HandoffService(
        app.project_store,
        ScriptedModelProvider([RuntimeError("offline")]),
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(),
        identity.workspace_id,
    )
    result, degraded = await service.generate_and_publish(session, expected_revision=saved.revision)
    assert result.status.value == "ok"
    assert degraded is True
    loaded = app.project_store.load_handoff(identity.workspace_id)
    assert loaded.value.handoff.current_goal == "old"
    assert loaded.value.handoff.recovery_note
    assert session.handoff_source_revision == loaded.revision
    assert session.dirty is False


@pytest.mark.asyncio
async def test_cancelling_handoff_generation_does_not_fallback_or_write(tmp_path):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    existing = Handoff(current_goal="unchanged")
    saved = app.project_store.write_handoff(identity.workspace_id, existing, expected_revision=0)
    session = Session(
        session_id="s", loaded_handoff=existing, handoff_source_revision=saved.revision
    )
    session.accept_user("request")
    service = HandoffService(
        app.project_store,
        ScriptedModelProvider(["cancel"]),
        ModelRef(provider_id="p", model_id="m"),
        ContextBuilder(),
        identity.workspace_id,
        timeout=30,
    )
    task = asyncio.create_task(
        service.generate_and_publish(session, expected_revision=saved.revision)
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (
        app.project_store.load_handoff(identity.workspace_id).value.handoff.current_goal
        == "unchanged"
    )
