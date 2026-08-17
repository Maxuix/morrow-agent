"""Three-layer Preferences/Profile configuration application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from morrow.application.configuration import (
    ALLOWED_PATHS as _ALLOWED_PATHS,
)
from morrow.application.configuration import (
    ConfigurationChangeResult,
    ConfigurationChangeStatus,
    ConfigurationCommand,
)
from morrow.core.models import (
    ConfigPatch,
    ConfigPatchOperation,
    Preferences,
    Profile,
    StateLoadStatus,
    StatePresence,
    StateWriteStatus,
)

ALLOWED_PATHS = _ALLOWED_PATHS


class ConfigurationValidationError(ValueError):
    """The typed configuration command is invalid for the current domain."""


class ConfigurationReadOnlyError(RuntimeError):
    """The relevant state layer is corrupt, future-versioned, or read-only."""


class ConfigurationNotFoundError(ValueError):
    """A required Profile document does not exist."""


class ConfigurationConflictError(RuntimeError):
    """A state document changed after the command was prepared."""


class ConfigurationStateError(RuntimeError):
    """A state store rejected an otherwise valid configuration write."""


def _legacy_command(patch: ConfigPatch, operation: ConfigPatchOperation) -> ConfigurationCommand:
    if operation.op == "unset":
        if "value" in operation.model_fields_set:
            raise ConfigurationValidationError("unset 不接受 value")
    elif "value" not in operation.model_fields_set:
        raise ConfigurationValidationError("此操作需要 value")
    payload: dict[str, Any] = {
        "scope": patch.scope,
        "target": patch.target,
        "operation": operation.op,
        "path": operation.path,
    }
    if operation.op != "unset":
        payload["value"] = operation.value
    try:
        return ConfigurationCommand.model_validate(payload, strict=True)
    except ValueError as exc:
        raise ConfigurationValidationError(str(exc)) from None


def _commands_from_patch(patch: ConfigPatch) -> tuple[ConfigurationCommand, ...]:
    if not patch.operations:
        raise ConfigurationValidationError("配置操作不能为空")
    return tuple(_legacy_command(patch, operation) for operation in patch.operations)


def validate_patch(patch: ConfigPatch) -> None:
    """Validate the unchanged legacy patch shape through the new command authority."""
    _commands_from_patch(patch)


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

    @dataclass(frozen=True)
    class _TargetState:
        base: Preferences | Profile | None
        revision: int | None
        presence: StatePresence | None

    @dataclass(frozen=True)
    class _OperationPlan:
        command: ConfigurationCommand
        state: ConfigPatchService._TargetState
        candidate: Preferences | Profile | None
        changed: bool

    @staticmethod
    def _normalize_item(value: object) -> str:
        return " ".join(str(value).split()).casefold()

    @classmethod
    def _candidate(
        cls,
        command: ConfigurationCommand,
        base: Preferences | Profile,
    ) -> tuple[Preferences | Profile, bool]:
        data = base.model_copy(deep=True)
        path = command.path
        if path is None:
            raise ConfigurationValidationError("此操作需要 path")
        if command.operation == "set":
            try:
                setattr(data, path, command.value)
            except ValueError as exc:
                raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
        elif command.operation == "unset":
            try:
                setattr(data, path, None)
            except ValueError as exc:
                raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
        elif command.operation in {"append", "remove"}:
            if not isinstance(command.value, str):
                raise ConfigurationValidationError("列表操作的 value 必须是字符串")
            values = list(getattr(data, path))
            normalized = cls._normalize_item(command.value)
            matches = [item for item in values if cls._normalize_item(item) == normalized]
            if command.operation == "append":
                if matches:
                    return base, False
                values.append(command.value)
                try:
                    setattr(data, path, values)
                except ValueError as exc:
                    raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
            elif not matches:
                return base, False
            elif len(matches) > 1:
                raise ConfigurationValidationError("删除目标必须精确匹配一个值")
            else:
                values.remove(matches[0])
                try:
                    setattr(data, path, values)
                except ValueError as exc:
                    raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
        try:
            candidate = type(base).model_validate(data)
        except ValueError as exc:
            raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
        return candidate, candidate != base

    def _target_state(self, command: ConfigurationCommand) -> _TargetState:
        if command.scope == "session":
            if self.session is None:
                raise ConfigurationValidationError("session scope is unavailable")
            return self._TargetState(self.session.preferences, None, StatePresence.PRESENT)
        if command.scope == "global":
            current = self.global_store.load()
            if current.status != StateLoadStatus.OK or current.value is None:
                raise ConfigurationStateError("全局配置不可安全加载")
            return self._TargetState(current.value.preferences, current.revision or 0, None)
        if self.session is not None and self.session.read_only:
            raise ConfigurationReadOnlyError("当前工作空间状态版本较新，只允许独立只读对话")
        if (
            command.target == "preferences"
            and self.session is not None
            and self.session.workspace_preferences_read_only
        ):
            raise ConfigurationReadOnlyError("工作空间 Preferences 不可安全加载，已禁止覆盖")
        if command.target == "preferences":
            current = self.project_store.load_preferences(self.workspace_id)
        else:
            current = self.project_store.load_profile(self.workspace_id)
        if current.status != StateLoadStatus.OK:
            raise ConfigurationReadOnlyError("工作空间状态不可安全加载，已禁止覆盖")
        if command.target == "preferences":
            base = current.value.preferences if current.value else Preferences()
        else:
            base = current.value.profile if current.value else None
        return self._TargetState(base, current.revision or 0, current.presence)

    def _prepare_from_state(
        self,
        command: ConfigurationCommand,
        state: _TargetState,
    ) -> _OperationPlan:
        if command.operation == "reset":
            if command.scope == "workspace":
                changed = state.presence == StatePresence.PRESENT
                candidate = None if command.target == "profile" else Preferences()
            elif command.target == "preferences":
                candidate = Preferences()
                changed = state.base != candidate
            else:
                raise ConfigurationValidationError("不允许重置此目标")
            return self._OperationPlan(command, state, candidate, changed)
        if state.base is None:
            raise ConfigurationNotFoundError("Profile 尚未创建")
        candidate, changed = self._candidate(command, state.base)
        return self._OperationPlan(command, state, candidate, changed)

    def _prepare(self, command: ConfigurationCommand) -> _OperationPlan:
        if not isinstance(command, ConfigurationCommand):
            try:
                command = ConfigurationCommand.model_validate(command, strict=True)
            except ValueError as exc:
                raise ConfigurationValidationError(str(exc)) from None
        return self._prepare_from_state(command, self._target_state(command))

    @staticmethod
    def _result(
        command: ConfigurationCommand,
        status: ConfigurationChangeStatus,
        revision: int | None,
    ) -> ConfigurationChangeResult:
        return ConfigurationChangeResult(
            status=status,
            scope=command.scope,
            target=command.target,
            operation=command.operation,
            path=command.path,
            revision=revision,
        )

    @staticmethod
    def _raise_write_failure(result) -> None:
        if result.status == StateWriteStatus.REVISION_CONFLICT:
            raise ConfigurationConflictError("配置版本已变化，请重试")
        if result.status != StateWriteStatus.OK:
            raise ConfigurationStateError("配置写入失败")

    def _commit_target(
        self,
        state: _TargetState,
        command: ConfigurationCommand,
        candidate: Preferences | Profile | None,
    ) -> int | None:
        if command.scope == "session":
            if not isinstance(candidate, Preferences):
                raise ConfigurationStateError("session Preferences 状态无效")
            self.session.preferences = candidate
            return None
        if command.scope == "global":
            if not isinstance(candidate, Preferences):
                raise ConfigurationStateError("全局 Preferences 状态无效")
            result = self.global_store.update(
                lambda config: config.model_copy(update={"preferences": candidate}),
                expected_revision=state.revision,
            )
            self._raise_write_failure(result)
            if self.session is not None:
                self.session.global_preferences = result.value.preferences
            return result.revision
        if command.target == "preferences":
            if command.operation == "reset":
                result = self.project_store.clear_preferences(
                    self.workspace_id, expected_revision=state.revision
                )
            else:
                if not isinstance(candidate, Preferences):
                    raise ConfigurationStateError("workspace Preferences 状态无效")
                result = self.project_store.write_preferences(
                    self.workspace_id, candidate, expected_revision=state.revision
                )
            self._raise_write_failure(result)
            if self.session is not None:
                self.session.workspace_preferences = (
                    Preferences() if command.operation == "reset" else result.value.preferences
                )
            return result.revision
        if command.operation == "reset":
            result = self.project_store.clear_profile(
                self.workspace_id, expected_revision=state.revision
            )
        else:
            if not isinstance(candidate, Profile):
                raise ConfigurationStateError("Profile 状态无效")
            result = self.project_store.write_profile(
                self.workspace_id, candidate, expected_revision=state.revision
            )
        self._raise_write_failure(result)
        if self.session is not None:
            self.session.profile = None if command.operation == "reset" else result.value.profile
        return result.revision

    def preflight(self, command: ConfigurationCommand) -> ConfigurationChangeResult:
        plan = self._prepare(command)
        status = (
            ConfigurationChangeStatus.APPLIED
            if plan.changed
            else ConfigurationChangeStatus.UNCHANGED
        )
        return self._result(plan.command, status, plan.state.revision)

    def apply_command(self, command: ConfigurationCommand) -> ConfigurationChangeResult:
        plan = self._prepare(command)
        if not plan.changed:
            return self._result(
                plan.command, ConfigurationChangeStatus.UNCHANGED, plan.state.revision
            )
        revision = self._commit_target(plan.state, plan.command, plan.candidate)
        return self._result(plan.command, ConfigurationChangeStatus.APPLIED, revision)

    def _apply_patch(self, patch: ConfigPatch) -> tuple[ConfigurationChangeResult, ...]:
        commands = _commands_from_patch(patch)
        state = self._target_state(commands[0])
        current = state.base
        plans: list[ConfigPatchService._OperationPlan] = []
        for command in commands:
            plan = self._prepare_from_state(
                command, self._TargetState(current, state.revision, state.presence)
            )
            plans.append(plan)
            current = plan.candidate
        if not any(plan.changed for plan in plans) or current == state.base:
            return tuple(
                self._result(plan.command, ConfigurationChangeStatus.UNCHANGED, state.revision)
                for plan in plans
            )
        final_command = next(plan.command for plan in reversed(plans) if plan.changed)
        revision = self._commit_target(state, final_command, current)
        return tuple(
            self._result(
                plan.command,
                ConfigurationChangeStatus.APPLIED
                if plan.changed
                else ConfigurationChangeStatus.UNCHANGED,
                revision,
            )
            for plan in plans
        )

    def apply(
        self, patch: ConfigPatch | ConfigurationCommand
    ) -> ConfigurationChangeResult | tuple[ConfigurationChangeResult, ...]:
        if isinstance(patch, ConfigurationCommand):
            return self.apply_command(patch)
        if not isinstance(patch, ConfigPatch):
            raise ConfigurationValidationError("配置补丁类型无效")
        return self._apply_patch(patch)
