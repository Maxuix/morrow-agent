"""REPL command adapter for Learning Inbox and Project Knowledge."""

from __future__ import annotations

import json

from morrow.application.command_types import (
    CommandResult,
    KnowledgeLifecycleCommandRequest,
    LearningCandidateCommandRequest,
    LearningPromotionRecoveryRequest,
)
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.learning import LearningCandidateStatus
from morrow.core.learning_commands import (
    AcceptLearningCandidateCommand,
    DeleteProjectKnowledgeCommand,
    DisableProjectKnowledgeCommand,
    EditAndAcceptLearningCandidateCommand,
    EnableProjectKnowledgeCommand,
    MarkProjectKnowledgeDisputedCommand,
    RejectLearningCandidateCommand,
)
from morrow.core.learning_memory import LearningConflictResolution


class LearningCommandMixin:
    """Keep Learning/Memory slash commands out of the general Task command file."""

    def _new_command_id(self) -> str:
        source = self.id_source or getattr(self.api, "id_source", None)
        if source is None:
            raise RuntimeError("command ID source is unavailable")
        return source.new_id("cmd")

    @staticmethod
    def _json_line(value) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def _candidate_lines(cls, view) -> list[str]:
        candidate = view.candidate
        lines = [
            f"候选：{candidate.candidate_id}",
            f"类型：{candidate.candidate_type.value}；状态：{candidate.status.value}；版本：{candidate.row_version}",
            f"语义键：{candidate.semantic_key}；操作：{candidate.operation.value}；作用域：{candidate.proposed_scope.value}",
            f"证据：{len(view.evidence)} 条；冲突：{len(view.conflicts)} 条",
        ]
        if view.target.reason:
            lines.append(f"当前目标：{view.target.reason}")
        for item in view.evidence:
            lines.append(
                f"证据 {item.evidence_id}：{item.authority} / {item.source_kind}；"
                f"摘要={item.excerpt_redacted or '已省略'}"
            )
        lines.append(
            "提议值：" + cls._json_line(candidate.proposed_payload.model_dump(mode="json"))
        )
        return lines

    @classmethod
    def _preview_lines(cls, preview) -> list[str]:
        lines = [
            "Learning 决策预览：",
            f"候选：{preview.candidate.candidate_id}；类型：{preview.candidate.candidate_type.value}",
            f"版本：{preview.expected_row_version}；作用域：{preview.scope.value}",
            f"冲突处理：{preview.conflict_resolution.value}；可执行：{'是' if preview.available else '否'}",
        ]
        if preview.reason:
            lines.append(f"原因：{preview.reason}")
        if preview.conflict:
            lines.append(f"冲突：{preview.conflict}")
        if preview.before is not None:
            lines.append("当前值：" + cls._json_line(preview.before.value))
        lines.append("之后值：" + cls._json_line(preview.after.value))
        lines.extend(preview.configuration_preview)
        return lines

    def _learn_command(self, parts: list[str]) -> CommandResult:
        if self.api is None:
            return CommandResult(["Learning 服务尚未就绪。"])
        operation = parts[1].casefold() if len(parts) > 1 else "status"
        if operation == "status":
            status = self.api.learning_status()
            return CommandResult(
                [
                    f"Learning Policy：{status.policy.mode.value}；已持久化：{'是' if status.persisted else '否'}",
                    f"Review：待处理 {status.pending_reviews}，运行中 {status.running_reviews}，失败 {status.failed_reviews}",
                    f"Inbox：{status.proposed_candidates} 个 proposed Candidate",
                ],
                value=status,
            )
        if operation in {"inbox", "list"}:
            page = self.api.list_learning_candidate_views(
                status=LearningCandidateStatus.PROPOSED,
                limit=50,
            )
            lines = [
                f"Learning Inbox：{len(page.items)} 个候选"
                + (f"；下一页游标 {page.next_cursor}" if page.next_cursor else "")
            ]
            for item in page.items:
                lines.append(
                    f"{item.candidate_id}\t{item.candidate_type.value}\t"
                    f"{item.status.value}\tversion={item.row_version}"
                )
            return CommandResult(lines, value=page)
        if operation == "reviews":
            page = self.api.list_learning_review_views(limit=50)
            lines = [f"Learning Reviews：{len(page.items)} 个"]
            lines.extend(
                f"{item.review_id}\t{item.status.value}\tcandidates={item.candidate_count}"
                for item in page.items
            )
            return CommandResult(lines, value=page)
        if operation == "promotions":
            if len(parts) >= 4:
                action = parts[3].casefold()
                if action not in {"retry", "finalize", "cancel", "abort"}:
                    raise ApplicationError(ApplicationErrorCode.INVALID, "配置恢复动作无效")
                operation_id = parts[2]
                operation = self.api.get_learning_promotion(operation_id)
                if operation is None:
                    return CommandResult(["配置 promotion 不存在。"])
                return CommandResult(
                    [
                        f"将对配置 promotion {operation_id} 执行 {action}；",
                        "请确认后才会处理。",
                    ],
                    action="learning_promotion_recovery_preview",
                    value=LearningPromotionRecoveryRequest(operation_id, action),
                )
            items = self.api.list_learning_promotions()
            lines = [f"配置 promotions：{len(items)} 个"]
            lines.extend(
                f"{item.operation_id}\t{item.state.value}\t{item.target}.{item.path}"
                f"\t{item.scope.value}\trow={item.row_version}"
                for item in items
            )
            return CommandResult(lines, value=items)
        if operation == "undo":
            if len(parts) < 3:
                return CommandResult(["用法：/learn undo <activation-id>"])
            activation = self.api.get_learning_activation(parts[2])
            if activation is None:
                return CommandResult(["配置 activation 不存在。"])
            prepared = self.api.preview_learning_undo(parts[2])
            return CommandResult(
                ["配置撤销预览：", *prepared.preview_lines],
                action="learning_undo_preview",
                value=parts[2],
            )
        if operation == "show":
            if len(parts) < 3:
                return CommandResult(["用法：/learn show <candidate-id>"])
            view = self.api.get_learning_candidate_view(parts[2])
            if view is None:
                return CommandResult(["Learning Candidate 不存在。"])
            return CommandResult(self._candidate_lines(view), value=view)
        if operation in {"accept", "edit", "reject"}:
            if len(parts) < 3:
                return CommandResult([f"用法：/learn {operation} <candidate-id>"])
            view = self.api.get_learning_candidate_view(parts[2])
            if view is None:
                return CommandResult(["Learning Candidate 不存在。"])
            if operation == "reject":
                never_suggest = "--never-suggest" in parts[3:]
                preview = self.api.preview_learning_candidate_decision(view.candidate.candidate_id)
                return CommandResult(
                    self._preview_lines(preview),
                    action="learning_reject_preview",
                    value=LearningCandidateCommandRequest(
                        candidate_id=view.candidate.candidate_id,
                        expected_row_version=view.candidate.row_version,
                        never_suggest=never_suggest,
                    ),
                )
            scope, resolution = self._parse_learning_options(parts[3:])
            preview = self.api.preview_learning_candidate_decision(
                view.candidate.candidate_id,
                scope=scope,
                conflict_resolution=resolution,
            )
            return CommandResult(
                self._preview_lines(preview),
                action=(
                    "learning_edit_preview" if operation == "edit" else "learning_accept_preview"
                ),
                value=LearningCandidateCommandRequest(
                    candidate_id=view.candidate.candidate_id,
                    expected_row_version=view.candidate.row_version,
                    scope=scope,
                    conflict_resolution=resolution,
                ),
            )
        return CommandResult(
            [
                "用法：/learn [status|inbox|show|accept|edit|reject|reviews|promotions|undo]",
                "恢复：/learn promotions <operation-id> <retry|finalize|cancel|abort>",
            ]
        )

    @staticmethod
    def _parse_conflict_resolution(value: str) -> LearningConflictResolution:
        try:
            return LearningConflictResolution(value.casefold())
        except ValueError as exc:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "冲突处理必须是 none、confirm、replace、merge、re_enable 或 resolve_dispute",
            ) from exc

    @classmethod
    def _parse_learning_options(
        cls, values: list[str]
    ) -> tuple[str | None, LearningConflictResolution]:
        scope = None
        resolution = LearningConflictResolution.NONE
        index = 0
        while index < len(values):
            value = values[index]
            if value == "--scope":
                if index + 1 >= len(values):
                    raise ApplicationError(ApplicationErrorCode.INVALID, "--scope 需要一个值")
                scope = values[index + 1].casefold()
                index += 2
                continue
            if value.startswith("--"):
                raise ApplicationError(ApplicationErrorCode.INVALID, f"未知 Learning 选项：{value}")
            if resolution is not LearningConflictResolution.NONE:
                raise ApplicationError(ApplicationErrorCode.INVALID, "冲突处理选项只能指定一次")
            resolution = cls._parse_conflict_resolution(value)
            index += 1
        return scope, resolution

    def _memory_command(self, parts: list[str]) -> CommandResult:
        if self.api is None:
            return CommandResult(["Memory 服务尚未就绪。"])
        operation = parts[1].casefold() if len(parts) > 1 else "list"
        if operation == "list":
            if len(parts) > 1 and parts[2:] not in ([], ["--type", "knowledge"]):
                return CommandResult(["用法：/memory list [--type knowledge]"])
            page = self.api.list_project_knowledge(limit=50)
            lines = [f"Project Knowledge：{len(page.items)} 条"]
            lines.extend(
                f"{item.head.knowledge_id}\t{item.head.status.value}\t"
                f"{item.head.category.value}\t{item.head.semantic_key}"
                for item in page.items
            )
            return CommandResult(lines, value=page)
        if operation == "show":
            if len(parts) < 3:
                return CommandResult(["用法：/memory show <knowledge-id>"])
            value = self.api.get_project_knowledge(parts[2])
            if value is None:
                return CommandResult(["Project Knowledge 不存在。"])
            return CommandResult(
                [
                    f"Knowledge：{value.head.knowledge_id}；状态：{value.head.status.value}；版本：{value.head.row_version}",
                    f"语义键：{value.head.semantic_key}；历史版本：{len(value.timeline)}",
                    "当前值："
                    + self._json_line(
                        value.revision.model_dump(mode="json") if value.revision else None
                    ),
                ],
                value=value,
            )
        if operation in {"disable", "enable", "dispute", "delete"}:
            if len(parts) < 3:
                return CommandResult([f"用法：/memory {operation} <knowledge-id>"])
            value = self.api.get_project_knowledge(parts[2])
            if value is None:
                return CommandResult(["Project Knowledge 不存在。"])
            return CommandResult(
                [
                    f"将对 {value.head.knowledge_id} 执行 {operation}；"
                    f"当前状态={value.head.status.value}；版本={value.head.row_version}。",
                    "请确认后才会写入。",
                ],
                action="memory_lifecycle_preview",
                value=KnowledgeLifecycleCommandRequest(
                    knowledge_id=value.head.knowledge_id,
                    expected_row_version=value.head.row_version,
                    operation=operation,
                ),
            )
        return CommandResult(
            ["用法：/memory [list [--type knowledge]|show|disable|enable|dispute|delete]"]
        )

    def accept_learning_candidate(self, request: LearningCandidateCommandRequest):
        self._ensure_workspace_writable()
        return self.api.accept_learning_candidate(
            AcceptLearningCandidateCommand(
                workspace_id=self.identity.workspace_id,
                candidate_id=request.candidate_id,
                expected_row_version=request.expected_row_version,
                command_id=self._new_command_id(),
                scope=request.scope,
                conflict_resolution=request.conflict_resolution,
            )
        ).value

    def edit_learning_candidate(self, request: LearningCandidateCommandRequest):
        self._ensure_workspace_writable()
        if request.final_payload is None:
            raise ApplicationError(ApplicationErrorCode.INVALID, "编辑后的候选值不能为空")
        return self.api.edit_and_accept_learning_candidate(
            EditAndAcceptLearningCandidateCommand(
                workspace_id=self.identity.workspace_id,
                candidate_id=request.candidate_id,
                expected_row_version=request.expected_row_version,
                command_id=self._new_command_id(),
                scope=request.scope,
                conflict_resolution=request.conflict_resolution,
                final_payload=request.final_payload,
            )
        ).value

    def reject_learning_candidate(self, request: LearningCandidateCommandRequest):
        self._ensure_workspace_writable()
        return self.api.learning.reject_candidate(
            RejectLearningCandidateCommand(
                workspace_id=self.identity.workspace_id,
                candidate_id=request.candidate_id,
                expected_row_version=request.expected_row_version,
                command_id=self._new_command_id(),
                never_suggest=request.never_suggest,
                reason=request.reason,
            )
        ).value

    def undo_learning_activation(self, activation_id: str):
        self._ensure_workspace_writable()
        return self.api.undo_learning_activation(
            activation_id,
            command_id=self._new_command_id(),
        ).value

    def recover_learning_promotion(self, request: LearningPromotionRecoveryRequest):
        self._ensure_workspace_writable()
        return self.api.recover_learning_promotion(request.operation_id, action=request.action)

    def mutate_knowledge(self, request: KnowledgeLifecycleCommandRequest):
        self._ensure_workspace_writable()
        kwargs = {
            "workspace_id": self.identity.workspace_id,
            "knowledge_id": request.knowledge_id,
            "expected_row_version": request.expected_row_version,
            "command_id": self._new_command_id(),
        }
        commands = {
            "disable": (DisableProjectKnowledgeCommand, self.api.disable_project_knowledge),
            "enable": (EnableProjectKnowledgeCommand, self.api.enable_project_knowledge),
            "dispute": (MarkProjectKnowledgeDisputedCommand, self.api.dispute_project_knowledge),
            "delete": (DeleteProjectKnowledgeCommand, self.api.delete_project_knowledge),
        }
        command_type, call = commands[request.operation]
        return call(command_type(**kwargs)).value


__all__ = ["LearningCommandMixin"]
