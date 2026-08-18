"""Bounded asyncio subprocess adapter with process-group cleanup."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from morrow.core.local_tools import CommandStatus


class ProcessAdapterError(RuntimeError):
    """Stable local failure from process creation or cleanup."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ProcessOutput:
    status: CommandStatus
    returncode: int | None
    stdout_tail: bytes
    stderr_tail: bytes
    stdout_original_bytes: int
    stdout_original_lines: int
    stderr_original_bytes: int
    stderr_original_lines: int
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int


class _TailBuffer:
    def __init__(self, limit: int, overlap: int) -> None:
        self.limit = limit
        self.capacity = limit + max(0, overlap)
        self.data = bytearray()
        self.original_bytes = 0
        self.original_newlines = 0
        self.ends_with_newline = False

    def add(self, chunk: bytes) -> None:
        self.original_bytes += len(chunk)
        self.original_newlines += chunk.count(b"\n")
        self.ends_with_newline = chunk.endswith(b"\n")
        self.data.extend(chunk)
        if len(self.data) > self.capacity:
            del self.data[: len(self.data) - self.capacity]

    def result(self) -> tuple[bytes, int, bool]:
        value = bytes(self.data)
        lines = self.original_newlines + (
            1 if self.original_bytes and not self.ends_with_newline else 0
        )
        return value, lines, self.original_bytes > self.limit


class HostProcessAdapter:
    """Run one non-interactive command without inheriting the caller's environment."""

    def __init__(self, *, termination_grace_seconds: float = 1.0) -> None:
        self.termination_grace_seconds = termination_grace_seconds

    async def run(
        self,
        *,
        argv: tuple[str, ...] | None,
        shell: str | None,
        cwd: Path,
        timeout_seconds: float,
        environment: dict[str, str],
        output_limit: int,
        redaction_overlap: int = 0,
    ) -> ProcessOutput:
        if output_limit < 1:
            raise ProcessAdapterError("invalid_output_limit", "进程输出预算无效")
        started = time.monotonic()
        stdout_buffer = _TailBuffer(output_limit, redaction_overlap)
        stderr_buffer = _TailBuffer(output_limit, redaction_overlap)
        process = None
        readers: tuple[asyncio.Task, ...] = ()
        timed_out = False
        try:
            kwargs = {
                "cwd": str(cwd),
                "env": environment,
                "stdin": asyncio.subprocess.DEVNULL,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            if os.name == "posix":
                kwargs["start_new_session"] = True
            if shell is not None:
                process = await asyncio.create_subprocess_shell(shell, **kwargs)
            else:
                process = await asyncio.create_subprocess_exec(*argv, **kwargs)
            readers = (
                asyncio.create_task(self._drain(process.stdout, stdout_buffer)),
                asyncio.create_task(self._drain(process.stderr, stderr_buffer)),
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
            except TimeoutError:
                timed_out = True
                await self._terminate(process)
            await asyncio.gather(*readers)
        except asyncio.CancelledError:
            if process is not None:
                await asyncio.shield(self._terminate(process))
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            raise
        except ProcessAdapterError:
            if process is not None:
                await self._terminate(process)
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            raise
        except OSError as exc:
            if process is not None:
                await self._terminate(process)
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            raise ProcessAdapterError("spawn_failed", "宿主进程无法启动") from exc
        except Exception as exc:
            if process is not None:
                try:
                    await self._terminate(process)
                except Exception:
                    pass
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            raise ProcessAdapterError("process_failed", "宿主进程执行失败") from exc
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()
        stdout_tail, stdout_lines, stdout_truncated = stdout_buffer.result()
        stderr_tail, stderr_lines, stderr_truncated = stderr_buffer.result()
        returncode = process.returncode if process is not None else None
        if timed_out:
            status = CommandStatus.TIMED_OUT
        elif returncode is not None and returncode < 0:
            status = CommandStatus.SIGNALED
        else:
            status = CommandStatus.EXITED
        return ProcessOutput(
            status=status,
            returncode=returncode,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            stdout_original_bytes=stdout_buffer.original_bytes,
            stdout_original_lines=stdout_lines,
            stderr_original_bytes=stderr_buffer.original_bytes,
            stderr_original_lines=stderr_lines,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            duration_ms=min(120_000, max(0, int((time.monotonic() - started) * 1000))),
        )

    @staticmethod
    async def _drain(stream, buffer: _TailBuffer) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return
            buffer.add(chunk)

    async def _terminate(self, process) -> None:
        if process.returncode is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        else:
            try:
                process.terminate()
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        try:
            await asyncio.wait_for(process.wait(), timeout=self.termination_grace_seconds)
            return
        except TimeoutError:
            pass
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        else:
            try:
                process.kill()
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        try:
            await asyncio.wait_for(process.wait(), timeout=self.termination_grace_seconds)
        except (TimeoutError, OSError) as exc:
            raise ProcessAdapterError("cleanup_failed", "宿主进程清理未完成") from exc
