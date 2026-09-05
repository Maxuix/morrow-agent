from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import httpx
import openai
import pytest

from morrow.adapters.credentials.keyring import (
    CredentialAccessError,
    KeyringCredentialStore,
    MemoryCredentialStore,
    translate_keyring_error,
)
from morrow.adapters.models.openai_compatible import (
    OpenAICompatibleProvider,
    StreamAccumulator,
    classify_failure,
    normalize_tool_schema,
    provider_error_message,
    serialize_message,
    serialize_tool,
)
from morrow.bootstrap import build_application
from morrow.core.models import (
    CredentialRef,
    FunctionToolCall,
    ModelErrorCode,
    ModelEvent,
    ModelFailure,
    ModelFailureOrigin,
    ModelFinishReason,
    ModelProviderError,
    ModelRef,
    ProviderConfig,
    ProviderModelConfig,
    ToolDefinition,
    ToolFunction,
    UserMessage,
)
from morrow.core.preference_documents import PreferenceEntriesPayload
from morrow.core.preference_models import PreferenceEntry, PreferenceScope


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
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def provider_with_stream(response):
    provider = OpenAICompatibleProvider("https://example.test", "credential-sentinel")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(response)))
    return provider


@pytest.mark.asyncio
async def test_non_streaming_completion_uses_the_full_review_transport_budget():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"drafts":[]}'))
        ]
    )
    provider = provider_with_stream(response)

    result = await provider.complete(
        ModelRef(provider_id="test", model_id="reviewer"),
        [UserMessage(content="review")],
    )

    assert result == '{"drafts":[]}'
    assert provider._client.chat.completions.kwargs["stream"] is False
    assert provider._client.chat.completions.kwargs["timeout"] == 300.0


def stream_chunk(*, text=None, reasoning=None, finish=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, reasoning_content=reasoning),
                finish_reason=finish,
            )
        ]
    )


def tool_call_fragment(index, *, call_id=None, name=None, arguments=None, call_type=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        type=call_type,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def tool_stream_chunk(fragments, *, text=None, finish=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=text, reasoning_content=None, tool_calls=list(fragments)
                ),
                finish_reason=finish,
            )
        ]
    )


def usage_only_chunk(*, prompt_tokens=None, completion_tokens=None, total_tokens=None):
    values = {
        name: value
        for name, value in {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }.items()
        if value is not None
    }
    return SimpleNamespace(choices=[], usage=SimpleNamespace(**values))


def demo_tool(name="lookup_record"):
    return ToolDefinition(
        function=ToolFunction(
            name=name,
            description="Look up an injected in-memory record.",
            parameters={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        )
    )


async def collect_stream_with_tools(provider, tools):
    return [
        event
        async for event in provider.stream(
            ModelRef(provider_id="p", model_id="m"),
            [UserMessage(content="hello")],
            tools,
        )
    ]


async def collect_stream(provider):
    return [
        event
        async for event in provider.stream(
            ModelRef(provider_id="p", model_id="m"),
            [UserMessage(content="hello")],
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


def test_opencode_go_mimo_preset_registers_mimo_v25(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    fake = FakeProvider()
    app.registry.register("openai-compatible", lambda config, credential: fake)

    model = app.provider_service.add("opencode-go-mimo", "credential-sentinel")

    assert str(model) == "opencode-go/mimo-v2.5"
    config = app.global_store.load().value
    provider = config.providers["opencode-go"]
    assert provider.models["mimo-v2.5"].api_model_id == "mimo-v2.5"
    assert config.active_model == model


def test_adding_mimo_preset_keeps_existing_active_model_and_registers_both_models(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)
    app.registry.register("openai-compatible", lambda config, credential: FakeProvider())

    existing = app.provider_service.add("opencode-go", "first")
    added = app.provider_service.add("opencode-go-mimo", "second")

    config = app.global_store.load().value
    assert added == ModelRef(provider_id="opencode-go", model_id="mimo-v2.5")
    assert config.active_model == existing
    assert set(config.providers["opencode-go"].models) == {
        "deepseek-v4-flash",
        "mimo-v2.5",
    }


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
    entry = PreferenceEntry(
        preference_id="pref_provider_test",
        statement="默认使用中文。",
        scope=PreferenceScope.GLOBAL,
    )
    app.global_store.update(
        lambda value: value.model_copy(
            update={"preferences": PreferenceEntriesPayload(entries=(entry,))}
        ),
        expected_revision=app.global_store.load().revision,
    )
    app.provider_service.configure("opencode-go", secret="second")
    config = app.global_store.load().value
    assert config.active_model == first
    assert config.preferences.entries == (entry,)
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


def test_provider_test_distinguishes_unknown_provider_from_missing_credential(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)

    with pytest.raises(ValueError, match="未知 Provider: demo"):
        app.provider_service.test("demo")

    written = app.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "demo": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://example.test",
                        models={"m": ProviderModelConfig(api_model_id="m")},
                    )
                }
            }
        )
    )
    assert written.status.value == "ok"

    with pytest.raises(ValueError, match="凭据不可用"):
        app.provider_service.test("demo")


def test_keyring_errors_are_translated_without_backend_text():
    denied = translate_keyring_error(RuntimeError("User canceled (-128)"))
    locked = translate_keyring_error(RuntimeError("Keychain is locked"))
    unknown = translate_keyring_error(RuntimeError("(-50, Unknown Error)"))

    assert denied.code == "denied"
    assert locked.code == "locked"
    assert unknown.code == "unavailable"
    for error in (denied, locked, unknown):
        assert "(-50" not in str(error)
        assert "Unknown Error" not in str(error)
        assert "解锁 Keychain" in error.message


def test_keyring_store_get_raises_sanitized_error(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("(-50, Unknown Error)")
            )
        ),
    )
    with pytest.raises(CredentialAccessError) as caught:
        KeyringCredentialStore().get("provider:demo:abcd")
    assert caught.value.code == "unavailable"
    assert "(-50" not in str(caught.value)


def test_inspect_credential_reports_backend_failure_without_secret(tmp_path):
    class RaisingStore(MemoryCredentialStore):
        def get(self, ref):
            del ref
            raise CredentialAccessError(
                "unavailable",
                "凭据暂时不可用；请解锁 Keychain 或检查钥匙串权限后重试",
            )

    app = build_application(state_root=tmp_path / "state", credentials=RaisingStore())
    written = app.global_store.update(
        lambda value: value.model_copy(
            update={
                "providers": {
                    "demo": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://example.test",
                        credential_ref=CredentialRef(ref="provider:demo:test"),
                        models={"m": ProviderModelConfig(api_model_id="m")},
                    )
                }
            }
        )
    )
    assert written.status.value == "ok"
    inspection = app.provider_service.inspect_credential("demo")
    assert inspection.available is False
    assert inspection.code == "unavailable"
    assert app.provider_service.credential_available("demo") is False


def test_provider_error_messages_distinguish_network_auth_rate_limit_and_timeout():
    assert "认证" in provider_error_message(ModelErrorCode.AUTH)
    assert "网络" in provider_error_message(ModelErrorCode.NETWORK)
    assert "限流" in provider_error_message(ModelErrorCode.RATE_LIMIT)
    assert "超时" in provider_error_message(ModelErrorCode.TIMEOUT)
    assert provider_error_message(ModelErrorCode.NETWORK, phase="connect") == "连接模型服务超时"
    assert (
        provider_error_message(ModelErrorCode.TIMEOUT, phase="first_token")
        == "等待模型首个响应超时"
    )
    assert classify_failure(TimeoutError()).code is ModelErrorCode.TIMEOUT


def test_sdk_transport_errors_and_wrapped_causes_are_network_without_io():
    request = httpx.Request("POST", "https://example.test")
    connection = openai.APIConnectionError(request=request)
    timeout = openai.APITimeoutError(request)
    proxy = httpx.ProxyError("proxy refused", request=request)
    wrapped = RuntimeError("provider request failed")
    wrapped.__cause__ = httpx.ConnectError("connection refused", request=request)

    assert classify_failure(connection).code is ModelErrorCode.NETWORK
    assert classify_failure(timeout).code is ModelErrorCode.TIMEOUT
    assert classify_failure(proxy).code is ModelErrorCode.NETWORK
    assert classify_failure(wrapped).code is ModelErrorCode.NETWORK


@pytest.mark.asyncio
async def test_adapter_connect_timeout_is_network_without_waiting_for_token():
    class HangingCompletions:
        async def create(self, **kwargs):
            del kwargs
            await asyncio.Event().wait()

    provider = OpenAICompatibleProvider(
        "https://example.test",
        "credential-sentinel",
        connect_timeout=0.01,
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=HangingCompletions()))

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["error"]
    assert events[0].failure.code == ModelErrorCode.NETWORK
    assert events[0].failure.message == "连接模型服务超时"
    assert events[0].failure.retryable is True


@pytest.mark.asyncio
async def test_adapter_first_token_timeout_is_distinct_from_connect_timeout():
    class FirstTokenHang:
        async def create(self, **kwargs):
            del kwargs

            async def gen():
                await asyncio.Event().wait()
                yield stream_chunk(text="late", finish="stop")

            return gen()

    provider = OpenAICompatibleProvider(
        "https://example.test",
        "credential-sentinel",
        first_token_timeout=0.01,
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=FirstTokenHang()))

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["error"]
    assert events[0].failure.code == ModelErrorCode.TIMEOUT
    assert events[0].failure.message == "等待模型首个响应超时"
    assert events[0].failure.retryable is True


def test_provider_test_persists_typed_failure_code(tmp_path):
    credentials = MemoryCredentialStore()
    app = build_application(state_root=tmp_path / "state", credentials=credentials)

    class FailingProvider(FakeProvider):
        async def complete(self, model, messages):
            raise ModelProviderError(
                ModelFailure(
                    code=ModelErrorCode.AUTH,
                    origin=ModelFailureOrigin.PROVIDER,
                    message="sanitized failure",
                )
            )

    app.registry.register("openai-compatible", lambda config, credential: FakeProvider())
    app.provider_service.add("opencode-go", "stored-secret")
    app.registry.register("openai-compatible", lambda config, credential: FailingProvider())

    result = app.provider_service.test("opencode-go")

    assert result.ok is False
    assert result.error_code == ModelErrorCode.AUTH
    assert result.message == provider_error_message(ModelErrorCode.AUTH)
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
    assert events[-1].finish_reason == ModelFinishReason.STOP
    assert events[-1].message is not None
    assert events[-1].message.content == "visible"
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


@pytest.mark.parametrize("finish", ["length", "content_filter"])
@pytest.mark.asyncio
async def test_adapter_normalizes_abnormal_finish_without_assembled_message(finish):
    provider = provider_with_stream(
        AsyncChunks([stream_chunk(text="partial"), stream_chunk(finish=finish)])
    )

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["text_delta", "completed"]
    assert events[-1].finish_reason == ModelFinishReason(finish)
    assert events[-1].message is None


@pytest.mark.parametrize("finish", ["function_call", "vendor_custom"])
@pytest.mark.asyncio
async def test_adapter_rejects_non_normal_finish_as_invalid_response(finish):
    provider = provider_with_stream(
        AsyncChunks([stream_chunk(text="partial"), stream_chunk(finish=finish)])
    )

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["text_delta", "error"]
    assert events[-1].failure.code == ModelErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_adapter_rejects_missing_finish_signal():
    provider = provider_with_stream(AsyncChunks([stream_chunk(text="partial")]))

    stream = provider.stream(
        ModelRef(provider_id="p", model_id="m"),
        [UserMessage(content="hello")],
        (),
    )
    events = [event async for event in stream]

    assert events[0].kind == "text_delta"
    assert events[-1].failure.code is ModelErrorCode.INTERNAL
    assert events[-1].failure.origin is ModelFailureOrigin.PROVIDER
    assert events[-1].failure.retryable is True


@pytest.mark.asyncio
async def test_adapter_classifies_malformed_stream_as_invalid_response():
    provider = provider_with_stream(BrokenChunks())

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["error"]
    assert events[-1].failure.code == ModelErrorCode.INVALID_RESPONSE


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
        [UserMessage(content="Reply with the single word 好。")],
    ):
        if event.text:
            visible.append(event.text)
    assert "".join(visible).strip()


# --- Stage 2 request serialization and fragment accumulation ------------------


@pytest.mark.asyncio
async def test_adapter_request_whitelist_sends_tools_and_choice_only_when_present():
    provider = provider_with_stream(AsyncChunks([stream_chunk(text="ok", finish="stop")]))
    events = await collect_stream_with_tools(provider, (demo_tool(), demo_tool("calculate")))
    completions = provider._client.chat.completions
    assert completions.kwargs["tools"] == [
        serialize_tool(demo_tool()),
        serialize_tool(demo_tool("calculate")),
    ]
    assert completions.kwargs["tool_choice"] == "auto"
    assert completions.kwargs["stream"] is True
    assert [event.kind for event in events][-1] == "completed"


@pytest.mark.asyncio
async def test_adapter_omits_tools_and_choice_for_text_only_requests():
    provider = provider_with_stream(AsyncChunks([stream_chunk(text="ok", finish="stop")]))
    await collect_stream(provider)
    kwargs = provider._client.chat.completions.kwargs
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_adapter_assembles_split_pure_tool_call_stream():
    provider = provider_with_stream(
        AsyncChunks(
            [
                tool_stream_chunk(
                    [tool_call_fragment(0, call_id="call_1", name="lookup", call_type="function")]
                ),
                tool_stream_chunk([tool_call_fragment(0, name="_record")]),
                tool_stream_chunk([tool_call_fragment(0, arguments='{"dataset": ')]),
                tool_stream_chunk(
                    [tool_call_fragment(0, arguments='"plans"}')], finish="tool_calls"
                ),
            ]
        )
    )

    events = await collect_stream_with_tools(provider, (demo_tool(),))

    assert [event.kind for event in events] == ["completed"]
    completed = events[-1]
    assert completed.finish_reason == ModelFinishReason.TOOL_CALLS
    assert completed.message is not None
    assert completed.message.content is None
    assert completed.message.tool_calls == (
        FunctionToolCall(id="call_1", name="lookup_record", arguments='{"dataset": "plans"}'),
    )


@pytest.mark.asyncio
async def test_adapter_assembles_mixed_content_and_normalizes_stop_with_calls():
    provider = provider_with_stream(
        AsyncChunks(
            [
                tool_stream_chunk(text="正在查询", fragments=[]),
                tool_stream_chunk(
                    [tool_call_fragment(0, call_id="call_9", name="calculate", arguments="[1, 2]")],
                    finish="stop",
                ),
            ]
        )
    )

    events = await collect_stream_with_tools(provider, (demo_tool("calculate"),))

    assert [event.kind for event in events] == ["text_delta", "completed"]
    completed = events[-1]
    assert completed.finish_reason == ModelFinishReason.TOOL_CALLS
    assert completed.message is not None
    assert completed.message.content == "正在查询"
    assert completed.message.tool_calls[0].id == "call_9"


@pytest.mark.asyncio
async def test_adapter_sorts_interleaved_calls_by_vendor_index():
    provider = provider_with_stream(
        AsyncChunks(
            [
                tool_stream_chunk(
                    [tool_call_fragment(1, call_id="call_b", name="calculate", arguments="{}")]
                ),
                tool_stream_chunk(
                    [
                        tool_call_fragment(
                            0, call_id="call_a", name="lookup_record", arguments='{"k": 1}'
                        )
                    ],
                    finish="tool_calls",
                ),
            ]
        )
    )

    events = await collect_stream_with_tools(provider, (demo_tool(), demo_tool("calculate")))

    completed = events[-1]
    assert [call.id for call in completed.message.tool_calls] == ["call_a", "call_b"]


@pytest.mark.asyncio
async def test_adapter_retains_leading_and_trailing_usage_without_extra_events():
    provider = provider_with_stream(
        AsyncChunks(
            [
                usage_only_chunk(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                stream_chunk(text="vis"),
                stream_chunk(text="ible", finish="stop"),
                usage_only_chunk(prompt_tokens=2, completion_tokens=1, total_tokens=3),
            ]
        )
    )

    events = await collect_stream(provider)

    assert [(event.kind, event.text) for event in events] == [
        ("text_delta", "vis"),
        ("text_delta", "ible"),
        ("completed", None),
    ]
    assert events[-1].usage.availability.value == "available"
    assert events[-1].usage.input_tokens == 2
    assert events[-1].usage.output_tokens == 1
    assert events[-1].usage.total_tokens == 3


@pytest.mark.asyncio
async def test_adapter_accepts_nonsemantic_terminal_echo_with_usage():
    terminal_echo = stream_chunk(finish="stop")
    terminal_echo.usage = SimpleNamespace(
        prompt_tokens=2,
        completion_tokens=1,
        total_tokens=3,
    )
    provider = provider_with_stream(
        AsyncChunks(
            [
                stream_chunk(text="ok"),
                stream_chunk(finish="stop"),
                terminal_echo,
            ]
        )
    )

    events = await collect_stream(provider)

    assert [event.kind for event in events] == ["text_delta", "completed"]
    assert events[-1].finish_reason == ModelFinishReason.STOP
    assert events[-1].usage.total_tokens == 3


@pytest.mark.asyncio
async def test_adapter_marks_missing_usage_unavailable_and_preserves_explicit_zero():
    missing = provider_with_stream(AsyncChunks([stream_chunk(text="ok", finish="stop")]))
    missing_events = await collect_stream(missing)
    assert missing_events[-1].usage.availability.value == "unavailable"
    assert missing_events[-1].usage.total_tokens is None

    zero = provider_with_stream(
        AsyncChunks(
            [
                stream_chunk(text="ok", finish="stop"),
                usage_only_chunk(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            ]
        )
    )
    zero_events = await collect_stream(zero)
    assert zero_events[-1].usage.availability.value == "available"
    assert zero_events[-1].usage.total_tokens == 0


@pytest.mark.asyncio
async def test_adapter_keeps_semantic_completion_when_usage_snapshot_is_malformed():
    events = await collect_stream(
        provider_with_stream(
            AsyncChunks(
                [stream_chunk(text="ok", finish="stop"), usage_only_chunk(total_tokens="3")]
            )
        )
    )

    assert [event.kind for event in events] == ["text_delta", "completed"]
    assert events[-1].usage.availability.value == "unavailable"


@pytest.mark.asyncio
async def test_adapter_uses_latest_cumulative_usage_snapshot_without_model_special_case():
    events = await collect_stream(
        provider_with_stream(
            AsyncChunks(
                [
                    usage_only_chunk(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                    usage_only_chunk(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                    stream_chunk(text="ok", finish="stop"),
                ]
            )
        )
    )

    assert [event.kind for event in events] == ["text_delta", "completed"]
    assert events[-1].usage.input_tokens == 1
    assert events[-1].usage.output_tokens == 2
    assert events[-1].usage.total_tokens == 3


@pytest.mark.asyncio
async def test_adapter_still_rejects_semantic_content_after_finish():
    events = await collect_stream(
        provider_with_stream(
            AsyncChunks([stream_chunk(text="ok", finish="stop"), stream_chunk(text="late")])
        )
    )

    assert events[-1].kind == "error"
    assert events[-1].failure.code == ModelErrorCode.INVALID_RESPONSE
    assert events[-1].usage.availability.value == "unavailable"


@pytest.mark.asyncio
async def test_adapter_preserves_valid_usage_when_later_semantic_chunk_is_invalid():
    events = await collect_stream(
        provider_with_stream(
            AsyncChunks(
                [
                    stream_chunk(text="ok", finish="stop"),
                    usage_only_chunk(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                    stream_chunk(text="late"),
                ]
            )
        )
    )

    assert events[-1].kind == "error"
    assert events[-1].failure.code == ModelErrorCode.INVALID_RESPONSE
    assert events[-1].usage.total_tokens == 3


@pytest.mark.asyncio
async def test_adapter_requests_stream_usage_with_supported_request_field():
    provider = provider_with_stream(AsyncChunks([stream_chunk(text="ok", finish="stop")]))
    await collect_stream(provider)
    assert provider._client.chat.completions.kwargs["stream_options"] == {"include_usage": True}


def test_accumulator_preserves_argument_string_fidelity():
    accumulator = StreamAccumulator()
    accumulator.add_tool_fragment(
        tool_call_fragment(0, call_id="c", name="calculate", arguments='{"a": 1')
    )
    accumulator.add_tool_fragment(tool_call_fragment(0, arguments=', "b": [2, 3]} '))
    accumulator.set_finish("tool_calls")
    message, reason = accumulator.build()
    assert reason == ModelFinishReason.TOOL_CALLS
    assert message.tool_calls[0].arguments == '{"a": 1, "b": [2, 3]} '


def test_accumulator_ignores_whitespace_only_text_alongside_tool_calls():
    accumulator = StreamAccumulator()
    accumulator.add_text(" \n\t")
    accumulator.add_tool_fragment(
        tool_call_fragment(0, call_id="call_1", name="lookup_record", arguments='{"key": 1}')
    )
    accumulator.set_finish("tool_calls")

    message, reason = accumulator.build()

    assert reason == ModelFinishReason.TOOL_CALLS
    assert message.content is None
    assert message.tool_calls[0].name == "lookup_record"


@pytest.mark.asyncio
async def test_adapter_accepts_whitespace_only_text_with_valid_tool_calls():
    provider = provider_with_stream(
        AsyncChunks(
            [
                tool_stream_chunk(
                    [
                        tool_call_fragment(
                            0,
                            call_id="call_1",
                            name="lookup_record",
                            arguments='{"key": 1}',
                        )
                    ],
                    text=" \n\t",
                    finish="tool_calls",
                )
            ]
        )
    )

    events = await collect_stream_with_tools(provider, (demo_tool(),))

    assert events[-1].kind == "completed"
    assert events[-1].finish_reason == ModelFinishReason.TOOL_CALLS
    assert events[-1].message.content is None


@pytest.mark.parametrize(
    "chunks",
    [
        # conflicting id for the same vendor index
        AsyncChunks(
            [
                tool_stream_chunk([tool_call_fragment(0, call_id="call_1", name="lookup_record")]),
                tool_stream_chunk([tool_call_fragment(0, call_id="call_2")]),
                stream_chunk(finish="tool_calls"),
            ]
        ),
        # duplicate ids across vendor indexes
        AsyncChunks(
            [
                tool_stream_chunk([tool_call_fragment(0, call_id="same", name="lookup_record")]),
                tool_stream_chunk([tool_call_fragment(1, call_id="same", name="calculate")]),
                stream_chunk(finish="tool_calls"),
            ]
        ),
        # id never arrives
        AsyncChunks(
            [
                tool_stream_chunk([tool_call_fragment(0, name="lookup_record")]),
                stream_chunk(finish="tool_calls"),
            ]
        ),
        # name never arrives
        AsyncChunks(
            [
                tool_stream_chunk([tool_call_fragment(0, call_id="call_1")]),
                stream_chunk(finish="tool_calls"),
            ]
        ),
        # name violates the Core function-name contract
        AsyncChunks(
            [
                tool_stream_chunk(
                    [tool_call_fragment(0, call_id="call_1", name="not a valid name")]
                ),
                stream_chunk(finish="tool_calls"),
            ]
        ),
        # unsupported fragment type
        AsyncChunks(
            [
                tool_stream_chunk([tool_call_fragment(0, call_id="c", name="x", call_type="code")]),
                stream_chunk(finish="tool_calls"),
            ]
        ),
        # tool_calls finish without any call fragment
        AsyncChunks([stream_chunk(text="partial", finish="tool_calls")]),
        # more than one logical choice
        AsyncChunks(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=None, reasoning_content=None),
                            finish_reason=None,
                        ),
                        SimpleNamespace(
                            delta=SimpleNamespace(content=None, reasoning_content=None),
                            finish_reason=None,
                        ),
                    ]
                ),
                stream_chunk(finish="stop"),
            ]
        ),
    ],
)
@pytest.mark.asyncio
async def test_adapter_rejects_malformed_tool_streams(chunks):
    provider = provider_with_stream(chunks)
    events = await collect_stream_with_tools(provider, (demo_tool(),))
    assert events[-1].kind == "error"
    assert events[-1].failure.code == ModelErrorCode.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_adapter_rejects_non_string_fragment_arguments():
    provider = provider_with_stream(
        AsyncChunks(
            [
                tool_stream_chunk(
                    [
                        SimpleNamespace(
                            index=0,
                            id="c",
                            type="function",
                            function=SimpleNamespace(name="calculate", arguments=123),
                        )
                    ],
                    finish="tool_calls",
                ),
            ]
        )
    )
    events = await collect_stream_with_tools(provider, (demo_tool("calculate"),))
    assert events[-1].kind == "error"
    assert events[-1].failure.code == ModelErrorCode.INVALID_RESPONSE


def test_accumulator_rejects_conflicting_finish_and_reports_progress():
    accumulator = StreamAccumulator()
    assert accumulator.made_progress is False
    accumulator.set_finish("stop")
    with pytest.raises(ValueError, match="conflicting"):
        accumulator.set_finish("length")
    accumulator.add_text("hi")
    assert accumulator.made_progress is True


def test_serialize_after_assemble_round_trip_is_deterministic():
    accumulator = StreamAccumulator()
    accumulator.add_tool_fragment(
        tool_call_fragment(0, call_id="call_1", name="lookup_record", arguments='{"k": 1}')
    )
    accumulator.set_finish("tool_calls")
    message, reason = accumulator.build()
    assert reason == ModelFinishReason.TOOL_CALLS
    assert serialize_message(message) == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup_record", "arguments": '{"k": 1}'},
            }
        ],
    }


def test_tool_schema_wire_clamps_non_interoperable_integer_bounds_without_mutation():
    schema = {
        "type": "object",
        "properties": {
            "offset": {
                "type": "integer",
                "minimum": -(10**309 - 1),
                "maximum": 10**309 - 1,
            }
        },
    }

    normalized = normalize_tool_schema(schema)

    assert normalized["properties"]["offset"] == {
        "type": "integer",
        "minimum": -(2**53 - 1),
        "maximum": 2**53 - 1,
    }
    assert schema["properties"]["offset"]["maximum"] == 10**309 - 1


@pytest.mark.parametrize("status_code", [400, 402, 404, 422])
def test_adapter_classifies_provider_request_rejections_as_invalid_response(status_code):
    error = RuntimeError("provider rejected request")
    error.status_code = status_code

    assert classify_failure(error).code is ModelErrorCode.INVALID_RESPONSE


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (408, ModelErrorCode.TIMEOUT),
        (409, ModelErrorCode.INTERNAL),
        (429, ModelErrorCode.RATE_LIMIT),
        (500, ModelErrorCode.INTERNAL),
        (502, ModelErrorCode.INTERNAL),
        (503, ModelErrorCode.INTERNAL),
        (504, ModelErrorCode.INTERNAL),
        (524, ModelErrorCode.INTERNAL),
    ],
)
def test_adapter_classifies_pi_transient_provider_statuses(status_code, expected):
    error = RuntimeError("provider request failed")
    error.status_code = status_code

    assert classify_failure(error).code is expected


@pytest.mark.asyncio
async def test_adapter_marks_server_internal_as_confirmed_provider_transient():
    class FailingCompletions:
        async def create(self, **kwargs):
            del kwargs
            error = RuntimeError("provider request failed")
            error.status_code = 503
            raise error

    provider = OpenAICompatibleProvider("https://example.test", "credential-sentinel")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))

    events = await collect_stream(provider)

    assert events[-1].failure.code is ModelErrorCode.INTERNAL
    assert events[-1].failure.origin is ModelFailureOrigin.PROVIDER
    assert events[-1].failure.retryable is True


@pytest.mark.parametrize(
    "message",
    [
        "GoUsageLimitError",
        "Monthly usage limit reached; enable available balance",
        "insufficient_quota",
        "out of budget",
        "quota exceeded",
        "billing is not active",
        "insufficient balance",
    ],
)
def test_adapter_does_not_classify_terminal_provider_limits_as_retryable(message):
    error = RuntimeError(message)
    error.status_code = 429

    assert classify_failure(error).code is ModelErrorCode.INVALID_RESPONSE


def test_adapter_classifies_nested_value_errors_as_invalid_response():
    error = RuntimeError("provider request failed")
    error.__cause__ = ValueError("malformed provider chunk")

    assert classify_failure(error).code is ModelErrorCode.INVALID_RESPONSE
    assert serialize_tool(demo_tool()) == {
        "type": "function",
        "function": {
            "name": "lookup_record",
            "description": "Look up an injected in-memory record.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    }


@pytest.mark.asyncio
async def test_completion_facts_preserve_usage_and_output_limit_and_do_not_hide_truncation():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="length", message=SimpleNamespace(content='{"goal":"partial"}')
            )
        ],
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=30, total_tokens=230),
    )
    provider = provider_with_stream(response)
    model = ModelRef(provider_id="test", model_id="summary")
    messages = [UserMessage(content="Summarize")]
    result = await provider.complete_result(model, messages, max_output_tokens=30)
    assert result.finish_reason is ModelFinishReason.LENGTH
    assert result.usage.total_tokens == 230
    assert provider._client.chat.completions.kwargs["max_tokens"] == 30
    from morrow.core.models import ModelProviderError

    with pytest.raises(ModelProviderError):
        await provider.complete(model, messages)
