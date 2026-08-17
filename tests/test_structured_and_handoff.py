from __future__ import annotations

import asyncio
import time

import pytest

from morrow.application.context import ContextBudgetError
from morrow.application.structured import StructuredCompletionError, complete_structured
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    FunctionToolCall,
    Handoff,
    ModelRef,
    ToolMessage,
    UserMessage,
)
from morrow.runtime.session import Session
from morrow.services.handoff import HandoffService
from morrow.testing import ScriptedModelProvider, make_context_builder, seed_user_turn


class DelayedCompletionProvider:
    def __init__(self, delays, responses):
        self.delays = list(delays)
        self.responses = list(responses)
        self.complete_calls = []

    async def complete(self, model, messages):
        del model
        index = len(self.complete_calls)
        self.complete_calls.append(list(messages))
        await asyncio.sleep(self.delays[index])
        return self.responses[index]


@pytest.mark.asyncio
async def test_structured_completion_repairs_once_through_context_builder():
    provider = ScriptedModelProvider(["not json", '{"current_goal":"done"}'])
    value, repaired = await complete_structured(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
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
async def test_structured_completion_wraps_context_budget_as_structured_error():
    session = Session(session_id="s")
    seed_user_turn(session, "x" * 200, assistant="y" * 200)
    with pytest.raises(StructuredCompletionError) as exc_info:
        await complete_structured(
            ScriptedModelProvider(['{"current_goal":"done"}']),
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(40),
            session,
            Handoff,
            "return handoff",
        )
    assert not isinstance(exc_info.value, ContextBudgetError)
    assert "预算" in str(exc_info.value)


@pytest.mark.asyncio
async def test_structured_completion_stops_after_one_repair():
    provider = ScriptedModelProvider(["bad", "still bad"])
    with pytest.raises(StructuredCompletionError):
        await complete_structured(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            Session(session_id="s"),
            Handoff,
            "return handoff",
        )
    assert len(provider.complete_calls) == 2


@pytest.mark.asyncio
async def test_structured_completion_uses_one_total_deadline_across_repair():
    provider = DelayedCompletionProvider(
        [0.06, 0.06],
        ["not json", '{"current_goal":"too late"}'],
    )
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        await complete_structured(
            provider,
            ModelRef(provider_id="p", model_id="m"),
            make_context_builder(),
            Session(session_id="s"),
            Handoff,
            "return handoff for the original task",
            timeout=0.1,
        )

    assert time.monotonic() - started < 0.14
    assert len(provider.complete_calls) == 2


@pytest.mark.asyncio
async def test_structured_repair_retains_original_instruction_and_target_schema():
    provider = ScriptedModelProvider(["not json", '{"current_goal":"done"}'])

    await complete_structured(
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        Session(session_id="s"),
        Handoff,
        "return handoff for the original task",
    )

    repair_prompt = provider.complete_calls[1][-1].content
    assert "return handoff for the original task" in repair_prompt
    assert "current_goal" in repair_prompt
    assert "校验" in repair_prompt


@pytest.mark.asyncio
async def test_structured_and_handoff_fallback_never_consume_tool_envelopes(tmp_path):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session = Session(session_id="s")
    session.log.begin_turn(UserMessage(content="completed user"))
    session.log.append_assistant(
        AssistantMessage(
            content="intermediate-secret",
            tool_calls=(FunctionToolCall(id="c1", name="lookup", arguments="{}"),),
        )
    )
    session.log.append_tool_result("c1", "tool-envelope-secret")
    session.log.append_assistant(AssistantMessage(content="safe final"))
    session.log.finish_turn(FinishReason.STOP)
    session.log.begin_turn(UserMessage(content="latest failed user"))
    session.log.append_assistant(
        AssistantMessage(tool_calls=(FunctionToolCall(id="c2", name="lookup", arguments="{}"),))
    )
    session.log.append_tool_result("c2", "failed-tool-secret")
    session.log.finish_turn(FinishReason.ERROR)

    structured_provider = ScriptedModelProvider(['{"current_goal":"done"}'])
    await complete_structured(
        structured_provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        session,
        Handoff,
        "return handoff",
    )
    wire = structured_provider.complete_calls[0]
    assert all(not isinstance(message, ToolMessage) for message in wire)
    assert all(not getattr(message, "tool_calls", ()) for message in wire)
    assert "tool-envelope-secret" not in str(wire)
    assert "intermediate-secret" not in str(wire)

    service = HandoffService(
        app.project_store,
        ScriptedModelProvider([RuntimeError("offline")]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        identity.workspace_id,
    )
    fallback, degraded = await service.generate(session)
    assert degraded is True
    assert fallback.current_goal == "latest failed user"
    assert "safe final" in (fallback.recovery_note or "")
    assert "secret" not in (fallback.recovery_note or "")


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
    seed_user_turn(session, "current request")
    service = HandoffService(
        app.project_store,
        provider,
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        identity.workspace_id,
    )
    value, degraded = await service.generate(session)
    assert degraded is True
    assert value.current_goal == "current request"
    assert "摘要生成失败" in (value.recovery_note or "")


@pytest.mark.asyncio
async def test_independent_fallback_without_user_text_uses_fixed_safe_goal(tmp_path):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    service = HandoffService(
        app.project_store,
        ScriptedModelProvider([RuntimeError("offline")]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        identity.workspace_id,
    )

    value, degraded = await service.generate(Session(session_id="s"))

    assert degraded is True
    assert value.current_goal == "继续推进当前工作"


@pytest.mark.parametrize("clear_existing", [False, True])
@pytest.mark.asyncio
async def test_independent_fallback_never_copies_unloaded_disk_handoff(tmp_path, clear_existing):
    from morrow.adapters.credentials.keyring import MemoryCredentialStore
    from morrow.bootstrap import build_application

    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    saved = app.project_store.write_handoff(
        identity.workspace_id, Handoff(current_goal="unloaded old goal"), expected_revision=0
    )
    expected_revision = saved.revision
    if clear_existing:
        cleared = app.project_store.clear_handoff(
            identity.workspace_id, expected_revision=expected_revision
        )
        expected_revision = cleared.revision
    session = Session(session_id="s")
    seed_user_turn(session, "independent new goal")
    service = HandoffService(
        app.project_store,
        ScriptedModelProvider([RuntimeError("offline")]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
        identity.workspace_id,
    )

    result, degraded = await service.generate_and_publish(
        session, expected_revision=expected_revision
    )

    assert result.status.value == "ok"
    assert degraded is True
    loaded = app.project_store.load_handoff(identity.workspace_id)
    assert loaded.value.handoff.current_goal == "independent new goal"


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
    seed_user_turn(session, "new request")
    service = HandoffService(
        app.project_store,
        ScriptedModelProvider([RuntimeError("offline")]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
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
    seed_user_turn(session, "request")
    service = HandoffService(
        app.project_store,
        ScriptedModelProvider(["cancel"]),
        ModelRef(provider_id="p", model_id="m"),
        make_context_builder(),
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
