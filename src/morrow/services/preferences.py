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
    PreparedConfigurationChange,
    configuration_state_digest,
    render_configuration_preview,
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
        presence: StatePresence

    @dataclass(frozen=True)
    class _OperationPlan:
        command: ConfigurationCommand
        state: ConfigPatchService._TargetState
        candidate: Preferences | Profile | None
        changed: bool
        after_presence: StatePresence
        inverse_command: ConfigurationCommand | None

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
            return self._TargetState(
                current.value.preferences,
                current.revision or 0,
                StatePresence.PRESENT,
            )
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
            return self._OperationPlan(
                command,
                state,
                candidate,
                changed,
                StatePresence.CLEARED if command.scope == "workspace" else StatePresence.PRESENT,
                None,
            )
        if state.base is None:
            if not (
                command.scope == "workspace"
                and command.target == "profile"
                and command.operation == "set"
                and command.path == "name"
                and state.presence in {StatePresence.MISSING, StatePresence.CLEARED}
            ):
                raise ConfigurationNotFoundError("Profile 尚未创建")
            try:
                base: Preferences | Profile = Profile(name=command.value)
            except ValueError as exc:
                raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
        else:
            base = state.base
        candidate, changed = self._candidate(command, base)
        return self._OperationPlan(
            command,
            state,
            candidate,
            changed,
            StatePresence.PRESENT if changed else state.presence,
            self._inverse_command(command, state.base),
        )

    def _prepare(self, command: ConfigurationCommand) -> _OperationPlan:
        command = self._command(command)
        return self._prepare_from_state(command, self._target_state(command))

    @staticmethod
    def _command(command: ConfigurationCommand) -> ConfigurationCommand:
        if isinstance(command, ConfigurationCommand):
            return command
        try:
            return ConfigurationCommand.model_validate(command, strict=True)
        except ValueError as exc:
            raise ConfigurationValidationError(str(exc)) from None

    @classmethod
    def _inverse_command(
        cls,
        command: ConfigurationCommand,
        base: Preferences | Profile | None,
    ) -> ConfigurationCommand | None:
        """Build a one-field inverse; never snapshot an entire YAML document."""

        if base is None or command.path is None or command.operation == "reset":
            return None
        if command.operation == "append":
            return ConfigurationCommand(
                scope=command.scope,
                target=command.target,
                operation="remove",
                path=command.path,
                value=command.value,
            )
        if command.operation == "remove":
            values = list(getattr(base, command.path))
            normalized = cls._normalize_item(command.value)
            matches = [item for item in values if cls._normalize_item(item) == normalized]
            if len(matches) != 1:
                return None
            return ConfigurationCommand(
                scope=command.scope,
                target=command.target,
                operation="append",
                path=command.path,
                value=matches[0],
            )
        old_value = getattr(base, command.path)
        if command.operation == "unset":
            if old_value is None:
                return None
            return ConfigurationCommand(
                scope=command.scope,
                target=command.target,
                operation="set",
                path=command.path,
                value=old_value,
            )
        if old_value is None:
            if command.target == "profile" and command.path == "name":
                return None
            return ConfigurationCommand(
                scope=command.scope,
                target=command.target,
                operation="unset",
                path=command.path,
            )
        return ConfigurationCommand(
            scope=command.scope,
            target=command.target,
            operation="set",
            path=command.path,
            value=old_value,
        )

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
                self.session.global_preferences_revision = result.revision or 0
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
                self.session.preferences_revision = result.revision or 0
                self.session.workspace_preferences_presence = (
                    StatePresence.CLEARED if command.operation == "reset" else StatePresence.PRESENT
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
            self.session.profile_revision = result.revision or 0
            self.session.profile_presence = (
                StatePresence.CLEARED if command.operation == "reset" else StatePresence.PRESENT
            )
        return result.revision

    def _sync_projection(
        self,
        command: ConfigurationCommand,
        state: _TargetState,
    ) -> None:
        """Refresh an attached Session after a proven idempotent external write."""

        if self.session is None:
            return
        if command.scope == "session":
            if isinstance(state.base, Preferences):
                self.session.preferences = state.base
            return
        if command.scope == "global":
            if isinstance(state.base, Preferences):
                self.session.global_preferences = state.base
            self.session.global_preferences_revision = state.revision or 0
            return
        if command.target == "preferences":
            self.session.workspace_preferences = (
                state.base if isinstance(state.base, Preferences) else Preferences()
            )
            self.session.preferences_revision = state.revision or 0
            self.session.workspace_preferences_presence = state.presence
            return
        self.session.profile = state.base if isinstance(state.base, Profile) else None
        self.session.profile_revision = state.revision or 0
        self.session.profile_presence = state.presence

    def preflight(self, command: ConfigurationCommand) -> ConfigurationChangeResult:
        plan = self.prepare(command)
        status = (
            ConfigurationChangeStatus.APPLIED
            if plan.changed
            else ConfigurationChangeStatus.UNCHANGED
        )
        return self._result(plan.command, status, plan.expected_revision)

    def prepare(self, command: ConfigurationCommand) -> PreparedConfigurationChange:
        """Read and freeze the exact optimistic-concurrency evidence for one write."""

        plan = self._prepare(command)
        before_digest = configuration_state_digest(plan.state.presence, plan.state.base)
        after_digest = configuration_state_digest(plan.after_presence, plan.candidate)
        expected_applied_revision = (
            plan.state.revision + 1 if plan.changed and plan.state.revision is not None else None
        )
        return PreparedConfigurationChange(
            command=plan.command,
            expected_revision=plan.state.revision,
            before_presence=plan.state.presence,
            before_digest=before_digest,
            after_digest=after_digest,
            expected_applied_revision=expected_applied_revision,
            inverse_command=plan.inverse_command,
            changed=plan.changed,
            preview_lines=tuple(render_configuration_preview(plan.command)),
        )

    def current_state(self, command: ConfigurationCommand) -> tuple[int | None, StatePresence, str]:
        """Return only revision/presence/digest evidence for Saga finalization and recovery."""

        state = self._target_state(self._command(command))
        return (
            state.revision,
            state.presence,
            configuration_state_digest(state.presence, state.base),
        )

    def apply_prepared(
        self,
        prepared: PreparedConfigurationChange,
        *,
        operation_id: str,
    ) -> ConfigurationChangeResult:
        """Apply only after proving the prepared before-state still owns the target."""

        if not operation_id or not operation_id.strip():
            raise ConfigurationValidationError("operation_id 不能为空")
        if not isinstance(prepared, PreparedConfigurationChange):
            try:
                prepared = PreparedConfigurationChange.model_validate(prepared, strict=True)
            except ValueError as exc:
                raise ConfigurationValidationError("Prepared configuration 无效") from exc
        state = self._target_state(prepared.command)
        current_digest = configuration_state_digest(state.presence, state.base)
        if (
            prepared.changed
            and current_digest == prepared.after_digest
            and (
                state.revision == prepared.expected_applied_revision
                or prepared.command.scope == "session"
            )
        ):
            self._sync_projection(prepared.command, state)
            return self._result(
                prepared.command,
                ConfigurationChangeStatus.APPLIED,
                state.revision,
            )
        if state.revision != prepared.expected_revision or current_digest != prepared.before_digest:
            raise ConfigurationConflictError("配置版本或内容已变化，请重新预览")
        plan = self._prepare_from_state(prepared.command, state)
        recomputed_after = configuration_state_digest(plan.after_presence, plan.candidate)
        if recomputed_after != prepared.after_digest or plan.changed != prepared.changed:
            raise ConfigurationConflictError("配置预览已失效，请重新预览")
        if not plan.changed:
            return self._result(
                prepared.command,
                ConfigurationChangeStatus.UNCHANGED,
                state.revision,
            )
        revision = self._commit_target(state, plan.command, plan.candidate)
        if (
            prepared.expected_applied_revision is not None
            and revision != prepared.expected_applied_revision
        ):
            raise ConfigurationConflictError("配置写入版本异常，请检查当前状态")
        return self._result(prepared.command, ConfigurationChangeStatus.APPLIED, revision)

    def apply_command(self, command: ConfigurationCommand) -> ConfigurationChangeResult:
        prepared = self.prepare(command)
        return self.apply_prepared(prepared, operation_id=prepared.after_digest)

    def _apply_patch(self, patch: ConfigPatch) -> tuple[ConfigurationChangeResult, ...]:
        commands = _commands_from_patch(patch)
        state = self._target_state(commands[0])
        current = state.base
        plans: list[ConfigPatchService._OperationPlan] = []
        for command in commands:
            plan = self._prepare_from_state(
                command,
                self._TargetState(
                    current,
                    state.revision,
                    state.presence if not plans else plans[-1].after_presence,
                ),
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
