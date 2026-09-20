"""Conflict ordering uses barriers; distinct IDs never prove confinement."""

import asyncio

import pytest

from morrow.application.execution_gate import ExecutionCoordinator
from morrow.core.application import ApplicationError
from morrow.core.interactions import InteractionRequest
from test_stage8_chat_submission import drain
from test_stage8_core_api import ServerFixture, wait_for_run
from test_workflow_controls import _started_direct_run


async def test_direct_run_text_control_pause_idempotent_and_continue(tmp_path):
    """Explicit-pause text reaches a published-workflow run that never had a
    planning binding: the run resolves from the Session root, the client-side
    expected target is validated, retries replay, and continue resumes."""

    fx, sid, root, run_id, release = await _started_direct_run(tmp_path)
    try:
        # A stale expected target settles unresolved and pauses nothing.
        mismatch = await fx.client.post(
            root + "/task-plan/control",
            {
                "command_id": "cmd_direct_mismatch",
                "session_id": sid,
                "text": "暂停",
                "expected_workflow_run_id": "wrun_someone_else",
            },
        )
        assert mismatch.status == 200, mismatch.body
        body = mismatch.json()
        assert body["disposition"] == "unresolved" and body["intent"] == "none", body
        assert "执行目标已变化" in body["message"]
        run = await fx.on_core(
            lambda: fx.host.context.journal.workflows.get_run(fx.workspace_id, run_id)
        )
        assert run.pause_requested is False

        # The matching target executes the durable pause.
        pause_body = {
            "command_id": "cmd_direct_pause",
            "session_id": sid,
            "text": "暂停",
            "expected_workflow_run_id": run_id,
        }
        pause = await fx.client.post(root + "/task-plan/control", pause_body)
        assert pause.status == 200, pause.body
        body = pause.json()
        assert body["disposition"] == "executed" and body["intent"] == "pause_run", body
        assert body["run"]["pause_requested"] is True

        # The same command id replays the stored receipt: never a second pause.
        replay = await fx.client.post(root + "/task-plan/control", pause_body)
        assert replay.status == 200, replay.body
        assert replay.json()["replayed"] is True
        assert replay.json()["disposition"] == "executed"

        # Drain settles paused; a continue word resumes the same run in place.
        release()
        settled = await wait_for_run(fx.client, run_id, "paused")
        assert settled["run"]["pause_requested"] is True
        resumed = await fx.client.post(
            root + "/task-plan/control",
            {
                "command_id": "cmd_direct_continue",
                "session_id": sid,
                "text": "继续",
                "expected_workflow_run_id": run_id,
            },
        )
        assert resumed.status == 200, resumed.body
        assert resumed.json()["disposition"] == "executed", resumed.json()
        assert resumed.json()["intent"] == "resume_run"
        finished = await wait_for_run(fx.client, run_id, "completed")
        assert all(node["node"]["status"] == "completed" for node in finished["nodes"])
        await drain(fx, sid)

        def run_counts():
            backend = fx.host.context.journal._backend
            return backend.read_one("SELECT count(*) FROM workflow_runs", ())[0]

        assert await fx.on_core(run_counts) == 1
    finally:
        fx.close()


@pytest.mark.parametrize("kind", ["same", "nested", "host", "full-access"])
async def test_conflicting_roots_queue_and_cancel_without_leaking(kind, tmp_path):
    coordinator = ExecutionCoordinator()
    root = tmp_path / "a"
    other = root if kind == "same" else root / "nested" if kind == "nested" else tmp_path / "b"
    gate_a = coordinator.gate(root, confined=lambda: kind != "full-access")
    gate_b = coordinator.gate(other, confined=lambda: kind != "host")
    entered = asyncio.Event()
    attempted = asyncio.Event()
    release = asyncio.Event()

    async def second():
        attempted.set()
        async with gate_b:
            entered.set()
            await release.wait()

    async with gate_a:
        task = asyncio.create_task(second())
        await attempted.wait()
        assert not entered.is_set()
        assert len(coordinator.waiting) == 1
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not coordinator.active and not coordinator.waiting


async def test_disjoint_confined_roots_parallel_and_host_waiter_fairness(tmp_path):
    coordinator = ExecutionCoordinator()
    a = coordinator.gate(tmp_path / "a", confined=lambda: True)
    b = coordinator.gate(tmp_path / "b", confined=lambda: True)
    host = coordinator.gate(tmp_path / "elsewhere")
    attempted = asyncio.Event()
    entered = asyncio.Event()

    async def host_run():
        attempted.set()
        async with host:
            entered.set()

    async with a:
        async with b:
            assert len(coordinator.active) == 2
        task = asyncio.create_task(host_run())
        await attempted.wait()
        assert not entered.is_set()
    await task
    assert entered.is_set()


async def test_cancelled_waiter_unblocks_and_confinement_rechecked(tmp_path):
    coordinator = ExecutionCoordinator()
    a = coordinator.gate(tmp_path / "a", confined=lambda: True)
    host = coordinator.gate(tmp_path / "b")
    attempted = asyncio.Event()

    async def wait():
        attempted.set()
        async with host:
            pytest.fail("cancelled waiter entered")

    async with a:
        task = asyncio.create_task(wait())
        await attempted.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert coordinator.waiting == []
    assert coordinator.active == []


async def test_maintenance_blocks_admission_across_workspaces(tmp_path):
    fixture = ServerFixture(tmp_path)
    try:

        def exercise():
            registry = fixture.host.context.workspaces
            api = registry.default.api
            session = api.create_session(command_id="cmd_maintenance_session").value
            with registry.maintenance():
                with pytest.raises(ApplicationError, match="maintenance"):
                    registry.default.chat.interactions.submit(
                        session.session_id,
                        InteractionRequest(client_message_id="maintenance", text="x"),
                    )
            assert not registry.maintaining
            registry.default.chat.interactions.submit(
                session.session_id, InteractionRequest(client_message_id="accepted", text="x")
            )
            with pytest.raises(ApplicationError, match="Data root"):
                registry.require_maintenance_idle()
            with pytest.raises(ApplicationError, match="Data root"):
                api.cleanup_orphans(dry_run=False)

        await fixture.on_core(exercise)
    finally:
        fixture.close()


async def test_production_confined_runtimes_overlap_with_isolated_files_cwd_and_environment(
    tmp_path,
):
    import os

    from morrow.core.capabilities import PermissionPreset, PermissionProfile
    from morrow.core.local_tools import CommandRequest
    from morrow.services.files import LocalFileError

    fixture = ServerFixture(tmp_path)
    initial_cwd = os.getcwd()
    initial_env = dict(os.environ)
    try:

        async def journey():
            registry = fixture.host.context.workspaces
            registry.permission_profile = PermissionProfile.from_preset(
                PermissionPreset.AUTO_SANDBOXED
            )
            contexts = []
            for name in ("isolated-a", "isolated-b"):
                folder = tmp_path / name
                folder.mkdir()
                (folder / "identity.txt").write_text(name)
                service = fixture.app.workspace_service
                identity = service.confirm(service.resolve(folder))
                context = registry.get(identity.workspace_id)
                sid = context.api.create_session(command_id="cmd_" + name).value.session_id
                contexts.append((context, sid, folder, name))
            entered = [asyncio.Event(), asyncio.Event()]
            release = asyncio.Event()
            products = []

            async def drive(index):
                context, sid, folder, name = contexts[index]
                runtime = context.chat.runtime(sid)
                products.append(runtime)
                assert runtime.files.read_file("identity.txt").text.strip() == name
                with pytest.raises(LocalFileError):
                    runtime.files.read_file("../" + contexts[1 - index][3] + "/identity.txt")
                assert runtime.process.preflight(CommandRequest(argv=("pwd",))).cwd == folder
                assert runtime.process.requires_sandbox and not runtime.process.requires_host
                assert runtime.session.workspace_capability.root == folder
                runtime.process.environment["LANG"] = name
                entered[index].set()
                await release.wait()

            for i, (context, sid, _, _) in enumerate(contexts):
                context.chat.ensure_driver(sid, lambda i=i: drive(i))
            tasks = [c.chat.drivers[sid] for c, sid, _, _ in contexts]
            try:
                await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered)), timeout=30)
                assert len(registry.coordinator.active) == 2
                assert (
                    products[0].process._minimal_environment()["LANG"]
                    != products[1].process._minimal_environment()["LANG"]
                )
                assert products[0].session.log is not products[1].session.log
            finally:
                release.set()
                await asyncio.gather(*tasks)

        await fixture.host.execute_preparation(journey)
        assert os.getcwd() == initial_cwd and dict(os.environ) == initial_env
    finally:
        fixture.close()
