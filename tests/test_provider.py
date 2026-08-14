from __future__ import annotations

import os

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.models.openai_compatible import OpenAICompatibleProvider
from morrow.bootstrap import build_application
from morrow.core.models import Message, ModelEvent, ModelRef


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
