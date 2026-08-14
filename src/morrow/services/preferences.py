"""Three-layer preferences and conservative natural-language intent gate."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.core.models import (
    ConfigExtractionResult,
    ConfigPatch,
    ConfigPatchOperation,
    Decision,
    Preferences,
)


@dataclass(frozen=True)
class GateDecision:
    matched: bool
    mixed_task: bool = False
    forbidden: bool = False


class ConfigIntentGate:
    persistence_words = ("记住", "保存", "写入", "写进", "设为", "设置", "加入", "更新偏好", "配置")
    scope_words = (
        "全局",
        "全局偏好",
        "项目",
        "工作空间",
        "档案",
        "交接",
        "这次",
        "会话",
        "回复语言",
        "详细程度",
        "约束",
        "指令",
    )
    task_words = ("修复", "实现", "检查", "运行", "分析", "解释", "提交", "修改代码")
    forbidden_words = (
        "api key",
        "apikey",
        "凭据",
        "provider",
        "模型连接",
        "base url",
        "权限",
        "安全规则",
        "workspace_id",
    )

    def match(self, text: str) -> GateDecision:
        stripped = text.strip()
        has_persist = any(word in stripped for word in self.persistence_words)
        has_scope = any(word in stripped for word in self.scope_words)
        persistence_attempt = has_persist and has_scope
        mixed = persistence_attempt and any(word in stripped for word in self.task_words)
        forbidden = persistence_attempt and any(
            word.casefold() in stripped.casefold() for word in self.forbidden_words
        )
        return GateDecision(
            matched=persistence_attempt and not mixed and not forbidden,
            mixed_task=mixed,
            forbidden=forbidden,
        )


ALLOWED_PATHS = {
    ("global", "preferences"): {"language", "response_detail", "instructions"},
    ("workspace", "preferences"): {"language", "response_detail", "instructions"},
    ("workspace", "profile"): {
        "name",
        "summary",
        "goals",
        "tech_stack",
        "constraints",
        "conventions",
    },
    ("workspace", "handoff"): {
        "current_goal",
        "progress",
        "decisions",
        "blockers",
        "open_questions",
        "next_actions",
        "recovery_note",
    },
    ("session", "preferences"): {"language", "response_detail", "instructions"},
}


def validate_patch(patch: ConfigPatch) -> None:
    allowed = ALLOWED_PATHS.get((patch.scope, patch.target))
    if allowed is None:
        raise ValueError("不允许修改此作用域或目标")
    for operation in patch.operations:
        if operation.path not in allowed:
            raise ValueError(f"不允许修改字段: {operation.path}")
        if operation.op in {"set", "unset"} and operation.path in {
            "instructions",
            "goals",
            "tech_stack",
            "constraints",
            "conventions",
            "progress",
            "decisions",
            "blockers",
            "open_questions",
            "next_actions",
        }:
            raise ValueError("列表字段只能使用 append 或 remove")
        if operation.op in {"append", "remove"} and operation.path not in {
            "instructions",
            "goals",
            "tech_stack",
            "constraints",
            "conventions",
            "progress",
            "decisions",
            "blockers",
            "open_questions",
            "next_actions",
        }:
            raise ValueError("标量字段只能使用 set 或 unset")
        if operation.op != "unset" and operation.value is None:
            raise ValueError("此操作需要 value")


def render_patch_preview(patch: ConfigPatch) -> list[str]:
    validate_patch(patch)
    lines = ["配置预览：", f"作用域：{patch.scope}", f"目标：{patch.target}"]
    for operation in patch.operations:
        line = f"- {operation.op} {operation.path}"
        if operation.op != "unset":
            line += f" = {operation.value}"
        lines.append(line)
    return lines


class ConfigPatchService:
    def __init__(self, project_store, global_store, workspace_id: str, session=None) -> None:
        self.project_store = project_store
        self.global_store = global_store
        self.workspace_id = workspace_id
        self.session = session

    @staticmethod
    def _apply(target, operation: ConfigPatchOperation):
        data = target.model_copy(deep=True)
        path = operation.path
        if operation.op == "set":
            setattr(data, path, operation.value)
        elif operation.op == "unset":
            setattr(data, path, None)
        elif operation.op == "append":
            values = list(getattr(data, path))
            if operation.value not in values:
                values.append(operation.value)
            setattr(data, path, values)
        elif operation.op == "remove":
            values = list(getattr(data, path))
            normalized = " ".join(str(operation.value).split()).casefold()
            matches = [
                item
                for item in values
                if " ".join(
                    str(item.decision if isinstance(item, Decision) else item).split()
                ).casefold()
                == normalized
            ]
            if len(matches) != 1:
                raise ValueError("删除目标必须精确匹配一个值")
            values.remove(matches[0])
            setattr(data, path, values)
        return data

    def apply(self, patch: ConfigPatch):
        if self.session is not None and self.session.read_only and patch.scope == "workspace":
            raise RuntimeError("当前工作空间状态版本较新，只允许独立只读对话")
        if (
            self.session is not None
            and self.session.workspace_preferences_read_only
            and patch.scope == "workspace"
            and patch.target == "preferences"
        ):
            raise RuntimeError("工作空间 Preferences 不可安全加载，已禁止覆盖")
        validate_patch(patch)
        if patch.scope == "session":
            if self.session is None:
                raise ValueError("session scope is unavailable")
            value = self.session.preferences
            for operation in patch.operations:
                value = self._apply(value, operation)
            self.session.preferences = Preferences.model_validate(value)
            return "ok"
        if patch.scope == "global":
            current = self.global_store.load()
            expected = current.revision
            result = self.global_store.update(
                lambda config: config.model_copy(
                    update={"preferences": self._apply_many(config.preferences, patch.operations)}
                ),
                expected_revision=expected,
            )
            if result.status.value != "ok":
                raise RuntimeError(result.error or result.status.value)
            if self.session is not None:
                self.session.global_preferences = result.value.preferences
            return result.value
        if patch.target == "preferences":
            current = self.project_store.load_preferences(self.workspace_id)
            value = current.value.preferences if current.value else Preferences()
            value = self._apply_many(value, patch.operations)
            result = self.project_store.write_preferences(
                self.workspace_id, value, expected_revision=current.revision or 0
            )
        elif patch.target == "profile":
            current = self.project_store.load_profile(self.workspace_id)
            if not current.value:
                raise ValueError("Profile 尚未创建")
            result = self.project_store.write_profile(
                self.workspace_id,
                self._apply_many(current.value.profile, patch.operations),
                expected_revision=current.revision,
            )
        else:
            current = self.project_store.load_handoff(self.workspace_id)
            if not current.value:
                raise ValueError("Handoff 尚未创建")
            result = self.project_store.write_handoff(
                self.workspace_id,
                self._apply_many(current.value.handoff, patch.operations),
                expected_revision=current.revision,
            )
        if result.status.value != "ok":
            raise RuntimeError(result.error or result.status.value)
        if self.session is not None:
            if patch.target == "preferences":
                self.session.workspace_preferences = result.value.preferences
            elif patch.target == "profile":
                self.session.profile = result.value.profile
            elif patch.target == "handoff" and self.session.is_continuation:
                self.session.loaded_handoff = result.value.handoff
        return result.value

    def _apply_many(self, value, operations):
        for operation in operations:
            value = self._apply(value, operation)
        return value


def extraction_result(result: str) -> ConfigExtractionResult:
    """Parse a bounded JSON result; callers decide whether to ask clarification."""
    import json

    try:
        payload = json.loads(result)
        return ConfigExtractionResult.model_validate(payload)
    except Exception:
        return ConfigExtractionResult(
            result="clarification_required", question="你希望保存到哪个作用域和字段？"
        )
