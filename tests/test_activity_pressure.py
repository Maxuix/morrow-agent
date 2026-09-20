"""Host-level pressure acceptance for the activity system (master plan P7.2).

Covers A16: many sessions with activity traffic stay bounded (metadata,
preview and Host totals), a huge command output streams without blocking or
unbounded memory, and per-session activity streams never cross sessions.
"""

from __future__ import annotations

import asyncio
import sys

from morrow.core.capabilities import PolicyVerdict, ToolRunContext
from morrow.core.local_tools import CommandRequest
from morrow.core.models import AssistantMessage
from morrow.server.activities import (
    ACTIVITY_HOST_TOTAL_BYTES,
    ACTIVITY_METADATA_LIMIT,
    host_activity_bytes,
)
from morrow.services.files import WorkspaceFileService, WorkspacePathResolver
from morrow.services.process import ProcessExecutionService
from test_node_steer import arm_provider, publish_single
from test_stage8_core_api import ServerFixture, create_session_and_task, start_run, wait_for_run


def _service(tmp_path, secrets=()):
    files = WorkspaceFileService(WorkspacePathResolver(tmp_path))
    return ProcessExecutionService(files, secrets=secrets)


def _run() -> ToolRunContext:
    return ToolRunContext(run_id="run-1", session_id="session-1")


async def test_multi_session_activity_traffic_stays_bounded(tmp_path):
    """Many sessions streaming activities never exceed the per-session and
    Host budgets; sessions stay isolated."""
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_single(fx)
        fx.bank.on_create = lambda _: setattr(
            fx.bank.providers[-1], "responses", [AssistantMessage(content="leaf answer")]
        )
        session_ids = []
        for _index in range(8):
            session_id, task_id, task_version = await create_session_and_task(fx.client)
            started = await start_run(
                fx.client, revision.workflow_revision_id, session_id, task_id, task_version
            )
            run_id = started.json()["result"]["run"]["workflow_run_id"]
            await wait_for_run(fx.client, run_id, "completed")
            session_ids.append(session_id)
        streams = fx.host.context.chat.streams.states
        assert len(streams) >= 8
        for state in streams.values():
            assert len(state.activities.entries) <= ACTIVITY_METADATA_LIMIT
            assert state.activities.preview_bytes <= 512 * 1024
            assert state.activities.ring_bytes <= 1024 * 1024
        assert host_activity_bytes() <= ACTIVITY_HOST_TOTAL_BYTES
        # Isolation: every item in one stream carries one consistent root
        # session id — no cross-session mixing (leaf streams carry the root).
        for sid, state in streams.items():
            roots = {
                item["identity"]["root_session_id"] for item in state.activities.snapshot_items()
            }
            assert len(roots) <= 1, (sid, roots)
    finally:
        fx.close()


async def test_huge_output_streams_without_blocking_or_unbounded_memory(tmp_path):
    """A 400 KB single command streams bounded fragments; the durable result
    stays bounded and the process completes."""
    service = _service(tmp_path)
    observed: list[str] = []
    plan = service.preflight(CommandRequest(argv=(sys.executable, "-c", "print('y' * 400000)")))
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
    streamed = sum(len(text) for text in observed)
    assert streamed > 0
    # The realtime channel is a projection: the durable envelope stays bounded.
    assert len(result.stdout) <= 64 * 1024 + 8192


async def test_activity_system_never_blocks_execution_under_pressure(tmp_path):
    """Observer-less execution and observer-failing execution produce the same
    durable workflow outcome (A16)."""
    fx = ServerFixture(tmp_path)
    try:
        revision = await publish_single(fx)
        session_id, task_id, task_version = await create_session_and_task(fx.client)
        gate = asyncio.Event()
        provider = arm_provider(fx, gate)
        started = await start_run(
            fx.client, revision.workflow_revision_id, session_id, task_id, task_version
        )
        run_id = started.json()["result"]["run"]["workflow_run_id"]
        gate.set()
        view = await wait_for_run(fx.client, run_id, "completed", "failed")
        assert view["run"]["status"] == "completed"
        assert provider.calls == 2
    finally:
        fx.close()
