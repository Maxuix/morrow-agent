"""Reply reconnect, committed identity and bounded slow-consumer resources."""

import asyncio
import json

from fixtures.core_api_client import WSSession
from morrow.server.events import EventHub
from morrow.server.replies import DELIVERY_BYTES, RING_BYTES, RING_FRAMES
from test_stage8_chat_submission import drain, new_session
from test_stage8_core_api import SERVE_TOKEN, ServerFixture


async def test_snapshot_to_websocket_replays_and_commits_same_message(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        snapshot = (await fixture.client.get(path + "/snapshot")).json()
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "first", "text": "hello"}
        )
        await drain(fixture, sid)
        ws = WSSession(
            fixture.client.app,
            path + "/stream?token=" + SERVE_TOKEN,
            headers=[(b"host", b"127.0.0.1")],
        )
        assert (await ws.accept())["type"] == "websocket.accept"
        await ws._to_app.put(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "subscribe",
                        "stream_epoch": snapshot["stream_epoch"],
                        "after_sequence": snapshot["sequence"],
                    }
                ),
            }
        )
        frames = []
        while True:
            frame = await ws.receive_json()
            frames.append(frame)
            if frame["type"] == "run_state" and frame["payload"]["status"] == "stop":
                break
        assert [f["sequence"] for f in frames] == list(range(1, len(frames) + 1))
        commits = [f for f in frames if f["type"] == "reply_committed"]
        assert len(commits) == 1
        timeline = (await fixture.client.get(path + "/timeline")).json()["items"]
        reply = next(i for i in timeline if i["kind"] == "assistant_message")
        assert commits[0]["message_id"] == reply["item_id"]
        assert (
            "".join(f["payload"]["text"] for f in frames if f["type"] == "reply_delta")
            == reply["content"]
        )
        await ws.close()
        assert await fixture.on_core(lambda: fixture.host.context.chat.streams.subscribers) == 0
    finally:
        fixture.close()


async def test_l01_eight_subscriptions_ten_thousand_deltas_bounded(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, _ = await new_session(fixture)
        loop = asyncio.get_running_loop()

        def load():
            streams = fixture.host.context.chat.streams
            state = streams.state(sid)
            epoch = state.epoch
            subscriptions = [streams.subscribe(sid, loop) for _ in range(8)]
            for _ in range(10000):
                streams.emit(sid, "reply_delta", {"text": "x" * 256}, message_id="load")
            assert state.ring_bytes <= RING_BYTES
            assert len(state.frames) <= RING_FRAMES
            assert state.hub.peak_scheduled <= 8
            assert streams.pull(sid, epoch, 0)[0]["type"] == "resync_required"
            start = state.frames[0][0]["sequence"] - 1
            batch = streams.pull(sid, epoch, start)
            assert (
                sum(len(json.dumps(f, ensure_ascii=False).encode()) for f in batch)
                <= DELIVERY_BYTES
            )
            return subscriptions, {
                "ring_bytes": state.peak_bytes,
                "frames": len(state.frames),
                "callbacks": state.hub.peak_scheduled,
                "resyncs": state.resyncs,
            }

        subscriptions, metrics = await fixture.on_core(load)
        # A query barrier lets all previously scheduled target-loop callbacks run.
        await fixture.on_core(lambda: None)
        assert all(queue.qsize() <= 1 for _, queue in subscriptions)
        print("L01", metrics)
        await fixture.on_core(
            lambda: [
                fixture.host.context.chat.streams.unsubscribe(sid, token)
                for token, _ in subscriptions
            ]
        )
        assert await fixture.on_core(lambda: fixture.host.context.chat.streams.subscribers) == 0
    finally:
        fixture.close()


async def test_event_hints_coalesce_before_and_after_delivery():
    hub = EventHub()
    token, queue = hub.subscribe(asyncio.get_running_loop())
    for cursor in range(1, 10001):
        hub.publish(cursor)
    assert hub.peak_scheduled == 1
    assert await queue.get() == 10000
    hub.publish(20000)
    hub.publish(10001)
    assert await queue.get() == 20000
    hub.unsubscribe(token)


async def test_l02_large_reply_and_overflow_are_bounded(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sid, path = await new_session(fixture)
        text = "中abc" * (128 * 1024 // 6)

        def scripts(value):
            # The first Session composition consumes one non-executed provider.
            fixture.bank.scripts[:] = [[["unused"]], [[value]]]

        await fixture.on_core(lambda: scripts(text))
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "large", "text": "large answer"}
        )
        await drain(fixture, sid)
        page = (await fixture.client.get(path + "/timeline")).json()
        reply = next(i for i in page["items"] if i["kind"] == "assistant_message")
        assert reply["content"] is None and reply["content_ref"]
        assert (await fixture.client.get(reply["content_ref"])).json()["content"] == text
        sid, path = await new_session(fixture, "cmd_overflow_session")
        await fixture.on_core(lambda: scripts("x" * (256 * 1024 + 1)))
        await fixture.client.post(
            path + "/interactions", {"client_message_id": "overflow", "text": "overflow answer"}
        )
        await drain(fixture, sid)
        receipt = (await fixture.client.get(path + "/interactions/overflow")).json()["receipt"]
        assert receipt["run_status"] == "error", receipt
        snapshot = (await fixture.client.get(path + "/snapshot")).json()
        assert snapshot["draft"] is None
        assert snapshot["queue"]["paused"]
        assert (
            await fixture.on_core(lambda: fixture.host.context.chat.streams.state(sid).ring_bytes)
            <= RING_BYTES
        )
    finally:
        fixture.close()


async def test_l04_twenty_sessions_two_hundred_scope_subscriptions(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:
        sessions = [await new_session(fixture, f"cmd_switch_{index}") for index in range(20)]
        loop = asyncio.get_running_loop()

        def switches():
            streams = fixture.host.context.chat.streams
            for index in range(200):
                sid = sessions[index % 20][0]
                initial = streams.snapshot(sid)
                registrations = [streams.subscribe(sid, loop) for _ in range(4)]
                streams.emit(sid, "queue_changed", {})
                frames = streams.pull(sid, initial["stream_epoch"], initial["sequence"])
                assert all(frame["session_id"] == sid for frame in frames)
                for token, _ in registrations:
                    streams.unsubscribe(sid, token)
            assert streams.subscribers == 0
            assert len(streams.states) == 20
            assert not fixture.host.context.chat.drivers
            print(
                "L04",
                {
                    "sessions": 20,
                    "switches": 200,
                    "tabs": 4,
                    "remaining_subscribers": 0,
                    "drivers": 0,
                },
            )

        await fixture.on_core(switches)
    finally:
        fixture.close()
