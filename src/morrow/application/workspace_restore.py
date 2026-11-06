"""Scoped file review and explicit, idempotent compensation operations."""

from __future__ import annotations

import base64
import json
import os
from collections import defaultdict
from datetime import timedelta
from difflib import unified_diff
from pathlib import Path

from morrow.adapters.local.filesystem import FileSystemMutationError
from morrow.adapters.local.restore import RestorePublisher, identity
from morrow.application.restore_hunks import HunkConflict, HunkPlan, enabled
from morrow.application.task_artifacts import TaskArtifactsService
from morrow.application.workspace_changes import WorkspaceChangeRecorder, equivalent, manifest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.artifacts import ArtifactError, ArtifactErrorCode, ArtifactRetention
from morrow.core.domain import canonical_json_bytes, refuse_secret_material, sha256_digest
from morrow.core.workspace_changes import (
    MAX_SNAPSHOT_BYTES,
    RestoreItem,
    RestoreOperation,
    WorkspaceChange,
)
from morrow.services.files import (
    LocalFileError,
    WorkspaceFileService,
    WorkspaceMutationService,
    WorkspacePathResolver,
)


def digest(value):
    return sha256_digest(canonical_json_bytes(value))


def continuous(a, b):
    return equivalent(a, b) and (
        a.file_identity is None or b.file_identity is None or a.file_identity == b.file_identity
    )


def fail(message, code=ApplicationErrorCode.CONFLICT):
    raise ApplicationError(code, message)


class WorkspaceRestoreService:
    def __init__(
        self,
        journal,
        artifacts,
        root: Path,
        workspace_id: str,
        *,
        workflow_queries=None,
        busy=None,
        writable=None,
        clock=None,
        publisher=None,
        event_sink=None,
    ):
        self.journal, self.artifacts, self.root, self.workspace_id = (
            journal,
            artifacts,
            root,
            workspace_id,
        )
        self.records = journal.workspace_changes
        self.clock = clock or journal.now
        self.busy = busy or (lambda: False)
        self.writable = writable or (lambda: journal.supports_writes())
        self.publisher = publisher or RestorePublisher()
        self.event_sink = event_sink
        self.scopes = TaskArtifactsService(
            journal,
            artifacts=artifacts,
            workflow_queries=workflow_queries,
            workspace_id=workspace_id,
        )
        mutation = WorkspaceMutationService(WorkspaceFileService(WorkspacePathResolver(root)))
        self.reader = WorkspaceChangeRecorder(
            journal, artifacts, mutation, workspace_id=workspace_id
        )

    def scope(self, session_id, selectors):
        return self.scopes._resolve(
            session_id,
            task_run_id=selectors.get("task_run_id"),
            workflow_run_id=selectors.get("workflow_run_id"),
            node_run_id=selectors.get("node_run_id"),
        )

    def selected(self, session_id, ids, selectors):
        scope = self.scope(session_id, selectors)
        if not ids or len(ids) > 1000 or len(set(ids)) != len(ids):
            fail("修改选择无效", ApplicationErrorCode.INVALID)
        rows = []
        for change_id in ids:
            row = self.records.get(self.workspace_id, change_id)
            if row is None or row.task_run_id not in scope.task_ids:
                fail("修改不在当前任务范围内", ApplicationErrorCode.NOT_FOUND)
            rows.append(row)
        return sorted(rows, key=lambda r: r.workspace_sequence)

    def bytes(self, value):
        if not value.exists:
            return None
        if not value.snapshot_id:
            fail("snapshot_missing：缺少完整恢复快照", ApplicationErrorCode.UNAVAILABLE)
        try:
            metadata = self.artifacts.get(value.snapshot_id)
            if metadata is None:
                fail("snapshot_missing", ApplicationErrorCode.UNAVAILABLE)
            if (
                metadata.retention is not ArtifactRetention.PINNED
                and metadata.created_at + timedelta(days=30) <= self.clock()
            ):
                fail(
                    "snapshot_expired：快照已超出保留期，请固定后重新预览",
                    ApplicationErrorCode.UNAVAILABLE,
                )
            raw = self.artifacts.read_workspace_snapshot(value.snapshot_id)
        except ArtifactError as exc:
            reason = (
                "snapshot_missing：快照缺失"
                if exc.code is ArtifactErrorCode.MISSING
                else "snapshot_corrupt：快照损坏"
                if exc.code is ArtifactErrorCode.INTEGRITY
                else "snapshot_unavailable：快照不可用"
            )
            fail(reason, ApplicationErrorCode.UNAVAILABLE)
        if len(raw) != value.byte_size or sha256_digest(raw) != value.sha256:
            fail("snapshot_corrupt", ApplicationErrorCode.UNAVAILABLE)
        return raw

    def _hunk_plan(self, row):
        if (
            not enabled("HUNKS")
            or row.coverage != "complete"
            or row.effect_state != "applied"
            or row.operation not in {"patch", "replace", "restore"}
            or not row.before.exists
            or not row.after.exists
        ):
            raise HunkConflict("该版本不支持修改块")
        return HunkPlan(row, self.bytes(row.before), self.bytes(row.after))

    def _hunk_candidate(self, row, ids):
        if (
            not enabled("CHANGES")
            or not self.publisher.supported()
            or row.root_identity != identity(self.root)
        ):
            raise HunkConflict("当前工作区或平台不支持修改块恢复")
        plan = self._hunk_plan(row)
        current, raw = self.reader.current_bytes(row.path)
        lineage = row.after.file_identity
        later = self.records.list(
            self.workspace_id, path=row.path, after=row.workspace_sequence, limit=101
        )
        if len(later) > 100:
            raise HunkConflict("后续修改链超出比较预算")
        for change in later:
            if (
                change.path != row.path
                or change.operation not in {"patch", "replace", "restore"}
                or change.coverage != "complete"
                or change.effect_state != "applied"
                or change.root_identity != row.root_identity
                or not lineage
                or not change.after.file_identity
                or change.before.file_identity != lineage
                or change.before.mode != row.after.mode
                or change.after.mode != row.after.mode
            ):
                raise HunkConflict("存在无法确认的后续修改链")
            lineage = change.after.file_identity
        if (
            raw is None
            or current.mode != row.after.mode
            or (lineage is not None and current.file_identity != lineage)
            or (lineage is None and not equivalent(current, row.after))
        ):
            raise HunkConflict("文件身份或权限已变化")
        result = plan.inverse(raw, ids)
        if len(result) > MAX_SNAPSHOT_BYTES:
            raise HunkConflict("恢复结果超出文件预算")
        for value in (raw, result):
            try:
                refuse_secret_material(
                    value, label="file snapshot", profile="workflow_value_sensitive"
                )
            except ValueError as exc:
                raise HunkConflict("当前或恢复内容无法通过快照安全检查") from exc
        return current, raw, result

    def operation_view(self, op):
        """Render bounded C→D for the still-current preview; never persist source in a receipt."""
        view = op.model_dump(mode="json")
        if op.state != "previewed" or self.clock() >= op.expires_at:
            return view
        for item, item_view in zip(op.items, view["items"], strict=True):
            if not item.hunk_ids or item.state != "pending":
                continue
            try:
                (row,) = self.selected(op.session_id, item.change_ids, op.selectors)
                if (
                    digest([row.model_dump(mode="json")]) != op.selection_digest
                    or identity((self.root / item.path).parent) != item.parent_identity
                ):
                    raise HunkConflict("预览已失效")
                current, raw, desired = self._hunk_candidate(row, item.hunk_ids)
                if current != item.expected or not equivalent(
                    manifest(desired, current.mode), item.desired
                ):
                    raise HunkConflict("当前文件已变化，请重新预览")
                before = raw.decode("utf-8-sig").splitlines(keepends=True)
                after = desired.decode("utf-8-sig").splitlines(keepends=True)
                parts = []
                remaining = 32 * 1024
                for line in unified_diff(before, after, fromfile="C", tofile="D"):
                    if not line.endswith("\n"):
                        line += "\n\\ No newline at end of file\n"
                    parts.append(line[:remaining])
                    remaining -= len(line)
                    if remaining < 0:
                        break
                item_view["preview_diff"] = "".join(parts)
                item_view["preview_truncated"] = remaining < 0
            except (
                ApplicationError,
                HunkConflict,
                LocalFileError,
                OSError,
                FileSystemMutationError,
            ):
                item_view["preview_unavailable"] = True
        return view

    def capability(self, row):
        if os.environ.get("MORROW_RESTORE_CHANGES", "1") == "0":
            return "unsupported", "restore_disabled"
        try:
            if row.root_identity is None or identity(self.root) != row.root_identity:
                return "unsupported", "workspace_identity_changed_or_missing"
        except (OSError, FileSystemMutationError):
            return "unavailable", "workspace_unavailable"
        if row.coverage != "complete" or row.operation not in {
            "create",
            "patch",
            "replace",
            "restore",
        }:
            return "unsupported", row.omission_reason or "unsupported_operation"
        if row.effect_state != "applied":
            return "unavailable", row.effect_state
        if not self.publisher.supported():
            return "unsupported", "unsupported_platform"
        try:
            self.bytes(row.before)
            self.bytes(row.after)
            current = self.reader.current(row.path)
            if equivalent(current, row.before):
                return "already_target", "目标内容已存在；没有本操作的执行证据"
            if (
                row.after.file_identity is not None
                and current.file_identity != row.after.file_identity
            ):
                return "conflict", "文件身份在修改后已变化"
            if not equivalent(current, row.after):
                return "conflict", "当前文件有后续修改或权限变化"
            later = self.records.list(
                self.workspace_id, path=row.path, after=row.workspace_sequence, limit=1
            )
            if later:
                return "conflict", "存在后续修改记录，请先选择最新变化"
        except (ApplicationError, LocalFileError, OSError) as exc:
            return "unavailable", getattr(exc, "message", "文件状态无法确认")
        if self.busy():
            return "busy", "工作区有尚未结束的写操作"
        return "ready", None

    def listing(self, session_id, *, selectors=None, limit=50, cursor=None):
        selectors = selectors or {}
        scope = self.scope(session_id, selectors)
        if not 1 <= limit <= 200:
            fail("分页大小无效", ApplicationErrorCode.INVALID)
        through = self.records.high_watermark(self.workspace_id)
        after = 0

        def revision(bound):
            ids = scope.task_ids
            if not ids:
                return 0
            row = self.records.backend.read_one(
                "SELECT COALESCE(SUM(row_version),0) FROM workspace_changes WHERE workspace_id=? AND workspace_sequence<=? AND task_run_id IN ("
                + ",".join("?" for _ in ids)
                + ")",
                (self.workspace_id, bound, *ids),
            )
            return int(row[0])

        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor))
                through, after = int(data["through"]), int(data["after"])
                if data["scope"] != digest([self.workspace_id, session_id, selectors]) or data[
                    "revision"
                ] != revision(through):
                    fail("变更视图已更新，请刷新", ApplicationErrorCode.STALE)
            except (ValueError, KeyError, TypeError):
                fail("分页游标无效", ApplicationErrorCode.INVALID)
        rows = (
            self.records.list(
                self.workspace_id,
                task_ids=scope.task_ids,
                after=after,
                through=through,
                limit=limit + 1,
            )
            if scope.task_ids
            else ()
        )
        page = rows[:limit]
        items = []
        read_budget = 16 * 1024 * 1024
        for row in page:
            required = row.before.byte_size + row.after.byte_size * 2
            if required > read_budget:
                support, reason = "unavailable", "完整性检查待预览时进行"
            else:
                support, reason = self.capability(row)
                read_budget -= required
            items.append(
                {
                    **row.model_dump(mode="json"),
                    "restore_support": support,
                    "restore_reason": reason,
                }
            )
        commands = [
            e
            for task in scope.task_ids
            for e in self.journal.list_task_executions(self.workspace_id, task)
            if e.tool_name in {"bash", "run_command"}
        ]
        next_cursor = (
            base64.urlsafe_b64encode(
                canonical_json_bytes(
                    {
                        "through": through,
                        "after": page[-1].workspace_sequence,
                        "revision": revision(through),
                        "scope": digest([self.workspace_id, session_id, selectors]),
                    }
                )
            ).decode()
            if len(rows) > limit
            else None
        )
        return {
            "schema_version": 1,
            "items": items,
            "next_cursor": next_cursor,
            "view_digest": digest([(r.change_id, r.row_version) for r in page]),
            "uncovered_commands": len(commands),
            "coverage_message": "命令的文件影响未完整捕获"
            if commands
            else "仅受控文本写入具备恢复保障；外部编辑和命令写入不自动归属",
            "legacy_message": "旧任务或未启用捕获的修改没有恢复快照" if not items else None,
        }

    def detail(
        self, session_id, change_id, *, selectors=None, offset=0, limit=128 * 1024, chain_ids=None
    ):
        chain = self.selected(session_id, chain_ids or [change_id], selectors or {})
        row = chain[-1]
        if row.change_id != change_id or any(
            r.path != row.path or r.task_run_id != row.task_run_id for r in chain
        ):
            fail("净变化选择无效", ApplicationErrorCode.INVALID)
        for a, b in zip(chain, chain[1:], strict=False):
            between = self.records.list(
                self.workspace_id,
                path=row.path,
                after=a.workspace_sequence,
                through=b.workspace_sequence,
                limit=1001,
            )
            if not continuous(a.after, b.before) or [r.change_id for r in between] != [b.change_id]:
                fail("中间存在其他修改，请查看修改时间线")
        if offset < 0 or not 1 <= limit <= 128 * 1024:
            fail("Diff 读取范围无效", ApplicationErrorCode.INVALID)
        from morrow.services.files import _complete_diff

        a, b = self.bytes(chain[0].before), self.bytes(row.after)
        text = _complete_diff(
            (a or b"").decode("utf-8-sig"), (b or b"").decode("utf-8-sig"), row.path
        )
        # Character pagination avoids splitting UTF-8 codepoints; each response <= 512 KiB.
        page = text[offset : offset + limit]
        support, reason = self.capability(row)
        hunks, hunk_reason = [], None
        if offset == 0 and len(chain) == 1 and a is not None and b is not None:
            if enabled("HUNKS") and row.operation in {"patch", "replace", "restore"}:
                try:
                    if row.coverage == "complete" and row.effect_state == "applied":
                        hunks = HunkPlan(row, a, b).descriptions()
                except HunkConflict as exc:
                    hunk_reason = str(exc)
        return {
            "hunks": hunks,
            "hunk_reason": hunk_reason,
            "change": row.model_dump(mode="json"),
            "before": chain[0].before.model_dump(mode="json"),
            "after": row.after.model_dump(mode="json"),
            "chain_ids": [r.change_id for r in chain],
            "diff": page,
            "next_offset": offset + len(page) if offset + len(page) < len(text) else None,
            "complete": offset + len(page) >= len(text),
            "restore_support": support,
            "restore_reason": reason,
            "version_digest": digest([row.before.model_dump(), row.after.model_dump()]),
        }

    def review(self, session_id, ids, versions, *, selectors=None):
        rows = self.selected(session_id, ids, selectors or {})
        for row in rows:
            if row.effect_state == "prepared":
                fail("修改仍在发布，请收束后再标记审阅", ApplicationErrorCode.BUSY)
            if versions.get(row.change_id) != row.row_version:
                fail("审阅版本已变化", ApplicationErrorCode.STALE)

        def work(_):
            return tuple(
                self.records.save(
                    r.model_copy(
                        update={
                            "review_state": "reviewed",
                            "reviewed_digest": digest(
                                [r.before.model_dump(), r.after.model_dump()]
                            ),
                        }
                    )
                )
                for r in rows
            )

        return self.journal.transact(work)

    def reconcile_changes(self, session_id, ids, *, selectors=None):
        self._write_allowed(restore=False)
        rows = self.selected(session_id, ids, selectors or {})
        for row in rows:
            if row.effect_state in {"prepared", "outcome_unknown"} and row.coverage == "complete":
                if row.root_identity != identity(self.root):
                    fail("工作区身份已变化", ApplicationErrorCode.STALE)
                self.bytes(row.before)
                self.bytes(row.after)
                self.reader.finish(row, succeeded=False)
        return tuple(self.records.get(self.workspace_id, row.change_id) for row in rows)

    def pin(self, session_id, ids, *, selectors=None):
        self._write_allowed(restore=False)
        rows = self.selected(session_id, ids, selectors or {})
        for row in rows:
            if row.coverage != "complete":
                fail("此修改没有可固定的完整恢复快照", ApplicationErrorCode.UNAVAILABLE)
            for value in (row.before, row.after):
                if value.exists:
                    if not value.snapshot_id:
                        fail("snapshot_missing", ApplicationErrorCode.UNAVAILABLE)
                    try:
                        raw = self.artifacts.read_workspace_snapshot(value.snapshot_id)
                    except ArtifactError:
                        fail("snapshot_missing_or_corrupt", ApplicationErrorCode.UNAVAILABLE)
                    if sha256_digest(raw) != value.sha256:
                        fail("snapshot_corrupt", ApplicationErrorCode.UNAVAILABLE)

        def work(_):
            for row in rows:
                for value in (row.before, row.after):
                    if value.snapshot_id:
                        self.artifacts.pin(value.snapshot_id)
            return tuple(self.records.save(row) for row in rows)

        return self.journal.transact(work)

    def _write_allowed(self, *, restore=True):
        if restore and os.environ.get("MORROW_RESTORE_CHANGES", "1") == "0":
            fail("unsupported_restore：恢复写入已关闭", ApplicationErrorCode.UNAVAILABLE)
        if not self.writable():
            fail("工作区只读", ApplicationErrorCode.READ_ONLY)
        if self.busy():
            fail("workspace_busy：工作区写入尚未结束", ApplicationErrorCode.BUSY)

    def preview(self, session_id, ids, *, command_id, selectors=None, hunk_ids=()):
        selectors = selectors or {}
        hunk_ids = tuple(hunk_ids)
        request = digest(
            [session_id, ids, selectors, hunk_ids] if hunk_ids else [session_id, ids, selectors]
        )
        if self.records.operation(self.workspace_id, apply_command_id=command_id):
            fail("同一 command_id 已用于执行恢复")
        previous = self.records.operation(self.workspace_id, command_id=command_id)
        if previous:
            if previous.session_id != session_id or previous.request_digest != request:
                fail("同一 command_id 对应不同请求")
            return previous
        self._write_allowed()
        if self.records.pending(self.workspace_id):
            fail("operation_in_progress：请先核对未完成恢复", ApplicationErrorCode.BUSY)
        rows = self.selected(session_id, ids, selectors)
        if hunk_ids and (len(rows) != 1 or len(hunk_ids) != len(set(hunk_ids))):
            fail("修改块必须选自同一次修改，且不能重复", ApplicationErrorCode.INVALID)
        groups = defaultdict(list)
        for row in rows:
            groups[row.path].append(row)
        if len(groups) > 50:
            fail("一次最多恢复 50 个文件", ApplicationErrorCode.INVALID)
        operation_id = self.artifacts.id_source.new_id("rst")
        items = []
        total = 0
        for index, (path, chain) in enumerate(sorted(groups.items())):
            first, last = chain[0], chain[-1]
            support, reason = self.capability(last)
            for a, b in zip(chain, chain[1:], strict=False):
                if a.task_run_id != b.task_run_id or not continuous(a.after, b.before):
                    support, reason = "conflict", "修改链中间存在其他变化"
                between = self.records.list(
                    self.workspace_id,
                    path=path,
                    after=a.workspace_sequence,
                    through=b.workspace_sequence,
                    limit=1001,
                )
                if [r.change_id for r in between] != [b.change_id]:
                    support, reason = "conflict", "修改链存在未选择的变化"
            for row in chain:
                if row.effect_state != "applied" or row.coverage != "complete":
                    support, reason = "unsupported", "缺少完整修改材料"
                self.bytes(row.before)
                self.bytes(row.after)
            expected, desired = last.after, first.before
            file_identity = None
            if hunk_ids:
                try:
                    expected, raw, candidate = self._hunk_candidate(last, hunk_ids)
                    desired = manifest(candidate, expected.mode)
                    file_identity = expected.file_identity
                    support, reason = ("already_target" if raw == candidate else "ready"), None
                except (
                    ApplicationError,
                    HunkConflict,
                    LocalFileError,
                    OSError,
                    FileSystemMutationError,
                ) as exc:
                    support, reason = (
                        "conflict",
                        getattr(exc, "message", None)
                        or (str(exc) if isinstance(exc, HunkConflict) else "当前文件无法安全比较"),
                    )
            total += desired.byte_size + expected.byte_size
            try:
                parent = identity((self.root / path).parent)
            except (OSError, FileSystemMutationError):
                parent = (0, 0)
                support, reason = "conflict", "父目录不存在或已变化"
            token = sha256_digest(f"{operation_id}:{index}")
            items.append(
                RestoreItem(
                    change_ids=tuple(r.change_id for r in chain),
                    path=path,
                    expected=expected,
                    desired=desired,
                    hunk_ids=hunk_ids,
                    parent_identity=parent,
                    file_identity=(
                        file_identity if hunk_ids else self.reader.current(path).file_identity
                    )
                    if support in {"ready", "already_target"}
                    else None,
                    capture_name=f".morrow-restore-{token}-capture",
                    temporary_name=f".morrow-restore-{token}-new",
                    state="pending"
                    if support == "ready"
                    else "target_reached"
                    if support == "already_target"
                    else "conflict",
                    reason=reason,
                )
            )
        if total > 128 * 1024 * 1024:
            fail("恢复字节预算超限，请拆批", ApplicationErrorCode.INVALID)
        stamp = self.clock()
        op = RestoreOperation(
            operation_id=operation_id,
            workspace_id=self.workspace_id,
            session_id=session_id,
            command_id=command_id,
            request_digest=request,
            preview_digest=digest([r.model_dump(mode="json") for r in rows]),
            selection_digest=digest([r.model_dump(mode="json") for r in rows]),
            selectors=selectors,
            root_identity=identity(self.root),
            items=tuple(items),
            created_at=stamp,
            expires_at=stamp + timedelta(minutes=5),
        )
        op = op.model_copy(update={"preview_digest": digest(op.model_dump(mode="json"))})
        return self.records.put_operation(op, new=True)

    def get_operation(self, session_id, operation_id):
        op = self.records.operation(self.workspace_id, operation_id)
        if op is None or op.session_id != session_id:
            fail("恢复操作不在当前会话内", ApplicationErrorCode.NOT_FOUND)
        return op

    def apply(self, session_id, operation_id, *, preview_digest, command_id):
        op = self.get_operation(session_id, operation_id)
        request = digest([session_id, operation_id, preview_digest])
        if self.records.operation(self.workspace_id, command_id=command_id):
            fail("同一 command_id 已用于创建预览")
        prior = self.records.operation(self.workspace_id, apply_command_id=command_id)
        if prior:
            if prior.operation_id != operation_id or prior.apply_digest != request:
                fail("同一 command_id 对应不同恢复请求")
            return (
                self.reconcile(session_id, operation_id)
                if prior.state in {"prepared", "applying", "needs_reconciliation"}
                else prior
            )
        if op.apply_command_id is not None:
            fail("此预览已经执行，请查询原操作")
        self._write_allowed()
        if self.records.pending(self.workspace_id):
            fail("operation_in_progress：请先核对未完成恢复", ApplicationErrorCode.BUSY)
        if (
            op.state != "previewed"
            or self.clock() >= op.expires_at
            or preview_digest != op.preview_digest
        ):
            fail("preview_expired：预览失效，请重新预览", ApplicationErrorCode.STALE)
        if any(i.state == "conflict" for i in op.items):
            return self.records.put_operation(
                op.model_copy(
                    update={
                        "state": "blocked",
                        "apply_command_id": command_id,
                        "apply_digest": request,
                    }
                )
            )
        if identity(self.root) != op.root_identity:
            fail("工作区身份已变化", ApplicationErrorCode.STALE)
        selected_rows = self.selected(
            session_id, [cid for item in op.items for cid in item.change_ids], op.selectors
        )
        if digest([r.model_dump(mode="json") for r in selected_rows]) != op.selection_digest:
            fail("预览后的修改记录已变化", ApplicationErrorCode.STALE)
        for item in op.items:
            last = self.records.get(self.workspace_id, item.change_ids[-1])
            if not item.hunk_ids and self.records.list(
                self.workspace_id, path=item.path, after=last.workspace_sequence, limit=1
            ):
                fail("预览后出现后续修改记录", ApplicationErrorCode.STALE)
        # All prechecks before the first effect. Exact selected versions stay bound.
        prepared_items = []
        for item in op.items:
            for cid in item.change_ids:
                row = self.records.get(self.workspace_id, cid)
                if row is None:
                    fail("修改记录已失效", ApplicationErrorCode.STALE)
            current = self.reader.current(item.path)
            expected_state = item.desired if item.state == "target_reached" else item.expected
            if (
                not equivalent(current, expected_state)
                or current.file_identity != item.file_identity
                or identity((self.root / item.path).parent) != item.parent_identity
            ):
                fail("restore_conflict：文件或父目录在预览后已变化", ApplicationErrorCode.STALE)
            if item.hunk_ids:
                row = self.records.get(self.workspace_id, item.change_ids[0])
                try:
                    verified, raw, candidate = self._hunk_candidate(row, item.hunk_ids)
                except (HunkConflict, LocalFileError, OSError, FileSystemMutationError):
                    fail("修改块预览已失效，请重新预览", ApplicationErrorCode.STALE)
                if verified != item.expected or not equivalent(
                    manifest(candidate, verified.mode), item.desired
                ):
                    fail("修改块预览已失效，请重新预览", ApplicationErrorCode.STALE)
                if item.state == "pending":
                    try:
                        captured = [
                            self.artifacts.publish_workspace_snapshot(
                                data, session_id=row.session_id, task_run_id=row.task_run_id
                            )
                            for data in (raw, candidate)
                        ]
                    except ArtifactError:
                        fail("无法持久化修改块恢复快照", ApplicationErrorCode.UNAVAILABLE)
                    item = item.model_copy(
                        update={
                            "expected": item.expected.model_copy(
                                update={"snapshot_id": captured[0].artifact_id}
                            ),
                            "desired": item.desired.model_copy(
                                update={"snapshot_id": captured[1].artifact_id}
                            ),
                        }
                    )
            else:
                self.bytes(item.expected)
                self.bytes(item.desired)
            prepared_items.append(item)
        op = self.records.put_operation(
            op.model_copy(
                update={
                    "state": "prepared",
                    "items": tuple(prepared_items),
                    "apply_command_id": command_id,
                    "apply_digest": request,
                }
            )
        )
        items = list(op.items)
        for index, item in enumerate(items):
            if item.state == "target_reached":
                continue
            # Compensation itself has a durable prepared change before publication.
            original = self.records.get(self.workspace_id, item.change_ids[-1])
            change = self.records.insert(
                WorkspaceChange(
                    change_id=self.artifacts.id_source.new_id("chg"),
                    workspace_id=self.workspace_id,
                    root_identity=op.root_identity,
                    session_id=original.session_id,
                    task_run_id=original.task_run_id,
                    mutation_key=f"{op.operation_id}:{index}",
                    path=item.path,
                    operation="restore",
                    origin="restore",
                    related_change_ids=item.change_ids,
                    restore_operation_id=op.operation_id,
                    before=item.expected,
                    after=item.desired,
                    created_at=self.clock(),
                )
            )
            item = item.model_copy(update={"change_id": change.change_id})
            items[index] = item
            op = self.records.put_operation(
                op.model_copy(update={"state": "applying", "items": tuple(items)})
            )
            try:
                self.publisher.publish(
                    self.root,
                    item.path,
                    expected=self.bytes(item.expected),
                    expected_mode=item.expected.mode,
                    desired=self.bytes(item.desired),
                    desired_mode=item.desired.mode,
                    capture_name=item.capture_name,
                    temporary_name=item.temporary_name,
                    root_identity=op.root_identity,
                    parent_identity=item.parent_identity,
                    expected_identity=item.file_identity,
                )
                try:
                    actual = self.reader.current(item.path)
                    if not equivalent(actual, item.desired):
                        raise LocalFileError("conflict", "发布后的文件已变化")
                except (LocalFileError, OSError) as exc:
                    raise FileSystemMutationError(
                        "outcome_unknown", "恢复发布后文件状态无法确认", effect_applied=True
                    ) from exc
            except (FileSystemMutationError, OSError, ApplicationError) as exc:
                unknown = getattr(exc, "effect_applied", False)
                self.records.save(
                    change.model_copy(
                        update={"effect_state": "outcome_unknown" if unknown else "not_applied"}
                    )
                )
                items[index] = item.model_copy(
                    update={
                        "state": "needs_reconciliation" if unknown else "conflict",
                        "reason": getattr(exc, "message", "恢复文件发布失败"),
                    }
                )
                for remaining in range(index + 1, len(items)):
                    items[remaining] = items[remaining].model_copy(update={"state": "not_executed"})
                return self.records.put_operation(
                    op.model_copy(
                        update={
                            "state": "needs_reconciliation"
                            if unknown
                            else "partially_applied"
                            if index
                            else "blocked",
                            "items": tuple(items),
                        }
                    )
                )
            self.records.save(
                change.model_copy(
                    update={
                        "effect_state": "applied",
                        "after": change.after.model_copy(
                            update={"file_identity": actual.file_identity}
                        ),
                    }
                )
            )
            items[index] = item.model_copy(update={"state": "applied"})
            op = self.records.put_operation(op.model_copy(update={"items": tuple(items)}))
        op = self.records.put_operation(op.model_copy(update={"state": "completed"}))
        if self.event_sink:
            self.event_sink(op)
        return op

    def reconcile(self, session_id, operation_id):
        """Read disk and settle intent only. Never resume file writes on restart/retry."""
        op = self.get_operation(session_id, operation_id)
        if op.state not in {"prepared", "applying", "needs_reconciliation"}:
            return op
        self._write_allowed(restore=False)
        items = []
        for item in op.items:
            state, reason = item.state, item.reason
            if state not in {"applied", "not_executed"}:
                try:
                    current = self.reader.current(item.path)
                    root_ok = (
                        identity(self.root) == op.root_identity
                        and identity((self.root / item.path).parent) == item.parent_identity
                    )
                    if root_ok and equivalent(current, item.desired):
                        state, reason = "target_reached", "目标已达到；依据持久化意图核对"
                    elif root_ok and equivalent(current, item.expected):
                        state, reason = "not_executed", "尚未执行；重新预览后再决定"
                    else:
                        state, reason = (
                            "needs_reconciliation",
                            "当前内容无法确认；保留捕获与临时文件供核对",
                        )
                except (OSError, LocalFileError):
                    state, reason = "needs_reconciliation", "文件身份无法确认；保全材料未删除"
                if item.change_id:
                    row = self.records.get(self.workspace_id, item.change_id)
                    if row:
                        if state == "target_reached":
                            row = row.model_copy(
                                update={
                                    "after": row.after.model_copy(
                                        update={"file_identity": current.file_identity}
                                    )
                                }
                            )
                        self.records.save(
                            row.model_copy(
                                update={
                                    "effect_state": "applied"
                                    if state == "target_reached"
                                    else "not_applied"
                                    if state == "not_executed"
                                    else "outcome_unknown"
                                }
                            )
                        )
            items.append(item.model_copy(update={"state": state, "reason": reason}))
        state = (
            "needs_reconciliation"
            if any(i.state == "needs_reconciliation" for i in items)
            else "completed"
            if all(i.state in {"applied", "target_reached"} for i in items)
            else "partially_applied"
        )
        return self.records.put_operation(
            op.model_copy(update={"state": state, "items": tuple(items)})
        )
