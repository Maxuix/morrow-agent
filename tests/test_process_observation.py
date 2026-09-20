"""Realtime process output observation tests (master plan P4.3/P4.5)."""

import asyncio
import sys
from pathlib import Path

import pytest

from morrow.adapters.local.process import HostProcessAdapter, _TailBuffer
from morrow.core.capabilities import PolicyVerdict, ToolRunContext
from morrow.core.local_tools import CommandRequest
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import ProcessExecutionService


def _service(tmp_path: Path, *, secrets: tuple[str, ...] = ()):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    return ProcessExecutionService(files, secrets=secrets)


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def _run() -> ToolRunContext:
    return ToolRunContext(run_id="run-1", session_id="session-1")


class _FakeStream:
    """Scripted pipe: yields scripted chunks, then EOF after the barrier."""

    def __init__(self, chunks: list[bytes], *, gate: asyncio.Event | None = None):
        self._chunks = list(chunks)
        self._gate = gate

    async def read(self, size: int) -> bytes:
        del size
        if self._chunks:
            await asyncio.sleep(0)
            return self._chunks.pop(0)
        if self._gate is not None:
            await self._gate.wait()
        return b""


async def test_output_observed_before_process_completes():
    """P4.5: a fragment is observable while the fake process is still running."""
    gate = asyncio.Event()
    observed: list[str] = []
    stream = _FakeStream([b"partial "], gate=gate)
    drain = asyncio.create_task(
        HostProcessAdapter._drain(
            stream, _TailBuffer(4096, 0), None, lambda text: observed.append(text)
        )
    )
    for _ in range(1000):
        if observed:
            break
        await asyncio.sleep(0)
    assert observed, "fragment must arrive before completion"
    gate.set()
    await drain


async def test_multibyte_split_across_chunks_is_assembled():
    gate = asyncio.Event()
    observed: list[str] = []
    whole = "中文输出完成".encode()
    split = [whole[:3], whole[3:7], whole[7:]]
    drain = asyncio.create_task(
        HostProcessAdapter._drain(
            _FakeStream(split, gate=gate),
            _TailBuffer(4096, 0),
            None,
            lambda text: observed.append(text),
        )
    )
    await asyncio.sleep(0.05)
    gate.set()
    await drain
    assert "".join(observed) == "中文输出完成"


async def test_no_output_stream_still_signals_end():
    gate = asyncio.Event()
    observed: list[str] = []
    drain = asyncio.create_task(
        HostProcessAdapter._drain(
            _FakeStream([], gate=gate),
            _TailBuffer(4096, 0),
            None,
            lambda text: observed.append(text),
        )
    )
    await asyncio.sleep(0.05)
    gate.set()
    await drain
    assert observed == [""]


async def test_listener_exception_never_breaks_the_drain():
    gate = asyncio.Event()

    def explode(_text: str) -> None:
        raise RuntimeError("observer exploded")

    drain = asyncio.create_task(
        HostProcessAdapter._drain(
            _FakeStream([b"abc", b"def"], gate=gate), _TailBuffer(4096, 0), None, explode
        )
    )
    await asyncio.sleep(0.05)
    gate.set()
    await drain  # completes despite the broken observer


async def test_service_level_output_is_redacted_and_fragmented(tmp_path):
    """P4.3: the redaction layer sits between the drain and the observer."""
    service = _service(tmp_path, secrets=("topsecret-value",))
    observed: list[str] = []
    plan = service.preflight(
        CommandRequest(argv=_python("print('token: topsecret-value'); print('done-out')"))
    )
    result, _fact, _artifact = await service.execute_with_artifact(
        plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        output_listener=lambda stream, text: observed.append((stream, text)),
    )
    combined = "".join(text for _stream, text in observed)
    assert "topsecret-value" not in combined
    assert "done-out" in combined
    assert all(stream in {"stdout", "stderr"} for stream, _text in observed)
    assert (
        result.stdout.strip() == "token: topsecret-value\ndone-out" or "done-out" in result.stdout
    )


@pytest.mark.asyncio
async def test_huge_output_does_not_block_the_drain(tmp_path):
    service = _service(tmp_path)
    observed: list[str] = []
    plan = service.preflight(CommandRequest(argv=_python("print('x' * 200000)")))
    result, _fact, _artifact = await service.execute_with_artifact(
        plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        output_listener=lambda stream, text: observed.append(text),
    )
    assert result.status.value in {"exited", "signalled"}
    assert sum(len(text) for text in observed) > 0


_LONG_SECRET = "sk-long-" + "0123456789abcdef" * 6  # 100 chars, longer than the fixed hold


def _assert_no_secret_fragment(frames: list[str], secret: str = _LONG_SECRET) -> None:
    combined = "".join(frames)
    for index in range(0, len(secret) - 16 + 1):
        assert secret[index : index + 16] not in combined


@pytest.mark.asyncio
async def test_long_secret_in_single_chunk_never_reaches_listener(tmp_path):
    """A secret longer than the fixed 64-char hold must still be fully redacted live."""

    service = _service(tmp_path, secrets=(_LONG_SECRET,))
    observed: list[str] = []
    plan = service.preflight(CommandRequest(argv=_python(f"print('before {_LONG_SECRET} after')")))
    result, _fact, artifact = await service.execute_with_artifact(
        plan,
        result_limit=16 * 1024,
        run=_run(),
        call_id="call-1",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.REQUIRE_APPROVAL,
        output_listener=lambda stream, text: observed.append(text),
    )
    frames = [text for text in observed if text]
    _assert_no_secret_fragment(frames)
    assert "before" in "".join(frames)
    assert "after" in "".join(frames)
    assert _LONG_SECRET not in result.stdout
    assert _LONG_SECRET not in artifact.decode("utf-8", errors="replace")
    assert "<redacted>" in result.stdout


def test_long_secret_split_across_listener_chunks_is_held(tmp_path):
    """A secret straddling two realtime frames is held and redacted before release."""

    service = _service(tmp_path, secrets=(_LONG_SECRET,))
    observed: list[str] = []
    emit = service._incremental_redacted_listener(lambda _stream, text: observed.append(text))
    half = len(_LONG_SECRET) // 2
    emit("stdout", "prefix " + _LONG_SECRET[:half])
    emit("stdout", _LONG_SECRET[half:] + " suffix")
    emit("stdout", "")
    frames = [text for text in observed if text]
    _assert_no_secret_fragment(frames)
    combined = "".join(frames)
    assert "prefix" in combined
    assert "suffix" in combined
    assert "<redacted>" in combined


@pytest.mark.parametrize("prefix", ["stdout", "中" * 2000], ids=["ascii", "multibyte"])
async def test_agent_loop_process_output_reaches_activity_observer(tmp_path, prefix):
    from morrow.core.models import AssistantMessage, FunctionToolCall
    from morrow.runtime.tool_output import current_output_listener
    from morrow.runtime.tools import ToolExecutor, ToolRegistry, make_tool
    from morrow.testing import make_run_policy
    from test_tool_observation import Collector, EchoArguments, _loop, _provider
    from test_tool_observation import _run as run_loop

    service = _service(tmp_path, secrets=("test-secret",))
    collector = Collector()
    outputs = []
    collector.tool_output = lambda **event: outputs.append(event)

    async def handler(arguments: EchoArguments):
        plan = service.preflight(
            CommandRequest(
                argv=_python(
                    f"import sys; print({prefix!r} + ' test-secret'); print('stderr message', file=sys.stderr)"
                )
            )
        )
        result, _, _ = await service.execute_with_artifact(
            plan,
            result_limit=16384,
            run=_run(),
            call_id="c1",
            tool_name="command",
            ordinal=1,
            approval_verdict=PolicyVerdict.ALLOW,
            output_listener=current_output_listener(),
        )
        return result.model_dump(mode="json")

    registry = ToolRegistry()
    registry.register(
        make_tool(
            name="command",
            description="command",
            arguments_model=EchoArguments,
            handler=handler,
        )
    )
    loop = _loop(
        _provider(
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="c1",
                        name="command",
                        arguments='{"value":"go"}',
                    ),
                )
            ),
            AssistantMessage(content="done"),
        ),
        ToolExecutor(registry.snapshot(), make_run_policy()),
        collector,
    )
    events = await run_loop(loop)
    assert outputs, [(e.type, e.payload) for e in events if "tool" in e.type]
    combined = "".join(event["text"] for event in outputs)
    assert prefix[:50] in combined
    assert all(len(event["text"].encode()) <= 4096 for event in outputs)
    assert "stderr message" in combined
    assert "test-secret" not in combined
    assert {event["call_id"] for event in outputs} == {"c1"}
    assert loop.activity_observer_drops == 0


def test_redaction_hold_and_eof_are_independent_for_each_stream(tmp_path):
    service = _service(tmp_path, secrets=("SECRET",))
    observed = []
    emit = service._incremental_redacted_listener(
        lambda stream, text: observed.append((stream, text))
    )
    emit("stdout", "SEC")
    emit("stderr", "RET")
    emit("stderr", "")
    assert observed == [("stderr", "RET")]
    emit("stdout", "RET")
    emit("stdout", "")
    assert observed == [("stderr", "RET"), ("stdout", "<redacted>")]


async def test_incomplete_utf8_at_eof_flushes_redaction_hold(tmp_path):
    service = _service(tmp_path)
    observed = []
    emit = service._incremental_redacted_listener(lambda stream, text: observed.append(text))
    await HostProcessAdapter._drain(
        _FakeStream([b"hello\xe4"]),
        _TailBuffer(4096, 0),
        listener=lambda text: emit("stdout", text),
    )
    assert "".join(observed) == "hello\ufffd"
