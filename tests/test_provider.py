from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import OpenAICompatibleProvider
from morrow.bootstrap import build_application
from morrow.core.models import (
    FinishReason,
    Message,
    ModelErrorCode,
    ModelEvent,
    ModelProviderError,
    ModelRef,
)


class AsyncChunks:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self.chunks:
            yield chunk


class BrokenChunks:
    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        raise ValueError("malformed provider chunk")
        yield  # pragma: no cover


class FakeCompletions:
    def __init__(self, response):
        self.response = response

    async def create(self, **kwargs):
        return self.response


def provider_with_stream(response):
    provider = OpenAICompatibleProvider("https://example.test", "credential-sentinel")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))
    return provider


def stream_chunk(*, text=None, reasoning=None, finish=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, reasoning_content=reasoning),
                finish_reason=finish,
            )
        ]
    )


async def collect_stream(provider):
    return [
        event
        async for event in provider.stream(
            ModelRef(provider_id="p", model_id="m"),
            [Message(role="user", content="hello")],
        )
    ]


class FakeProvider:
    def __init__(self):
        self.complete_messages = []

    async def complete(self, model, messages):
        self.complete_messages.append(messages)
        return "ok"

    async def stream(self, model, messages):
        yield ModelEvent(kind="completed")


def test_provider_onboarding_publishes_model_after_explicit_test(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    fake = FakeProvider()
    app.registry.register("openai-compatible", lambda config, credential: fake)
    model = app.provider_service.add("opencode-go", "credential-sentinel")
    assert str(model) == "opencode-go/deepseek-v4-flash"
    config = app.global_store.load().value
    assert config.active_model == model
    assert "credential-sentinel" not in (tmp_path / "state" / "config.yaml").read_text(
        encoding="utf-8"
    )
    assert (
        credentials.get(config.providers["opencode-go"].credential_ref.ref) == "credential-sentinel"
    )
    assert all(
        "credential-sentinel" not in path.read_text(encoding="utf-8")
        for path in (tmp_path / "state").rglob("*")
        if path.is_file() and path.suffix in {".yaml", ".log", ".txt"}
    )
    assert fake.complete_messages and fake.complete_messages[0]
    assert fake.complete_messages[0][0].role == "user"


def test_adding_another_provider_preserves_existing_active_model(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    app.registry.register("openai-compatible", lambda config, credential: FakeProvider())
    first = app.provider_service.add("opencode-go", "first")
    second = app.provider_service.add("opencode-go", "second")
    assert second == first
    assert app.provider_service.current_model() == first


def test_provider_reconfigure_keeps_active_model_and_global_preferences(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    app.registry.register("openai-compatible", lambda config, credential: FakeProvider())
    first = app.provider_service.add("opencode-go", "first")
    app.global_store.update(
        lambda value: value.model_copy(
            update={"preferences": value.preferences.model_copy(update={"language": "中文"})}
        ),
        expected_revision=app.global_store.load().revision,
    )
    app.provider_service.configure("opencode-go", secret="second")
    config = app.global_store.load().value
    assert config.active_model == first
    assert config.preferences.language == "中文"
    assert credentials.get(config.providers["opencode-go"].credential_ref.ref) == "second"


def test_environment_credential_has_precedence_for_active_build_and_non_secret_configure(
    tmp_path, monkeypatch
):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    seen_credentials = []

    def factory(config, credential):
        seen_credentials.append(credential)
        return FakeProvider()

    app.registry.register("openai-compatible", factory)
    app.provider_service.add("opencode-go", "stored-secret")
    config = app.global_store.load().value
    credential_ref = config.providers["opencode-go"].credential_ref.ref
    credentials.delete(credential_ref)
    monkeypatch.setenv("MORROW_OPENCODE_GO_API_KEY", "environment-secret")

    app.provider_service.build_active()
    app.provider_service.configure("opencode-go", base_url="https://updated.example.test")

    updated = app.global_store.load().value.providers["opencode-go"]
    assert updated.base_url == "https://updated.example.test"
    assert updated.credential_ref.ref == credential_ref
    assert seen_credentials[-2:] == ["environment-secret", "environment-secret"]


def test_credential_rotation_is_refused_while_environment_masks_store(tmp_path, monkeypatch):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    app.registry.register("openai-compatible", lambda config, credential: FakeProvider())
    app.provider_service.add("opencode-go", "stored-secret")
    before = app.data_root.config_path.read_bytes()
    monkeypatch.setenv("MORROW_OPENCODE_GO_API_KEY", "environment-secret")

    with pytest.raises(ValueError, match="环境变量"):
        app.provider_service.configure("opencode-go", secret="replacement", replace_credential=True)

    assert app.data_root.config_path.read_bytes() == before


def test_provider_test_persists_typed_failure_code(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)

    class FailingProvider(FakeProvider):
        async def complete(self, model, messages):
            raise ModelProviderError(ModelErrorCode.AUTH, "sanitized failure")

    app.registry.register("openai-compatible", lambda config, credential: FakeProvider())
    app.provider_service.add("opencode-go", "stored-secret")
    app.registry.register("openai-compatible", lambda config, credential: FailingProvider())

    result = app.provider_service.test("opencode-go")

    assert result.ok is False
    assert result.error_code == ModelErrorCode.AUTH
    assert app.provider_service.provider("opencode-go").last_test.error_code == ModelErrorCode.AUTH


@pytest.mark.asyncio
async def test_adapter_accepts_only_explicit_stop_and_isolates_reasoning():
    provider = provider_with_stream(
        AsyncChunks(
            [
                stream_chunk(reasoning="private reasoning"),
                stream_chunk(text="visible"),
                stream_chunk(finish="stop"),
            ]
        )
    )

    events = await collect_stream(provider)

    assert [(event.kind, event.text) for event in events] == [
        ("text_delta", "visible"),
        ("completed", None),
    ]
    assert events[-1].finish_reason == FinishReason.STOP
    assert "private reasoning" not in str(events)


@pytest.mark.asyncio
async def test_adapter_reasoning_only_stream_has_no_visible_delta():
    provider = provider_with_stream(
        AsyncChunks(
            [
                stream_chunk(reasoning="private reasoning"),
                stream_chunk(finish="stop"),
            ]
        )
    )

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["completed"]
    assert "private reasoning" not in str(events)


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls", "function_call"])
@pytest.mark.asyncio
async def test_adapter_rejects_non_normal_finish_as_invalid_response(finish):
    provider = provider_with_stream(
        AsyncChunks([stream_chunk(text="partial"), stream_chunk(finish=finish)])
    )

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["text_delta", "error"]
    assert events[-1].error_code == ModelErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_adapter_rejects_missing_finish_signal():
    provider = provider_with_stream(AsyncChunks([stream_chunk(text="partial")]))

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["text_delta", "error"]
    assert events[-1].error_code == ModelErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_adapter_classifies_malformed_stream_as_invalid_response():
    provider = provider_with_stream(BrokenChunks())

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["error"]
    assert events[-1].error_code == ModelErrorCode.INVALID_RESPONSE


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_provider_streams_visible_text_without_reasoning():
    credential = os.environ.get("MORROW_OPENCODE_GO_API_KEY")
    if not credential:
        pytest.skip("set MORROW_OPENCODE_GO_API_KEY for the explicit Live checklist")
    provider = OpenAICompatibleProvider("https://opencode.ai/zen/go/v1", credential)
    visible = []
    async for event in provider.stream(
        ModelRef(provider_id="opencode-go", model_id="deepseek-v4-flash"),
        [Message(role="user", content="Reply with the single word 好。")],
    ):
        if event.text:
            visible.append(event.text)
    assert "".join(visible).strip()
