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
import time
from dataclasses import dataclass

from morrow.adapters.local.process import HostProcessAdapter, ProcessAdapterError, SpawnedCommand
from morrow.core.local_tools import TrackedCommandStatus, TrackedCommandView, TrackedLifecycle
from morrow.core.runtime_policy import COMMAND_DURATION_MAX_MS

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
    validation_kind: str | None = None
    validation_scope: str | None = None
    terminal_fact_recorded: bool = False


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
        validation_kind: str | None = None,
        validation_scope: str | None = None,
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
            validation_kind=validation_kind,
            validation_scope=validation_scope,
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

    def claim_terminal_fact(
        self, execution_id: str, *, session_id: str, task_id: str
    ) -> tuple[str | None, str | None, int] | None:
        """Claim the first observed terminal state for one fact projection."""

        with self._lock:
            execution = self._visible(execution_id, session_id=session_id, task_id=task_id)
            if execution.status is TrackedCommandStatus.RUNNING or execution.terminal_fact_recorded:
                return None
            if execution.status is TrackedCommandStatus.CANCELLED and _group_alive(
                execution.spawned.process.pid
            ):
                return None
            execution.terminal_fact_recorded = True
            duration_ms = min(
                COMMAND_DURATION_MAX_MS,
                max(0, int((time.monotonic() - execution.spawned.started) * 1000)),
            )
            return execution.validation_kind, execution.validation_scope, duration_ms

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
            stdout, next_stdout, stdout_skipped = _safe_page(
                execution.spawned.stdout, offset, chunk_limit, redactor.secret_bytes
            )
            stderr, next_stderr, stderr_skipped = _safe_page(
                execution.spawned.stderr, stderr_offset, chunk_limit, redactor.secret_bytes
            )
        except ProcessAdapterError as exc:
            raise TrackedProcessError(exc.code, exc.message) from exc
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


def _safe_page(
    buffer, offset: int, limit: int, secrets: tuple[bytes, ...]
) -> tuple[str, int, bool]:
    """Page by raw byte cursors after finding secret spans in the retained window.

    The bounded capture is scanned before a page is cut. This protects arbitrary
    cursors, tiny limits, and a ring buffer whose left edge bisects a secret.
    """

    base = buffer.base
    raw, total, skipped = buffer.read(base, max(1, buffer.total - base))
    if offset < 0 or offset > total:
        raise ProcessAdapterError("invalid_range", "输出读取位置无效")
    start = max(offset, base)
    if start == total:
        return "", start, skipped or offset < base
    if any(len(secret) > buffer.limit for secret in secrets):
        # A credential longer than the entire ring can leave an unrecognizable
        # middle fragment after truncation. Keep the byte cursor advancing,
        # but do not disclose that window.
        return "<redacted>", min(total, start + limit), skipped or offset < base
    spans: list[tuple[int, int]] = []
    for secret in secrets:
        position = raw.find(secret)
        while position >= 0:
            spans.append((position, position + len(secret)))
            position = raw.find(secret, position + 1)
        # The ring's left edge or a still-growing right edge may retain only
        # part of an exact secret. Suppress those boundary fragments as well.
        if base:
            width = _boundary_overlap(secret[::-1], raw[::-1])
            if width:
                spans.append((0, width))
        width = _boundary_overlap(secret, raw)
        if width:
            spans.append((len(raw) - width, len(raw)))
    local_start = start - base
    end = min(len(raw), local_start + limit)
    for left, right in spans:
        if left < end < right:
            end = right
    # Never split a UTF-8 code point between pages. Invalid bytes still decode
    # with replacement; the cursor always advances in the original byte stream.
    while end < len(raw) and raw[end] & 0xC0 == 0x80:
        end += 1
    page = bytearray()
    position = local_start
    for left, right in sorted(spans):
        if right <= position or left >= end:
            continue
        if left > position:
            page.extend(raw[position:left])
        page.extend(b"<redacted>")
        position = min(right, end)
    if position < end:
        page.extend(raw[position:end])
    return page.decode("utf-8", errors="replace"), base + end, skipped or offset < base


def _boundary_overlap(pattern: bytes, data: bytes) -> int:
    """Longest proper pattern prefix at the data tail, in linear time."""

    failure = [0] * len(pattern)
    matched = 0
    for index in range(1, len(pattern)):
        while matched and pattern[index] != pattern[matched]:
            matched = failure[matched - 1]
        if pattern[index] == pattern[matched]:
            matched += 1
        failure[index] = matched
    matched = 0
    for value in data:
        while matched and value != pattern[matched]:
            matched = failure[matched - 1]
        if value == pattern[matched]:
            matched += 1
        if matched == len(pattern):
            matched = failure[matched - 1]
    return matched
