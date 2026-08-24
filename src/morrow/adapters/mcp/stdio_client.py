"""Narrow, cancellable official-SDK stdio MCP client for Catalog discovery.

The adapter owns only initialize/list-tools/close. It never registers tools or
executes a remote call; that boundary belongs to the next MCP runtime subplan.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import PaginatedRequestParams

from morrow.core.mcp import (
    MCP_MAX_DIAGNOSTIC_BYTES,
    MCP_MAX_REMOTE_TOOLS,
    McpDiscovery,
    McpHandshake,
    McpRemoteTool,
    McpServerDefinition,
)


class McpAdapterError(RuntimeError):
    """Stable adapter error without SDK messages, command lines, or stderr."""

    def __init__(self, code: str, phase: str, message: str = "MCP stdio operation failed") -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.message = message


class McpStdioDiagnostic:
    """Bounded stderr sink; content is intentionally discarded after counting."""

    def __init__(self) -> None:
        self.bytes_seen = 0

    def write(self, value: str) -> int:
        encoded = value.encode("utf-8", errors="replace")
        self.bytes_seen = min(MCP_MAX_DIAGNOSTIC_BYTES, self.bytes_seen + len(encoded))
        return len(value)

    def record_bytes(self, size: int) -> None:
        self.bytes_seen = min(MCP_MAX_DIAGNOSTIC_BYTES, self.bytes_seen + max(0, size))

    def flush(self) -> None:
        return None

    @property
    def redacted_summary(self) -> str | None:
        return "stderr_suppressed" if self.bytes_seen else None


class McpStdioClient:
    """One short-lived stdio connection with explicit lifecycle methods."""

    def __init__(
        self,
        definition: McpServerDefinition,
        *,
        workspace_root: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.definition = definition
        self.workspace_root = workspace_root
        self.environment = dict(environment or {})
        self.diagnostic = McpStdioDiagnostic()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._handshake: McpHandshake | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_read_fd: int | None = None

    @property
    def handshake(self) -> McpHandshake | None:
        return self._handshake

    def _cwd(self) -> str | None:
        if self.definition.cwd_policy.value == "workspace":
            if self.workspace_root is None:
                raise McpAdapterError(
                    "workspace_required", "launch", "MCP workspace cwd is missing"
                )
            return str(self.workspace_root)
        if self.definition.cwd_policy.value == "absolute":
            return self.definition.cwd
        return None

    def _parameters(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.definition.executable,
            args=list(self.definition.argv),
            env=self.environment or None,
            cwd=self._cwd(),
        )

    async def connect(self) -> McpHandshake:
        if self._session is not None:
            if self._handshake is None:
                raise McpAdapterError("state", "handshake")
            return self._handshake
        try:
            await asyncio.wait_for(self._connect_inner(), self.definition.timeout_ms / 1000)
        except TimeoutError as exc:
            await self._close_after_failure()
            raise McpAdapterError("timeout", "handshake", "MCP handshake timed out") from exc
        except McpAdapterError:
            await self._close_after_failure()
            raise
        except (OSError, ValueError) as exc:
            await self._close_after_failure()
            raise McpAdapterError("spawn_failed", "handshake") from exc
        except Exception as exc:
            await self._close_after_failure()
            raise McpAdapterError("handshake_failed", "handshake") from exc
        if self._handshake is None:
            raise McpAdapterError("handshake_failed", "handshake")
        return self._handshake

    async def _connect_inner(self) -> None:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            read_fd, write_fd = os.pipe()
            errlog = os.fdopen(write_fd, "w", encoding="utf-8", buffering=1)
            self._stderr_read_fd = read_fd
            self._stderr_task = asyncio.create_task(self._drain_stderr(read_fd))
            # The SDK passes ``errlog`` as the child process' stderr file
            # descriptor. The pipe is drained asynchronously and only its
            # bounded byte count survives; raw stderr never enters a result.
            stack.callback(os.close, read_fd)
            stack.push_async_callback(self._finish_stderr)
            stack.callback(errlog.close)
            read, write = await stack.enter_async_context(
                stdio_client(self._parameters(), errlog=errlog)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            result = await session.initialize()
            self._stack = stack
            self._session = session
            self._handshake = McpHandshake(
                server_name=result.server_info.name,
                server_version=result.server_info.version,
                protocol_version=result.protocol_version,
            )
        except BaseException:
            await stack.aclose()
            raise

    async def _drain_stderr(self, read_fd: int) -> None:
        try:
            while True:
                chunk = await asyncio.to_thread(os.read, read_fd, 4096)
                if not chunk:
                    return
                self.diagnostic.record_bytes(len(chunk))
        except (OSError, asyncio.CancelledError):
            return

    async def _finish_stderr(self) -> None:
        task, self._stderr_task = self._stderr_task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, 1.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def list_tools(self) -> tuple[McpRemoteTool, ...]:
        if self._session is None:
            raise McpAdapterError(
                "not_connected", "list_tools", "MCP stdio session is not connected"
            )
        try:
            return await asyncio.wait_for(
                self._list_tools_inner(), self.definition.timeout_ms / 1000
            )
        except TimeoutError as exc:
            raise McpAdapterError("timeout", "list_tools", "MCP tool discovery timed out") from exc
        except McpAdapterError:
            raise
        except Exception as exc:
            raise McpAdapterError("list_failed", "list_tools") from exc

    async def _list_tools_inner(self) -> tuple[McpRemoteTool, ...]:
        assert self._session is not None
        values: list[McpRemoteTool] = []
        cursor: str | None = None
        while True:
            params = PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await self._session.list_tools(params=params)
            for item in page.tools:
                values.append(
                    McpRemoteTool(
                        name=item.name,
                        description=item.description or "",
                        input_schema=item.input_schema,
                        output_schema=item.output_schema,
                        annotations=(
                            item.annotations.model_dump(mode="json", by_alias=True)
                            if item.annotations is not None
                            else {}
                        ),
                    )
                )
                if len(values) > MCP_MAX_REMOTE_TOOLS:
                    raise McpAdapterError(
                        "tool_limit", "list_tools", "MCP tool catalog is too large"
                    )
            cursor = page.next_cursor
            if not cursor:
                return tuple(values)

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        self._handshake = None
        if stack is None:
            return
        try:
            await asyncio.wait_for(stack.aclose(), max(1.0, self.definition.timeout_ms / 1000))
        except TimeoutError as exc:
            raise McpAdapterError("timeout", "close", "MCP stdio close timed out") from exc
        except Exception as exc:
            raise McpAdapterError("close_failed", "close") from exc

    async def _close_after_failure(self) -> None:
        try:
            await self.close()
        except McpAdapterError:
            # The original phase is the useful stable diagnostic; close remains best effort.
            return

    async def discover(self) -> McpDiscovery:
        try:
            handshake = await self.connect()
            tools = await self.list_tools()
            return McpDiscovery(
                handshake=handshake,
                tools=tools,
                stderr_bytes=self.diagnostic.bytes_seen,
            )
        finally:
            await self.close()


__all__ = ["McpAdapterError", "McpStdioClient", "McpStdioDiagnostic"]
