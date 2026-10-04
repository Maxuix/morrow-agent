"""Real receipt parsing and transport bookkeeping, without a model or native SDK."""

import asyncio
import json
import runpy

import pytest

from morrow.core.models import ModelRef

module = runpy.run_path("evals/computer_use/live_bridge.py")
Provider = module["LiveControllerProvider"]
atomic_json = module["atomic_json"]
MODEL = ModelRef(provider_id="fixture", model_id="fixture")


@pytest.mark.parametrize("fault", [None, "binding", "json", "tool", "content"])
async def test_acceptance_and_rejection_are_recorded_with_request_and_receipt_hashes(
    tmp_path, monkeypatch, fault
):
    provider = Provider(
        tmp_path, case="click", goal="Use actual observation", delivery="foreground"
    )

    async def supply_receipt(_):
        request = tmp_path / "request-001.json"
        value = {
            "request_sha256": module["sha"](request.read_bytes()),
            "decision_basis": "Read actual tool result",
            "content": "Done",
        }
        if fault == "binding":
            value["request_sha256"] = "wrong"
        elif fault == "tool":
            value.pop("content")
            value["tool"] = {"name": "run_command", "arguments": {}}
        elif fault == "content":
            value["content"] = 42
        path = tmp_path / "decision-001.json"
        if fault == "json":
            path.write_text("{private truncated receipt")
        else:
            atomic_json(path, value)

    monkeypatch.setattr(asyncio, "sleep", supply_receipt)
    if fault:
        with pytest.raises(module["ReceiptError"]) as error:
            async for _ in provider.stream(MODEL, []):
                pass
        assert "private" not in str(error.value)
    else:
        events = [event async for event in provider.stream(MODEL, [])]
        assert events[-1].message.content == "Done"
    status = json.loads((tmp_path / "receipt-001-status.json").read_bytes())
    assert status["accepted"] is (fault is None)
    assert status["receipt_received"] is True
    assert status["decision_sha256"] == module["sha"]((tmp_path / "decision-001.json").read_bytes())
    assert len(provider.decisions) == (0 if fault else 1)
    assert (
        status["error_category"] is None
        if fault is None
        else status["error_category"].startswith("receipt_")
    )


async def test_controller_collector_uses_transport_without_constructing_http(monkeypatch):
    from pathlib import Path

    from morrow.adapters.models import openai_compatible
    from morrow.core.models import AssistantMessage
    from morrow.testing import ScriptedModelProvider

    directory = Path("evals/computer_use").resolve()
    monkeypatch.syspath_prepend(str(directory))

    def forbidden(*args, **kwargs):
        pytest.fail("receipt mode must not construct an HTTP adapter")

    monkeypatch.setattr(openai_compatible, "OpenAICompatibleProvider", forbidden)
    collector = runpy.run_path(str(directory / "live_provider.py"))["NativeProvider"]
    transport = ScriptedModelProvider([AssistantMessage(content="Receipt decision")])
    provider = collector("", transport=transport)
    events = [event async for event in provider.stream(MODEL, [])]
    assert events[-1].message.content == "Receipt decision"
    assert provider.real is transport


async def test_cancelled_wait_records_no_accepted_decision(tmp_path, monkeypatch):
    provider = Provider(
        tmp_path, case="click", goal="Use actual observation", delivery="background"
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(_):
        entered.set()
        await release.wait()

    monkeypatch.setattr(asyncio, "sleep", wait)

    async def collect():
        async for _ in provider.stream(MODEL, []):
            pass

    task = asyncio.create_task(collect())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    status = json.loads((tmp_path / "receipt-001-status.json").read_bytes())
    assert status["termination"] == "cancelled"
    assert status["error_category"] == "controller_cancelled"
    assert status["accepted"] is False and status["receipt_received"] is False
    assert provider.decisions == []


async def test_timeout_and_old_campaign_files_cannot_be_accepted(tmp_path):
    provider = Provider(
        tmp_path, case="click", goal="Read", delivery="foreground", timeout_seconds=0
    )
    with pytest.raises(module["ReceiptError"], match="receipt_timeout"):
        async for _ in provider.stream(MODEL, []):
            pass
    status = json.loads((tmp_path / "receipt-001-status.json").read_bytes())
    assert not status["accepted"] and status["termination"] == "failed"
    provider = Provider(tmp_path, case="click", goal="Read", delivery="foreground")
    with pytest.raises(module["ReceiptError"], match="controller_sequence_reused"):
        async for _ in provider.stream(MODEL, []):
            pass


def test_atomic_writer_preserves_previous_record_on_encoding_failure(tmp_path):
    path = tmp_path / "receipt.json"
    atomic_json(path, {"accepted": False})
    original = path.read_bytes()
    with pytest.raises(ValueError):
        atomic_json(path, {"coordinate": float("nan")})
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".receipt.json-*"))
