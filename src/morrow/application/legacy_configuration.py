"""Compatibility validator for historical fixed-field configuration commands."""

from __future__ import annotations

import math

LEGACY_PREFERENCE_PATHS = frozenset({"language", "response_detail", "instructions"})
PROFILE_PATHS = frozenset({"name", "summary", "goals", "tech_stack", "constraints", "conventions"})
LEGACY_ALLOWED_PATHS: dict[tuple[str, str], frozenset[str]] = {
    ("session", "preferences"): LEGACY_PREFERENCE_PATHS,
    ("workspace", "preferences"): LEGACY_PREFERENCE_PATHS,
    ("global", "preferences"): LEGACY_PREFERENCE_PATHS,
    ("workspace", "profile"): PROFILE_PATHS,
}
LEGACY_LIST_PATHS = frozenset({"instructions", "goals", "tech_stack", "constraints", "conventions"})


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def validate_legacy_configuration_fields(model, *, validate_values: bool = True) -> None:
    """Validate persisted v12 commands used only by history/Promotion compatibility."""

    scope = model.scope
    target = model.target
    operation = model.operation
    path = model.path
    fields = model.model_fields_set
    allowed = LEGACY_ALLOWED_PATHS.get((scope, target))
    if allowed is None:
        raise ValueError("不允许修改此作用域或目标")
    if operation == "reset":
        if path is not None or model.value is not None:
            raise ValueError("reset 不接受 path 或 value")
        return
    if path is None or not path.strip():
        raise ValueError("此操作需要 path")
    if path not in allowed:
        raise ValueError(f"不允许修改字段: {path}")
    if target == "profile" and path == "name" and operation == "unset":
        raise ValueError("Profile 的 name 不能取消设置")
    if operation == "unset":
        if model.value is not None:
            raise ValueError("unset 不接受 value")
        if path in LEGACY_LIST_PATHS:
            raise ValueError("列表字段只能使用 append 或 remove")
        return
    if "value" not in fields:
        raise ValueError(f"{operation} 操作需要 value")
    if not _is_json_value(model.value):
        raise ValueError("value 必须是有限的 JSON 值")
    is_list = path in LEGACY_LIST_PATHS
    if validate_values and not is_list and path in {"language", "name", "summary"}:
        if not isinstance(model.value, str) or not model.value.strip():
            raise ValueError(f"{path} 必须是非空字符串")
        maximum = 128 if path == "language" else 2_048
        if len(model.value) > maximum:
            raise ValueError(f"{path} 超出长度限制")
    if (
        validate_values
        and is_list
        and (not isinstance(model.value, str) or not model.value.strip())
    ):
        raise ValueError(f"{path} 的值必须是非空字符串")
    if validate_values and is_list and isinstance(model.value, str) and len(model.value) > 512:
        raise ValueError(f"{path} 的值超出长度限制")
    if operation in {"append", "remove"} and not is_list:
        raise ValueError("标量字段只能使用 set 或 unset")
    if operation == "set" and is_list:
        raise ValueError("列表字段只能使用 append 或 remove")


__all__ = ["LEGACY_ALLOWED_PATHS", "PROFILE_PATHS", "validate_legacy_configuration_fields"]
