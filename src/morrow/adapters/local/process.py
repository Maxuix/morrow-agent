"""Bounded asyncio subprocess adapter with process-group cleanup."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.local.shell import PinnedShell, pinned_shell, shell_invocation
from morrow.core.artifacts import ARTIFACT_MAX_BYTES
from morrow.core.local_tools import CommandStatus
from morrow.core.runtime_policy import COMMAND_DURATION_MAX_MS

_ARTIFACT_STREAM_CAPTURE_BYTES = ARTIFACT_MAX_BYTES // 4


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
    stdout_full: bytes | None = None
    stderr_full: bytes | None = None
    full_output_truncated: bool = False


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


class _HeadCaptureBuffer:
    """Retain a bounded head for the redacted command Artifact projection."""

    def __init__(self, limit: int, overlap: int) -> None:
        self.limit = limit
        self.capacity = limit + max(0, overlap)
        self.data = bytearray()
        self.original_bytes = 0

    def add(self, chunk: bytes) -> None:
        self.original_bytes += len(chunk)
        if len(self.data) < self.capacity:
            remaining = self.capacity - len(self.data)
            self.data.extend(chunk[:remaining])

    @property
    def truncated(self) -> bool:
        return self.original_bytes > self.limit

    def value(self) -> bytes:
        return bytes(self.data)


class _CursorBuffer:
    """Bounded capture with a stable absolute read position.

    Bytes dropped from the front advance ``base``. A reader that asks for an
    offset below ``base`` is told the window moved; the offset is not rewritten
    into a silent zero.
    """

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ProcessAdapterError("invalid_output_limit", "进程输出预算无效")
        self.limit = limit
        self.data = bytearray()
        self.base = 0
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.data.extend(chunk)
        self.total += len(chunk)
        overflow = len(self.data) - self.limit
        if overflow > 0:
            del self.data[:overflow]
            self.base += overflow

    def read(self, offset: int, maximum: int) -> tuple[bytes, int, bool]:
        if offset < 0 or maximum < 1:
            raise ProcessAdapterError("invalid_range", "输出读取位置无效")
        if offset > self.total:
            raise ProcessAdapterError("invalid_range", "输出读取位置超出已捕获输出")
        truncated = offset < self.base
        start = self.base if truncated else offset
        local = start - self.base
        end = min(len(self.data), local + maximum)
        chunk = bytes(self.data[local:end])
        return chunk, start + len(chunk), truncated


@dataclass
class SpawnedCommand:
    """A live process group that outlives the tool call which started it."""

    process: asyncio.subprocess.Process
    readers: tuple[asyncio.Task, ...]
    stdout: _CursorBuffer
    stderr: _CursorBuffer
    started: float
    loop: asyncio.AbstractEventLoop


class HostProcessAdapter:
    """Run one non-interactive command without inheriting the caller's environment."""

    def __init__(
        self,
        *,
        termination_grace_seconds: float = 1.0,
        drain_timeout_seconds: float = 2.0,
        shell: PinnedShell | None = None,
    ) -> None:
        if drain_timeout_seconds <= 0:
            raise ProcessAdapterError("invalid_drain_timeout", "进程输出收尾预算无效")
        self.termination_grace_seconds = termination_grace_seconds
        self.drain_timeout_seconds = drain_timeout_seconds
        self.shell = pinned_shell() if shell is None else shell

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
        output_listener: Callable[[str, str], None] | None = None,
    ) -> ProcessOutput:
        if output_limit < 1:
            raise ProcessAdapterError("invalid_output_limit", "进程输出预算无效")
        stdout_listener = (
            (lambda text: output_listener("stdout", text)) if output_listener else None
        )
        stderr_listener = (
            (lambda text: output_listener("stderr", text)) if output_listener else None
        )
        started = time.monotonic()
        stdout_buffer = _TailBuffer(output_limit, redaction_overlap)
        stderr_buffer = _TailBuffer(output_limit, redaction_overlap)
        stdout_capture = _HeadCaptureBuffer(_ARTIFACT_STREAM_CAPTURE_BYTES, redaction_overlap)
        stderr_capture = _HeadCaptureBuffer(_ARTIFACT_STREAM_CAPTURE_BYTES, redaction_overlap)
        process = None
        readers: tuple[asyncio.Task, ...] = ()
        timed_out = False
        try:
            process = await self._open_process(
                argv=argv, shell=shell, cwd=cwd, environment=environment
            )
            readers = (
                asyncio.create_task(
                    self._drain(process.stdout, stdout_buffer, stdout_capture, stdout_listener)
                ),
                asyncio.create_task(
                    self._drain(process.stderr, stderr_buffer, stderr_capture, stderr_listener)
                ),
            )
            try:
                await self._wait_main_exit(process, timeout_seconds)
            except TimeoutError:
                timed_out = True
                await self._terminate(process)
            # The main process exiting does not mean the process group is
            # empty: detached grandchildren may still hold the pipes, which
            # would hang the bounded drain below forever.
            if self._group_alive(process.pid):
                await self._terminate(process)
            try:
                await asyncio.wait_for(asyncio.gather(*readers), timeout=self.drain_timeout_seconds)
            except TimeoutError:
                # A descendant escaped the group and still holds the pipe fd:
                # kill the group outright, abandon the blocked reads and keep
                # whatever output was already drained.
                timed_out = True
                await self._terminate(process, force=True)
                for reader in readers:
                    reader.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
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
            duration_ms=_reported_duration_ms(started),
            stdout_full=stdout_capture.value(),
            stderr_full=stderr_capture.value(),
            full_output_truncated=stdout_capture.truncated or stderr_capture.truncated,
        )

    async def spawn(
        self,
        *,
        argv: tuple[str, ...] | None,
        shell: str | None,
        cwd: Path,
        environment: dict[str, str],
        output_limit: int,
    ) -> SpawnedCommand:
        """Start a process group and return without waiting or reaping it.

        Foreground ``run`` still kills surviving group members when its main
        process exits. This handle stays alive across tool calls until stop,
        cancel, or task release.
        """

        process = None
        readers: tuple[asyncio.Task, ...] = ()
        try:
            process = await self._open_process(
                argv=argv, shell=shell, cwd=cwd, environment=environment
            )
            stdout = _CursorBuffer(output_limit)
            stderr = _CursorBuffer(output_limit)
            readers = (
                asyncio.create_task(self._drain(process.stdout, stdout)),
                asyncio.create_task(self._drain(process.stderr, stderr)),
            )
            return SpawnedCommand(
                process=process,
                readers=readers,
                stdout=stdout,
                stderr=stderr,
                started=time.monotonic(),
                loop=asyncio.get_running_loop(),
            )
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

    def request_stop(self, spawned: SpawnedCommand) -> None:
        """Signal the process group and reap it on the loop that spawned it."""

        self._signal_group(spawned.process)
        loop = spawned.loop
        if loop.is_closed():
            return

        def schedule() -> None:
            asyncio.create_task(self._reap_spawned(spawned))

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            schedule()
            return
        loop.call_soon_threadsafe(schedule)

    async def stop(self, spawned: SpawnedCommand) -> None:
        await self._reap_spawned(spawned)

    async def _reap_spawned(self, spawned: SpawnedCommand) -> None:
        try:
            await self._terminate(spawned.process)
        except ProcessAdapterError:
            pass
        except asyncio.CancelledError:
            raise
        for reader in spawned.readers:
            if not reader.done():
                reader.cancel()
        await asyncio.gather(*spawned.readers, return_exceptions=True)

    async def _open_process(
        self,
        *,
        argv: tuple[str, ...] | None,
        shell: str | None,
        cwd: Path,
        environment: dict[str, str],
    ):
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
            try:
                program, flag, script = shell_invocation(self.shell, shell)
            except LookupError as exc:
                raise ProcessAdapterError(
                    "invalid_command",
                    "命令未启动，进程没有执行。当前环境没有可用的 shell",
                ) from exc
            return await asyncio.create_subprocess_exec(program, flag, script, **kwargs)
        return await asyncio.create_subprocess_exec(*(argv or ()), **kwargs)

    def _signal_group(self, process) -> None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
            return
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc

    @staticmethod
    async def _drain(
        stream,
        buffer: _TailBuffer | _CursorBuffer,
        capture: _HeadCaptureBuffer | None = None,
        listener: Callable[[str], None] | None = None,
    ) -> None:
        """Consume one pipe; optionally emit incrementally decoded text (P4.3).

        The observer channel is bounded and non-blocking: decoding is
        incremental (multibyte-safe), listener failures are swallowed and the
        existing pipe consumption, timeouts and reaping are untouched.
        """
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                tail = decoder.decode(b"", final=True)
                if listener is not None:
                    try:
                        # Empty fragment signals end-of-stream so downstream
                        # hold buffers can flush their bounded remainder.
                        if tail:
                            listener(tail)
                        listener("")
                    except Exception:
                        pass
                return
            buffer.add(chunk)
            if capture is not None:
                capture.add(chunk)
            if listener is not None:
                text = decoder.decode(chunk)
                if text:
                    try:
                        listener(text)
                    except Exception:
                        pass

    @staticmethod
    def _group_alive(pgid: int) -> bool:
        """Probe the process group; members may survive the main process."""
        if os.name != "posix":
            return False
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return False
        return True

    @staticmethod
    async def _wait_main_exit(process, timeout: float) -> int:
        """Reap the main process, decoupled from pipe EOF.

        ``asyncio.Process.wait()`` only resolves its waiters once every pipe
        disconnects, so a grandchild inheriting a pipe fd would stall the wait
        even though the main process already exited; ``returncode`` is set
        promptly on actual exit (the child watcher reaps independently).
        """
        if process.returncode is not None:
            return process.returncode
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while process.returncode is None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.sleep(min(0.01, remaining))
        return process.returncode

    async def _terminate(self, process, *, force: bool = False) -> None:
        # The main process may already be reaped while grandchildren in its
        # process group still run, so never early-exit on returncode: the
        # killpg probes below report an empty group instead.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        elif process.returncode is None and not force:
            try:
                process.terminate()
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        elif process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                return
            except OSError as exc:
                raise ProcessAdapterError("cleanup_failed", "宿主进程清理失败") from exc
        try:
            await self._wait_main_exit(process, self.termination_grace_seconds)
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
            await self._wait_main_exit(process, self.termination_grace_seconds)
        except (TimeoutError, OSError) as exc:
            raise ProcessAdapterError("cleanup_failed", "宿主进程清理未完成") from exc


def _reported_duration_ms(started: float) -> int:
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    if elapsed_ms > COMMAND_DURATION_MAX_MS:
        raise ProcessAdapterError(
            "process_failed",
            f"进程耗时 {elapsed_ms} 毫秒，超过可记录上限 {COMMAND_DURATION_MAX_MS} 毫秒",
        )
    return elapsed_ms
