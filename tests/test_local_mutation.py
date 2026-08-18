from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.local.filesystem import FileSystemAdapter
from morrow.application.local_tools import (
    ApplyPatchArguments,
    ShowChangesArguments,
    WriteFileArguments,
    _blocking_mutation,
    make_apply_patch_tool,
)
from morrow.bootstrap import build_application, build_session_application
from morrow.core.capabilities import (
    ChangeToolFact,
    PermissionPreset,
    PermissionProfile,
    PolicyVerdict,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.local_tools import ExactEdit, MutationMode
from morrow.core.models import (
    AssistantMessage,
    FunctionToolCall,
    ModelRef,
    ToolApprovalDecision,
)
from morrow.runtime.capabilities import CapabilityPolicy
from morrow.runtime.tools import ToolExecutor, ToolRegistry
from morrow.services.changes import ChangeSetService
from morrow.services.files import (
    LocalFileError,
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)
from morrow.testing import ScriptedModelProvider, make_run_policy


def _services(tmp_path: Path, *, filesystem: FileSystemAdapter | None = None):
    files = WorkspaceFileService(
        resolver=WorkspacePathResolver(tmp_path),
        filesystem=filesystem,
    )
    return files, WorkspaceMutationService(files)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(run_id: str = "run-1") -> ToolRunContext:
    return ToolRunContext(run_id=run_id, session_id="session-1")


def _publish(mutation, plan, run: ToolRunContext | None = None, *, call_id: str = "call-1"):
    run = run or _run()
    result, fact = mutation.apply(
        plan,
        call_id=call_id,
        tool_name=plan.operation.value,
        ordinal=1,
        approval_verdict=PolicyVerdict.ALLOW,
        run=run,
    )
    run.record((fact,))
    return result, fact, run


def test_exact_patch_is_unique_non_fuzzy_and_non_overlapping(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nalpha\n", encoding="utf-8")
    _, mutation = _services(tmp_path)

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_patch(
            "sample.txt",
            expected_sha256=_sha(path),
            edits=(ExactEdit(old_text="alpha", new_text="beta"),),
        )
    assert error.value.code == "edit_not_unique"

    path.write_text("abcdef\n", encoding="utf-8")
    with pytest.raises(LocalFileError) as error:
        mutation.preflight_patch(
            "sample.txt",
            expected_sha256=_sha(path),
            edits=(
                ExactEdit(old_text="abc", new_text="A"),
                ExactEdit(old_text="cde", new_text="D"),
            ),
        )
    assert error.value.code == "edit_overlap"

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_patch(
            "sample.txt",
            expected_sha256=_sha(path),
            edits=(ExactEdit(old_text="not present", new_text="x"),),
        )
    assert error.value.code == "edit_not_found"


def test_stale_revision_conflicts_without_overwriting_external_change(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("one\ntwo\n", encoding="utf-8")
    _, mutation = _services(tmp_path)
    plan = mutation.preflight_patch(
        "sample.txt",
        expected_sha256=_sha(path),
        edits=(ExactEdit(old_text="two", new_text="TWO"),),
    )
    path.write_text("one\nexternal\n", encoding="utf-8")

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan)

    assert error.value.code == "conflict"
    assert path.read_text(encoding="utf-8") == "one\nexternal\n"


def test_patch_preserves_bom_newline_and_mode_and_returns_actual_diff(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    path.chmod(0o640)
    before_mode = stat.S_IMODE(path.stat().st_mode)
    _, mutation = _services(tmp_path)
    plan = mutation.preflight_patch(
        "sample.txt",
        expected_sha256=_sha(path),
        edits=(ExactEdit(old_text="two", new_text="TWO"),),
    )

    result, fact, _ = _publish(mutation, plan)

    assert path.read_bytes() == b"\xef\xbb\xbfone\r\nTWO\r\n"
    assert stat.S_IMODE(path.stat().st_mode) == before_mode
    assert result.status.value == "modified"
    assert "--- a/sample.txt" in result.diff
    assert "-two" in result.diff
    assert "+TWO" in result.diff
    assert fact.before_revision == plan.before.revision.sha256
    assert fact.after_revision == result.after_revision.sha256
    assert fact.edit_count == 1


def test_create_is_the_only_operation_allowed_to_make_four_parent_levels(tmp_path):
    _, mutation = _services(tmp_path)
    plan = mutation.preflight_write(
        "a/b/c/d/new.txt",
        content="created\n",
        mode="create",
    )
    assert plan.auxiliary_paths == ("a", "a/b", "a/b/c", "a/b/c/d")
    result, _, _ = _publish(mutation, plan)
    assert result.status.value == "created"
    assert (tmp_path / "a/b/c/d/new.txt").read_text(encoding="utf-8") == "created\n"

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_write(
            "e/f/g/h/i/too-deep.txt",
            content="x",
            mode="create",
        )
    assert error.value.code == "mutation_limit"

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_patch(
            "missing/file.txt",
            expected_sha256="0" * 64,
            edits=(ExactEdit(old_text="x", new_text="y"),),
        )
    assert error.value.code == "not_found"


def test_mutation_rejects_symlink_components_and_protected_content(tmp_path):
    outside = tmp_path.parent / "mutation-outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("untouched\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    _, mutation = _services(tmp_path)

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_write("link/new.txt", content="x", mode="create")
    assert error.value.code == "symlink_not_allowed"

    with pytest.raises(LocalFileError) as error:
        mutation.preflight_write(
            "ordinary.txt",
            content="-----BEGIN PRIVATE KEY-----\nsecret\n",
            mode="create",
        )
    assert error.value.code == "protected_resource"
    assert "secret" not in error.value.message

    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    with pytest.raises(LocalFileError) as error:
        mutation.preflight_write(
            ".git/hooks/pre-commit", content="#!/bin/sh\nexit 0\n", mode="create"
        )
    assert error.value.code == "protected_resource"
    assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "untouched\n"


def test_mixed_newline_files_are_rejected_without_rewriting_bytes(tmp_path):
    path = tmp_path / "mixed.txt"
    original = b"one\r\ntwo\nthree\r"
    path.write_bytes(original)
    _, mutation = _services(tmp_path)

    with pytest.raises(LocalFileError) as patch_error:
        mutation.preflight_patch(
            "mixed.txt",
            expected_sha256=_sha(path),
            edits=(ExactEdit(old_text="two", new_text="TWO"),),
        )
    assert patch_error.value.code == "unsupported_newline"

    with pytest.raises(LocalFileError) as replace_error:
        mutation.preflight_write(
            "mixed.txt",
            content="replacement\n",
            mode="replace",
            expected_sha256=_sha(path),
        )
    assert replace_error.value.code == "unsupported_newline"
    assert path.read_bytes() == original


def test_unchanged_patch_is_explicit_and_does_not_publish(tmp_path):
    path = tmp_path / "same.txt"
    path.write_text("same\n", encoding="utf-8")
    before_mtime = path.stat().st_mtime_ns
    _, mutation = _services(tmp_path)
    plan = mutation.preflight_patch(
        "same.txt",
        expected_sha256=_sha(path),
        edits=(ExactEdit(old_text="same", new_text="same"),),
    )

    result, fact, _ = _publish(mutation, plan)

    assert result.status.value == "unchanged"
    assert result.changed_lines == 0
    assert result.changed_bytes == 0
    assert result.diff == ""
    assert fact.after_revision == fact.before_revision
    assert path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("failure", ["replace", "fsync"])
def test_atomic_failure_preserves_original_and_exact_temp_cleanup(tmp_path, monkeypatch, failure):
    path = tmp_path / "sample.txt"
    path.write_text("before\n", encoding="utf-8")
    _, mutation = _services(tmp_path)
    plan = mutation.preflight_patch(
        "sample.txt",
        expected_sha256=_sha(path),
        edits=(ExactEdit(old_text="before", new_text="after"),),
    )
    if failure == "replace":
        monkeypatch.setattr(
            "morrow.adapters.local.filesystem.os.replace",
            lambda *a, **k: (_ for _ in ()).throw(OSError("injected")),
        )
    else:
        monkeypatch.setattr(
            "morrow.adapters.local.filesystem.os.fsync",
            lambda *a, **k: (_ for _ in ()).throw(OSError("injected")),
        )

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan)

    assert error.value.code == "publish_failed"
    assert path.read_text(encoding="utf-8") == "before\n"
    assert tuple(tmp_path.glob(".morrow-tmp-*")) == ()


class _SwapParentAdapter(FileSystemAdapter):
    def atomic_write(self, path, data, *, mode=None, workspace_root=None):
        parent = path.parent
        outside = workspace_root.parent / "mutation-race-outside"
        moved = parent.with_name(parent.name + "-original")
        parent.rename(moved)
        parent.symlink_to(outside, target_is_directory=True)
        try:
            return super().atomic_write(
                path,
                data,
                mode=mode,
                workspace_root=workspace_root,
            )
        finally:
            parent.unlink()
            moved.rename(parent)


def test_parent_swap_is_rejected_before_publication_and_outside_stays_untouched(tmp_path):
    outside = tmp_path.parent / "mutation-race-outside"
    outside.mkdir()
    (outside / "sample.txt").write_text("outside\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    path = nested / "sample.txt"
    path.write_text("before\n", encoding="utf-8")
    files, mutation = _services(tmp_path, filesystem=_SwapParentAdapter())
    plan = mutation.preflight_patch(
        "nested/sample.txt",
        expected_sha256=_sha(path),
        edits=(ExactEdit(old_text="before", new_text="after"),),
    )

    with pytest.raises(LocalFileError) as error:
        _publish(mutation, plan)

    assert error.value.code == "publish_failed"
    assert path.read_text(encoding="utf-8") == "before\n"
    assert (outside / "sample.txt").read_text(encoding="utf-8") == "outside\n"


def test_same_target_publication_serializes_and_second_stale_plan_conflicts(tmp_path):
    path = tmp_path / "same.txt"
    path.write_text("before\n", encoding="utf-8")
    _, mutation = _services(tmp_path)
    expected = _sha(path)
    plans = [
        mutation.preflight_patch(
            "same.txt",
            expected_sha256=expected,
            edits=(ExactEdit(old_text="before", new_text=value),),
        )
        for value in ("first", "second")
    ]

    def publish(plan, call_id):
        try:
            return _publish(mutation, plan, _run(call_id), call_id=call_id)[0].status.value
        except LocalFileError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = tuple(executor.map(publish, plans, ("one", "two")))

    assert sorted(statuses) == ["conflict", "modified"]
    assert path.read_text(encoding="utf-8") in {"first\n", "second\n"}


def test_mutation_thresholds_cover_per_call_and_cumulative_limits(tmp_path):
    _, mutation = _services(tmp_path)

    numbered_lines = "".join(f"keep-{index:03d}\n" for index in range(128))
    (tmp_path / "lines.txt").write_text(numbered_lines, encoding="utf-8")
    exact_lines = "".join(f"keep-{index:03d}\n" for index in range(32))
    plan = mutation.preflight_patch(
        "lines.txt",
        expected_sha256=_sha(tmp_path / "lines.txt"),
        edits=(ExactEdit(old_text=exact_lines, new_text="".join("change\n" for _ in range(32))),),
    )
    assert plan.changed_lines == 64
    assert plan.threshold_exceeded is False

    (tmp_path / "lines-over.txt").write_text(numbered_lines, encoding="utf-8")
    over_lines = "".join(f"keep-{index:03d}\n" for index in range(33))
    plan = mutation.preflight_patch(
        "lines-over.txt",
        expected_sha256=_sha(tmp_path / "lines-over.txt"),
        edits=(ExactEdit(old_text=over_lines, new_text="".join("change\n" for _ in range(33))),),
    )
    assert plan.changed_lines == 66
    assert plan.threshold_exceeded is True

    byte_path = tmp_path / "bytes.txt"
    byte_path.write_text("a" * 2048 + "\nkeep\nkeep\nkeep\nkeep\n", encoding="utf-8")
    plan = mutation.preflight_patch(
        "bytes.txt",
        expected_sha256=_sha(byte_path),
        edits=(ExactEdit(old_text="a" * 2048 + "\n", new_text="b" * 2048 + "\n"),),
    )
    assert plan.changed_bytes == 4096
    assert plan.threshold_exceeded is False
    plan = mutation.preflight_patch(
        "bytes.txt",
        expected_sha256=_sha(byte_path),
        edits=(ExactEdit(old_text="a" * 2048 + "\n", new_text="b" * 2049 + "\n"),),
    )
    assert plan.changed_bytes == 4097
    assert plan.threshold_exceeded is True

    content = "x\n" * 64
    plan = mutation.preflight_write("exact-create.txt", content=content, mode="create")
    assert plan.threshold_exceeded is False
    plan = mutation.preflight_write("over-create.txt", content=content + "x\n", mode="create")
    assert plan.threshold_exceeded is True

    replace_path = tmp_path / "replace.txt"
    replace_path.write_text("old\n", encoding="utf-8")
    replace_plan = mutation.preflight_write(
        "replace.txt",
        content="new\n",
        mode="replace",
        expected_sha256=_sha(replace_path),
    )
    assert replace_plan.threshold_exceeded is True

    path = tmp_path / "edit-count.txt"
    path.write_text("\n".join(f"line-{index}" for index in range(40)) + "\n", encoding="utf-8")
    edits = tuple(
        ExactEdit(old_text=f"line-{index}\n", new_text=f"changed-{index}\n") for index in range(8)
    )
    plan = mutation.preflight_patch("edit-count.txt", expected_sha256=_sha(path), edits=edits)
    assert plan.threshold_exceeded is False
    over_edits = edits + (ExactEdit(old_text="line-8\n", new_text="changed-8\n"),)
    plan = mutation.preflight_patch("edit-count.txt", expected_sha256=_sha(path), edits=over_edits)
    assert plan.threshold_exceeded is True

    run = _run("cumulative")
    run.record(
        (
            ChangeToolFact(
                call_id="prior",
                tool_name="apply_patch",
                ordinal=1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=("one.txt",),
                operation="patch",
                edit_count=15,
                changed_lines=127,
                changed_bytes=8191,
            ),
        )
    )
    path.write_text("old\n", encoding="utf-8")
    create_plan = mutation.preflight_write("new.txt", content="new\n", mode="create", run=run)
    assert create_plan.threshold_exceeded is True

    path_run = _run("path-cumulative")
    path_run.record(
        tuple(
            ChangeToolFact(
                call_id=f"prior-{index}",
                tool_name="apply_patch",
                ordinal=index + 1,
                approval_verdict=PolicyVerdict.ALLOW,
                relative_paths=(f"file-{index}.txt",),
                operation="patch",
                changed_lines=1,
                changed_bytes=2,
            )
            for index in range(4)
        )
    )
    assert mutation.preflight_write(
        "fifth.txt", content="x\n", mode="create", run=path_run
    ).threshold_exceeded


@pytest.mark.asyncio
async def test_cancellation_waits_for_background_mutation_to_settle():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def callback():
        started.set()
        release.wait(timeout=2)
        finished.set()
        return "settled"

    task = asyncio.create_task(_blocking_mutation(callback))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


class _Approval:
    def __init__(self, approved: bool = True):
        self.approved = approved
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=self.approved)


class _NoApproval(_Approval):
    async def request(self, request):
        raise AssertionError(f"unexpected approval: {request}")


def _tool_args(name: str, payload: dict, call_id: str) -> FunctionToolCall:
    return FunctionToolCall(
        id=call_id, name=name, arguments=json.dumps(payload, ensure_ascii=False)
    )


@pytest.mark.asyncio
async def test_manual_provider_path_approves_actual_diff_and_show_changes_uses_facts(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    source = project / "sample.txt"
    source.write_text("needle old\n", encoding="utf-8")
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _tool_args("search_text", {"path": ".", "pattern": "needle"}, "search"),
                )
            ),
            AssistantMessage(tool_calls=(_tool_args("read_file", {"path": "sample.txt"}, "read"),)),
            AssistantMessage(
                tool_calls=(
                    _tool_args(
                        "apply_patch",
                        {
                            "path": "sample.txt",
                            "expected_sha256": _sha(source),
                            "edits": [{"old_text": "needle", "new_text": "fixed"}],
                        },
                        "patch",
                    ),
                )
            ),
            AssistantMessage(tool_calls=(_tool_args("show_changes", {}, "changes"),)),
            AssistantMessage(content="已完成实际修改，并根据 ChangeSet 汇报。"),
        ]
    )
    approval = _Approval()
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    [item async for item in session_app.orchestrator.stream("定位并修复 needle")]

    assert source.read_text(encoding="utf-8") == "fixed old\n"
    assert len(approval.requests) == 1
    preview = "\n".join(approval.requests[0].preview)
    assert "--- a/sample.txt" in preview
    assert "-needle old" in preview
    assert "+fixed old" in preview
    messages = [message for message in session_app.session.messages if message.role == "tool"]
    patch_result = json.loads(messages[2].content)
    changes_result = json.loads(messages[3].content)
    assert patch_result["result"]["diff"] == changes_result["result"]["entries"][0]["diff"]
    assert session_app.session.latest_tool_facts[0].operation == "patch"


@pytest.mark.asyncio
async def test_auto_safe_small_patch_is_automatic_and_replace_still_requires_approval(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    source = project / "sample.txt"
    source.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_tool_args("read_file", {"path": "sample.txt"}, "read"),)),
            AssistantMessage(
                tool_calls=(
                    _tool_args(
                        "apply_patch",
                        {
                            "path": "sample.txt",
                            "expected_sha256": _sha(source),
                            "edits": [{"old_text": "two", "new_text": "TWO"}],
                        },
                        "patch",
                    ),
                )
            ),
            AssistantMessage(content="小范围修改已完成。"),
        ]
    )
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=_NoApproval(),
        permission_profile=PermissionProfile.from_preset(PermissionPreset.AUTO_SAFE),
    )

    [item async for item in session_app.orchestrator.stream("修改一行")]

    assert source.read_text(encoding="utf-8") == "one\nTWO\nthree\nfour\nfive\n"


@pytest.mark.asyncio
async def test_approval_preview_has_explicit_marker_for_large_diff(tmp_path):
    path = tmp_path / "large.txt"
    before = "".join(f"line-{index:03d}\n" for index in range(600))
    path.write_text(before, encoding="utf-8")
    _, mutation = _services(tmp_path)
    changes = ChangeSetService()
    registry = ToolRegistry()
    registry.register(make_apply_patch_tool(mutation, changes))
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=_Approval(),
        capability_policy=CapabilityPolicy(
            PermissionProfile(),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    call = _tool_args(
        "apply_patch",
        {
            "path": "large.txt",
            "expected_sha256": _sha(path),
            "edits": [{"old_text": before, "new_text": before.replace("line-", "changed-")}],
        },
        "large-patch",
    )
    outcome = await executor.execute_with_context(
        call,
        run_context=_run("large-run"),
        ordinal=1,
        total=1,
    )

    assert outcome.ok is True
    request = executor.approval_port.requests[0]
    assert "... diff truncated ..." in request.preview
    assert len(request.preview) <= 40
    assert sum(len(line.encode("utf-8")) for line in request.preview) <= 4 * 1024


@pytest.mark.asyncio
async def test_auto_safe_over_threshold_edit_count_requires_approval(tmp_path):
    path = tmp_path / "many-edits.txt"
    path.write_text("".join(f"line-{index}\n" for index in range(40)), encoding="utf-8")
    _, mutation = _services(tmp_path)
    registry = ToolRegistry()
    registry.register(make_apply_patch_tool(mutation, ChangeSetService()))
    approval = _Approval()
    executor = ToolExecutor(
        registry.snapshot(),
        make_run_policy(),
        approval_port=approval,
        capability_policy=CapabilityPolicy(
            PermissionProfile.from_preset(PermissionPreset.AUTO_SAFE),
            WorkspaceCapability(workspace_id="w1", root=tmp_path),
        ),
    )
    edits = [
        {"old_text": f"line-{index}\n", "new_text": f"changed-{index}\n"} for index in range(9)
    ]
    outcome = await executor.execute_with_context(
        _tool_args(
            "apply_patch",
            {"path": "many-edits.txt", "expected_sha256": _sha(path), "edits": edits},
            "many-edits",
        ),
        run_context=_run("many-edits-run"),
        ordinal=1,
        total=1,
    )

    assert outcome.ok is True
    assert len(approval.requests) == 1
    assert approval.requests[0].reason_codes == ("mutation_approval_required",)
    assert path.read_text(encoding="utf-8").startswith("changed-0\nchanged-1\n")


def test_mutation_arguments_are_strict_and_mode_bound():
    with pytest.raises(ValidationError):
        ApplyPatchArguments.model_validate(
            {
                "path": "x.txt",
                "expected_sha256": "0" * 64,
                "edits": [{"old_text": "x", "new_text": "y"}],
                "extra": True,
            },
            strict=True,
        )
    with pytest.raises(ValidationError):
        WriteFileArguments.model_validate(
            {"path": "x.txt", "content": "x", "mode": MutationMode.REPLACE}, strict=True
        )
    assert ShowChangesArguments.model_validate({}, strict=True).model_dump() == {}
