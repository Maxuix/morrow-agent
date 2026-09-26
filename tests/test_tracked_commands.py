"""Long-command lifecycle: no silent 90s clamp, poll does not spawn, cancel is scoped."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.adapters.local.process import HostProcessAdapter
from morrow.application.bash_tool import make_bash_tool
from morrow.core.capabilities import (
    PermissionProfile,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.local_tools import CommandRequest
from morrow.core.models import FunctionToolCall, ToolApprovalDecision
from morrow.core.runtime_policy import FOREGROUND_COMMAND_MAX_SECONDS
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import ProcessExecutionService, ProcessServiceError
from morrow.testing import make_run_policy

_HOLDER = """
import os
import subprocess
import sys
import time
from pathlib import Path

ready, parent_gone, release = map(Path, sys.argv[1:4])
child = r'''
import os, sys, time
from pathlib import Path
ready, parent_gone, release = map(Path, sys.argv[1:4])
parent = int(sys.argv[4])
ready.write_text(str(os.getpid()))
while True:
    try:
        os.kill(parent, 0)
    except OSError:
        parent_gone.write_text("gone")
        break
    time.sleep(0.01)
while not release.exists():
    time.sleep(0.01)
'''
subprocess.Popen(
    [sys.executable, "-c", child, sys.argv[1], sys.argv[2], sys.argv[3], str(os.getpid())]
)
"""


class _RecordingAdapter(HostProcessAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[float] = []
        self.spawns = 0

    async def run(self, **kwargs):
        self.timeouts.append(kwargs["timeout_seconds"])
        return await super().run(**kwargs)

    async def spawn(self, **kwargs):
        self.spawns += 1
        return await super().spawn(**kwargs)


class _Approval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=True)


def _service(tmp_path: Path, *, limit: float, adapter: _RecordingAdapter | None = None):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    return ProcessExecutionService(
        files,
        adapter=adapter or _RecordingAdapter(),
        foreground_timeout_seconds=limit,
    )


def _executor(tmp_path: Path, service: ProcessExecutionService) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(make_bash_tool(service))
    return ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=_Approval(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )


def _call(payload: dict, call_id: str) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id,
        name="bash",
        arguments=json.dumps(payload, ensure_ascii=False),
    )


def _run(task_id: str, session_id: str = "session-1") -> ToolRunContext:
    return ToolRunContext(run_id=f"run-{task_id}", session_id=session_id, owner_task_id=task_id)


async def _wait_for(path: Path) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3
    while not path.exists():
        if loop.time() >= deadline:
            raise AssertionError(f"handshake missing: {path.name}")
        await asyncio.sleep(0.01)


async def _wait_dead(pid: int) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3
    while _alive(pid):
        if loop.time() >= deadline:
            raise AssertionError(f"pid {pid} still alive")
        await asyncio.sleep(0.01)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _body(outcome) -> dict:
    return json.loads(outcome.envelope)


@pytest.mark.asyncio
async def test_foreground_timeout_is_rejected_above_the_active_limit_and_not_clamped(tmp_path):
    adapter = _RecordingAdapter()
    limited = _service(tmp_path, limit=120, adapter=adapter)
    refused = await _executor(tmp_path, limited).execute_with_context(
        _call({"command": "printf ok", "timeout": 180}, "over"),
        run_context=_run("task-a"),
        ordinal=1,
        total=1,
    )
    body = _body(refused)
    assert refused.ok is False
    assert "120" in body["error"]["message"]
    assert "maximum" in body["error"]["message"] or "拒绝" in body["error"]["message"]
    assert adapter.timeouts == []
    assert adapter.spawns == 0

    with pytest.raises(ValidationError):
        CommandRequest(shell="printf ok", timeout_seconds=FOREGROUND_COMMAND_MAX_SECONDS + 1)
    with pytest.raises(ProcessServiceError) as error:
        limited.preflight(CommandRequest(shell="printf ok", timeout_seconds=180))
    assert error.value.code == "invalid_timeout"
    assert "120" in error.value.message

    allowed = _RecordingAdapter()
    service = _service(tmp_path, limit=FOREGROUND_COMMAND_MAX_SECONDS, adapter=allowed)
    outcome = await _executor(tmp_path, service).execute_with_context(
        _call({"command": "printf ok", "timeout": 180}, "admitted"),
        run_context=_run("task-a"),
        ordinal=1,
        total=1,
    )
    assert outcome.ok is True
    assert _body(outcome)["result"]["status"] == "exited"
    assert allowed.timeouts == [180]
    assert allowed.spawns == 0
    omitted = await _executor(tmp_path, service).execute_with_context(
        _call({"command": "printf ok"}, "default-timeout"),
        run_context=_run("task-a"),
        ordinal=1,
        total=1,
    )
    assert omitted.ok is True
    assert allowed.timeouts == [180, FOREGROUND_COMMAND_MAX_SECONDS]

    tool = make_bash_tool(limited)
    timeout = tool.definition.function.parameters["properties"]["timeout"]
    description = tool.definition.function.description
    assert timeout["minimum"] == 1
    assert timeout["maximum"] == 120
    assert "120" in timeout["description"]
    assert "workspace root" in description
    assert "do not persist" in description
    assert "1–120" in description
    assert "mode=start" in description
    assert "lifecycle=acceptance" in description


@pytest.mark.asyncio
async def test_start_poll_and_cancel_keep_ownership_without_restarting(tmp_path):
    adapter = _RecordingAdapter()
    service = _service(tmp_path, limit=120, adapter=adapter)
    executor = _executor(tmp_path, service)
    refused = await executor.execute_with_context(
        _call({"command": "printf nope", "mode": "start"}, "start-no-task"),
        run_context=ToolRunContext(run_id="run-only", session_id="session-1"),
        ordinal=1,
        total=1,
    )
    assert refused.ok is False
    assert "没有任务" in _body(refused)["error"]["message"]
    assert adapter.spawns == 0
    script = tmp_path / "hold.py"
    script.write_text(_HOLDER)
    ready_a = tmp_path / "ready-a"
    gone_a = tmp_path / "gone-a"
    release_a = tmp_path / "release-a"
    ready_b = tmp_path / "ready-b"
    gone_b = tmp_path / "gone-b"
    release_b = tmp_path / "release-b"
    command_a = f"{sys.executable} {script} {ready_a} {gone_a} {release_a}"
    command_b = f"{sys.executable} {script} {ready_b} {gone_b} {release_b}"
    try:
        started = await executor.execute_with_context(
            _call({"command": command_a, "mode": "start"}, "start-a"),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        assert started.ok is True
        first = _body(started)["result"]
        execution_id = first["execution_id"]
        assert first["status"] == "running"
        assert "output_offset" in first
        await _wait_for(gone_a)
        pid_a = int(ready_a.read_text())
        assert _alive(pid_a)

        polled = await executor.execute_with_context(
            _call(
                {
                    "command": command_a,
                    "mode": "poll",
                    "execution_id": execution_id,
                    "offset": first["output_offset"],
                },
                "poll-a",
            ),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        assert polled.ok is True
        again = _body(polled)["result"]
        assert again["execution_id"] == execution_id
        assert again["status"] == "running"
        assert adapter.spawns == 1
        assert _alive(pid_a)

        ignored = await executor.execute_with_context(
            _call(
                {
                    "command": "echo 'unterminated",
                    "mode": "poll",
                    "execution_id": execution_id,
                },
                "poll-ignores-command",
            ),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        assert ignored.ok is True
        assert _body(ignored)["result"]["execution_id"] == execution_id
        assert adapter.spawns == 1

        successor_adapter = _RecordingAdapter()
        successor = _service(tmp_path, limit=120, adapter=successor_adapter)
        successor.tracked = service.tracked
        shared = await _executor(tmp_path, successor).execute_with_context(
            _call(
                {"command": "printf shared", "mode": "poll", "execution_id": execution_id},
                "poll-shared",
            ),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        assert shared.ok is True
        assert _body(shared)["result"]["execution_id"] == execution_id
        assert successor_adapter.spawns == 0
        assert adapter.spawns == 1

        other = await executor.execute_with_context(
            _call({"command": command_b, "mode": "start"}, "start-b"),
            run_context=_run("task-b"),
            ordinal=1,
            total=1,
        )
        assert other.ok is True
        second_id = _body(other)["result"]["execution_id"]
        await _wait_for(gone_b)
        pid_b = int(ready_b.read_text())
        assert pid_b != pid_a

        stranger = await executor.execute_with_context(
            _call(
                {"command": "printf no", "mode": "poll", "execution_id": execution_id},
                "poll-stranger",
            ),
            run_context=_run("task-a", session_id="session-2"),
            ordinal=1,
            total=1,
        )
        assert stranger.ok is False
        assert adapter.spawns == 2
        assert _alive(pid_a)

        stopped = service.release_owned(session_id="session-1", task_id="task-a", reason="cancel")
        assert stopped == (execution_id,)
        await _wait_dead(pid_a)
        assert _alive(pid_b)
        assert second_id != execution_id

        kept = await executor.execute_with_context(
            _call(
                {"command": "printf still", "mode": "poll", "execution_id": second_id},
                "poll-b",
            ),
            run_context=_run("task-b"),
            ordinal=1,
            total=1,
        )
        assert kept.ok is True
        assert _body(kept)["result"]["status"] == "running"
        assert adapter.spawns == 2
    finally:
        service.release_owned(session_id="session-1", task_id="task-a", reason="cancel")
        service.release_owned(session_id="session-1", task_id="task-b", reason="cancel")
        release_a.write_text("go")
        release_b.write_text("go")
        if ready_b.exists():
            await _wait_dead(int(ready_b.read_text()))


@pytest.mark.asyncio
async def test_acceptance_service_survives_task_acceptance_and_later_polls(tmp_path):
    adapter = _RecordingAdapter()
    service = _service(tmp_path, limit=120, adapter=adapter)
    executor = _executor(tmp_path, service)
    script = tmp_path / "hold.py"
    script.write_text(_HOLDER)
    paths = {}
    for name in ("service", "task"):
        paths[name] = (
            tmp_path / f"ready-{name}",
            tmp_path / f"gone-{name}",
            tmp_path / f"release-{name}",
        )
    commands = {
        name: f"{sys.executable} {script} {ready} {gone} {release}"
        for name, (ready, gone, release) in paths.items()
    }
    try:
        service_start = await executor.execute_with_context(
            _call(
                {"command": commands["service"], "mode": "start", "lifecycle": "acceptance"},
                "start-service",
            ),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        task_start = await executor.execute_with_context(
            _call({"command": commands["task"], "mode": "start"}, "start-task"),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        assert service_start.ok is True
        assert task_start.ok is True
        service_id = _body(service_start)["result"]["execution_id"]
        task_id = _body(task_start)["result"]["execution_id"]
        await _wait_for(paths["service"][1])
        await _wait_for(paths["task"][1])
        service_pid = int(paths["service"][0].read_text())
        task_pid = int(paths["task"][0].read_text())

        followed = await executor.execute_with_context(
            _call(
                {"command": "printf poll", "mode": "poll", "execution_id": service_id},
                "poll-service",
            ),
            run_context=_run("task-a"),
            ordinal=1,
            total=1,
        )
        assert followed.ok is True
        assert _body(followed)["result"]["execution_id"] == service_id
        assert _body(followed)["result"]["status"] == "running"
        assert adapter.spawns == 2

        released = service.release_owned(session_id="session-1", task_id="task-a", reason="accept")
        assert released == (task_id,)
        await _wait_dead(task_pid)
        assert _alive(service_pid)

        still = await executor.execute_with_context(
            _call(
                {"command": "printf again", "mode": "poll", "execution_id": service_id},
                "poll-after-accept",
            ),
            run_context=_run("task-later"),
            ordinal=1,
            total=1,
        )
        assert still.ok is True
        assert _body(still)["result"]["status"] == "running"
        assert _body(still)["result"]["execution_id"] == service_id
        assert adapter.spawns == 2

        stopped = await executor.execute_with_context(
            _call(
                {"command": "printf stop", "mode": "stop", "execution_id": service_id},
                "stop-service",
            ),
            run_context=_run("task-later"),
            ordinal=1,
            total=1,
        )
        assert stopped.ok is True
        assert _body(stopped)["result"]["status"] == "cancelled"
        await _wait_dead(service_pid)
        assert adapter.spawns == 2
    finally:
        service.release_owned(session_id="session-1", task_id="task-a", reason="cancel")
        for _ready, _gone, release in paths.values():
            release.write_text("go")
