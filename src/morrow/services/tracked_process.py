"""Tracked command registry.

The registry does not prepare shell commands. Callers hand it an adapter only
for the process they are starting; later poll, stop, and release use that same
adapter. Records stay locked across status changes so a task-thread release
cannot race an event-loop poll.
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass

from morrow.adapters.local.process import HostProcessAdapter, ProcessAdapterError, SpawnedCommand
from morrow.core.local_tools import TrackedCommandStatus, TrackedCommandView, TrackedLifecycle

TRACKED_OUTPUT_RETAIN_BYTES = 256 * 1024
_POLL_CHUNK_BYTES = 8 * 1024
_RELEASE_REASONS = frozenset({"cancel", "terminate", "accept", "open"})


class TrackedProcessError(RuntimeError):
    """Stable failure from the tracked-process registry."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TrackedExecution:
    execution_id: str
    session_id: str
    task_id: str
    lifecycle: TrackedLifecycle
    command_class: str
    cwd_relative: str
    adapter: HostProcessAdapter
    spawned: SpawnedCommand
    stop_requested: bool = False
    status: TrackedCommandStatus = TrackedCommandStatus.RUNNING
    exit_code: int | None = None
    signal: int | None = None


class TrackedProcessRegistry:
    """In-process record of commands started with ``mode=start``."""

    def __init__(self) -> None:
        self._items: dict[str, TrackedExecution] = {}
        self._lock = threading.Lock()

    async def start(
        self,
        adapter: HostProcessAdapter,
        *,
        argv: tuple[str, ...] | None,
        shell: str | None,
        cwd,
        environment: dict[str, str],
        session_id: str,
        task_id: str,
        lifecycle: TrackedLifecycle,
        command_class: str,
        cwd_relative: str,
    ) -> TrackedExecution:
        spawned = await adapter.spawn(
            argv=argv,
            shell=shell,
            cwd=cwd,
            environment=environment,
            output_limit=TRACKED_OUTPUT_RETAIN_BYTES,
        )
        execution = TrackedExecution(
            execution_id="exec_" + secrets.token_hex(12),
            session_id=session_id,
            task_id=task_id,
            lifecycle=lifecycle,
            command_class=command_class,
            cwd_relative=cwd_relative,
            adapter=adapter,
            spawned=spawned,
        )
        with self._lock:
            self._items[execution.execution_id] = execution
        return execution

    def read(
        self,
        execution_id: str,
        *,
        session_id: str,
        task_id: str,
        offset: int,
        stderr_offset: int,
        limit: int,
        redactor,
    ) -> TrackedCommandView:
        with self._lock:
            execution = self._visible(execution_id, session_id=session_id, task_id=task_id)
            self._refresh(execution)
            return self._view(
                execution,
                offset=offset,
                stderr_offset=stderr_offset,
                limit=limit,
                redactor=redactor,
            )

    async def stop(
        self,
        execution_id: str,
        *,
        session_id: str,
        task_id: str,
        offset: int,
        stderr_offset: int,
        limit: int,
        redactor,
    ) -> TrackedCommandView:
        with self._lock:
            execution = self._visible(execution_id, session_id=session_id, task_id=task_id)
            self._refresh(execution)
            should_stop = (
                execution.status is TrackedCommandStatus.RUNNING and not execution.stop_requested
            )
            if should_stop:
                execution.stop_requested = True
                self._apply(_observe(True, execution.spawned.process.returncode, True), execution)
            adapter = execution.adapter
            spawned = execution.spawned
        if should_stop:
            await adapter.stop(spawned)
        with self._lock:
            self._refresh(execution)
            return self._view(
                execution,
                offset=offset,
                stderr_offset=stderr_offset,
                limit=limit,
                redactor=redactor,
            )

    def release(self, *, session_id: str, task_id: str, reason: str) -> tuple[str, ...]:
        """Apply one task-level decision to processes owned by that task only."""

        if reason not in _RELEASE_REASONS:
            raise TrackedProcessError("invalid_mode", "未知的进程释放原因")
        signalled: list[TrackedExecution] = []
        with self._lock:
            owned = [
                item
                for item in self._items.values()
                if item.session_id == session_id and item.task_id == task_id
            ]
            for execution in owned:
                self._refresh(execution)
                if reason == "open":
                    continue
                if reason == "accept" and execution.lifecycle is TrackedLifecycle.ACCEPTANCE:
                    continue
                if execution.status is not TrackedCommandStatus.RUNNING or execution.stop_requested:
                    continue
                execution.stop_requested = True
                self._apply(_observe(True, execution.spawned.process.returncode, True), execution)
                signalled.append(execution)
        for execution in signalled:
            execution.adapter.request_stop(execution.spawned)
        return tuple(item.execution_id for item in signalled)

    def _visible(self, execution_id: str, *, session_id: str, task_id: str) -> TrackedExecution:
        if (
            not isinstance(execution_id, str)
            or len(execution_id) != 29
            or not execution_id.startswith("exec_")
        ):
            raise TrackedProcessError(
                "invalid_execution",
                "执行编号无效。poll 和 stop 不会启动新进程。",
            )
        execution = self._items.get(execution_id)
        if execution is None:
            raise TrackedProcessError(
                "not_found",
                "没有这个执行。poll 和 stop 不会启动新进程。",
            )
        same_task = execution.session_id == session_id and execution.task_id == task_id
        same_session_service = (
            execution.session_id == session_id
            and execution.lifecycle is TrackedLifecycle.ACCEPTANCE
        )
        if not same_task and not same_session_service:
            raise TrackedProcessError(
                "not_owner",
                "这个执行属于其他任务或会话，没有启动或停止任何进程。",
            )
        return execution

    def _refresh(self, execution: TrackedExecution) -> None:
        if execution.status not in {
            TrackedCommandStatus.RUNNING,
            TrackedCommandStatus.CANCELLED,
        }:
            return
        self._apply(
            _observe(
                _group_alive(execution.spawned.process.pid),
                execution.spawned.process.returncode,
                execution.stop_requested,
            ),
            execution,
        )

    @staticmethod
    def _apply(
        observed: tuple[TrackedCommandStatus, int | None, int | None],
        execution: TrackedExecution,
    ) -> None:
        execution.status, execution.exit_code, execution.signal = observed

    def _view(
        self,
        execution: TrackedExecution,
        *,
        offset: int,
        stderr_offset: int,
        limit: int,
        redactor,
    ) -> TrackedCommandView:
        chunk_limit = max(1, min(limit, _POLL_CHUNK_BYTES))
        try:
            stdout_raw, next_stdout, stdout_skipped = execution.spawned.stdout.read(
                offset, chunk_limit
            )
            stderr_raw, next_stderr, stderr_skipped = execution.spawned.stderr.read(
                stderr_offset, chunk_limit
            )
        except ProcessAdapterError as exc:
            raise TrackedProcessError(exc.code, exc.message) from exc
        running = execution.status is TrackedCommandStatus.RUNNING
        stdout_raw, next_stdout = _hold_secret_tail(
            stdout_raw,
            next_offset=next_stdout,
            hold=redactor.max_secret_length,
            running=running,
        )
        stderr_raw, next_stderr = _hold_secret_tail(
            stderr_raw,
            next_offset=next_stderr,
            hold=redactor.max_secret_length,
            running=running,
        )
        stdout, _, _ = redactor.redact(stdout_raw)
        stderr, _, _ = redactor.redact(stderr_raw)
        return TrackedCommandView(
            execution_id=execution.execution_id,
            status=execution.status,
            exit_code=execution.exit_code,
            signal=execution.signal,
            stdout=stdout,
            stderr=stderr,
            output_offset=next_stdout,
            stderr_offset=next_stderr,
            output_truncated=stdout_skipped or stderr_skipped,
            lifecycle=execution.lifecycle,
            cwd=execution.cwd_relative,
            command_class=execution.command_class,
        )


def _observe(
    alive: bool, code: int | None, stop_requested: bool
) -> tuple[TrackedCommandStatus, int | None, int | None]:
    """One status from liveness, the leader's return code, and stop."""

    if stop_requested:
        exit_code, signal = _exit_fields(code)
        return TrackedCommandStatus.CANCELLED, exit_code, signal
    if alive:
        return TrackedCommandStatus.RUNNING, None, None
    if code is None:
        return TrackedCommandStatus.LOST, None, None
    exit_code, signal = _exit_fields(code)
    if signal is not None:
        return TrackedCommandStatus.SIGNALED, None, signal
    return TrackedCommandStatus.EXITED, exit_code, None


def _exit_fields(code: int | None) -> tuple[int | None, int | None]:
    if code is None:
        return None, None
    if code < 0:
        return None, -code
    return code, None


def _group_alive(pid: int) -> bool:
    if os.name != "posix":
        return False
    return HostProcessAdapter._group_alive(pid)


def _hold_secret_tail(
    raw: bytes, *, next_offset: int, hold: int, running: bool
) -> tuple[bytes, int]:
    """Keep a secret split across polls inside the unread tail."""

    if not running or hold <= 0 or len(raw) <= hold:
        if running and hold > 0 and len(raw) <= hold:
            return b"", next_offset - len(raw)
        return raw, next_offset
    kept = raw[:-hold]
    return kept, next_offset - hold
