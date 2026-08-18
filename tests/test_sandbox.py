from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.local.process import ProcessAdapterError
from morrow.adapters.local.sandbox import (
    LinuxBubblewrapBackend,
    MacOSSeatbeltBackend,
    NativeSandboxProcessAdapter,
    SandboxBackend,
    SandboxBackendError,
    SandboxCapability,
)
from morrow.application.local_tools import make_promote_sandbox_tool, make_show_changes_tool
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    PermissionPreset,
    PermissionProfile,
    PolicyVerdict,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.local_tools import CommandRequest, CommandStatus
from morrow.core.models import FunctionToolCall, ModelRef, ToolApprovalDecision
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.services.process import ProcessExecutionService
from morrow.services.sandbox import SandboxSnapshotService
from morrow.testing import ScriptedModelProvider, make_run_policy


def _files(root: Path) -> WorkspaceFileService:
    return WorkspaceFileService(WorkspacePathResolver(root))


def test_platform_backend_builders_are_fixed_and_fail_closed(tmp_path, monkeypatch):
    mac = MacOSSeatbeltBackend(executable="/usr/bin/sandbox-exec")
    profile = mac._profile(
        tmp_path / "workspace",
        tmp_path / "tmp",
        tmp_path / "home",
        tmp_path,
        blocked_paths=(tmp_path / "workspace",),
    )
    assert "(deny default)" in profile
    assert "(deny network*)" in profile
    assert str(tmp_path / "workspace") in profile
    monkeypatch.setattr("morrow.adapters.local.sandbox.platform.system", lambda: "Linux")
    linux = LinuxBubblewrapBackend(executable="/usr/bin/bwrap")
    capability = linux.probe()
    assert capability.supported is False
    assert "真实 Linux runner" in capability.reason
    with pytest.raises(SandboxBackendError):
        linux.build_command(
            argv=("/usr/bin/python3", "-c", "print(1)"),
            snapshot_root=tmp_path / "workspace",
            temp_root=tmp_path / "tmp",
            private_home=tmp_path / "home",
            cwd=tmp_path / "workspace",
        )
    command = linux._build_command(
        executable="/usr/bin/bwrap",
        argv=("/usr/bin/python3", "-c", "print(1)"),
        snapshot_root=tmp_path / "workspace",
        temp_root=tmp_path / "tmp",
        private_home=tmp_path / "home",
        cwd=tmp_path / "workspace",
    )
    assert "--unshare-network" in command
    assert "--unshare-pid" in command
    assert "--die-with-parent" in command
    assert "--ro-bind" in command


def test_snapshot_excludes_sensitive_external_and_cache_paths(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('ok')\n")
    (workspace / ".env").write_text("SECRET=hidden\n")
    (workspace / ".pytest_cache").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    (workspace / "external-link").symlink_to(outside)
    (workspace / "internal-link").symlink_to("main.py")
    service = SandboxSnapshotService(_files(workspace))
    session = service.prepare(workspace, run_id="run", call_id="call")
    try:
        assert (session.snapshot_root / "main.py").exists()
        assert not (session.snapshot_root / ".env").exists()
        assert not (session.snapshot_root / ".pytest_cache").exists()
        assert not (session.snapshot_root / "external-link").exists()
        assert (session.snapshot_root / "internal-link").is_symlink()
        (session.snapshot_root / "main.py").write_text("print('changed')\n")
        changes = service.collect(session)
        assert changes.changed_paths == ("main.py",)
        assert changes.eligible_changes[0].content == "print('changed')\n"
    finally:
        service.cleanup(session)
    assert (workspace / "main.py").read_text() == "print('ok')\n"


class _PassThroughSandboxBackend(SandboxBackend):
    name = "test-pass-through"

    def probe(self):
        return SandboxCapability(
            platform="test",
            backend=self.name,
            supported=True,
            reason="test backend",
        )

    def build_command(self, **kwargs):
        return tuple(kwargs["argv"])


class _PrepareTimeoutSnapshots(SandboxSnapshotService):
    def _copy_tree(self, source, destination, *, cancel_event=None):
        del source, destination
        assert cancel_event is not None
        cancel_event.wait()
        self._check_cancelled(cancel_event)
        return {}


class _CollectTimeoutSnapshots(SandboxSnapshotService):
    def collect(self, session, *, cancel_event=None):
        del session
        assert cancel_event is not None
        cancel_event.wait()
        self._check_cancelled(cancel_event)
        raise AssertionError("cancelled collect must not continue")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["prepare", "collect"])
async def test_snapshot_phase_timeout_cancels_worker_and_removes_reserved_root(tmp_path, phase):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
    snapshot_type = _PrepareTimeoutSnapshots if phase == "prepare" else _CollectTimeoutSnapshots
    snapshots = snapshot_type(_files(workspace), temp_parent=tmp_path)
    adapter = NativeSandboxProcessAdapter(
        workspace,
        snapshots,
        _PassThroughSandboxBackend(),
        prepare_timeout_seconds=0.05,
        collect_timeout_seconds=0.05,
        cleanup_timeout_seconds=1.0,
    )

    with pytest.raises(ProcessAdapterError) as error:
        await adapter.run(
            argv=(sys.executable, "-c", "print('ok')"),
            shell=None,
            cwd=workspace,
            timeout_seconds=1.0,
            environment={},
            output_limit=1024,
        )

    assert error.value.code == "sandbox_timeout"
    assert tuple(tmp_path.glob("morrow-sandbox-*")) == ()


class _Approval:
    async def request(self, request):
        self.request = request
        return ToolApprovalDecision(approved=True)


@pytest.mark.asyncio
async def test_sandbox_text_change_requires_approval_and_promotes_safely(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("print('before')\n")
    files = _files(workspace)
    snapshots = SandboxSnapshotService(files, temp_parent=tmp_path)
    session = snapshots.prepare(workspace, run_id="run", call_id="command")
    try:
        (session.snapshot_root / "main.py").write_text("print('after')\n")
        change_set = snapshots.collect(session)
    finally:
        snapshots.cleanup(session)
    run = ToolRunContext(run_id="run", session_id="session")
    snapshots.retain(run, change_set)
    changes = ChangeSetService()
    registry = ToolRegistry()
    registry.register(
        make_promote_sandbox_tool(snapshots, WorkspaceMutationService(files), changes)
    )
    registry.register(make_show_changes_tool(changes))
    approval = _Approval()
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=workspace),
        ),
    )
    call = FunctionToolCall(
        id="promote",
        name="promote_sandbox_changes",
        arguments=json.dumps({"change_set_id": change_set.change_set_id, "paths": ["main.py"]}),
    )
    outcome = await executor.execute_with_context(call, run_context=run, ordinal=1, total=1)
    assert outcome.ok is True
    assert "main.py" in "\n".join(approval.request.preview)
    assert target.read_text() == "print('after')\n"
    shown = await executor.execute_with_context(
        FunctionToolCall(id="show", name="show_changes", arguments="{}"),
        run_context=run,
        ordinal=2,
        total=2,
    )
    assert shown.ok is True
    assert json.loads(shown.envelope)["result"]["entries"][0]["path"] == "main.py"


@pytest.mark.asyncio
async def test_sandbox_promotion_conflict_preserves_external_change_and_run_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "main.py"
    target.write_text("print('before')\n")
    files = _files(workspace)
    snapshots = SandboxSnapshotService(files, temp_parent=tmp_path)
    session = snapshots.prepare(workspace, run_id="run", call_id="command")
    try:
        (session.snapshot_root / "main.py").write_text("print('sandbox')\n")
        change_set = snapshots.collect(session)
    finally:
        snapshots.cleanup(session)

    run = ToolRunContext(run_id="run", session_id="session")
    snapshots.retain(run, change_set)
    registry = ToolRegistry()
    registry.register(
        make_promote_sandbox_tool(snapshots, WorkspaceMutationService(files), ChangeSetService())
    )
    approval = _Approval()
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=workspace),
        ),
    )
    target.write_text("print('external')\n")
    call = FunctionToolCall(
        id="conflict",
        name="promote_sandbox_changes",
        arguments=json.dumps({"change_set_id": change_set.change_set_id, "paths": ["main.py"]}),
    )
    outcome = await executor.execute_with_context(call, run_context=run, ordinal=1, total=1)
    assert outcome.ok is False
    assert outcome.error_code is ToolErrorCode.CONFLICT
    assert target.read_text() == "print('external')\n"
    assert approval.request.effect.value == "persistent_write"

    expired = ToolRunContext(run_id="other", session_id="session")
    expired_outcome = await executor.execute_with_context(
        FunctionToolCall(
            id="expired",
            name="promote_sandbox_changes",
            arguments=call.arguments,
        ),
        run_context=expired,
        ordinal=1,
        total=1,
    )
    assert expired_outcome.ok is False
    assert expired_outcome.error_code is ToolErrorCode.NOT_FOUND


@pytest.mark.skipif(
    sys.platform != "darwin", reason="claimed native backend is macOS in this workspace"
)
@pytest.mark.skipif(
    os.environ.get("CODEX_SANDBOX") == "seatbelt",
    reason="real Seatbelt test must run at host level, not inside the nested Codex sandbox",
)
@pytest.mark.asyncio
async def test_production_auto_sandbox_registers_only_native_tools_and_keeps_real_workspace_clean(
    tmp_path,
):
    project = tmp_path / "project"
    project.mkdir()
    application = build_application(
        state_root=tmp_path / "state", credentials=MemoryCredentialStore()
    )
    identity = application.workspace_service.confirm(application.workspace_service.resolve(project))
    session_application = build_session_application(
        application,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
        permission_profile=PermissionProfile.from_preset(PermissionPreset.AUTO_SANDBOXED),
    )
    executor = session_application.orchestrator.runtime.loop.tool_executor
    names = {definition.function.name for definition in executor.definitions}
    assert "run_command" in names
    assert "promote_sandbox_changes" in names
    system_python = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
    call = FunctionToolCall(
        id="sandbox-command",
        name="run_command",
        arguments=json.dumps(
            {
                "argv": [
                    system_python,
                    "-c",
                    "from pathlib import Path; Path('sandbox-only.txt').write_text('ok')",
                ]
            }
        ),
    )
    outcome = await executor.execute_with_context(
        call,
        run_context=ToolRunContext(run_id="run", session_id="session"),
        ordinal=1,
        total=1,
    )
    assert outcome.ok is True, outcome.envelope
    result = json.loads(outcome.envelope)["result"]
    assert result["status"] == "exited"
    assert result["sandbox_change_set_id"].startswith("sbx_")
    assert result["sandbox_changed_paths"] == ["sandbox-only.txt"]
    assert not (project / "sandbox-only.txt").exists()


@pytest.mark.skipif(
    sys.platform != "darwin", reason="claimed native backend is macOS in this workspace"
)
@pytest.mark.skipif(
    os.environ.get("CODEX_SANDBOX") == "seatbelt",
    reason="real Seatbelt test must run at host level, not inside the nested Codex sandbox",
)
@pytest.mark.asyncio
async def test_macos_native_sandbox_blocks_real_workspace_home_and_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=hidden\n")
    sentinel = tmp_path / "real-workspace-sentinel"
    files = _files(workspace)
    snapshots = SandboxSnapshotService(files, temp_parent=Path("/private/tmp"))
    backend = MacOSSeatbeltBackend()
    capability = backend.probe()
    assert capability.supported is True
    adapter = NativeSandboxProcessAdapter(workspace, snapshots, backend)
    service = ProcessExecutionService(
        files,
        adapter=adapter,
        requires_host=False,
        requires_sandbox=True,
    )
    code = f"""from pathlib import Path
import json
import socket

Path('sandbox-created.txt').write_text('inside')
checks = Path({str(sentinel)!r})
try:
    checks.write_text('escaped')
    outside = 'allowed'
except OSError:
    outside = 'blocked'
try:
    Path('/Users/ruirui/morrow-sandbox-home').write_text('escaped')
    home = 'allowed'
except OSError:
    home = 'blocked'
try:
    list(Path('/Users/ruirui/.ssh').iterdir())
    home_read = 'allowed'
except OSError:
    home_read = 'blocked'
try:
    Path('.env').read_text()
    protected = 'allowed'
except OSError:
    protected = 'blocked'
try:
    socket.create_connection(('127.0.0.1', 9), 0.2)
    network = 'allowed'
except OSError:
    network = 'blocked'
print(json.dumps({{'outside': outside, 'home': home, 'home_read': home_read, 'protected': protected, 'network': network}}))
"""
    system_python = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
    plan = service.preflight(CommandRequest(argv=(system_python, "-c", code)))
    result, _ = await service.execute(
        plan,
        result_limit=16 * 1024,
        run=ToolRunContext(run_id="run", session_id="session"),
        call_id="call",
        tool_name="run_command",
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
    )
    assert result.status is CommandStatus.EXITED
    assert result.stdout.strip(), result.stderr
    assert json.loads(result.stdout.strip()) == {
        "outside": "blocked",
        "home": "blocked",
        "home_read": "blocked",
        "protected": "blocked",
        "network": "blocked",
    }
    assert not sentinel.exists()
    assert not (workspace / "sandbox-created.txt").exists()
    assert result.sandbox_change_set_id is not None
    assert result.sandbox_changed_paths == ("sandbox-created.txt",)
