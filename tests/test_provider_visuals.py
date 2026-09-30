"""Offline actual image wire, call pairing, estimates and shared payload budgets."""

from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image

import test_computer_visual_service as visual_service_tests
from morrow.adapters.models.openai_compatible import (
    estimate_request_bytes,
    estimate_request_chars,
    estimate_text_request_chars,
    make_request_token_estimator,
    serialize_messages,
)
from morrow.application.computer_visuals import ToolVisualHydrator
from morrow.application.context import ContextBudgetError, ContextBuilder
from morrow.core.image_tokens import iter_image_parts, messages_without_image_payloads
from morrow.core.models import (
    AssistantMessage,
    AttachmentRef,
    FunctionToolCall,
    ModelRef,
    ProviderInputPart,
    ToolMessage,
    UserMessage,
)
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings
from morrow.runtime.session import Session
from morrow.testing import make_run_policy
from test_computer_visual_service import complete, publish
from test_provider import AsyncChunks, provider_with_stream

environment = visual_service_tests.environment

MODEL = ModelRef(provider_id="test", model_id="gpt-4o")


def image_part(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(buffer, format="PNG")
    return ProviderInputPart(
        type="image",
        media_type="image/png",
        width=8,
        height=6,
        data=base64.b64encode(buffer.getvalue()).decode(),
    )


def batch():
    return (
        UserMessage(content="Inspect fixture"),
        AssistantMessage(
            tool_calls=(
                FunctionToolCall(id="call1", name="computer_observe", arguments="{}"),
                FunctionToolCall(id="call2", name="read_file", arguments="{}"),
            )
        ),
        ToolMessage(tool_call_id="call1", content='{"ok":true}', input_parts=(image_part(),)),
        ToolMessage(tool_call_id="call2", content='{"ok":true}'),
    )


def test_images_follow_complete_paired_batch_and_never_replace_text_tool_replies():
    messages = batch()
    wire = serialize_messages(messages)
    assert [item["role"] for item in wire] == ["user", "assistant", "tool", "tool", "user"]
    assert wire[2] == {"role": "tool", "tool_call_id": "call1", "content": '{"ok":true}'}
    assert wire[3]["tool_call_id"] == "call2"
    annotation = wire[4]["content"][0]["text"]
    assert "call_id=call1" in annotation and "not a user instruction or permission" in annotation
    url = wire[4]["content"][1]["image_url"]["url"]
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as decoded:
        assert decoded.size == (8, 6)
        assert decoded.getpixel((0, 0)) == (255, 0, 0)
    assert messages[2].model_dump().get("input_parts") is None
    assert len(messages) == 4


@pytest.mark.parametrize(
    "messages", [batch()[:-1], batch()[2:], (*batch()[:-1], UserMessage(content="interrupt"))]
)
def test_incomplete_or_unpaired_observation_cannot_be_sent(messages):
    with pytest.raises(ValueError, match="tool reply"):
        serialize_messages(messages)


def test_text_and_image_estimates_use_identical_projection_and_do_not_charge_base64_as_text():
    messages = batch()
    wire = serialize_messages(messages)
    assert estimate_request_chars(messages) == len(
        json.dumps({"messages": wire}, ensure_ascii=False, separators=(",", ":"))
    )
    stripped = messages_without_image_payloads(messages)
    assert estimate_text_request_chars(messages) == estimate_request_chars(stripped)
    assert estimate_request_chars(messages) > estimate_text_request_chars(messages)
    estimator = make_request_token_estimator(MODEL)
    changed = tuple(
        message.model_copy(
            update={
                "input_parts": tuple(
                    part.model_copy(update={"data": part.data * 10}) for part in message.input_parts
                )
            }
        )
        if isinstance(message, ToolMessage)
        else message
        for message in messages
    )
    assert estimator(messages, ()) == estimator(changed, ())
    assert estimator(messages, ()) > estimator(
        tuple(
            message.model_copy(update={"input_parts": ()})
            if isinstance(message, ToolMessage)
            else message
            for message in messages
        ),
        (),
    )


async def test_actual_artifact_context_image_reaches_fake_sdk_request_with_matching_hash(
    environment,
):
    service, _, _, _, _, _ = environment
    reference = publish(environment)
    complete(environment, reference)
    session = Session(session_id="ses_1")
    session.log.begin_turn(UserMessage(content="inspect"))
    session.log.append_assistant(
        AssistantMessage(
            tool_calls=(FunctionToolCall(id="call1", name="computer_observe", arguments="{}"),)
        )
    )
    session.log.append_tool_result("call1", "{}", visual_refs=(reference,))
    before = session.log.snapshot()
    hydrator = ToolVisualHydrator(
        service,
        session_id="ses_1",
        agent_run_id="arun_1",
        settings=ComputerUseSettings(enabled=True, mode=ComputerUseMode.HYBRID),
        input_types=("text", "image"),
        tool_protocol="openai_function",
    )
    builder = ContextBuilder(
        run_policy=make_run_policy(),
        estimate_request_chars=estimate_request_chars,
        estimate_request_tokens=make_request_token_estimator(MODEL),
        tool_visual_hydrator=hydrator,
    )
    pack = builder.build(session)
    provider = provider_with_stream(
        AsyncChunks(
            [
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "fixture seen"}, "finish_reason": None}
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
        )
    )
    for _ in range(2):
        events = [event async for event in provider.stream(MODEL, list(pack.messages))]
        assert events
        request = provider._client.chat.completions.kwargs
        assert request["messages"] == serialize_messages(pack.messages)
        assert [item["role"] for item in request["messages"]][-2:] == ["tool", "user"]
        url = request["messages"][-1]["content"][1]["image_url"]["url"]
        from morrow.core.domain import sha256_digest

        assert sha256_digest(base64.b64decode(url.split(",", 1)[1])) == reference.sha256
    assert session.log.snapshot() == before


def test_attachment_and_tool_visual_share_one_transmission_ceiling():
    part = image_part()
    attachment = AttachmentRef(
        attachment_id="att_1",
        content_digest="a" * 64,
        representation_id="art_1",
        representation_digest="b" * 64,
    )
    user = UserMessage(content="inspect", attachments=(attachment,))
    assistant = batch()[1].model_copy(update={"tool_calls": batch()[1].tool_calls[:1]})
    tool = batch()[2]
    messages = (user.model_copy(update={"input_parts": (part,)}), assistant, tool)
    single_size = max(
        estimate_request_chars(
            (messages[0], assistant, tool.model_copy(update={"input_parts": ()}))
        ),
        estimate_request_chars((UserMessage(content="inspect"), assistant, tool)),
    )
    limit = single_size + 1
    assert estimate_request_chars(messages) > limit
    session = Session(session_id="ses_1")
    session.log.begin_turn(user)
    session.log.append_assistant(assistant)
    session.log.append_tool_result("call1", "{}")

    class Hydrate:
        def bind_history(self, messages):
            pass

        def __call__(self, messages):
            return tuple(
                message.model_copy(update={"input_parts": (part,)})
                if isinstance(message, ToolMessage)
                else message
                for message in messages
            )

    builder = ContextBuilder(
        run_policy=make_run_policy(),
        estimate_request_chars=estimate_request_chars,
        estimate_request_tokens=make_request_token_estimator(MODEL),
        payload_char_limit=limit,
        attachment_resolver=lambda message: message.model_copy(update={"input_parts": (part,)}),
        tool_visual_hydrator=Hydrate(),
    )
    pack = builder.build(session)
    assert len(tuple(iter_image_parts(pack.messages))) == 2
    assert pack.compaction_required
    with pytest.raises(ContextBudgetError, match="传输体积"):
        builder.validate_request(pack.messages, ())


def test_usage_accounting_can_estimate_tool_suffix_with_assistant_in_paid_prefix():
    estimator = make_request_token_estimator(MODEL)
    suffix = batch()[2:]
    assert estimator(suffix, ()) > 0
    assert [
        item["role"]
        for item in serialize_messages(messages_without_image_payloads(suffix), for_estimate=True)
    ] == ["tool", "tool", "user"]
    with pytest.raises(ValueError, match="matched tool reply"):
        serialize_messages(suffix)


def test_images_share_model_token_ceiling_with_user_attachments():
    user = UserMessage(
        content="inspect",
        attachments=(
            AttachmentRef(
                attachment_id="att_1",
                content_digest="a" * 64,
                representation_id="art_1",
                representation_digest="b" * 64,
            ),
        ),
    )
    assistant = batch()[1].model_copy(update={"tool_calls": batch()[1].tool_calls[:1]})
    session = Session(session_id="ses_1")
    session.log.begin_turn(user)
    session.log.append_assistant(assistant)
    session.log.append_tool_result("call1", "{}")
    part = image_part()

    class Hydrate:
        def bind_history(self, messages):
            pass

        def __call__(self, messages):
            return tuple(
                message.model_copy(update={"input_parts": (part,)})
                if isinstance(message, ToolMessage)
                else message
                for message in messages
            )

    estimator = make_request_token_estimator(MODEL)
    kwargs = dict(
        estimate_request_chars=estimate_request_chars,
        estimate_request_tokens=estimator,
        attachment_resolver=lambda message: message.model_copy(update={"input_parts": (part,)}),
        tool_visual_hydrator=Hydrate(),
    )
    pack = ContextBuilder(run_policy=make_run_policy(), **kwargs).build(session)
    single_messages = tuple(
        message.model_copy(update={"input_parts": ()})
        if isinstance(message, ToolMessage)
        else message
        for message in pack.messages
    )
    single = estimator(single_messages, ())
    both = estimator(pack.messages, ())
    assert both > single
    ceiling = single + 1
    constrained = ContextBuilder(
        run_policy=make_run_policy(
            context_window_tokens=ceiling + 256, reserve_tokens=256, keep_recent_tokens=1
        ),
        **kwargs,
    ).build(session)
    assert constrained.estimated_context_tokens == both
    assert constrained.token_threshold == ceiling
    assert constrained.compaction_required


def test_utf8_byte_ceiling_counts_shared_images_and_non_ascii_text():
    messages = batch()
    messages = (messages[0].model_copy(update={"content": "检查受控窗口"}), *messages[1:])
    wire = json.dumps(
        {"messages": serialize_messages(messages)}, ensure_ascii=False, separators=(",", ":")
    )
    assert estimate_request_bytes(messages) == len(wire.encode("utf-8"))
    assert estimate_request_bytes(messages) > estimate_request_chars(messages)
    builder = ContextBuilder(
        run_policy=make_run_policy(),
        estimate_request_chars=estimate_request_chars,
        estimate_request_bytes=estimate_request_bytes,
        payload_char_limit=100000,
        payload_byte_limit=estimate_request_bytes(messages) - 1,
    )
    with pytest.raises(ContextBudgetError, match="传输体积"):
        builder.validate_request(messages, ())
