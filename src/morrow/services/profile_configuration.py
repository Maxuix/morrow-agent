"""Workspace Profile configuration service."""

from __future__ import annotations

from dataclasses import dataclass

from morrow.application.configuration import (
    ConfigurationChangeResult,
    ConfigurationChangeStatus,
    ConfigurationCommand,
    PreparedConfigurationChange,
    configuration_state_digest,
    render_configuration_preview,
)
from morrow.core.models import Profile, StateLoadStatus, StatePresence, StateWriteStatus


class ConfigurationValidationError(ValueError):
    """The typed configuration command is invalid for Profile."""


class ConfigurationReadOnlyError(RuntimeError):
    """The Profile document is corrupt, future-versioned, or read-only."""


class ConfigurationNotFoundError(ValueError):
    """A required Profile document does not exist."""


class ConfigurationConflictError(RuntimeError):
    """The Profile document changed after the command was prepared."""


class ConfigurationStateError(RuntimeError):
    """The Profile store rejected an otherwise valid write."""


class ConfigPatchService:
    """Prepare and apply optimistic single-field Profile changes."""

    def __init__(
        self, project_store, global_store, workspace_id: str, session=None, **_ignored
    ) -> None:
        del global_store
        self.project_store = project_store
        self.workspace_id = workspace_id
        self.session = session

    @dataclass(frozen=True)
    class _State:
        profile: Profile | None
        revision: int
        presence: StatePresence

    @staticmethod
    def _command(command: ConfigurationCommand) -> ConfigurationCommand:
        if isinstance(command, ConfigurationCommand):
            return command
        try:
            return ConfigurationCommand.model_validate(command, strict=True)
        except ValueError as exc:
            raise ConfigurationValidationError(str(exc)) from None

    def _state(self, command: ConfigurationCommand) -> _State:
        if command.scope != "workspace" or command.target != "profile":
            raise ConfigurationValidationError("配置服务只支持 workspace Profile")
        if self.session is not None and self.session.read_only:
            raise ConfigurationReadOnlyError("当前工作空间状态版本较新，只允许独立只读对话")
        current = self.project_store.load_profile(self.workspace_id)
        if current.status is not StateLoadStatus.OK:
            raise ConfigurationReadOnlyError("Profile 不可安全加载，已禁止覆盖")
        return self._State(
            current.value.profile if current.value is not None else None,
            current.revision or 0,
            current.presence,
        )

    @staticmethod
    def _normalize(value: object) -> str:
        return " ".join(str(value).split()).casefold()

    @classmethod
    def _candidate(cls, command: ConfigurationCommand, base: Profile) -> tuple[Profile, bool]:
        data = base.model_copy(deep=True)
        if command.path is None:
            raise ConfigurationValidationError("此操作需要 path")
        try:
            if command.operation == "set":
                setattr(data, command.path, command.value)
            elif command.operation == "unset":
                setattr(data, command.path, None)
            elif command.operation in {"append", "remove"}:
                if not isinstance(command.value, str):
                    raise ConfigurationValidationError("列表操作的 value 必须是字符串")
                values = list(getattr(data, command.path))
                matches = [
                    item for item in values if cls._normalize(item) == cls._normalize(command.value)
                ]
                if command.operation == "append":
                    if matches:
                        return base, False
                    values.append(command.value)
                elif not matches:
                    return base, False
                elif len(matches) > 1:
                    raise ConfigurationValidationError("删除目标必须精确匹配一个值")
                else:
                    values.remove(matches[0])
                setattr(data, command.path, values)
            else:
                raise ConfigurationValidationError("不支持的 Profile 操作")
            candidate = Profile.model_validate(data)
        except ConfigurationValidationError:
            raise
        except ValueError as exc:
            raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
        return candidate, candidate != base

    @classmethod
    def _inverse(
        cls, command: ConfigurationCommand, base: Profile | None
    ) -> ConfigurationCommand | None:
        if base is None or command.path is None or command.operation == "reset":
            return None
        if command.operation == "append":
            return command.model_copy(update={"operation": "remove"})
        if command.operation == "remove":
            matches = [
                item
                for item in getattr(base, command.path)
                if cls._normalize(item) == cls._normalize(command.value)
            ]
            return (
                command.model_copy(update={"operation": "append", "value": matches[0]})
                if len(matches) == 1
                else None
            )
        old_value = getattr(base, command.path)
        if command.operation == "unset":
            return (
                command.model_copy(update={"operation": "set", "value": old_value})
                if old_value is not None
                else None
            )
        if old_value is None:
            return (
                None
                if command.path == "name"
                else command.model_copy(update={"operation": "unset", "value": None})
            )
        return command.model_copy(update={"operation": "set", "value": old_value})

    def prepare(self, command: ConfigurationCommand, **_ignored) -> PreparedConfigurationChange:
        command = self._command(command)
        state = self._state(command)
        if command.operation == "reset":
            candidate = None
            changed = state.presence is StatePresence.PRESENT
            after_presence = StatePresence.CLEARED
        elif state.profile is None:
            if command.operation != "set" or command.path != "name":
                raise ConfigurationNotFoundError("Profile 尚未创建")
            try:
                candidate = Profile(name=command.value)
            except ValueError as exc:
                raise ConfigurationValidationError("配置值不符合字段类型或约束") from exc
            changed = True
            after_presence = StatePresence.PRESENT
        else:
            candidate, changed = self._candidate(command, state.profile)
            after_presence = StatePresence.PRESENT if changed else state.presence
        before_digest = configuration_state_digest(state.presence, state.profile)
        after_digest = configuration_state_digest(after_presence, candidate)
        return PreparedConfigurationChange(
            command=command,
            expected_revision=state.revision,
            before_presence=state.presence,
            before_digest=before_digest,
            after_digest=after_digest,
            expected_applied_revision=state.revision + 1 if changed else None,
            inverse_command=self._inverse(command, state.profile),
            changed=changed,
            preview_lines=tuple(render_configuration_preview(command)),
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

    def current_state(self, command: ConfigurationCommand) -> tuple[int, StatePresence, str]:
        state = self._state(self._command(command))
        return (
            state.revision,
            state.presence,
            configuration_state_digest(state.presence, state.profile),
        )

    def apply_prepared(
        self, prepared: PreparedConfigurationChange, *, operation_id: str
    ) -> ConfigurationChangeResult:
        if not operation_id.strip():
            raise ConfigurationValidationError("operation_id 不能为空")
        state = self._state(prepared.command)
        current_digest = configuration_state_digest(state.presence, state.profile)
        if (
            prepared.changed
            and current_digest == prepared.after_digest
            and state.revision == prepared.expected_applied_revision
        ):
            return self._result(prepared.command, ConfigurationChangeStatus.APPLIED, state.revision)
        if state.revision != prepared.expected_revision or current_digest != prepared.before_digest:
            raise ConfigurationConflictError("配置版本或内容已变化，请重新预览")
        if not prepared.changed:
            return self._result(
                prepared.command, ConfigurationChangeStatus.UNCHANGED, state.revision
            )
        if prepared.command.operation == "reset":
            written = self.project_store.clear_profile(
                self.workspace_id, expected_revision=state.revision
            )
            candidate = None
            presence = StatePresence.CLEARED
        else:
            if state.profile is None:
                candidate = Profile(name=prepared.command.value)
            else:
                candidate, _ = self._candidate(prepared.command, state.profile)
            written = self.project_store.write_profile(
                self.workspace_id, candidate, expected_revision=state.revision
            )
            presence = StatePresence.PRESENT
        if written.status is StateWriteStatus.REVISION_CONFLICT:
            raise ConfigurationConflictError("配置版本已变化，请重试")
        if written.status is not StateWriteStatus.OK:
            raise ConfigurationStateError("配置写入失败")
        if self.session is not None:
            self.session.profile = candidate
            self.session.profile_revision = written.revision or 0
            self.session.profile_presence = presence
        return self._result(prepared.command, ConfigurationChangeStatus.APPLIED, written.revision)

    def preflight(self, command: ConfigurationCommand) -> ConfigurationChangeResult:
        prepared = self.prepare(command)
        status = (
            ConfigurationChangeStatus.APPLIED
            if prepared.changed
            else ConfigurationChangeStatus.UNCHANGED
        )
        return self._result(prepared.command, status, prepared.expected_revision)

    def apply_command(self, command: ConfigurationCommand) -> ConfigurationChangeResult:
        prepared = self.prepare(command)
        return self.apply_prepared(prepared, operation_id=prepared.after_digest)
