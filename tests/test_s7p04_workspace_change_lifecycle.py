from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.local.filesystem import (
    FileSystemAdapter,
    FileSystemCapabilityError,
    FileSystemMutationError,
)
from morrow.application.local_tools import (
    DELETE_FILE_PROVIDER_SCHEMA,
    MOVE_FILE_PROVIDER_SCHEMA,
    RENAME_FILE_PROVIDER_SCHEMA,
    DeleteFileArguments,
    MoveFileArguments,
    RenameFileArguments,
    make_delete_file_tool,
    make_move_file_tool,
    make_promote_sandbox_tool,
    make_rename_file_tool,
)
from morrow.application.prepared import file_evidence_from_plan
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    PermissionPreset,
    PermissionProfile,
    PolicyVerdict,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.execution import (
    PRODUCTION_TOOL_NAMES,
    EffectClass,
    FileMutationEvidence,
    MissingCompletionPolicy,
    RecoveryClassification,
    ToolExecutionDisposition,
    ToolExecutionState,
    tool_declaration,
)
from morrow.core.local_tools import MutationOperation, MutationStatus
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolApprovalDecision
from morrow.core.recovery import (
    FileObservation,
    classify_execution,
    classify_file_observations,
    observe_file,
)
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tool_arguments import JsonSchemaArgumentsValidator, ToolArgumentsValidationError
from morrow.runtime.tools import ToolErrorCode, ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    LocalFileError,
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.services.sandbox import SandboxSnapshotService
from morrow.testing import ScriptedModelProvider, make_run_policy


def test_explicit_workspace_change_operations_have_strict_provider_contracts():
    assert {
        MutationOperation.CREATE,
        MutationOperation.PATCH,
        MutationOperation.REPLACE,
        MutationOperation.DELETE,
        MutationOperation.MOVE,
        MutationOperation.RENAME,
    } == set(MutationOperation)
    assert {
        MutationStatus.CREATED,
        MutationStatus.MODIFIED,
        MutationStatus.UNCHANGED,
        MutationStatus.DELETED,
        MutationStatus.MOVED,
        MutationStatus.RENAMED,
        MutationStatus.OUTCOME_UNKNOWN,
    } == set(MutationStatus)

    with pytest.raises(ValidationError):
        DeleteFileArguments.model_validate({"path": "gone.txt"}, strict=True)
    with pytest.raises(ValidationError):
        MoveFileArguments.model_validate(
            {"source_path": "old.txt", "destination_path": "new.txt"}, strict=True
        )
    with pytest.raises(ValidationError):
        RenameFileArguments.model_validate(
            {
                "source_path": "old.txt",
                "destination_path": "new.txt",
                "expected_sha256": "0" * 63,
            },
            strict=True,
        )

    valid = {
        "delete_file": (
            DELETE_FILE_PROVIDER_SCHEMA,
            {"path": "gone.txt", "expected_sha256": "0" * 64},
        ),
        "move_file": (
            MOVE_FILE_PROVIDER_SCHEMA,
            {
                "source_path": "old.txt",
                "destination_path": "new.txt",
                "expected_sha256": "0" * 64,
            },
        ),
        "rename_file": (
            RENAME_FILE_PROVIDER_SCHEMA,
            {
                "source_path": "old.txt",
                "destination_path": "new.txt",
                "expected_sha256": "0" * 64,
            },
        ),
    }
    for _name, (schema, value) in valid.items():
        validator = JsonSchemaArgumentsValidator(schema)
        assert validator.validate(json.dumps(value)) == value
        with pytest.raises(ToolArgumentsValidationError):
            validator.validate(json.dumps({**value, "force": True}))


def test_destructive_file_tools_are_in_production_inventory_and_reconcileable():
    assert {"delete_file", "move_file", "rename_file"} <= PRODUCTION_TOOL_NAMES
    for name in ("delete_file", "move_file", "rename_file"):
        declaration = tool_declaration(name, production_only=True)
        assert declaration.effect_class is EffectClass.RECONCILEABLE_FILE_WRITE
        assert (
            declaration.missing_handler_completed is MissingCompletionPolicy.REQUIRES_RECONCILIATION
        )


def test_production_session_keeps_destructive_factories_out_of_the_core_surface(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=ScriptedModelProvider(["done"]),
        model=ModelRef(provider_id="p", model_id="m"),
    )
    names = {
        tool.function.name
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
    }
    assert {"bash", "edit", "write"} <= names
    assert {"delete_file", "move_file", "rename_file"}.isdisjoint(names)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _services(root: Path, *, filesystem: FileSystemAdapter | None = None):
    files = WorkspaceFileService(WorkspacePathResolver(root), filesystem=filesystem)
    return files, WorkspaceMutationService(files)


def _publish(mutation: WorkspaceMutationService, plan, *, call_id: str = "call-1"):
    run = ToolRunContext(run_id="run-1", session_id="session-1")
    result, fact = mutation.apply(
        plan,
        call_id=call_id,
        tool_name=plan.operation.value,
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    return result, fact, run


def test_delete_move_and_rename_publish_only_regular_files_without_overwrite(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("move me\n", encoding="utf-8")
    source.chmod(0o640)
    _, mutation = _services(tmp_path)

    delete_source = tmp_path / "delete.txt"
    delete_source.write_text("remove me\n", encoding="utf-8")
    delete_plan = mutation.preflight_delete("delete.txt", expected_sha256=_sha(delete_source))
    assert delete_plan.operation is MutationOperation.DELETE
    deleted, delete_fact, _ = _publish(mutation, delete_plan, call_id="delete")
    assert not delete_source.exists()
    assert deleted.status is MutationStatus.DELETED
    assert deleted.source_path == "delete.txt"
    assert deleted.destination_path is None
    assert delete_fact.relative_paths == ("delete.txt",)
    assert "a/delete.txt" in deleted.diff

    (tmp_path / "dest").mkdir()
    move_plan = mutation.preflight_move(
        "source.txt", "dest/moved.txt", expected_sha256=_sha(source)
    )
    assert move_plan.relative_paths == ("source.txt", "dest/moved.txt")
    moved, move_fact, _ = _publish(mutation, move_plan, call_id="move")
    destination = tmp_path / "dest/moved.txt"
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "move me\n"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o640
    assert moved.status is MutationStatus.MOVED
    assert moved.source_path == "source.txt"
    assert moved.destination_path == "dest/moved.txt"
    assert move_fact.relative_paths == ("source.txt", "dest/moved.txt")
    assert "a/source.txt" in moved.diff and "b/dest/moved.txt" in moved.diff

    rename_source = tmp_path / "rename.txt"
    rename_source.write_text("rename me\n", encoding="utf-8")
    rename_plan = mutation.preflight_rename(
        "rename.txt", "renamed.txt", expected_sha256=_sha(rename_source)
    )
    renamed, rename_fact, _ = _publish(mutation, rename_plan, call_id="rename")
    assert not rename_source.exists()
    assert (tmp_path / "renamed.txt").read_text(encoding="utf-8") == "rename me\n"
    assert renamed.status is MutationStatus.RENAMED
    assert rename_fact.relative_paths == ("rename.txt", "renamed.txt")


@pytest.mark.parametrize(
    ("method", "arguments", "expected_code"),
    [
        ("preflight_delete", {"path": "missing.txt", "expected_sha256": "0" * 64}, "not_found"),
        (
            "preflight_move",
            {
                "source_path": "missing.txt",
                "destination_path": "new.txt",
                "expected_sha256": "0" * 64,
            },
            "not_found",
        ),
    ],
)
def test_destructive_preflight_requires_existing_source_and_exact_revision(
    tmp_path, method, arguments, expected_code
):
    _, mutation = _services(tmp_path)
    with pytest.raises(LocalFileError) as error:
        if method == "preflight_delete":
            mutation.preflight_delete(**arguments)
        else:
            mutation.preflight_move(**arguments)
    assert error.value.code == expected_code

    source = tmp_path / "source.txt"
    source.write_text("before\n", encoding="utf-8")
    with pytest.raises(LocalFileError) as error:
        mutation.preflight_delete("source.txt", expected_sha256="0" * 64)
    assert error.value.code == "conflict"


def test_move_rejects_existing_destination_missing_parent_outside_and_same_parent_rule(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    existing = tmp_path / "existing.txt"
    existing.write_text("keep\n", encoding="utf-8")
    _, mutation = _services(tmp_path)

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_move("source.txt", "existing.txt", expected_sha256=_sha(source))
    assert error.value.code == "conflict"
    assert existing.read_text(encoding="utf-8") == "keep\n"

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_move("source.txt", "missing/target.txt", expected_sha256=_sha(source))
    assert error.value.code == "conflict"
    assert not (tmp_path / "missing").exists()

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_move("source.txt", "../outside.txt", expected_sha256=_sha(source))
    assert error.value.code == "invalid_path"

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_rename("source.txt", "nested/target.txt", expected_sha256=_sha(source))
    assert error.value.code == "invalid_path"

    outside = tmp_path.parent / "s7p04-destination-outside"
    outside.mkdir()
    try:
        (tmp_path / "destination-link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(LocalFileError) as error:
            mutation.preflight_move(
                "source.txt", "destination-link/target.txt", expected_sha256=_sha(source)
            )
        assert error.value.code == "symlink_not_allowed"
        assert not (outside / "target.txt").exists()
    finally:
        (tmp_path / "destination-link").unlink(missing_ok=True)
        outside.rmdir()


def test_destructive_mutations_reject_symlink_directories_and_special_files(tmp_path):
    outside = tmp_path.parent / "s7p04-outside"
    outside.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("outside\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside_file)
    (tmp_path / "directory").mkdir()
    _, mutation = _services(tmp_path)

    with pytest.raises(LocalFileError) as link_error:
        mutation.preflight_delete("link", expected_sha256=_sha(outside_file))
    assert link_error.value.code == "symlink_not_allowed"

    with pytest.raises(LocalFileError) as directory_error:
        mutation.preflight_delete("directory", expected_sha256="0" * 64)
    assert directory_error.value.code == "invalid_target"

    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / "pipe")
        with pytest.raises(LocalFileError) as special_error:
            mutation.preflight_delete("pipe", expected_sha256="0" * 64)
        assert special_error.value.code == "invalid_target"
    assert outside_file.read_text(encoding="utf-8") == "outside\n"


class _DestinationRace(FileSystemAdapter):
    def move_no_replace(self, source, destination, *, workspace_root, **kwargs):
        destination.touch()
        return super().move_no_replace(source, destination, workspace_root=workspace_root, **kwargs)


class _UnsupportedMove(FileSystemAdapter):
    def move_no_replace(self, source, destination, *, workspace_root, **kwargs):
        del source, destination, workspace_root, kwargs
        raise FileSystemCapabilityError(
            "unsupported_capability", "no proven atomic no-replace primitive"
        )


class _ReplaceSourceAfterHash(FileSystemAdapter):
    def __init__(self, replacement: Path):
        super().__init__()
        self.replacement = replacement
        self.source: Path | None = None
        self.swapped = False

    def _open_regular_at(self, parent_fd, name, *, max_bytes):
        fd, metadata, raw = FileSystemAdapter._open_regular_at(parent_fd, name, max_bytes=max_bytes)
        if not self.swapped and self.source is not None and name == self.source.name:
            os.replace(self.replacement, self.source)
            self.swapped = True
        return fd, metadata, raw


class _ReplaceSourceAfterIdentityCheck(FileSystemAdapter):
    def __init__(self, replacement: Path):
        super().__init__()
        self.replacement = replacement
        self.source: Path | None = None
        self.swapped = False

    def _assert_entry_identity(self, parent_fd, name, expected):
        FileSystemAdapter._assert_entry_identity(parent_fd, name, expected)
        if not self.swapped and self.source is not None and name == self.source.name:
            os.replace(self.replacement, self.source)
            self.swapped = True


class _ModeDriftAfterMove(FileSystemAdapter):
    def __init__(self, destination: Path):
        super().__init__()
        self.destination = destination

    def _rename_no_replace(
        self, source_parent_fd, source_name, destination_parent_fd, destination_name
    ):
        result = FileSystemAdapter._rename_no_replace(
            source_parent_fd, source_name, destination_parent_fd, destination_name
        )
        if self.destination.exists():
            self.destination.chmod(0o600)
        return result


class _FsyncAfterEffect(FileSystemAdapter):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def _fsync_required(self, fd):
        self.calls += 1
        if self.calls == 2:
            raise FileSystemMutationError("publication_failed", "injected fsync failure")
        return FileSystemAdapter._fsync_required(fd)


class _FsyncAfterCapture(FileSystemAdapter):
    def __init__(self):
        super().__init__()
        self.fail = True

    def _fsync_required(self, fd):
        if self.fail:
            self.fail = False
            raise FileSystemMutationError("publication_failed", "injected fsync failure")
        return FileSystemAdapter._fsync_required(fd)


def test_move_no_replace_fails_closed_on_destination_race_and_unsupported_capability(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    (tmp_path / "dest").mkdir()
    _, mutation = _services(tmp_path, filesystem=_DestinationRace())
    plan = mutation.preflight_move("source.txt", "dest/target.txt", expected_sha256=_sha(source))
    with pytest.raises(LocalFileError) as race_error:
        _publish(mutation, plan)
    assert race_error.value.code == "conflict"
    assert source.read_text(encoding="utf-8") == "source\n"
    assert (tmp_path / "dest/target.txt").read_bytes() == b""

    source.write_text("source\n", encoding="utf-8")
    (tmp_path / "dest/target.txt").unlink()
    _, unsupported_mutation = _services(tmp_path, filesystem=_UnsupportedMove())
    unsupported_plan = unsupported_mutation.preflight_move(
        "source.txt", "dest/target.txt", expected_sha256=_sha(source)
    )
    with pytest.raises(LocalFileError) as unsupported_error:
        _publish(unsupported_mutation, unsupported_plan)
    assert unsupported_error.value.code == "unsupported_capability"
    assert source.exists()
    assert not (tmp_path / "dest/target.txt").exists()


@pytest.mark.parametrize("operation", ("delete", "move"))
def test_destructive_effect_binds_source_entry_identity_before_effect(tmp_path, operation):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("user!!\n", encoding="utf-8")
    filesystem = _ReplaceSourceAfterHash(replacement)
    filesystem.source = source
    _, mutation = _services(tmp_path, filesystem=filesystem)
    if operation == "delete":
        plan = mutation.preflight_delete(source.name, expected_sha256=_sha(source))
        destination = None
    else:
        destination = tmp_path / "destination.txt"
        plan = mutation.preflight_move(source.name, destination.name, expected_sha256=_sha(source))

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan, call_id=f"source-race-{operation}")

    assert error.value.code == "conflict"
    assert source.read_text(encoding="utf-8") == "user!!\n"
    assert destination is None or not destination.exists()


@pytest.mark.parametrize("operation", ("delete", "move", "rename"))
def test_final_identity_window_does_not_effect_replaced_source(tmp_path, operation):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("user!!\n", encoding="utf-8")
    filesystem = _ReplaceSourceAfterIdentityCheck(replacement)
    filesystem.source = source
    _, mutation = _services(tmp_path, filesystem=filesystem)
    if operation == "delete":
        destination = None
        plan = mutation.preflight_delete(source.name, expected_sha256=_sha(source))
    else:
        destination = tmp_path / f"{operation}-destination.txt"
        plan = (
            mutation.preflight_move(source.name, destination.name, expected_sha256=_sha(source))
            if operation == "move"
            else mutation.preflight_rename(
                source.name, destination.name, expected_sha256=_sha(source)
            )
        )

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan, call_id=f"final-identity-race-{operation}")

    assert error.value.code == "conflict"
    assert source.read_text(encoding="utf-8") == "user!!\n"
    assert destination is None or not destination.exists()
    assert not any(path.name.startswith(".morrow-capture-") for path in tmp_path.iterdir())


def test_fifo_leaf_race_is_nonblocking_and_fails_closed(tmp_path, monkeypatch):
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    flags_seen: list[int] = []
    original_open = os.open

    def guarded_open(path, flags, *args, **kwargs):
        if path == pipe.name:
            flags_seen.append(flags)
            raise OSError(40, "injected fifo race")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    adapter = FileSystemAdapter()
    with pytest.raises(FileSystemMutationError):
        adapter.read_confined_file(pipe, workspace_root=tmp_path)
    assert flags_seen and flags_seen[0] & os.O_NONBLOCK


def test_move_post_effect_mode_drift_is_outcome_unknown(tmp_path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source\n", encoding="utf-8")
    source.chmod(0o640)
    _, mutation = _services(tmp_path, filesystem=_ModeDriftAfterMove(destination))
    plan = mutation.preflight_move(source.name, destination.name, expected_sha256=_sha(source))

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan, call_id="mode-drift")

    assert error.value.code == "outcome_unknown"
    assert error.value.change_result is not None
    assert error.value.change_result.status is MutationStatus.OUTCOME_UNKNOWN
    assert error.value.facts[0].status == MutationStatus.OUTCOME_UNKNOWN.value
    assert not source.exists()
    assert destination.exists()


def test_delete_post_effect_fsync_failure_keeps_bounded_unknown_change_fact(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    _, mutation = _services(tmp_path, filesystem=_FsyncAfterEffect())
    plan = mutation.preflight_delete(source.name, expected_sha256=_sha(source))

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan, call_id="fsync-unknown")

    assert error.value.code == "outcome_unknown"
    assert error.value.change_result is not None
    assert error.value.facts[0].status == MutationStatus.OUTCOME_UNKNOWN.value
    assert not source.exists()
    assert "source\n" not in repr(error.value.facts)


@pytest.mark.parametrize("operation", ("delete", "move", "rename"))
def test_capture_failure_persists_staging_evidence_and_stays_unknown(tmp_path, operation):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    _, mutation = _services(tmp_path, filesystem=_FsyncAfterCapture())
    if operation == "delete":
        destination = None
        plan = mutation.preflight_delete(source.name, expected_sha256=_sha(source))
    else:
        destination = tmp_path / f"{operation}-destination.txt"
        plan = (
            mutation.preflight_move(source.name, destination.name, expected_sha256=_sha(source))
            if operation == "move"
            else mutation.preflight_rename(
                source.name, destination.name, expected_sha256=_sha(source)
            )
        )

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan, call_id=f"capture-fsync-{operation}")

    staging = plan.staging_target
    staging_relative = plan.staging_relative_path
    assert staging is not None and staging_relative is not None
    assert staging.exists()
    assert not source.exists()
    assert destination is None or not destination.exists()
    assert error.value.change_result is not None
    assert error.value.change_result.auxiliary_paths == (staging_relative,)
    assert error.value.facts[0].relative_paths[-1] == staging_relative
    evidence = file_evidence_from_plan(plan)
    assert evidence[0].staging_relative_path == staging_relative
    observations = tuple(observe_file(item, root=tmp_path) for item in evidence)
    assert observations[0] is FileObservation.STAGING_PRESENT
    assert classify_file_observations(observations) is RecoveryClassification.OUTCOME_UNKNOWN


class _Approve:
    def __init__(self, approved: bool = True):
        self.approved = approved
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.approved)


@pytest.mark.asyncio
async def test_destructive_tools_use_generic_approval_path_even_in_auto_safe(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("source\n", encoding="utf-8")
    files, mutation = _services(tmp_path)
    del files
    registry = ToolRegistry()
    changes = ChangeSetService()
    registry.register(make_delete_file_tool(mutation, changes))
    registry.register(make_move_file_tool(mutation, changes))
    registry.register(make_rename_file_tool(mutation, changes))
    approval = _Approve()
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=CapabilityPolicy(
            PermissionProfile.from_preset(PermissionPreset.AUTO_SAFE),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    run = ToolRunContext(run_id="run", session_id="session")
    outcome = await executor.execute_with_context(
        FunctionToolCall(
            id="delete",
            name="delete_file",
            arguments=json.dumps({"path": "source.txt", "expected_sha256": _sha(source)}),
        ),
        run_context=run,
        ordinal=1,
        total=1,
    )
    assert outcome.ok is True
    assert len(approval.requests) == 1
    assert approval.requests[0].effect.value == "persistent_write"
    assert not source.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("delete", "move", "rename"))
async def test_destructive_approval_denial_has_no_handler_side_effect(tmp_path, operation):
    source = tmp_path / f"{operation}-source.txt"
    source.write_text("source\n", encoding="utf-8")
    _, mutation = _services(tmp_path)
    changes = ChangeSetService()
    registry = ToolRegistry()
    tool_name = f"{operation}_file"
    tool_factory = {
        "delete": make_delete_file_tool,
        "move": make_move_file_tool,
        "rename": make_rename_file_tool,
    }[operation]
    registry.register(tool_factory(mutation, changes))
    approval = _Approve(approved=False)
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    destination = tmp_path / f"{operation}-destination.txt"
    payload = (
        {"path": source.name, "expected_sha256": _sha(source)}
        if operation == "delete"
        else {
            "source_path": source.name,
            "destination_path": destination.name,
            "expected_sha256": _sha(source),
        }
    )
    outcome = await executor.execute_with_context(
        FunctionToolCall(id=f"deny-{operation}", name=tool_name, arguments=json.dumps(payload)),
        run_context=ToolRunContext(run_id="run", session_id="session"),
        ordinal=1,
        total=1,
    )
    assert outcome.ok is False
    assert outcome.error_code is ToolErrorCode.APPROVAL_REJECTED
    assert source.read_text(encoding="utf-8") == "source\n"
    assert not destination.exists()
    assert len(approval.requests) == 1


class _WaitForApproval:
    def __init__(self):
        self.requested = asyncio.Event()
        self.released = asyncio.Event()

    async def request(self, request):
        del request
        self.requested.set()
        await self.released.wait()
        return ToolApprovalDecision(approved=True)


@pytest.mark.asyncio
async def test_destructive_cancellation_while_awaiting_approval_has_no_side_effect(tmp_path):
    source = tmp_path / "cancel-source.txt"
    source.write_text("source\n", encoding="utf-8")
    _, mutation = _services(tmp_path)
    registry = ToolRegistry()
    registry.register(make_delete_file_tool(mutation, ChangeSetService()))
    approval = _WaitForApproval()
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    task = asyncio.create_task(
        executor.execute_with_context(
            FunctionToolCall(
                id="cancel-delete",
                name="delete_file",
                arguments=json.dumps({"path": source.name, "expected_sha256": _sha(source)}),
            ),
            run_context=ToolRunContext(run_id="run", session_id="session"),
            ordinal=1,
            total=1,
        )
    )
    await approval.requested.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.read_text(encoding="utf-8") == "source\n"


@pytest.mark.parametrize("operation", ("delete", "move", "rename"))
def test_destructive_apply_rechecks_dirty_source_and_destination(tmp_path, operation):
    source = tmp_path / f"{operation}-source.txt"
    source.write_text("source\n", encoding="utf-8")
    _, mutation = _services(tmp_path)
    destination = tmp_path / f"{operation}-destination.txt"
    if operation == "delete":
        plan = mutation.preflight_delete(source.name, expected_sha256=_sha(source))
    elif operation == "move":
        plan = mutation.preflight_move(source.name, destination.name, expected_sha256=_sha(source))
    else:
        plan = mutation.preflight_rename(
            source.name, destination.name, expected_sha256=_sha(source)
        )
    source.write_text("user dirty change\n", encoding="utf-8")
    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan, call_id=f"dirty-{operation}")
    assert error.value.code == "conflict"
    assert source.read_text(encoding="utf-8") == "user dirty change\n"
    assert not destination.exists()

    source.write_text("source\n", encoding="utf-8")
    if operation != "delete":
        destination.write_text("user destination\n", encoding="utf-8")
        with pytest.raises(LocalFileError) as error:
            _publish(
                mutation,
                mutation.preflight_move(source.name, destination.name, expected_sha256=_sha(source))
                if operation == "move"
                else mutation.preflight_rename(
                    source.name, destination.name, expected_sha256=_sha(source)
                ),
                call_id=f"destination-{operation}",
            )
        assert error.value.code == "conflict"
        assert source.read_text(encoding="utf-8") == "source\n"
        assert destination.read_text(encoding="utf-8") == "user destination\n"


def _evidence(
    *,
    relative_path: str,
    operation: str,
    existed_before: bool,
    before_sha256: str | None,
    expected_after_sha256: str | None,
    expected_size: int | None,
    expected_kind: str,
) -> FileMutationEvidence:
    return FileMutationEvidence(
        relative_path=relative_path,
        operation=operation,
        existed_before=existed_before,
        before_sha256=before_sha256,
        expected_after_sha256=expected_after_sha256,
        expected_size=expected_size,
        expected_kind=expected_kind,
        policy_version="files-v1",
        conflict_input_digest=hashlib.sha256(
            (before_sha256 or "absent").encode("utf-8")
        ).hexdigest(),
    )


def test_expected_absence_and_two_path_recovery_never_turn_missing_or_mixed_into_success(
    tmp_path,
):
    before = b"before\n"
    after = b"after\n"
    source = tmp_path / "source.txt"
    source.write_bytes(before)
    delete = _evidence(
        relative_path="source.txt",
        operation="delete",
        existed_before=True,
        before_sha256=hashlib.sha256(before).hexdigest(),
        expected_after_sha256=None,
        expected_size=None,
        expected_kind="absent",
    )
    assert observe_file(delete, root=tmp_path) is FileObservation.MATCHES_BEFORE
    source.unlink()
    assert observe_file(delete, root=tmp_path) is FileObservation.MATCHES_EXPECTED
    source.write_bytes(after)
    assert observe_file(delete, root=tmp_path) is FileObservation.THIRD_PARTY

    source_hash = hashlib.sha256(before).hexdigest()
    source_evidence = _evidence(
        relative_path="source.txt",
        operation="rename",
        existed_before=True,
        before_sha256=source_hash,
        expected_after_sha256=None,
        expected_size=None,
        expected_kind="absent",
    )
    destination_evidence = _evidence(
        relative_path="renamed.txt",
        operation="rename",
        existed_before=False,
        before_sha256=None,
        expected_after_sha256=source_hash,
        expected_size=len(before),
        expected_kind="file",
    )
    assert (
        classify_file_observations((FileObservation.MATCHES_BEFORE, FileObservation.MATCHES_BEFORE))
        is RecoveryClassification.SAFE_TO_RETRY
    )
    source.unlink()
    (tmp_path / "renamed.txt").write_bytes(before)
    assert observe_file(source_evidence, root=tmp_path) is FileObservation.MATCHES_EXPECTED
    assert observe_file(destination_evidence, root=tmp_path) is FileObservation.MATCHES_EXPECTED
    assert (
        classify_file_observations(
            (
                observe_file(source_evidence, root=tmp_path),
                observe_file(destination_evidence, root=tmp_path),
            )
        )
        is RecoveryClassification.COMPLETED
    )
    (tmp_path / "renamed.txt").write_bytes(after)
    assert (
        classify_file_observations(
            (
                FileObservation.MATCHES_EXPECTED,
                observe_file(destination_evidence, root=tmp_path),
            )
        )
        is RecoveryClassification.OUTCOME_UNKNOWN
    )


def test_recovery_file_evidence_rejects_parent_traversal_even_if_constructed_unsafely(tmp_path):
    outside = tmp_path.parent / "s7p04-recovery-traversal.txt"
    outside.write_bytes(b"outside\n")
    try:
        digest = _sha(outside)
        with pytest.raises(ValidationError):
            FileMutationEvidence(
                relative_path="../s7p04-recovery-traversal.txt",
                operation="delete",
                existed_before=True,
                before_sha256=digest,
                expected_kind="file",
                policy_version="files-v1",
                conflict_input_digest=digest,
            )
        forged = FileMutationEvidence.model_construct(
            relative_path="../s7p04-recovery-traversal.txt",
            operation="delete",
            existed_before=True,
            before_sha256=digest,
            expected_kind="file",
            policy_version="files-v1",
            conflict_input_digest=digest,
        )
        assert observe_file(forged, root=tmp_path) is FileObservation.EVIDENCE_MISSING
    finally:
        outside.unlink(missing_ok=True)


def test_recovery_expected_absence_requires_existing_confined_parent(tmp_path):
    source = tmp_path / "nested" / "source.txt"
    source.parent.mkdir()
    source.write_bytes(b"before\n")
    evidence = _evidence(
        relative_path="nested/source.txt",
        operation="delete",
        existed_before=True,
        before_sha256=_sha(source),
        expected_after_sha256=None,
        expected_size=None,
        expected_kind="absent",
    )
    source.unlink()
    source.parent.rmdir()
    assert observe_file(evidence, root=tmp_path) is FileObservation.EVIDENCE_MISSING

    outside = tmp_path.parent / "s7p04-recovery-outside"
    outside.mkdir()
    try:
        (outside / "source.txt").write_bytes(b"before\n")
        (tmp_path / "nested").symlink_to(outside, target_is_directory=True)
        assert observe_file(evidence, root=tmp_path) is FileObservation.THIRD_PARTY
    finally:
        (tmp_path / "nested").unlink(missing_ok=True)
        (outside / "source.txt").unlink(missing_ok=True)
        outside.rmdir()


def test_closed_unknown_file_execution_is_reconciled_from_ordered_observations():
    declaration = tool_declaration("rename_file", production_only=True)
    expected = (FileObservation.MATCHES_EXPECTED, FileObservation.MATCHES_EXPECTED)
    before = (FileObservation.MATCHES_BEFORE, FileObservation.MATCHES_BEFORE)
    mixed = (FileObservation.MATCHES_EXPECTED, FileObservation.THIRD_PARTY)
    assert (
        classify_execution(
            state=ToolExecutionState.CLOSED,
            disposition=ToolExecutionDisposition.UNKNOWN,
            declaration=declaration,
            observations=expected,
        )
        is RecoveryClassification.COMPLETED
    )
    assert (
        classify_execution(
            state=ToolExecutionState.CLOSED,
            disposition=ToolExecutionDisposition.UNKNOWN,
            declaration=declaration,
            observations=before,
        )
        is RecoveryClassification.SAFE_TO_RETRY
    )
    assert (
        classify_execution(
            state=ToolExecutionState.CLOSED,
            disposition=ToolExecutionDisposition.UNKNOWN,
            declaration=declaration,
            observations=mixed,
        )
        is RecoveryClassification.OUTCOME_UNKNOWN
    )


@pytest.mark.asyncio
async def test_sandbox_promotion_pair_and_delete_apply_and_preserve_changes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "delete.txt").write_text("remove\n", encoding="utf-8")
    (workspace / "old.txt").write_text("rename\n", encoding="utf-8")
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    snapshots = SandboxSnapshotService(files, temp_parent=tmp_path)
    session = snapshots.prepare(workspace, run_id="run", call_id="sandbox")
    try:
        (session.snapshot_root / "delete.txt").unlink()
        (session.snapshot_root / "old.txt").rename(session.snapshot_root / "new.txt")
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
    approval = _Approve()
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
        arguments=json.dumps(
            {
                "change_set_id": change_set.change_set_id,
                "paths": ["new.txt", "delete.txt"],
            }
        ),
    )
    result = await executor.execute_with_context(call, run_context=run, ordinal=1, total=1)
    assert result.ok is True
    assert not (workspace / "delete.txt").exists()
    assert not (workspace / "old.txt").exists()
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "rename\n"
    assert len(approval.requests) == 1
    applied = tuple(entry for entry in run.change_sets if hasattr(entry, "status"))
    assert len(applied) == 2
    assert {entry.status for entry in applied} == {
        MutationStatus.DELETED,
        MutationStatus.RENAMED,
    }


def test_sandbox_duplicate_content_is_not_guessed_as_a_move(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("same\n", encoding="utf-8")
    (workspace / "b.txt").write_text("same\n", encoding="utf-8")
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    snapshots = SandboxSnapshotService(files, temp_parent=tmp_path)
    session = snapshots.prepare(workspace, run_id="run", call_id="sandbox")
    try:
        (session.snapshot_root / "a.txt").unlink()
        (session.snapshot_root / "b.txt").unlink()
        (session.snapshot_root / "new.txt").write_text("same\n", encoding="utf-8")
        change_set = snapshots.collect(session)
    finally:
        snapshots.cleanup(session)
    assert {change.operation for change in change_set.changes} == {"created", "deleted"}
    assert all(change.destination_path is None for change in change_set.changes)


class _FailSecondPromotion(WorkspaceMutationService):
    def __init__(self, files):
        super().__init__(files)
        self.calls = 0

    def apply(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 2:
            result, fact = super().apply(*args, **kwargs)
            unknown_result = result.model_copy(update={"status": MutationStatus.OUTCOME_UNKNOWN})
            raise LocalFileError(
                "outcome_unknown",
                "second publication became uncertain after effect",
                facts=(fact.model_copy(update={"status": MutationStatus.OUTCOME_UNKNOWN.value}),),
                change_result=unknown_result,
            )
        return super().apply(*args, **kwargs)


@pytest.mark.asyncio
async def test_promotion_preflights_all_then_returns_bounded_partial_failure(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files = WorkspaceFileService(WorkspacePathResolver(workspace))
    snapshots = SandboxSnapshotService(files, temp_parent=tmp_path)
    session = snapshots.prepare(workspace, run_id="run", call_id="sandbox")
    try:
        (session.snapshot_root / "one.txt").write_text("one\n", encoding="utf-8")
        (session.snapshot_root / "two.txt").write_text("two\n", encoding="utf-8")
        change_set = snapshots.collect(session)
    finally:
        snapshots.cleanup(session)
    run = ToolRunContext(run_id="run", session_id="session")
    snapshots.retain(run, change_set)
    changes = ChangeSetService()
    mutation = _FailSecondPromotion(files)
    registry = ToolRegistry()
    registry.register(make_promote_sandbox_tool(snapshots, mutation, changes))
    approval = _Approve()
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
        id="partial",
        name="promote_sandbox_changes",
        arguments=json.dumps(
            {"change_set_id": change_set.change_set_id, "paths": ["one.txt", "two.txt"]}
        ),
    )
    result = await executor.execute_with_context(call, run_context=run, ordinal=1, total=1)
    assert result.ok is False
    assert result.error_code is ToolErrorCode.PUBLISH_FAILED
    assert (workspace / "one.txt").read_text(encoding="utf-8") == "one\n"
    assert (workspace / "two.txt").read_text(encoding="utf-8") == "two\n"
    assert len(tuple(entry for entry in run.change_sets if hasattr(entry, "status"))) == 2
    assert len(run.facts) == 2
    assert run.facts[-1].status == MutationStatus.OUTCOME_UNKNOWN.value
    assert result.disposition is ToolExecutionDisposition.UNKNOWN
    assert '"key":"applied"' in result.envelope


@pytest.mark.asyncio
async def test_scripted_direct_production_bash_keeps_destructive_policy_boundary(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    deleted = project / "delete.txt"
    renamed = project / "old.txt"
    deleted.write_text("delete\n", encoding="utf-8")
    renamed.write_text("rename\n", encoding="utf-8")
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id="move",
                        name="bash",
                        arguments=json.dumps({"command": "rm delete.txt && mv old.txt new.txt"}),
                    ),
                )
            ),
            AssistantMessage(content="done"),
        ]
    )
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=_Approve(),
    )
    tool_names = {
        tool.function.name
        for tool in session_app.orchestrator.runtime.loop.tool_executor.definitions
    }
    assert "bash" in tool_names
    assert {"delete_file", "rename_file", "show_changes"}.isdisjoint(tool_names)
    [item async for item in session_app.orchestrator.stream("perform the scripted changes")]

    assert deleted.read_text(encoding="utf-8") == "delete\n"
    assert renamed.read_text(encoding="utf-8") == "rename\n"
    assert not (project / "new.txt").exists()
    tool_payloads = [
        json.loads(message.content)
        for message in session_app.session.messages
        if message.role == "tool"
    ]
    assert tool_payloads[0]["ok"] is False
    assert tool_payloads[0]["error"]["code"] == "permission_denied"
