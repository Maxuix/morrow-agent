"""Real Session writer admission and structured interaction contracts."""

import pytest
from pydantic import ValidationError

from morrow.application.interactions import InteractionService
from morrow.core.interactions import InteractionRequest
from test_stage8_core_api import ServerFixture


@pytest.mark.parametrize(
    "extra",
    [
        {"settings": {"temperature": 1}},
        {"attachments": [{"attachment_id": "a"}]},
        {"intent": "explicit_workflow"},
        {"intent": "steer"},
        {"target_agent_run_id": "arun_other"},
        {"secret_field": "never echoed"},
    ],
)
def test_unsupported_or_invalid_input_rejected(extra):
    with pytest.raises(ValidationError):
        InteractionRequest(client_message_id="client.1", text="hello", **extra)


async def test_external_first_key_uses_original_turn_writer(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:

        async def journey():
            products = fixture.host.context.products
            key = "client.first"
            first = await products.orchestrator.dispatch("hello", client_message_id=key)
            assert any(e.type == "turn.completed" for e in first.events)
            sid = products.session.session_id
            journal = products.api.journal
            receipt = journal.get_receipt(fixture.workspace_id, sid, key)
            assert receipt.turn_id
            assert journal.get_turn(fixture.workspace_id, receipt.turn_id).client_message_id == key
            before = journal.load_records(fixture.workspace_id, sid)
            async for _ in InteractionService.stream(
                products.orchestrator, InteractionRequest(text="hello", client_message_id=key)
            ):
                pass
            assert journal.load_records(fixture.workspace_id, sid) == before
            assert len(journal.list_session_turns(fixture.workspace_id, sid)) == 1

        await fixture.host.execute_preparation(journey)
    finally:
        fixture.close()


async def new_session(fixture, key="cmd_create"):
    root = f"/v1/workspaces/{fixture.workspace_id}/sessions"
    result = await fixture.client.post(root, {"command_id": key})
    assert result.status == 200, result.body
    sid = result.json()["result"]["session"]["session_id"]
    return sid, root + "/" + sid


async def drain(fixture, sid):
    async def wait():
        import asyncio

        task = fixture.host.context.chat.drivers.get(sid)
        if task:
            await asyncio.shield(task)

    await fixture.host.execute_preparation(wait)


async def test_durable_api_two_rounds_and_idempotent_replay(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        body = {"client_message_id": "client.1", "text": "first question"}
        sent = await fixture.client.post(path + "/interactions", body)
        assert sent.status == 202, sent.body
        assert sent.json()["receipt"]["status"] == "queued"
        await drain(fixture, sid)
        receipt = (await fixture.client.get(path + "/interactions/client.1")).json()["receipt"]
        assert receipt["status"] == "settled", receipt
        assert receipt["turn_id"] and receipt["agent_run_id"] and receipt["user_record_id"]
        replay = (await fixture.client.post(path + "/interactions", body)).json()["receipt"]
        assert replay["interaction_id"] == receipt["interaction_id"]
        assert replay["disposition"] == "replay"
        assert (
            await fixture.client.post(path + "/interactions", {**body, "text": "other"})
        ).status == 409
        assert (
            await fixture.client.post(
                path + "/interactions", {"client_message_id": "client.2", "text": "follow question"}
            )
        ).status == 202
        await drain(fixture, sid)

        def verify():
            journal = fixture.host.context.journal
            records = journal.load_records(fixture.workspace_id, sid)
            users = [r.payload["content"] for r in records if r.payload.get("role") == "user"]
            assert users == ["first question", "follow question"]
            assert len(journal.list_session_turns(fixture.workspace_id, sid)) == 2
            requests = [request for p in fixture.bank.providers for request in p.stream_calls]
            assert len(requests) == 2
            assert "first question" in str(requests[-1])

        await fixture.on_core(verify)
    finally:
        fixture.close()


async def test_queued_admission_has_no_turn_and_preserves_bound_model(tmp_path):
    import asyncio

    from morrow.core.models import ModelRef

    fixture = ServerFixture(tmp_path)
    gate = fixture.host.context.supervisor.execution_lock
    try:
        sid, path = await new_session(fixture)

        async def hold():
            entered = asyncio.Event()
            release = asyncio.Event()

            async def run():
                async with gate:
                    entered.set()
                    await release.wait()

            task = asyncio.create_task(run())
            await entered.wait()
            return release, task

        release, holder = await fixture.host.execute_preparation(hold)
        body = {"client_message_id": "queued.1", "text": "bound question"}
        a, b = await asyncio.gather(
            fixture.client.post(path + "/interactions", body),
            fixture.client.post(path + "/interactions", body),
        )
        assert a.status == b.status == 202
        assert a.json()["receipt"]["interaction_id"] == b.json()["receipt"]["interaction_id"]
        assert a.json()["receipt"]["turn_id"] is None

        def change_default():
            assert not fixture.host.context.journal.load_records(fixture.workspace_id, sid)
            current = fixture.app.global_store.load()
            fixture.app.global_store.update(
                lambda config: config.model_copy(
                    update={"active_model": ModelRef(provider_id="fake-provider", model_id="m2")}
                ),
                expected_revision=current.revision,
            )
            release.set()

        await fixture.on_core(change_default)
        await drain(fixture, sid)
        receipt = (await fixture.client.get(path + "/interactions/queued.1")).json()["receipt"]
        assert receipt["status"] == "settled"
        run = await fixture.on_core(
            lambda: fixture.host.context.journal.get_agent_run(
                fixture.workspace_id, receipt["agent_run_id"]
            )
        )
        assert run.snapshot.model.model_id == "m1"
    finally:
        if "release" in locals():
            await fixture.on_core(release.set)
        fixture.close()
