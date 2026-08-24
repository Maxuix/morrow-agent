"""Subplan 63 spike: official MCP SDK narrow stdio connect/list/call/close.

Runs only when the official ``mcp`` SDK is importable (it is not a project
dependency yet; the spike evaluates it in a temporary environment). The Fake
server lives in this directory and needs nothing but the stdlib.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp", reason="official MCP SDK not installed (Subplan 63 spike)")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

FAKE_SERVER = Path(__file__).with_name("fake_mcp_stdio_server.py")


async def test_fake_stdio_connect_list_call_close(tmp_path: Path) -> None:
    call_log = tmp_path / "calls.jsonl"
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(FAKE_SERVER)],
        env={"FAKE_MCP_CALL_LOG": str(call_log)},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            assert info.server_info.name == "fake-stdio"
            assert info.protocol_version  # echoed by the Fake server

            tools = (await session.list_tools()).tools
            assert [tool.name for tool in tools] == ["echo", "add", "fail", "slow"]
            assert tools[0].input_schema["properties"]["text"]["type"] == "string"

            result = await session.call_tool("echo", {"text": "hello"})
            assert result.is_error is False
            assert result.content[0].text == "hello"

            result = await session.call_tool("add", {"a": 2, "b": 3})
            assert result.content[0].text == "5"

            result = await session.call_tool("fail", {})
            assert result.is_error is True
            assert result.content[0].text == "boom"

            # Timeout: the SDK must raise (no silent hang), and must not retry.
            with pytest.raises(Exception) as exc:
                await session.call_tool("slow", {"seconds": 2}, read_timeout_seconds=0.5)
            print(f"timeout raised: {type(exc.value).__name__}: {str(exc.value)[:120]}")

            # Session remains usable after a timed-out call.
            result = await session.call_tool("echo", {"text": "after-timeout"})
            assert result.content[0].text == "after-timeout"

    calls = [json.loads(line)["tool"] for line in call_log.read_text().splitlines()]
    assert calls.count("slow") == 1  # no auto-retry after a timed-out handler entry
