"""Deterministic Fake MCP stdio server (JSON-RPC 2.0 over newline-delimited JSON).

Subplan 63 spike fixture. Pure stdlib, no MCP SDK import, so it runs under any
Python 3.12+ and keeps the spike offline. Implements the narrow
connect/list/call/close surface only: initialize, ping, tools/list, tools/call.

When the FAKE_MCP_CALL_LOG environment variable points at a file, every
tools/call invocation is appended as one JSON line; tests use it to prove the
client never auto-retries a timed-out call.
"""

from __future__ import annotations

import json
import os
import sys
import time

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "fail",
        "description": "Always fail.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "slow",
        "description": "Sleeps, then echoes.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
]


def log_call(name: str) -> None:
    path = os.environ.get("FAKE_MCP_CALL_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tool": name}) + "\n")


def handle(msg: dict) -> str | None:
    if "id" not in msg:
        return None  # notification (e.g. notifications/initialized)

    req_id = msg["id"]
    method = msg.get("method", "")
    params = msg.get("params") or {}

    def ok(result: dict) -> str:
        return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})

    def err(code: int, message: str) -> str:
        return json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        )

    if method == "initialize":
        version = params.get("protocolVersion", "2025-11-25")
        return ok(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-stdio", "version": "0.0.1"},
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        log_call(name)
        if name == "echo":
            return ok({"content": [{"type": "text", "text": args.get("text", "")}]})
        if name == "add":
            total = args.get("a", 0) + args.get("b", 0)
            return ok({"content": [{"type": "text", "text": str(total)}]})
        if name == "fail":
            return ok({"content": [{"type": "text", "text": "boom"}], "isError": True})
        if name == "slow":
            time.sleep(float(args.get("seconds", 1)))
            return ok({"content": [{"type": "text", "text": "slow-done"}]})
        return err(-32602, f"unknown tool {name}")
    return err(-32601, f"method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            msg = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
