"""Exact capabilities intersect implemented mappings; fake SDK request evidence."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from morrow.adapters.models.openai_compatible import generation_fields
from morrow.adapters.registry import AdapterRegistry
from morrow.core.agent_runs import ProviderCapabilities, exact_model_capabilities
from morrow.core.models import GenerationOptions, ModelCapabilityOverrides, ModelRef, UserMessage
from test_provider import AsyncChunks, provider_with_stream

MODEL = ModelRef(provider_id="demo", model_id="reasoner")


def test_reasoning_requires_both_implementation_and_exact_model_declaration():
    registry = AdapterRegistry()
    declared = ProviderCapabilities(reasoning_efforts=("low", "high", "max"))
    registry.register(
        "fake", lambda c, k: None, capabilities=declared, reasoning_implementation=("low", "high")
    )
    adapter = registry.capabilities("fake")
    assert adapter.reasoning_efforts == ("low", "high")
    assert exact_model_capabilities("fake", adapter, MODEL).reasoning_efforts == ()
    exact = exact_model_capabilities(
        "fake", adapter, MODEL, ModelCapabilityOverrides(reasoning_efforts=("high", "max"))
    )
    assert exact.reasoning_efforts == ("high",)
    registry.register("no-mapping", lambda c, k: None, capabilities=declared)
    assert registry.capabilities("no-mapping").reasoning_efforts == ()
    with pytest.raises(ValidationError):
        GenerationOptions(reasoning_effort="made-up")
    with pytest.raises(ValidationError):
        GenerationOptions(temperature=1)
    with pytest.raises(ValueError):
        generation_fields({"reasoning_effort": "high"})


@pytest.mark.parametrize("effort", ["low", "high", None])
async def test_sdk_request_contains_only_the_selected_supported_parameter(effort):
    provider = provider_with_stream(
        AsyncChunks(
            [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            index=0,
                            delta=SimpleNamespace(content="visible", tool_calls=None),
                            finish_reason="stop",
                        )
                    ]
                )
            ]
        )
    )
    events = [
        e
        async for e in provider.stream(
            MODEL,
            [UserMessage(content="hello")],
            generation=GenerationOptions(reasoning_effort=effort),
        )
    ]
    assert any(e.kind == "completed" for e in events)
    request = provider._client.chat.completions.kwargs
    if effort is None:
        assert "reasoning_effort" not in request
    else:
        assert request["reasoning_effort"] == effort
    assert "reasoning" not in request


@pytest.mark.parametrize("with_attachment", [False, True])
async def test_actual_chat_sdk_tools_retry_next_run_and_rehydration(tmp_path, with_attachment):
    import httpx
    import openai

    from morrow.adapters.models.openai_compatible import OpenAICompatibleProvider
    from morrow.core.models import ChatSettings
    from morrow.core.runtime_policy import LongHorizonPolicyOverrides, RuntimePolicyOverrides
    from morrow.runtime.agent import ModelCallRunner
    from test_stage8_chat_submission import drain, new_session
    from test_stage8_core_api import ServerFixture

    fx = ServerFixture(tmp_path)
    requests, delays = [], []
    sid = None

    class SDK:
        async def create(self, **request):
            requests.append(request)
            if len(requests) == 1:
                # A selection change after admission cannot alter this run or its retries.
                svc = fx.host.context.chat.settings
                svc.put(
                    sid,
                    "session",
                    ChatSettings(generation=GenerationOptions(reasoning_effort="low")),
                    1,
                )
                raise openai.APIConnectionError(
                    request=httpx.Request("POST", "https://example.test")
                )
            if len(requests) == 2:
                delta = SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="read1",
                            type="function",
                            function=SimpleNamespace(name="read", arguments='{"path":"input.txt"}'),
                        )
                    ],
                )
                finish = "tool_calls"
            else:
                delta, finish = (
                    SimpleNamespace(
                        content="visible result",
                        tool_calls=None,
                        reasoning_content="hidden-private-sentinel",
                    ),
                    "stop",
                )
            return AsyncChunks(
                [
                    SimpleNamespace(
                        choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish)]
                    )
                ]
            )

    def factory(config, credential):
        provider = OpenAICompatibleProvider(
            config.base_url,
            credential,
            api_model_ids={mid: m.api_model_id for mid, m in config.models.items()},
        )
        provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SDK()))
        return provider

    try:
        (fx.workspace_dir / "input.txt").write_text("data")

        def configure():
            app = fx.app
            app.registry.register(
                "fake-adapter",
                factory,
                capabilities=ProviderCapabilities(
                    tool_protocol="openai_function",
                    multiple_tool_calls=True,
                    reasoning_efforts=("low", "high"),
                    input_types=("text", "image"),
                ),
                reasoning_implementation=("low", "high"),
            )
            app.provider_service.configure_model(
                "fake-provider",
                "m1",
                capabilities=ModelCapabilityOverrides(
                    reasoning_efforts=("low", "high"), input_types=("text", "image")
                ),
            )
            current = app.global_store.load()
            app.global_store.update(
                lambda v: v.model_copy(
                    update={
                        "runtime_policy": RuntimePolicyOverrides(
                            long_horizon=LongHorizonPolicyOverrides(
                                retry_enabled=True, max_retries=1
                            )
                        )
                    }
                ),
                expected_revision=current.revision,
            )

        await fx.on_core(configure)
        sid, path = await new_session(fx)
        response = await fx.client.post(
            path + "/settings",
            {"expected_revision": 0, "settings": {"generation": {"reasoning_effort": "high"}}},
        )
        assert response.status == 200, response.body

        async def no_sleep(delay):
            delays.append(delay)

        def runtime():
            products = fx.host.context.chat.runtime(
                sid, ModelRef(provider_id="fake-provider", model_id="m1")
            )
            products.orchestrator.runtime.loop.retry_sleep = no_sleep

        await fx.on_core(runtime)
        attachments = []
        if with_attachment:
            import io

            from PIL import Image

            from test_stage8_attachment_api import uploaded

            image = io.BytesIO()
            Image.new("RGB", (12, 14), "red").save(image, format="PNG")
            row, _ = await uploaded(fx, sid, image.getvalue(), mime="image/png")
            attachments.append(row["reference"])
        sent = await fx.client.post(
            path + "/interactions",
            {
                "client_message_id": "reason.first",
                "text": "read the file",
                "attachments": attachments,
            },
        )
        assert sent.status == 202, sent.body
        await drain(fx, sid)
        first = (await fx.client.get(path + "/interactions/reason.first")).json()["receipt"]
        assert first["status"] == "settled", first
        assert len(requests) == 3 and len(delays) == 1
        assert [r["reasoning_effort"] for r in requests] == ["high"] * 3
        assert any(m["role"] == "tool" for m in requests[-1]["messages"])
        assert (
            await fx.client.post(
                path + "/interactions",
                {"client_message_id": "reason.next", "text": "next question"},
            )
        ).status == 202
        await drain(fx, sid)
        assert requests[-1]["reasoning_effort"] == "low"

        async def recover_original():
            run = fx.host.context.journal.get_agent_run(fx.workspace_id, first["agent_run_id"])
            assert run.snapshot.provider_runtime.generation.reasoning_effort == "high"
            assert run.snapshot.provider_runtime.settings_sources["generation"].revision == 1
            restored = fx.host.context.chat.runtimes[sid].orchestrator.preparation.rehydrate(
                run.snapshot
            )
            runner = ModelCallRunner(
                restored.provider, restored.model, restored.spec.provider_runtime.generation
            )
            pack = restored.context_builder.build(fx.host.context.chat.runtimes[sid].session)
            async for _ in runner.attempt(pack.messages):
                pass
            return run.snapshot

        snapshot = await fx.host.execute_preparation(recover_original)
        assert requests[-1]["reasoning_effort"] == "high"
        if with_attachment:
            assert snapshot.input_attachments[0].model_dump(mode="json") == attachments[0]
            assert all(
                any(
                    isinstance(m["content"], list)
                    and any(
                        p["type"] == "image_url"
                        and p["image_url"]["url"].startswith("data:image/png;base64,")
                        for p in m["content"]
                    )
                    for m in request["messages"]
                    if m["role"] == "user"
                )
                for request in requests
            )
        public = (await fx.client.get(path + "/snapshot")).body.decode()
        assert "hidden-private-sentinel" not in public
        # Tampered evidence does not become a request, even with a registered model.
        corrupt = snapshot.model_copy(
            update={
                "provider_runtime": snapshot.provider_runtime.model_copy(
                    update={"generation": GenerationOptions(reasoning_effort="low")}
                )
            }
        )
        with pytest.raises(Exception, match="generation options are inconsistent"):
            await fx.on_core(
                lambda: fx.host.context.chat.runtimes[sid].orchestrator.preparation.rehydrate(
                    corrupt
                )
            )
    finally:
        fx.close()
