"""Capture before publication on the SQLite owner, then reconcile actual effects.

The filesystem worker never touches SQLite. This collaborator is shared by chat,
Workflow and sandbox promotion; Workflow-only output contracts remain separate.
"""

from __future__ import annotations

import difflib
import hashlib
import stat

from morrow.core.artifacts import TASK_ARTIFACT_MAX_BYTES, ArtifactError
from morrow.core.domain import refuse_secret_material
from morrow.core.workspace_changes import WORKSPACE_SNAPSHOT_BYTES, FileManifest, WorkspaceChange
from morrow.runtime.durable_log import durable_call_id
from morrow.services.files import LocalFileError


def manifest(raw: bytes | None, mode: int | None = None, snapshot_id=None, file_identity=None):
    if raw is None:
        return FileManifest()
    return FileManifest(
        exists=True,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
        mode=mode,
        snapshot_id=snapshot_id,
        file_identity=file_identity,
    )


def equivalent(a: FileManifest, b: FileManifest) -> bool:
    return (a.exists, a.sha256, a.byte_size, a.mode) == (b.exists, b.sha256, b.byte_size, b.mode)


class WorkspaceChangeRecorder:
    def __init__(self, journal, artifacts, mutation, *, workspace_id):
        self.journal = journal
        self.artifacts = artifacts
        self.mutation = mutation
        self.workspace_id = workspace_id
        self.records = journal.workspace_changes
        self.event_sink = None

    def prepare(self, plan, context, *, mutation_ordinal=1):
        if self.records.pending(self.workspace_id):
            raise LocalFileError("workspace_busy", "存在未核对的文件恢复，请先处理恢复状态")
        if plan.status.value == "unchanged":
            return None
        execution = next(
            (
                e
                for e in self.journal.list_executions(
                    self.workspace_id, agent_run_id=context.run.run_id
                )
                if e.call_id == durable_call_id(context.call_id)
            ),
            None,
        )
        if execution is None:
            raise LocalFileError("capture_failed", "修改来源无法绑定，未写入文件")
        key = f"{execution.tool_execution_id}:{mutation_ordinal}"
        previous = self.records.find(self.workspace_id, key)
        if previous is not None:
            raise LocalFileError("conflict", "这次修改已有记录，请先核对执行结果")
        before = plan.before.raw if plan.before is not None else None
        mode = stat.S_IMODE(plan.before.mode) if plan.before is not None else 0o644
        after = plan.desired_raw if plan.operation.value in {"create", "patch", "replace"} else None
        reason = None
        if plan.operation.value not in {"create", "patch", "replace"}:
            reason = "structural_operation"
        else:
            try:
                if before is not None and plan.target.stat(follow_symlinks=False).st_nlink != 1:
                    reason = "unsupported_link"
                for raw in (before, after):
                    if raw is not None:
                        if b"\x00" in raw:
                            reason = "unsupported_binary"
                        refuse_secret_material(
                            raw, label="file snapshot", profile="workflow_value_sensitive"
                        )
            except ValueError:
                reason = "sensitive_content"
        bm, am = manifest(before, mode), manifest(after, mode)
        if before is not None:
            source_stat = plan.target.stat(follow_symlinks=False)
            bm = bm.model_copy(update={"file_identity": (source_stat.st_dev, source_stat.st_ino)})
        if reason is None:
            required = sum(len(raw) for raw in (before, after) if raw is not None)
            task_used = self.journal.artifact_bytes_for_task(
                self.workspace_id, execution.task_run_id
            )
            workspace_used = sum(
                a.byte_size
                for a in self.journal.list_artifacts(self.workspace_id)
                if a.kind.value == "workspace_snapshot"
            )
            if (
                task_used + required > TASK_ARTIFACT_MAX_BYTES
                or workspace_used + required > WORKSPACE_SNAPSHOT_BYTES
            ):
                raise LocalFileError("snapshot_budget", "恢复快照额度不足，未写入文件")
            try:
                if before is not None:
                    a = self.artifacts.publish_workspace_snapshot(
                        before, session_id=execution.session_id, task_run_id=execution.task_run_id
                    )
                    bm = bm.model_copy(update={"snapshot_id": a.artifact_id})
                if after is not None:
                    a = self.artifacts.publish_workspace_snapshot(
                        after, session_id=execution.session_id, task_run_id=execution.task_run_id
                    )
                    am = am.model_copy(update={"snapshot_id": a.artifact_id})
            except ArtifactError as exc:
                raise LocalFileError("capture_failed", "恢复快照未能持久化，未写入文件") from exc
        from morrow.adapters.local.restore import identity

        added = removed = 0
        if reason is None:
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                None,
                (before or b"").decode("utf-8-sig").splitlines(),
                (after or b"").decode("utf-8-sig").splitlines(),
                autojunk=False,
            ).get_opcodes():
                if tag != "equal":
                    added += j2 - j1
                    removed += i2 - i1

        try:
            return self.records.insert(
                WorkspaceChange(
                    change_id=self.artifacts.id_source.new_id("chg"),
                    workspace_id=self.workspace_id,
                    root_identity=identity(self.mutation.files.resolver.root),
                    added_lines=added,
                    removed_lines=removed,
                    session_id=execution.session_id,
                    task_run_id=execution.task_run_id,
                    agent_run_id=execution.agent_run_id,
                    tool_execution_id=execution.tool_execution_id,
                    mutation_key=key,
                    path=plan.relative_path,
                    destination_path=plan.destination_relative_path,
                    operation=plan.operation.value,
                    before=bm,
                    after=am,
                    coverage="metadata_only" if reason else "complete",
                    omission_reason=reason,
                    created_at=self.journal.now(),
                )
            )
        except Exception as exc:
            raise LocalFileError("capture_failed", "修改意图未能持久化，未写入文件") from exc

    def current(self, path):
        return self.current_bytes(path)[0]

    def current_bytes(self, path):
        from morrow.adapters.local.filesystem import FileSystemAdapter, FileSystemMutationError
        from morrow.services.files import MAX_SOURCE_FILE_BYTES

        target = self.mutation.files.resolver.resolve_mutation(path).target
        try:
            state = target.stat(follow_symlinks=False)
        except FileNotFoundError:
            return FileManifest(), None
        if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
            raise LocalFileError("conflict", "当前文件类型或链接身份不支持恢复")
        try:
            source = FileSystemAdapter().read_confined_file(
                target,
                workspace_root=self.mutation.files.resolver.root,
                max_bytes=MAX_SOURCE_FILE_BYTES,
            )
        except FileSystemMutationError as exc:
            raise LocalFileError("conflict", "当前文件无法安全读取") from exc
        checked = target.stat(follow_symlinks=False)
        if checked.st_nlink != 1 or not FileSystemAdapter._same_file_identity(state, checked):
            raise LocalFileError("conflict", "读取时文件身份已变化")
        return manifest(
            source.raw, source.mode, file_identity=(state.st_dev, state.st_ino)
        ), source.raw

    def finish(self, record, *, succeeded):
        if record is None:
            return
        if record.coverage != "complete":
            state = "applied" if succeeded else "outcome_unknown"
        else:
            try:
                actual = self.current(record.path)
                state = (
                    "applied"
                    if equivalent(actual, record.after)
                    else "not_applied"
                    if equivalent(actual, record.before)
                    else "outcome_unknown"
                )
            except (LocalFileError, OSError):
                state = "outcome_unknown"

        def work(_):
            updates = {"effect_state": state}
            if state == "applied" and record.coverage == "complete":
                updates["after"] = record.after.model_copy(
                    update={"file_identity": actual.file_identity}
                )
            saved = self.records.save(record.model_copy(update=updates))
            if self.event_sink:
                self.event_sink(
                    "workspace_change_recorded",
                    "workspace_change",
                    saved.change_id,
                    {
                        "session_id": saved.session_id,
                        "task_run_id": saved.task_run_id,
                        "row_version": saved.row_version,
                        "effect_state": saved.effect_state,
                    },
                )

        self.journal.transact(work)

    def workflow_drafts(self, execution):
        from morrow.services.files import ChangeCaptureDraft, _complete_diff

        records = self.records.list(
            self.workspace_id, task_ids=(execution.task_run_id,), limit=1000
        )
        drafts = []
        for row in records:
            if row.tool_execution_id != execution.tool_execution_id:
                continue
            complete = row.coverage == "complete" and row.effect_state == "applied"
            diff = None
            reason = row.omission_reason
            if complete:
                before = (
                    self.artifacts.read_workspace_snapshot(row.before.snapshot_id)
                    if row.before.exists
                    else b""
                )
                after = (
                    self.artifacts.read_workspace_snapshot(row.after.snapshot_id)
                    if row.after.exists
                    else b""
                )
                diff = _complete_diff(
                    before.decode("utf-8-sig"), after.decode("utf-8-sig"), row.path
                )
                if len(diff.encode("utf-8")) > 1024 * 1024:
                    complete, diff, reason = False, None, "diff_budget_exceeded"
            drafts.append(
                ChangeCaptureDraft(
                    path=row.path,
                    operation=row.operation,
                    status={
                        "create": "created",
                        "patch": "modified",
                        "replace": "modified",
                        "delete": "deleted",
                        "move": "moved",
                        "rename": "renamed",
                    }[row.operation],
                    before_sha256=row.before.sha256,
                    after_sha256=row.after.sha256,
                    before_size=row.before.byte_size if row.before.exists else None,
                    after_size=row.after.byte_size if row.after.exists else None,
                    unified_diff=diff,
                    content_complete=complete,
                    omission_reason=reason if not complete else None,
                )
            )
        return tuple(drafts)
