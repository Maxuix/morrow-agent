"""Bounded argument validators for static and runtime-defined tools."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from morrow.core.domain import canonical_json_bytes, sha256_digest
from morrow.core.mcp.contracts import MCP_MAX_SCHEMA_BYTES

MAX_ARGUMENT_BYTES = 128 * 1024
MAX_SCHEMA_BYTES = MCP_MAX_SCHEMA_BYTES
MAX_SCHEMA_DEPTH = 16
MAX_VALUE_DEPTH = 32
MAX_OBJECT_PROPERTIES = 256
MAX_SCHEMA_PROPERTIES = 64
MAX_ARRAY_ITEMS = 256
MAX_STRING_CHARS = 64 * 1024
MAX_NUMBER_DIGITS = 309
MAX_SAFE_INTEGER = 10**MAX_NUMBER_DIGITS - 1
MAX_ENUM_VALUES = 64
MAX_COMBINATIONS = 16
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

_ANNOTATION_KEYWORDS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "format",
        "$comment",
    }
)
_SUPPORTED_KEYWORDS = _ANNOTATION_KEYWORDS | {
    "$schema",
    "$defs",
    "$ref",
    "type",
    "enum",
    "const",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "prefixItems",
    "minProperties",
    "maxProperties",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "anyOf",
    "oneOf",
    "allOf",
    "not",
}


class ToolArgumentsValidationError(ValueError):
    """Stable, bounded validation failure; raw argument values never enter it."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: tuple[dict[str, str], ...] = (),
        expected: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
        self.expected = expected


class ToolArgumentsValidator(Protocol):
    """One synchronous, bounded validation call shared by every RegisteredTool."""

    @property
    def schema(self) -> dict[str, Any] | bool: ...

    def validate(self, raw: str) -> object: ...


def _json_value(raw: str) -> object:
    if not isinstance(raw, str):
        raise ToolArgumentsValidationError("invalid_json", "工具参数必须是 JSON 文本")
    if len(raw.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ToolArgumentsValidationError("budget", "工具参数超过大小上限")
    try:
        value = json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_object_pairs)
        _check_value_budget(value, depth=0)
        return value
    except ToolArgumentsValidationError:
        raise
    except (TypeError, ValueError, RecursionError, json.JSONDecodeError):
        raise ToolArgumentsValidationError("invalid_json", "工具参数不是有效 JSON") from None


def _reject_constant(value: str) -> object:
    raise ToolArgumentsValidationError("invalid_json", "工具参数包含非法数字")


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ToolArgumentsValidationError("invalid_json", "工具参数包含重复字段")
        result[key] = value
    return result


def _check_value_budget(value: object, *, depth: int) -> None:
    if depth > MAX_VALUE_DEPTH:
        raise ToolArgumentsValidationError("budget", "工具参数嵌套过深")
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise ToolArgumentsValidationError("budget", "工具参数字符串超过大小上限")
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not _is_number(value) or (
            isinstance(value, int) and len(str(abs(value))) > MAX_NUMBER_DIGITS
        ):
            raise ToolArgumentsValidationError("budget", "工具参数数值超过上限")
        return
    if isinstance(value, dict):
        if len(value) > MAX_OBJECT_PROPERTIES:
            raise ToolArgumentsValidationError("budget", "工具参数字段数量超过上限")
        for name, item in value.items():
            _check_value_budget(name, depth=depth + 1)
            _check_value_budget(item, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_ARRAY_ITEMS:
            raise ToolArgumentsValidationError("budget", "工具参数数组长度超过上限")
        for item in value:
            _check_value_budget(item, depth=depth + 1)


def _path(path: tuple[str, ...]) -> str:
    return ".".join(path) or "$"


def _detail(path: tuple[str, ...], keyword: str) -> tuple[dict[str, str], ...]:
    return ({"path": _path(path), "type": keyword},)


def _schema_error(message: str) -> ToolArgumentsValidationError:
    return ToolArgumentsValidationError("schema_invalid", message)


def _is_number(value: object) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if not isinstance(value, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _normalize_generated_schema(node: Any) -> Any:
    """Make generated Pydantic bounds no looser than the raw argument budget."""

    if isinstance(node, list):
        return [_normalize_generated_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    normalized = {key: _normalize_generated_schema(value) for key, value in node.items()}
    node_type = normalized.get("type")
    if node_type == "string" or (isinstance(node_type, list) and "string" in node_type):
        current = normalized.get("maxLength")
        normalized["maxLength"] = (
            MAX_STRING_CHARS if current is None else min(current, MAX_STRING_CHARS)
        )
    if node_type == "array" or (isinstance(node_type, list) and "array" in node_type):
        current = normalized.get("maxItems")
        normalized["maxItems"] = (
            MAX_ARRAY_ITEMS if current is None else min(current, MAX_ARRAY_ITEMS)
        )
    if node_type == "object" or (isinstance(node_type, list) and "object" in node_type):
        current = normalized.get("maxProperties")
        normalized["maxProperties"] = (
            MAX_OBJECT_PROPERTIES if current is None else min(current, MAX_OBJECT_PROPERTIES)
        )
    if node_type == "integer":
        current = normalized.get("maximum")
        normalized["maximum"] = (
            MAX_SAFE_INTEGER if current is None else min(current, MAX_SAFE_INTEGER)
        )
    return normalized


def _pydantic_type_for_schema(node: Any) -> str | None:
    if isinstance(node, dict):
        schema_type = node.get("type")
        if schema_type == "integer":
            return "int_type"
        if schema_type == "number":
            return "float_type"
        if schema_type == "string":
            return "string_type"
        if schema_type == "boolean":
            return "bool_type"
        if schema_type == "array":
            return "list_type"
        if schema_type == "object":
            return "dict_type"
    return None


def _translate_schema_details(
    details: tuple[dict[str, str], ...], schema: Any
) -> tuple[dict[str, str], ...]:
    """Keep Pydantic-backed diagnostics compatible after the schema-first check."""

    if not isinstance(schema, dict):
        return details
    translated: list[dict[str, str]] = []
    for detail in details:
        if detail.get("type") != "type":
            translated.append(detail)
            continue
        node: Any = schema
        for part in detail.get("path", "").split("."):
            if part == "$" or not isinstance(node, dict):
                continue
            properties = node.get("properties")
            if isinstance(properties, dict) and part in properties:
                node = properties[part]
                continue
            if part.isdigit() and isinstance(node.get("items"), dict):
                node = node["items"]
                continue
            node = None
            break
        translated_type = _pydantic_type_for_schema(node)
        translated.append(
            {**detail, "type": translated_type} if translated_type is not None else detail
        )
    return tuple(translated)


@dataclass(frozen=True)
class PydanticArgumentsValidator:
    model: type[BaseModel]
    provider_schema: Mapping[str, Any] | bool | None = None
    expected_shape: str | None = None
    _compiled: Any = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.expected_shape is not None and (
            not isinstance(self.expected_shape, str)
            or not self.expected_shape
            or len(self.expected_shape) > 128
            or not self.expected_shape.isascii()
        ):
            raise ValueError("expected validation shape must be a bounded ASCII label")
        schema = (
            self.model.model_json_schema() if self.provider_schema is None else self.provider_schema
        )
        if self.provider_schema is None:
            schema = _normalize_generated_schema(schema)
        compiled = JsonSchemaArgumentsValidator(schema)
        normalized = compiled.schema
        if not isinstance(normalized, dict) or normalized.get("type") != "object":
            raise ValueError("Pydantic tool arguments schema must be an object schema")
        object.__setattr__(self, "_compiled", compiled)

    @property
    def schema(self) -> dict[str, Any]:
        return self._compiled.schema

    @property
    def schema_digest(self) -> str:
        return self._compiled.schema_digest

    def validate(self, raw: str) -> BaseModel:
        value = _json_value(raw)
        try:
            self._compiled.validate_value(value)
        except ToolArgumentsValidationError as exc:
            if self.expected_shape is not None and exc.expected is None:
                raise ToolArgumentsValidationError(
                    exc.code,
                    str(exc),
                    details=_translate_schema_details(exc.details, self.schema),
                    expected=self.expected_shape,
                ) from None
            if exc.details:
                raise ToolArgumentsValidationError(
                    exc.code,
                    str(exc),
                    details=_translate_schema_details(exc.details, self.schema),
                    expected=exc.expected,
                ) from None
            raise
        try:
            return self.model.model_validate_json(raw, strict=True)
        except ValidationError as exc:
            details = tuple(
                {
                    "path": ".".join(str(item) for item in error["loc"]) or "$",
                    "type": str(error["type"]),
                }
                for error in exc.errors(include_url=False)[:16]
            )
            raise ToolArgumentsValidationError(
                "validation_failed",
                "工具参数校验失败",
                details=details,
                expected=self.expected_shape,
            ) from None


class JsonSchemaArgumentsValidator:
    """A bounded JSON Schema subset with one explicit Draft 2020-12 dialect.

    The implementation intentionally rejects keywords it cannot prove safe. It is
    a dependency-free seam for the pre-MCP control plane, not a claim of full
    JSON Schema compatibility.
    """

    def __init__(self, schema: Mapping[str, Any] | bool) -> None:
        try:
            normalized = json.loads(json.dumps(schema, ensure_ascii=False, allow_nan=False))
            encoded = canonical_json_bytes(normalized)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise _schema_error("工具 Schema 不是有效 JSON") from None
        if isinstance(normalized, dict) and "$schema" not in normalized:
            normalized = {"$schema": SCHEMA_DIALECT, **normalized}
            encoded = canonical_json_bytes(normalized)
        if len(encoded) > MAX_SCHEMA_BYTES:
            raise _schema_error("工具 Schema 超过大小上限")
        self._schema = normalized
        self._defs: dict[str, Any] = {}
        self._refs: set[str] = set()
        self._check_schema(normalized, depth=0, root=True)
        if any(name not in self._defs for name in self._refs):
            raise _schema_error("工具 Schema 引用不存在")
        self._schema_digest = sha256_digest(encoded)

    @property
    def schema(self) -> dict[str, Any] | bool:
        return json.loads(json.dumps(self._schema, ensure_ascii=False))

    @property
    def schema_digest(self) -> str:
        return self._schema_digest

    def validate(self, raw: str) -> object:
        value = _json_value(raw)
        self.validate_value(value)
        return value

    def validate_value(self, value: object) -> None:
        _check_value_budget(value, depth=0)
        try:
            self._validate(self._schema, value, (), depth=0)
        except ToolArgumentsValidationError:
            raise

    def _check_schema(self, node: Any, *, depth: int, root: bool = False) -> None:
        if isinstance(node, bool):
            return
        if not isinstance(node, dict):
            raise _schema_error("工具 Schema 节点必须是对象或布尔值")
        if depth > MAX_SCHEMA_DEPTH:
            raise _schema_error("工具 Schema 嵌套过深")
        unknown = set(node) - _SUPPORTED_KEYWORDS
        if unknown:
            raise _schema_error("工具 Schema 含不支持的关键词")
        if "$schema" in node and node["$schema"] != SCHEMA_DIALECT:
            raise _schema_error("工具 Schema 方言不受支持")
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/$defs/") or ref.count("/") != 2:
                raise _schema_error("工具 Schema 禁止远程或非本地引用")
            self._refs.add(ref.removeprefix("#/$defs/"))
        if "$defs" in node:
            definitions = node["$defs"]
            if not isinstance(definitions, dict) or len(definitions) > MAX_SCHEMA_PROPERTIES:
                raise _schema_error("工具 Schema definitions 超出上限")
            for name, definition in definitions.items():
                if not isinstance(name, str) or not name or len(name) > 128:
                    raise _schema_error("工具 Schema definition 名称无效")
                self._defs[name] = definition
                self._check_schema(definition, depth=depth + 1)
        self._check_type(node.get("type"))
        self._check_enum(node.get("enum"))
        self._check_properties(node, depth)
        for keyword in ("items", "not", "additionalProperties"):
            value = node.get(keyword)
            if isinstance(value, (dict, bool)):
                self._check_schema(value, depth=depth + 1)
            elif value is not None:
                raise _schema_error("工具 Schema 节点无效")
        prefix = node.get("prefixItems")
        if prefix is not None:
            if not isinstance(prefix, list) or len(prefix) > MAX_ARRAY_ITEMS:
                raise _schema_error("工具 Schema prefixItems 超出上限")
            for item in prefix:
                self._check_schema(item, depth=depth + 1)
        for keyword in ("anyOf", "oneOf", "allOf"):
            values = node.get(keyword)
            if values is not None:
                if not isinstance(values, list) or not 1 <= len(values) <= MAX_COMBINATIONS:
                    raise _schema_error("工具 Schema 组合项超出上限")
                for item in values:
                    self._check_schema(item, depth=depth + 1)
        self._check_bounds(node)

    @staticmethod
    def _check_type(value: Any) -> None:
        allowed = {"null", "boolean", "object", "array", "number", "integer", "string"}
        if value is not None and (
            not isinstance(value, (str, list))
            or (isinstance(value, str) and value not in allowed)
            or (isinstance(value, list) and not value)
            or (isinstance(value, list) and any(item not in allowed for item in value))
        ):
            raise _schema_error("工具 Schema type 无效")

    @staticmethod
    def _check_enum(value: Any) -> None:
        if value is not None and (not isinstance(value, list) or len(value) > MAX_ENUM_VALUES):
            raise _schema_error("工具 Schema enum 超出上限")

    def _check_properties(self, node: dict[str, Any], depth: int) -> None:
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict) or len(properties) > MAX_SCHEMA_PROPERTIES:
                raise _schema_error("工具 Schema properties 超出上限")
            for name, child in properties.items():
                if not isinstance(name, str) or len(name) > 128:
                    raise _schema_error("工具 Schema 属性名无效")
                self._check_schema(child, depth=depth + 1)
        required = node.get("required")
        if required is not None:
            if not isinstance(required, list) or len(required) > MAX_SCHEMA_PROPERTIES:
                raise _schema_error("工具 Schema required 超出上限")
            if not all(isinstance(item, str) for item in required) or len(required) != len(
                set(required)
            ):
                raise _schema_error("工具 Schema required 无效")

    @staticmethod
    def _check_bounds(node: dict[str, Any]) -> None:
        limits = {
            "maxProperties": MAX_OBJECT_PROPERTIES,
            "maxItems": MAX_ARRAY_ITEMS,
            "maxLength": MAX_STRING_CHARS,
        }
        for keyword, maximum in limits.items():
            value = node.get(keyword)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > maximum
            ):
                raise _schema_error("工具 Schema bound 超出上限")
        minimums = {
            "minProperties": MAX_OBJECT_PROPERTIES,
            "minItems": MAX_ARRAY_ITEMS,
            "minLength": MAX_STRING_CHARS,
        }
        for keyword, maximum in minimums.items():
            value = node.get(keyword)
            if value is not None and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or value > maximum
            ):
                raise _schema_error("工具 Schema bound 超出上限")
        for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
            value = node.get(keyword)
            if value is not None and (
                not _is_number(value)
                or (isinstance(value, int) and len(str(abs(value))) > MAX_NUMBER_DIGITS)
            ):
                raise _schema_error("工具 Schema numeric bound 无效")
        if node.get("multipleOf") is not None and node["multipleOf"] <= 0:
            raise _schema_error("工具 Schema multipleOf 无效")
        pattern = node.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or len(pattern) > 1024:
                raise _schema_error("工具 Schema pattern 无效")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise _schema_error("工具 Schema pattern 无效") from exc

    def _validate(self, node: Any, value: Any, path: tuple[str, ...], *, depth: int) -> None:
        if depth > MAX_VALUE_DEPTH:
            raise ToolArgumentsValidationError(
                "budget", "工具参数嵌套过深", details=_detail(path, "depth")
            )
        if isinstance(node, bool):
            if not node:
                raise ToolArgumentsValidationError(
                    "validation_failed",
                    "工具参数不符合 Schema",
                    details=_detail(path, "false_schema"),
                )
            return
        if "$ref" in node:
            ref_name = node["$ref"].removeprefix("#/$defs/")
            definition = self._defs.get(ref_name)
            if definition is None:
                raise ToolArgumentsValidationError("schema_invalid", "工具 Schema 引用不存在")
            self._validate(definition, value, path, depth=depth + 1)
        for keyword in ("allOf", "anyOf", "oneOf"):
            variants = node.get(keyword)
            if variants is None:
                continue
            matches = 0
            failures: list[ToolArgumentsValidationError] = []
            for variant in variants:
                try:
                    self._validate(variant, value, path, depth=depth + 1)
                    matches += 1
                except ToolArgumentsValidationError as exc:
                    failures.append(exc)
            if keyword == "allOf" and failures:
                raise failures[0]
            if keyword == "anyOf" and matches == 0:
                raise ToolArgumentsValidationError(
                    "validation_failed", "工具参数不符合 Schema", details=_detail(path, "anyOf")
                )
            if keyword == "oneOf" and matches != 1:
                raise ToolArgumentsValidationError(
                    "validation_failed", "工具参数不符合 Schema", details=_detail(path, "oneOf")
                )
        if "not" in node:
            try:
                self._validate(node["not"], value, path, depth=depth + 1)
            except ToolArgumentsValidationError:
                pass
            else:
                raise ToolArgumentsValidationError(
                    "validation_failed", "工具参数不符合 Schema", details=_detail(path, "not")
                )
        if "const" in node and value != node["const"]:
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数不符合 Schema", details=_detail(path, "const")
            )
        if "enum" in node and value not in node["enum"]:
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数不符合 Schema", details=_detail(path, "enum")
            )
        expected = node.get("type")
        if expected is not None and not any(
            self._type_matches(item, value)
            for item in (expected if isinstance(expected, list) else [expected])
        ):
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数不符合 Schema", details=_detail(path, "type")
            )
        if isinstance(value, dict):
            self._validate_object(node, value, path, depth)
        elif isinstance(value, list):
            self._validate_array(node, value, path, depth)
        elif isinstance(value, str):
            self._validate_string(node, value, path)
        elif _is_number(value):
            self._validate_number(node, value, path)

    @staticmethod
    def _type_matches(expected: str, value: Any) -> bool:
        return {
            "null": value is None,
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "number": _is_number(value),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "string": isinstance(value, str),
        }.get(expected, False)

    def _validate_object(self, node, value, path, depth) -> None:
        properties = node.get("properties", {})
        required = node.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ToolArgumentsValidationError(
                "validation_failed",
                "工具参数缺少必填字段",
                details=tuple(
                    detail for name in missing[:16] for detail in _detail((*path, name), "missing")
                ),
            )
        if (
            len(value) > MAX_OBJECT_PROPERTIES
            or len(value) < node.get("minProperties", 0)
            or (node.get("maxProperties") is not None and len(value) > node["maxProperties"])
        ):
            raise ToolArgumentsValidationError(
                "validation_failed",
                "工具参数字段数量不符合 Schema",
                details=_detail(path, "properties"),
            )
        additional = node.get("additionalProperties", True)
        for name, item in value.items():
            child_path = (*path, str(name))
            if name in properties:
                self._validate(properties[name], item, child_path, depth=depth + 1)
            elif additional is False:
                raise ToolArgumentsValidationError(
                    "validation_failed",
                    "工具参数包含未声明字段",
                    details=_detail(child_path, "additionalProperties"),
                )
            elif isinstance(additional, (dict, bool)):
                self._validate(additional, item, child_path, depth=depth + 1)

    def _validate_array(self, node, value, path, depth) -> None:
        if (
            len(value) > MAX_ARRAY_ITEMS
            or len(value) < node.get("minItems", 0)
            or (node.get("maxItems") is not None and len(value) > node["maxItems"])
        ):
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数数组长度不符合 Schema", details=_detail(path, "items")
            )
        if node.get("uniqueItems") and len(
            {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value}
        ) != len(value):
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数数组包含重复项", details=_detail(path, "uniqueItems")
            )
        prefix = node.get("prefixItems", [])
        for index, item in enumerate(value):
            if index < len(prefix):
                self._validate(prefix[index], item, (*path, str(index)), depth=depth + 1)
            elif "items" in node:
                self._validate(node["items"], item, (*path, str(index)), depth=depth + 1)

    @staticmethod
    def _validate_string(node, value, path) -> None:
        length = len(value)
        if (
            length > MAX_STRING_CHARS
            or length < node.get("minLength", 0)
            or (node.get("maxLength") is not None and length > node["maxLength"])
        ):
            raise ToolArgumentsValidationError(
                "validation_failed",
                "工具参数字符串长度不符合 Schema",
                details=_detail(path, "length"),
            )
        pattern = node.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数字符串不符合 Schema", details=_detail(path, "pattern")
            )

    @staticmethod
    def _validate_number(node, value, path) -> None:
        checks = (
            ("minimum", lambda bound: value >= bound),
            ("maximum", lambda bound: value <= bound),
            ("exclusiveMinimum", lambda bound: value > bound),
            ("exclusiveMaximum", lambda bound: value < bound),
        )
        if any(keyword in node and not check(node[keyword]) for keyword, check in checks):
            raise ToolArgumentsValidationError(
                "validation_failed", "工具参数数值不符合 Schema", details=_detail(path, "number")
            )
        multiple = node.get("multipleOf")
        if multiple is not None and not math.isclose(
            value / multiple, round(value / multiple), rel_tol=0, abs_tol=1e-9
        ):
            raise ToolArgumentsValidationError(
                "validation_failed",
                "工具参数数值不符合 Schema",
                details=_detail(path, "multipleOf"),
            )


__all__ = [
    "JsonSchemaArgumentsValidator",
    "MAX_ARGUMENT_BYTES",
    "MAX_NUMBER_DIGITS",
    "MAX_SAFE_INTEGER",
    "PydanticArgumentsValidator",
    "ToolArgumentsValidationError",
    "ToolArgumentsValidator",
]
