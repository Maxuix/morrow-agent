"""Offline image token vs transmission budget split (parser/context/runtime)."""

from __future__ import annotations

import base64
import hashlib
import io

import pytest
from PIL import Image

from morrow.adapters.attachment_parser import parse
from morrow.adapters.models.openai_compatible import (
    estimate_request_chars,
    make_request_token_estimator,
)
from morrow.application.context import ContextBudgetError, ContextBuilder
from morrow.core.compaction import TokenAccountingBasis
from morrow.core.image_tokens import estimate_image_tokens, image_dimensions
from morrow.core.models import (
    AgentStopCode,
    AssistantMessage,
    AttachmentRef,
    FinishReason,
    ModelEvent,
    ModelFinishReason,
    ModelRef,
    ModelUsage,
    ProviderInputPart,
    UsageAvailability,
    UserMessage,
)
from morrow.runtime.agent import AgentLoop
from morrow.runtime.session import Session
from morrow.testing import ScriptedModelProvider, make_context_builder

MODEL = ModelRef(provider_id="test", model_id="test")


def _png_bytes(width: int, height: int, *, seed: int | None = None, color=(255, 0, 0)) -> bytes:
    if seed is None:
        image = Image.new("RGB", (width, height), color)
    else:
        need = width * height * 3
        payload = bytearray()
        index = 0
        while len(payload) < need:
            payload.extend(hashlib.sha256(f"{seed}:{index}".encode()).digest())
            index += 1
        image = Image.frombytes("RGB", (width, height), bytes(payload[:need]))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _parse_image(png_bytes: bytes) -> tuple[bytes, int, int]:
    result = parse(png_bytes, "image/png")
    mime, _page, data, preview, width, height = result["parts"][0]
    assert mime == "image/png" and preview is False
    assert (width, height) == image_dimensions(data)
    return data, width, height


def _ref(name: str) -> AttachmentRef:
    digest = hashlib.sha256(name.encode()).hexdigest()
    return AttachmentRef(
        attachment_id=f"att_{name}",
        content_digest=digest,
        representation_id=f"art_{name}",
        representation_digest=digest,
    )


def _resolver(products: dict[str, tuple[bytes, int, int]]):
    def resolve(message: UserMessage) -> UserMessage:
        text = [message.content]
        parts: list[ProviderInputPart] = []
        for reference in message.attachments:
            data, width, height = products[reference.attachment_id]
            text.append("\n[用户附件资料：image.png；读取方式：image；省略字符：0]")
            parts.append(
                ProviderInputPart(
                    type="image",
                    media_type="image/png",
                    data=base64.b64encode(data).decode(),
                    width=width,
                    height=height,
                )
            )
        content = "\n".join(text)
        return message.model_copy(
            update={
                "content": content,
                "input_parts": (ProviderInputPart(type="text", text=content), *parts),
            }
        )

    return resolve


def _builder(
    resolver,
    *,
    model: ModelRef = MODEL,
    context_window_tokens: int | None = 128_000,
    payload_char_limit: int | None = None,
    **policy,
) -> ContextBuilder:
    return make_context_builder(
        model=model,
        estimate_request_tokens=make_request_token_estimator(model),
        attachment_resolver=resolver,
        payload_char_limit=payload_char_limit,
        context_window_tokens=context_window_tokens,
        **policy,
    )


def _user(text: str, *refs: AttachmentRef) -> UserMessage:
    return UserMessage(content=text, attachments=refs)


def _terminal(events):
    completed = [event for event in events if event.type == "turn.completed"]
    assert completed
    return completed[-1]


async def _run_loop(builder, message, provider=None):
    session = Session(session_id="s")
    session.begin_user_turn(message)
    provider = provider or ScriptedModelProvider(["ok"])
    loop = AgentLoop(provider, MODEL, builder)
    events = [event async for event in loop.run_task(session, "", resume_current_turn=True)]
    return session, provider, events


def test_parser_preserves_sent_png_dimensions():
    data, width, height = _parse_image(_png_bytes(800, 640, color=(0, 128, 255)))
    assert (width, height) == (800, 640)
    assert image_dimensions(data) == (800, 640)


def test_same_size_images_do_not_scale_tokens_with_png_bytes():
    solid_src = _png_bytes(800, 640, color=(10, 20, 30))
    noisy_src = _png_bytes(800, 640, seed=7)
    solid, sw, sh = _parse_image(solid_src)
    noisy, nw, nh = _parse_image(noisy_src)
    assert (sw, sh) == (nw, nh) == (800, 640)
    assert len(noisy) > 1_000_000
    assert len(solid) < 8_000
    refs = (_ref("solid"), _ref("noisy"))
    products = {
        refs[0].attachment_id: (solid, sw, sh),
        refs[1].attachment_id: (noisy, nw, nh),
    }
    builder = _builder(_resolver(products))
    session = Session(session_id="s")
    session.log.begin_turn(_user("看图", refs[0]))
    solid_pack = builder.build(session)
    session = Session(session_id="s")
    session.log.begin_turn(_user("看图", refs[1]))
    noisy_pack = builder.build(session)
    assert solid_pack.estimated_context_tokens == noisy_pack.estimated_context_tokens
    payload_solid = estimate_request_chars(solid_pack.messages, ())
    payload_noisy = estimate_request_chars(noisy_pack.messages, ())
    assert payload_noisy > payload_solid * 10
    image_tokens = estimate_image_tokens(800, 640)
    assert abs(solid_pack.estimated_context_tokens - noisy_pack.estimated_context_tokens) == 0
    assert solid_pack.estimated_context_tokens > image_tokens


@pytest.mark.asyncio
async def test_large_png_within_model_window_calls_scripted_provider():
    data, width, height = _parse_image(_png_bytes(800, 640, seed=3))
    assert len(data) > 1_000_000
    ref = _ref("large")
    builder = _builder(_resolver({ref.attachment_id: (data, width, height)}))
    provider = ScriptedModelProvider(["描述完成"])
    _session, provider, events = await _run_loop(builder, _user("描述这张图", ref), provider)
    assert len(provider.stream_calls) == 1
    assert _terminal(events).payload["finish_reason"] == FinishReason.STOP.value
    wire = provider.stream_calls[0]
    assert any(getattr(message, "input_parts", ()) for message in wire)


@pytest.mark.asyncio
async def test_unknown_window_excludes_base64_from_text_budget():
    data, width, height = _parse_image(_png_bytes(320, 320, seed=11))
    ref = _ref("unknown")
    builder = _builder(
        _resolver({ref.attachment_id: (data, width, height)}),
        context_window_tokens=None,
    )
    provider = ScriptedModelProvider(["ok"])
    _session, provider, events = await _run_loop(builder, _user("看图", ref), provider)
    assert len(provider.stream_calls) == 1
    assert _terminal(events).payload["finish_reason"] == FinishReason.STOP.value
    session = Session(session_id="s")
    session.log.begin_turn(_user("看图", ref))
    pack = builder.build(session)
    assert pack.estimated_request_chars > builder.request_char_limit
    assert pack.compaction_required is False


@pytest.mark.asyncio
async def test_multiple_images_sum_dimension_tokens():
    first = _parse_image(_png_bytes(200, 200, seed=1))
    second = _parse_image(_png_bytes(200, 200, seed=2))
    refs = (_ref("a"), _ref("b"))
    products = {
        refs[0].attachment_id: first,
        refs[1].attachment_id: second,
    }
    builder = _builder(_resolver(products))
    session = Session(session_id="s")
    session.log.begin_turn(_user("两张", *refs))
    pack = builder.build(session)
    image_tokens = sum(
        estimate_image_tokens(part.width, part.height)
        for message in pack.messages
        for part in getattr(message, "input_parts", ())
        if getattr(part, "type", None) == "image"
    )
    assert image_tokens == 2 * estimate_image_tokens(200, 200)
    assert pack.estimated_context_tokens >= image_tokens


def test_scanned_pdf_uses_rendered_pixel_size():
    pdf = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(pdf, format="PDF")
    parsed = parse(pdf.getvalue(), "application/pdf")
    assert parsed["reading"] == "pdf_images"
    mime, page, data, preview, width, height = parsed["parts"][0]
    assert mime == "image/png" and page == 1 and preview is False
    assert (width, height) == image_dimensions(data)
    ref = _ref("pdf")
    builder = _builder(_resolver({ref.attachment_id: (data, width, height)}))
    session = Session(session_id="s")
    session.log.begin_turn(_user("扫描件", ref))
    pack = builder.build(session)
    image_tokens = estimate_image_tokens(width, height)
    assert pack.estimated_context_tokens == builder.estimate_request_tokens(pack.messages, ())
    assert pack.estimated_context_tokens >= image_tokens
    assert image_tokens == estimate_image_tokens(*image_dimensions(data))


def test_image_history_can_request_compaction_without_dropping_current_input():
    data, width, height = _parse_image(_png_bytes(800, 640, color=(1, 2, 3)))
    old_ref, new_ref = _ref("old"), _ref("new")
    products = {
        old_ref.attachment_id: (data, width, height),
        new_ref.attachment_id: (data, width, height),
    }
    builder = _builder(
        _resolver(products),
        context_window_tokens=4_000,
        reserve_tokens=1_000,
        keep_recent_tokens=400,
    )
    session = Session(session_id="s")
    session.log.begin_turn(_user("历史图", old_ref))
    session.log.append_assistant(AssistantMessage(content="old image seen"))
    session.log.finish_turn(FinishReason.STOP)
    session.log.begin_turn(_user("当前", new_ref))
    pack = builder.build(session)
    assert pack.compaction_required is True
    candidate = builder.prepare_compaction(session)
    assert candidate is not None


@pytest.mark.asyncio
async def test_provider_usage_anchor_adds_only_new_image_tokens():
    data, width, height = _parse_image(_png_bytes(96, 96, seed=5))
    ref = _ref("usage")
    builder = _builder(_resolver({ref.attachment_id: (data, width, height)}))
    usage = ModelUsage(
        availability=UsageAvailability.AVAILABLE,
        input_tokens=1_230,
        output_tokens=20,
        total_tokens=1_250,
    )

    class UsageProvider:
        stream_calls: list = []

        async def stream(self, model, messages, tools=(), generation=None):
            del model, tools, generation
            self.stream_calls.append(list(messages))
            yield ModelEvent(kind="text_delta", text="first")
            yield ModelEvent(
                kind="completed",
                finish_reason=ModelFinishReason.STOP,
                message=AssistantMessage(content="first"),
                usage=usage,
            )

    provider = UsageProvider()
    session = Session(session_id="s")
    loop = AgentLoop(provider, MODEL, builder)
    [event async for event in loop.run_task(session, "hello")]
    assert session.latest_model_usage.input_tokens == 1_230
    session.begin_user_turn(_user("加上图片", ref))
    pack = builder.build(session)
    assert pack.accounting_basis is TokenAccountingBasis.PROVIDER_USAGE
    assert pack.estimated_context_tokens >= 1_230 + 20 + estimate_image_tokens(96, 96)


@pytest.mark.asyncio
async def test_oversized_pixels_reject_current_input_without_provider_call():
    data, width, height = _parse_image(_png_bytes(4000, 4000, color=(9, 9, 9)))
    ref = _ref("huge")
    builder = _builder(
        _resolver({ref.attachment_id: (data, width, height)}),
        context_window_tokens=8_192,
        reserve_tokens=4_096,
    )
    provider = ScriptedModelProvider(["should not run"])
    _session, provider, events = await _run_loop(builder, _user("超大图", ref), provider)
    assert provider.stream_calls == []
    terminal = _terminal(events)
    assert terminal.payload["finish_reason"] == FinishReason.ERROR.value
    assert terminal.payload["stop_code"] == AgentStopCode.CONTEXT_BUDGET.value
    error = [event for event in events if event.type == "error"][0]
    assert error.payload["message"] == "当前输入超过模型上下文限制"
    assert all(event.payload.get("status") != "compacting" for event in events)


@pytest.mark.asyncio
async def test_payload_limit_is_separate_from_model_tokens():
    data, width, height = _parse_image(_png_bytes(800, 640, seed=9))
    ref = _ref("payload")
    builder = _builder(
        _resolver({ref.attachment_id: (data, width, height)}),
        payload_char_limit=50_000,
    )
    session = Session(session_id="s")
    session.log.begin_turn(_user("体积", ref))
    with pytest.raises(ContextBudgetError, match="模型请求超过传输体积限制") as exc:
        builder.build(session)
    assert exc.value.kind == "payload"
    provider = ScriptedModelProvider(["should not run"])
    _session, provider, events = await _run_loop(builder, _user("体积", ref), provider)
    assert provider.stream_calls == []
    assert _terminal(events).payload["stop_code"] == AgentStopCode.CONTEXT_BUDGET.value
    error = [event for event in events if event.type == "error"][0]
    assert error.payload["message"] == "模型请求超过传输体积限制"


def test_history_payload_can_compact_while_current_image_fits():
    data, width, height = _parse_image(_png_bytes(320, 320, seed=4))
    old_ref, new_ref = _ref("hist"), _ref("now")
    products = {
        old_ref.attachment_id: (data, width, height),
        new_ref.attachment_id: (data, width, height),
    }
    builder = _builder(_resolver(products), payload_char_limit=500_000, keep_recent_tokens=50)
    session = Session(session_id="s")
    session.log.begin_turn(_user("旧图", old_ref))
    session.log.append_assistant(AssistantMessage(content="old"))
    session.log.finish_turn(FinishReason.STOP)
    session.log.begin_turn(_user("新图", new_ref))
    pack = builder.build(session)
    assert pack.compaction_required is True
    candidate = builder.prepare_compaction(session)
    assert candidate is not None
    now = Session(session_id="now")
    now.log.begin_turn(_user("新图", new_ref))
    current = builder.build(now)
    assert current.compaction_required is False


def test_log_keeps_attachment_refs_without_base64():
    data, width, height = _parse_image(_png_bytes(32, 32, color=(0, 0, 0)))
    ref = _ref("refonly")
    builder = _builder(_resolver({ref.attachment_id: (data, width, height)}))
    session = Session(session_id="s")
    session.begin_user_turn(_user("引用", ref))
    pack = builder.build(session)
    dumped = session.log.snapshot().records[0].message.model_dump_json()
    assert "base64" not in dumped
    assert ref.attachment_id in dumped
    assert pack.messages[-1].input_parts[-1].data
