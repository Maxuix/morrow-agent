"""SDK-agnostic desktop observe/act contracts.

This module is the permission and device boundary for computer use. It does not
import a native driver, construct a session, or register model tools.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from morrow.core.capabilities import LocalCapabilityModel
from morrow.core.domain import (
    AGENT_RUN_ID_PREFIX,
    ARTIFACT_ID_PREFIX,
    COMPUTER_ELEMENT_ID_PREFIX,
    COMPUTER_OBSERVATION_ID_PREFIX,
    COMPUTER_PROCESS_ID_PREFIX,
    COMPUTER_RUN_ID_PREFIX,
    COMPUTER_TARGET_ID_PREFIX,
    COMPUTER_WINDOW_ID_PREFIX,
    DIGEST_PATTERN,
    TASK_RUN_ID_PREFIX,
    WORKSPACE_ID_PREFIX,
    refuse_secret_material,
    validate_prefixed_id,
)
from morrow.core.models import ToolEffect
from morrow.core.runtime_policy import ComputerUseMode, ComputerUseSettings

COMPUTER_USE_SCOPE_SCHEMA_VERSION = 1
COMPUTER_OBSERVE_TOOL = "computer_observe"
COMPUTER_ACTION_TOOL = "computer_action"
SHELL_TOOL_NAMES = frozenset({"run_command", "bash"})
COMPUTER_TOOL_NAMES = frozenset({COMPUTER_OBSERVE_TOOL, COMPUTER_ACTION_TOOL})
OBSERVATION_TOOL_EXECUTION_PREFIX = "tex"
TRUSTED_COMPUTER_USE_AUTHORITY = "local_interface_command"

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_LONG_EDGE_PX = 1920
MAX_AX_ELEMENTS = 200
MAX_AX_DEPTH = 8
MAX_OBSERVATION_AGE_SECONDS = 30
MAX_AX_TEXT_BYTES = 32 * 1024
MAX_TEXT_CHARS = 4096
MAX_HOTKEY_KEYS = 4
MAX_SCROLL_UNITS = 50
MAX_APPS = 8
MAX_DISCOVERED_TARGETS = 100

_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,127}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODIFIERS = ("ctrl", "alt", "shift", "meta")
_KEYS = frozenset(
    _MODIFIERS
    + tuple("abcdefghijklmnopqrstuvwxyz")
    + tuple("0123456789")
    + (
        "enter",
        "tab",
        "escape",
        "space",
        "backspace",
        "delete",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "pageup",
        "pagedown",
    )
)
_FORBIDDEN_ACTION_KEYS = frozenset(
    {
        "sdk_tool",
        "path",
        "grant",
        "grant_id",
        "session",
        "session_id",
        "actions",
        "script",
        "policy",
    }
)


class ComputerUseContractError(ValueError):
    """Stable rejection code. The message is the code and does not echo input."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            code = "rejected_action"
        super().__init__(code)
        self.code = code


class ComputerUseModel(LocalCapabilityModel):
    """Strict frozen computer-use value."""


class ComputerUseWindowBoundary(StrEnum):
    WINDOW = "window"
    EXPLICIT_DESKTOP_DIAGNOSTIC = "explicit_desktop_diagnostic"


class ComputerUseOperation(StrEnum):
    OBSERVE = "observe"
    ACTION = "action"


class ComputerUseDelivery(StrEnum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


class ComputerUseImageShare(StrEnum):
    NONE = "none"
    CONTROLLED_WINDOW = "controlled_window"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _finite_positive(value: float, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(code)
    if value <= 0:
        raise ValueError(code)
    return float(value)


def _as_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


def reject_untrusted_computer_use_authority(authority: str) -> None:
    """Accept only a local interface command. Screenshots and model hints are not grants."""

    if authority != TRUSTED_COMPUTER_USE_AUTHORITY:
        raise ComputerUseContractError("untrusted_authority")


class ComputerUseAppIdentity(ComputerUseModel):
    bundle_id: str

    @field_validator("bundle_id")
    @classmethod
    def valid_bundle_id(cls, value: str) -> str:
        if not isinstance(value, str) or not _BUNDLE_ID.fullmatch(value) or "*" in value:
            raise ValueError("invalid_bundle_id")
        refuse_secret_material(value, label="computer use app")
        return value


class ComputerUseScope(ComputerUseModel):
    """Frozen subject, generation, and allowed desktop range."""

    schema_version: Literal[1] = COMPUTER_USE_SCOPE_SCHEMA_VERSION
    generation: int = Field(ge=1)
    workspace_id: str
    task_run_id: str
    agent_run_id: str
    apps: tuple[ComputerUseAppIdentity, ...]
    window_boundary: ComputerUseWindowBoundary
    operations: tuple[ComputerUseOperation, ...]
    delivery: ComputerUseDelivery
    image_share: ComputerUseImageShare

    @field_validator("workspace_id")
    @classmethod
    def valid_workspace(cls, value: str) -> str:
        return validate_prefixed_id(value, WORKSPACE_ID_PREFIX)

    @field_validator("task_run_id")
    @classmethod
    def valid_task(cls, value: str) -> str:
        return validate_prefixed_id(value, TASK_RUN_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("apps", "operations", mode="before")
    @classmethod
    def tuples(cls, value: object) -> object:
        return _as_tuple(value)

    @field_validator("apps", mode="before")
    @classmethod
    def sort_apps(cls, value: object) -> object:
        items = _as_tuple(value)
        if not isinstance(items, tuple):
            return value

        def bundle(item: object) -> str:
            if isinstance(item, ComputerUseAppIdentity):
                return item.bundle_id
            if isinstance(item, Mapping):
                raw = item.get("bundle_id", "")
                return raw if isinstance(raw, str) else ""
            return ""

        return tuple(sorted(items, key=bundle))

    @field_validator("operations", mode="before")
    @classmethod
    def canonical_operations(cls, value: object) -> object:
        items = _as_tuple(value)
        if not isinstance(items, tuple):
            return value
        present: set[str] = set()
        for item in items:
            if isinstance(item, ComputerUseOperation):
                present.add(item.value)
            elif isinstance(item, str):
                present.add(item)
        return tuple(ComputerUseOperation(op) for op in ("observe", "action") if op in present)

    @field_validator("window_boundary", mode="before")
    @classmethod
    def boundary_value(cls, value: object) -> object:
        if isinstance(value, str):
            return ComputerUseWindowBoundary(value)
        return value

    @field_validator("delivery", mode="before")
    @classmethod
    def delivery_value(cls, value: object) -> object:
        if isinstance(value, str):
            return ComputerUseDelivery(value)
        return value

    @field_validator("image_share", mode="before")
    @classmethod
    def image_share_value(cls, value: object) -> object:
        if isinstance(value, str):
            return ComputerUseImageShare(value)
        return value

    @model_validator(mode="after")
    def enforce_range(self) -> ComputerUseScope:
        if not 1 <= len(self.apps) <= MAX_APPS:
            raise ValueError("app_bounds")
        if len({item.bundle_id for item in self.apps}) != len(self.apps):
            raise ValueError("duplicate_app")
        if not self.operations:
            raise ValueError("empty_operations")
        if (
            self.window_boundary is ComputerUseWindowBoundary.EXPLICIT_DESKTOP_DIAGNOSTIC
            and ComputerUseOperation.ACTION in self.operations
        ):
            raise ValueError("desktop_action_rejected")
        return self


def restrict_scope(
    scope: ComputerUseScope,
    *,
    operations: tuple[ComputerUseOperation, ...] | None = None,
    image_share: ComputerUseImageShare | None = None,
) -> ComputerUseScope:
    """Drop operations or image share. Adding apps, delivery, or generation is rejected."""

    if operations is not None and any(item not in scope.operations for item in operations):
        raise ComputerUseContractError("scope_expansion")
    if (
        image_share is not None
        and scope.image_share is ComputerUseImageShare.NONE
        and image_share is not ComputerUseImageShare.NONE
    ):
        raise ComputerUseContractError("scope_expansion")
    selected = scope.operations if operations is None else operations
    if not selected:
        raise ComputerUseContractError("empty_operations")
    payload = scope.model_dump(mode="json")
    payload["operations"] = [item.value for item in selected]
    if image_share is not None:
        payload["image_share"] = image_share.value
    return ComputerUseScope.model_validate(payload)


def reject_scope_expansion(current: ComputerUseScope, proposed: ComputerUseScope) -> None:
    """A wider app, operation, delivery, subject, or generation needs a new AgentRun."""

    if (
        proposed.workspace_id != current.workspace_id
        or proposed.task_run_id != current.task_run_id
        or proposed.agent_run_id != current.agent_run_id
        or proposed.generation != current.generation
        or proposed.delivery is not current.delivery
    ):
        raise ComputerUseContractError("scope_expansion")
    allowed = {item.bundle_id for item in current.apps}
    if any(item.bundle_id not in allowed for item in proposed.apps):
        raise ComputerUseContractError("scope_expansion")
    if any(item not in current.operations for item in proposed.operations):
        raise ComputerUseContractError("scope_expansion")
    if (
        current.image_share is ComputerUseImageShare.NONE
        and proposed.image_share is not ComputerUseImageShare.NONE
    ):
        raise ComputerUseContractError("scope_expansion")
    if (
        current.window_boundary is ComputerUseWindowBoundary.WINDOW
        and proposed.window_boundary is not ComputerUseWindowBoundary.WINDOW
    ):
        raise ComputerUseContractError("scope_expansion")


def images_allowed(settings: ComputerUseSettings, scope: ComputerUseScope) -> bool:
    return (
        settings.enabled
        and settings.mode is ComputerUseMode.HYBRID
        and scope.image_share is ComputerUseImageShare.CONTROLLED_WINDOW
    )


class ComputerUsePreflight(ComputerUseModel):
    status: Literal["unavailable"]
    reason: Literal[
        "disabled",
        "sdk_missing",
        "driver_not_activated",
        "unsupported_os",
        "native_version_mismatch",
        "abi_mismatch",
        "no_interactive_session",
        "tcc_missing",
        "native_unverified",
    ]


def preflight_computer_use(
    settings: ComputerUseSettings | None = None,
    *,
    spec_present: bool = False,
) -> ComputerUsePreflight:
    """Report availability without importing or constructing a driver.

    This check never returns available. ``spec_present`` is supplied by the
    adapter so this module does not name the native package. OS, TCC, and
    version results stay unavailable until a later native gate.
    """

    resolved = settings or ComputerUseSettings()
    if not resolved.enabled:
        return ComputerUsePreflight(status="unavailable", reason="disabled")
    if not spec_present:
        return ComputerUsePreflight(status="unavailable", reason="sdk_missing")
    return ComputerUsePreflight(status="unavailable", reason="driver_not_activated")


class TargetRef(ComputerUseModel):
    target_ref: str
    agent_run_id: str
    generation: int = Field(ge=1)
    app: ComputerUseAppIdentity
    process_identity: str
    window_identity: str
    display_label: str | None = None

    @field_validator("target_ref")
    @classmethod
    def valid_target(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_TARGET_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("process_identity")
    @classmethod
    def valid_process(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_PROCESS_ID_PREFIX)

    @field_validator("window_identity")
    @classmethod
    def valid_window(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_WINDOW_ID_PREFIX)

    @field_validator("display_label")
    @classmethod
    def valid_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 120:
            raise ValueError("rejected_action")
        refuse_secret_material(cleaned, label="computer use label")
        return cleaned


def target_authority_key(target: TargetRef) -> tuple[str, str, str, str, int]:
    """Identity used for admission. The display label is not part of it."""

    return (
        target.agent_run_id,
        target.app.bundle_id,
        target.process_identity,
        target.window_identity,
        target.generation,
    )


class CoordinateFrame(ComputerUseModel):
    space: Literal["image_px"] = "image_px"
    target_space: Literal["window"] = "window"
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    scale_x: float | None = None
    scale_y: float | None = None
    origin_x: int = Field(default=0, ge=0)
    origin_y: int = Field(default=0, ge=0)
    crop_width: int | None = Field(default=None, ge=1)
    crop_height: int | None = Field(default=None, ge=1)

    @field_validator("scale_x", "scale_y")
    @classmethod
    def finite_scale(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite_positive(value, "non_finite")

    @model_validator(mode="after")
    def complete_mapping(self) -> CoordinateFrame:
        if (self.scale_x is None) != (self.scale_y is None):
            raise ValueError("unknown_scale")
        if (self.crop_width is None) != (self.crop_height is None):
            raise ValueError("unknown_scale")
        if self.width * self.height > MAX_IMAGE_PIXELS:
            raise ValueError("image_bounds")
        if max(self.width, self.height) > MAX_IMAGE_LONG_EDGE_PX:
            raise ValueError("image_bounds")
        if self.crop_width is not None and (
            self.crop_width > self.width
            or self.crop_height is None
            or self.crop_height > self.height
        ):
            raise ValueError("unknown_scale")
        return self


def map_image_point(frame: CoordinateFrame, x: int, y: int) -> tuple[float, float]:
    """Map image pixels into the window frame. Missing scale or crop is not guessed."""

    if (
        frame.scale_x is None
        or frame.scale_y is None
        or frame.crop_width is None
        or frame.crop_height is None
        or frame.target_space != "window"
    ):
        raise ComputerUseContractError("unknown_scale")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
        or x < 0
        or y < 0
        or x >= frame.width
        or y >= frame.height
        or x >= frame.crop_width
        or y >= frame.crop_height
    ):
        raise ComputerUseContractError("out_of_bounds")
    mapped_x = (x + frame.origin_x) / frame.scale_x
    mapped_y = (y + frame.origin_y) / frame.scale_y
    if not math.isfinite(mapped_x) or not math.isfinite(mapped_y):
        raise ComputerUseContractError("non_finite")
    return mapped_x, mapped_y


class ObservationImageRef(ComputerUseModel):
    artifact_id: str
    sha256: str
    mime: Literal["image/png", "image/jpeg", "image/webp"]
    byte_size: int = Field(ge=1, le=MAX_IMAGE_BYTES)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    tool_execution_id: str

    @field_validator("artifact_id")
    @classmethod
    def valid_artifact(cls, value: str) -> str:
        return validate_prefixed_id(value, ARTIFACT_ID_PREFIX)

    @field_validator("tool_execution_id")
    @classmethod
    def valid_execution(cls, value: str) -> str:
        return validate_prefixed_id(value, OBSERVATION_TOOL_EXECUTION_PREFIX)

    @field_validator("sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("rejected_action")
        return value

    @model_validator(mode="after")
    def bounded_image(self) -> ObservationImageRef:
        if (
            self.width * self.height > MAX_IMAGE_PIXELS
            or max(self.width, self.height) > MAX_IMAGE_LONG_EDGE_PX
        ):
            raise ValueError("image_bounds")
        return self


class AxElement(ComputerUseModel):
    element_ref: str
    depth: int = Field(ge=0, le=MAX_AX_DEPTH)
    role: str
    label: str | None = None
    sensitive: bool = False

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        if not _ROLE.fullmatch(value):
            raise ValueError("rejected_action")
        return value

    @field_validator("label")
    @classmethod
    def valid_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 200:
            raise ValueError("rejected_action")
        refuse_secret_material(cleaned, label="computer use element")
        return cleaned

    @model_validator(mode="after")
    def sensitive_has_no_label(self) -> AxElement:
        if self.sensitive and self.label is not None:
            raise ValueError("sensitive_label")
        return self


class Observation(ComputerUseModel):
    schema_version: Literal[1] = 1
    observation_id: str
    target_ref: str
    agent_run_id: str
    generation: int = Field(ge=1)
    bundle_id: str
    process_identity: str
    window_identity: str
    capture_digest: str
    captured_at: datetime
    frame: CoordinateFrame
    elements: tuple[AxElement, ...] = ()
    complete: bool = True
    degraded: bool = False
    degraded_reason: Literal["ax_window_unresolved", "ax_tree_empty", "unknown"] | None = None
    truncated: bool = False
    omitted_count: int = Field(default=0, ge=0)
    image: ObservationImageRef | None = None

    @field_validator("observation_id")
    @classmethod
    def valid_observation(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_OBSERVATION_ID_PREFIX)

    @field_validator("target_ref")
    @classmethod
    def valid_target(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_TARGET_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)

    @field_validator("bundle_id")
    @classmethod
    def valid_bundle(cls, value: str) -> str:
        return ComputerUseAppIdentity(bundle_id=value).bundle_id

    @field_validator("process_identity")
    @classmethod
    def valid_process(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_PROCESS_ID_PREFIX)

    @field_validator("window_identity")
    @classmethod
    def valid_window(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_WINDOW_ID_PREFIX)

    @field_validator("capture_digest")
    @classmethod
    def valid_capture(cls, value: str) -> str:
        if not DIGEST_PATTERN.fullmatch(value):
            raise ValueError("rejected_action")
        return value

    @field_validator("captured_at", mode="before")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise ValueError("rejected_action")
        return _utc(value)

    @field_validator("elements", mode="before")
    @classmethod
    def tuple_elements(cls, value: object) -> object:
        return _as_tuple(value)

    @model_validator(mode="after")
    def bounded_tree(self) -> Observation:
        if len(self.elements) > MAX_AX_ELEMENTS:
            raise ValueError("ax_bounds")
        if len({item.element_ref for item in self.elements}) != len(self.elements):
            raise ValueError("duplicate_element")
        text = "".join(f"{item.role}{item.label or ''}" for item in self.elements)
        if len(text.encode("utf-8")) > MAX_AX_TEXT_BYTES:
            raise ValueError("ax_bounds")
        if self.complete and (self.degraded or self.truncated or self.omitted_count != 0):
            raise ValueError("ax_bounds")
        if not self.degraded and self.degraded_reason is not None:
            raise ValueError("ax_bounds")
        if self.truncated and self.omitted_count < 1:
            raise ValueError("ax_bounds")
        return self


@dataclass(slots=True)
class TransientCapture:
    """Request-local capture bytes. They are absent from repr, str, and JSON models."""

    content: bytes
    mime: str
    width: int
    height: int

    def __repr__(self) -> str:
        return (
            f"TransientCapture(mime={self.mime!r}, byte_size={len(self.content)}, "
            f"width={self.width}, height={self.height})"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class SensitiveCaptureRegion:
    """Transient trusted mapping to delivered-image pixels, never model input."""

    element_ref: str
    left: int
    top: int
    right: int
    bottom: int

    def __repr__(self) -> str:
        return "SensitiveCaptureRegion()"


@dataclass(frozen=True, slots=True)
class ObservedWindow:
    """Transient read result; safe Observation is the only persistent projection."""

    observation: Observation
    capture: TransientCapture | None = None
    image_error: str | None = None
    sensitive_regions: tuple[SensitiveCaptureRegion, ...] = ()

    def __repr__(self) -> str:
        return (
            f"ObservedWindow(observation_id={self.observation.observation_id!r}, "
            f"capture={self.capture is not None}, image_error={self.image_error!r})"
        )


class ElementExistsPostcondition(ComputerUseModel):
    type: Literal["element_exists"]
    element_ref: str

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)


class AttributeEqualsPostcondition(ComputerUseModel):
    type: Literal["attribute_equals"]
    element_ref: str
    attribute: Literal["enabled", "focused", "checked", "expanded"]
    value: Literal["true", "false"]

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)


class TextAppearsPostcondition(ComputerUseModel):
    type: Literal["text_appears"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        refuse_secret_material(value, label="computer use postcondition")
        return value


Postcondition = Annotated[
    ElementExistsPostcondition | AttributeEqualsPostcondition | TextAppearsPostcondition,
    Field(discriminator="type"),
]


def _exclusive_target(
    element_ref: str | None, x: int | None, y: int | None, *, require_one: bool
) -> None:
    has_ref = element_ref is not None
    has_x = x is not None
    has_y = y is not None
    if has_x != has_y or (has_ref and (has_x or has_y)):
        raise ValueError("mixed_target")
    if require_one and not has_ref and not has_x:
        raise ValueError("rejected_action")


class ClickAction(ComputerUseModel):
    type: Literal["click"]
    element_ref: str | None = None
    x: int | None = None
    y: int | None = None
    button: Literal["left", "right"] = "left"
    count: Literal[1, 2] = 1
    postcondition: Postcondition | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @model_validator(mode="after")
    def one_target(self) -> ClickAction:
        _exclusive_target(self.element_ref, self.x, self.y, require_one=True)
        return self


class TypeTextAction(ComputerUseModel):
    type: Literal["type_text"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    element_ref: str | None = None
    postcondition: Postcondition | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        try:
            refuse_secret_material(value, label="computer use text")
        except ValueError:
            raise ValueError("secret_material") from None
        return value


class ScrollAction(ComputerUseModel):
    type: Literal["scroll"]
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(ge=1, le=MAX_SCROLL_UNITS)
    element_ref: str | None = None
    x: int | None = None
    y: int | None = None
    postcondition: Postcondition | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @model_validator(mode="after")
    def optional_target(self) -> ScrollAction:
        _exclusive_target(self.element_ref, self.x, self.y, require_one=False)
        return self


class PressKeyAction(ComputerUseModel):
    type: Literal["press_key"]
    element_ref: str | None = None
    key: str
    postcondition: Postcondition | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @field_validator("key")
    @classmethod
    def canonical_key(cls, value: str) -> str:
        if value not in _KEYS or value in _MODIFIERS:
            raise ValueError("rejected_action")
        return value


class HotkeyAction(ComputerUseModel):
    type: Literal["hotkey"]
    element_ref: str | None = None
    keys: tuple[str, ...]
    postcondition: Postcondition | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @field_validator("keys", mode="before")
    @classmethod
    def tuple_keys(cls, value: object) -> object:
        return _as_tuple(value)

    @field_validator("keys")
    @classmethod
    def canonical_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not 2 <= len(value) <= MAX_HOTKEY_KEYS or len(set(value)) != len(value):
            raise ValueError("rejected_action")
        if any(item not in _KEYS for item in value):
            raise ValueError("rejected_action")
        modifiers = tuple(item for item in _MODIFIERS if item in value)
        rest = tuple(sorted(item for item in value if item not in _MODIFIERS))
        if len(rest) != 1:
            raise ValueError("rejected_action")
        return modifiers + rest


ComputerUseAction = Annotated[
    ClickAction | TypeTextAction | ScrollAction | PressKeyAction | HotkeyAction,
    Field(discriminator="type"),
]


def parse_computer_action(payload: object) -> ComputerUseAction:
    """Reject model-supplied action payloads before any device call."""

    if not isinstance(payload, dict) or _FORBIDDEN_ACTION_KEYS.intersection(payload):
        raise ComputerUseContractError("rejected_action")
    if isinstance(payload.get("action"), list) or isinstance(payload.get("actions"), list):
        raise ComputerUseContractError("rejected_action")
    if payload.get("type") == "type_text" and isinstance(payload.get("text"), str):
        try:
            refuse_secret_material(payload["text"], label="computer use text")
        except ValueError:
            raise ComputerUseContractError("secret_material") from None
    try:
        return TypeAdapter(ComputerUseAction).validate_python(payload)
    except ValidationError:
        raise ComputerUseContractError("rejected_action") from None


class ActionOutcome(ComputerUseModel):
    status: Literal["not_started", "completed", "unknown"]
    delivery: ComputerUseDelivery | None = None
    before_observation_id: str | None = None
    after_observation_id: str | None = None
    error_code: str | None = None
    postcondition: Literal["not_checked", "passed", "failed"] = "not_checked"

    @field_validator("error_code")
    @classmethod
    def valid_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _CODE.fullmatch(value):
            raise ValueError("rejected_action")
        return value


class ComputerActionResult(ComputerUseModel):
    """Device completion and the independent fresh-observation result."""

    outcome: ActionOutcome
    observation: Observation | None = None
    observation_error: str | None = None

    @field_validator("observation_error")
    @classmethod
    def valid_error(cls, value: str | None) -> str | None:
        return ActionOutcome.valid_code(value)

    @model_validator(mode="after")
    def paired_observation(self):
        if self.observation is not None and (
            self.outcome.status == "not_started"
            or self.outcome.after_observation_id != self.observation.observation_id
            or self.outcome.before_observation_id == self.observation.observation_id
        ):
            raise ValueError("stale_observation")
        return self


def outcome_for_rejection(code: str) -> ActionOutcome:
    if not _CODE.fullmatch(code):
        code = "rejected_action"
    return ActionOutcome(status="not_started", error_code=code, postcondition="not_checked")


@dataclass(frozen=True, slots=True)
class PreparedComputerAction:
    action: ComputerUseAction
    window_point: tuple[float, float] | None


class ExecuteRequest(ComputerUseModel):
    authority: str
    scope: ComputerUseScope
    target: TargetRef
    observation: Observation
    action: ComputerUseAction
    delivery: ComputerUseDelivery
    include_image: bool = False


def _matching_element(observation: Observation, element_ref: str | None) -> AxElement | None:
    if element_ref is None:
        return None
    for item in observation.elements:
        if item.element_ref == element_ref:
            return item
    raise ComputerUseContractError("unknown_element")


def prepare_execute_request(
    request: ExecuteRequest,
    *,
    settings: ComputerUseSettings | None = None,
) -> PreparedComputerAction:
    """Validate one action against the frozen scope. This never calls a port."""

    resolved = settings or ComputerUseSettings()
    reject_untrusted_computer_use_authority(request.authority)
    scope = request.scope
    target = request.target
    observation = request.observation
    if target.agent_run_id != scope.agent_run_id or observation.agent_run_id != scope.agent_run_id:
        raise ComputerUseContractError("subject_mismatch")
    if target.app.bundle_id not in {item.bundle_id for item in scope.apps}:
        raise ComputerUseContractError("app_not_granted")
    if observation.bundle_id != target.app.bundle_id:
        raise ComputerUseContractError("app_not_granted")
    if (
        observation.target_ref != target.target_ref
        or observation.generation != target.generation
        or target.generation != scope.generation
        or observation.process_identity != target.process_identity
        or observation.window_identity != target.window_identity
    ):
        raise ComputerUseContractError("stale_observation")
    if request.delivery is not scope.delivery:
        raise ComputerUseContractError("delivery_not_granted")
    if ComputerUseOperation.ACTION not in scope.operations:
        raise ComputerUseContractError("operation_not_granted")
    if scope.window_boundary is ComputerUseWindowBoundary.EXPLICIT_DESKTOP_DIAGNOSTIC:
        raise ComputerUseContractError("desktop_action_rejected")
    if request.include_image and not images_allowed(resolved, scope):
        if scope.image_share is not ComputerUseImageShare.CONTROLLED_WINDOW:
            raise ComputerUseContractError("image_share_not_granted")
        raise ComputerUseContractError("images_not_allowed")
    if request.include_image and observation.image is not None:
        long_edge = max(observation.image.width, observation.image.height)
        if long_edge > resolved.image_long_edge_px:
            raise ComputerUseContractError("image_budget")
    action = request.action
    element_ref = getattr(action, "element_ref", None)
    element = _matching_element(observation, element_ref)
    if isinstance(action, (TypeTextAction, PressKeyAction, HotkeyAction)):
        if element is None:
            raise ComputerUseContractError("element_required")
        if element.sensitive:
            raise ComputerUseContractError("sensitive_target")
        if isinstance(action, TypeTextAction) and element.role not in {
            "axtextfield",
            "axtextarea",
            "axcombobox",
            "axsearchfield",
        }:
            raise ComputerUseContractError("not_editable")
    if isinstance(action, ScrollAction) and element is None and action.x is None:
        raise ComputerUseContractError("element_required")
    point = None
    x = getattr(action, "x", None)
    y = getattr(action, "y", None)
    if x is not None or y is not None:
        if x is None or y is None:
            raise ComputerUseContractError("mixed_target")
        if observation.frame.target_space != "window":
            raise ComputerUseContractError("desktop_coordinates")
        point = map_image_point(observation.frame, x, y)
    postcondition = getattr(action, "postcondition", None)
    if postcondition is not None and hasattr(postcondition, "element_ref"):
        _matching_element(observation, postcondition.element_ref)
    return PreparedComputerAction(action=action, window_point=point)


class OpenRunSessionRequest(ComputerUseModel):
    authority: str
    agent_run_id: str
    scope: ComputerUseScope

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)


class RunSession(ComputerUseModel):
    run_session_id: str
    agent_run_id: str
    generation: int = Field(ge=1)

    @field_validator("run_session_id")
    @classmethod
    def valid_run(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_RUN_ID_PREFIX)

    @field_validator("agent_run_id")
    @classmethod
    def valid_agent_run(cls, value: str) -> str:
        return validate_prefixed_id(value, AGENT_RUN_ID_PREFIX)


class CloseRunSessionRequest(ComputerUseModel):
    authority: str
    run_session_id: str

    @field_validator("run_session_id")
    @classmethod
    def valid_run(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_RUN_ID_PREFIX)


class DiscoverRequest(ComputerUseModel):
    authority: str
    scope: ComputerUseScope
    run_session_id: str
    bundle_id: str | None = None

    @field_validator("run_session_id")
    @classmethod
    def valid_run(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_RUN_ID_PREFIX)

    @field_validator("bundle_id")
    @classmethod
    def valid_bundle(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ComputerUseAppIdentity(bundle_id=value).bundle_id


class DiscoverResult(ComputerUseModel):
    targets: tuple[TargetRef, ...] = ()

    @field_validator("targets", mode="before")
    @classmethod
    def tuple_targets(cls, value: object) -> object:
        return _as_tuple(value)


class ObserveWindowRequest(ComputerUseModel):
    authority: str
    scope: ComputerUseScope
    target: TargetRef
    delivery: ComputerUseDelivery
    include_image: bool = False


class ShutdownRequest(ComputerUseModel):
    authority: str
    run_session_id: str

    @field_validator("run_session_id")
    @classmethod
    def valid_run(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_RUN_ID_PREFIX)


class ComputerUsePort(Protocol):
    def preflight(self) -> ComputerUsePreflight: ...

    def open_run_session(self, request: OpenRunSessionRequest) -> RunSession: ...

    def close_run_session(self, request: CloseRunSessionRequest) -> None: ...

    def discover(self, request: DiscoverRequest) -> DiscoverResult: ...

    def observe_window(self, request: ObserveWindowRequest) -> Observation: ...

    def execute_one(self, request: PreparedComputerAction) -> ActionOutcome: ...

    def shutdown(self, request: ShutdownRequest) -> None: ...


def open_run_session_if_admitted(
    port: ComputerUsePort, request: OpenRunSessionRequest
) -> RunSession:
    reject_untrusted_computer_use_authority(request.authority)
    if request.scope.agent_run_id != request.agent_run_id:
        raise ComputerUseContractError("subject_mismatch")
    return port.open_run_session(request)


def close_run_session_if_admitted(port: ComputerUsePort, request: CloseRunSessionRequest) -> None:
    reject_untrusted_computer_use_authority(request.authority)
    port.close_run_session(request)


def discover_if_admitted(port: ComputerUsePort, request: DiscoverRequest) -> DiscoverResult:
    reject_untrusted_computer_use_authority(request.authority)
    if request.bundle_id is not None and request.bundle_id not in {
        item.bundle_id for item in request.scope.apps
    }:
        raise ComputerUseContractError("app_not_granted")
    return port.discover(request)


def observe_window_if_admitted(
    port: ComputerUsePort,
    request: ObserveWindowRequest,
    *,
    settings: ComputerUseSettings | None = None,
) -> Observation:
    resolved = settings or ComputerUseSettings()
    reject_untrusted_computer_use_authority(request.authority)
    if request.target.agent_run_id != request.scope.agent_run_id:
        raise ComputerUseContractError("subject_mismatch")
    if request.target.generation != request.scope.generation:
        raise ComputerUseContractError("stale_observation")
    if request.target.app.bundle_id not in {item.bundle_id for item in request.scope.apps}:
        raise ComputerUseContractError("app_not_granted")
    if ComputerUseOperation.OBSERVE not in request.scope.operations:
        raise ComputerUseContractError("operation_not_granted")
    if request.delivery is not request.scope.delivery:
        raise ComputerUseContractError("delivery_not_granted")
    if request.include_image and not images_allowed(resolved, request.scope):
        if request.scope.image_share is not ComputerUseImageShare.CONTROLLED_WINDOW:
            raise ComputerUseContractError("image_share_not_granted")
        raise ComputerUseContractError("images_not_allowed")
    return port.observe_window(request)


def execute_one_if_admitted(
    port: ComputerUsePort,
    request: ExecuteRequest,
    *,
    settings: ComputerUseSettings | None = None,
) -> ActionOutcome:
    prepared = prepare_execute_request(request, settings=settings)
    return port.execute_one(prepared)


def shutdown_if_admitted(port: ComputerUsePort, request: ShutdownRequest) -> None:
    reject_untrusted_computer_use_authority(request.authority)
    port.shutdown(request)


def assert_computer_use_device_gate(
    *,
    tool_name: str,
    authority: str,
    scope: ComputerUseScope | None,
    workspace_id: str,
    task_run_id: str,
    agent_run_id: str,
    delivery: ComputerUseDelivery,
    include_image: bool,
    grant_active: bool,
) -> None:
    """Re-check frozen scope after approval and before any device call."""

    reject_untrusted_computer_use_authority(authority)
    if not grant_active:
        raise ComputerUseContractError("grant_inactive")
    if scope is None:
        raise ComputerUseContractError("computer_use_scope_required")
    if (
        scope.workspace_id != workspace_id
        or scope.task_run_id != task_run_id
        or scope.agent_run_id != agent_run_id
    ):
        raise ComputerUseContractError("subject_mismatch")
    if delivery is not scope.delivery:
        raise ComputerUseContractError("delivery_not_granted")
    if include_image and scope.image_share is not ComputerUseImageShare.CONTROLLED_WINDOW:
        raise ComputerUseContractError("image_share_not_granted")
    if tool_name == COMPUTER_OBSERVE_TOOL:
        if ComputerUseOperation.OBSERVE not in scope.operations:
            raise ComputerUseContractError("operation_not_granted")
        return
    if tool_name == COMPUTER_ACTION_TOOL:
        if ComputerUseOperation.ACTION not in scope.operations:
            raise ComputerUseContractError("operation_not_granted")
        if scope.window_boundary is ComputerUseWindowBoundary.EXPLICIT_DESKTOP_DIAGNOSTIC:
            raise ComputerUseContractError("desktop_action_rejected")
        return
    raise ComputerUseContractError("unknown_computer_use_tool")


@dataclass(frozen=True, slots=True)
class ComputerUseIntentSpec:
    tool_name: str
    operation: str
    tool_effect: ToolEffect
    effect_class: str
    missing_completion: str
    requires_approval: bool


def computer_use_intent(tool_name: str) -> ComputerUseIntentSpec:
    if tool_name == COMPUTER_OBSERVE_TOOL:
        return ComputerUseIntentSpec(
            tool_name=COMPUTER_OBSERVE_TOOL,
            operation=COMPUTER_OBSERVE_TOOL,
            tool_effect=ToolEffect.NONE,
            effect_class="bounded_external_read",
            missing_completion="safe_to_retry",
            requires_approval=False,
        )
    if tool_name == COMPUTER_ACTION_TOOL:
        return ComputerUseIntentSpec(
            tool_name=COMPUTER_ACTION_TOOL,
            operation=COMPUTER_ACTION_TOOL,
            tool_effect=ToolEffect.PERSISTENT_WRITE,
            effect_class="unconfined_external_effect",
            missing_completion="outcome_unknown",
            requires_approval=True,
        )
    raise ComputerUseContractError("unknown_computer_use_tool")


class ComputerUseSessionPort(Protocol):
    """Run-bound asynchronous device surface, with no SDK objects or native ids."""

    async def discover(self, request: DiscoverRequest) -> DiscoverResult: ...

    async def observe(
        self, request: ObserveWindowRequest, *, settings: ComputerUseSettings
    ) -> ObservedWindow: ...

    async def execute_one(
        self,
        request: ExecuteRequest,
        *,
        settings: ComputerUseSettings,
        authority: Callable[[], None],
    ) -> ActionOutcome: ...

    def invalidate(self) -> None: ...


class ComputerUseLifecyclePort(Protocol):
    """Async lifecycle on the runtime owner; no native handle crosses this port."""

    @property
    def shutdown_pending(self) -> bool: ...

    def stop_admission(self) -> None: ...

    async def open_run_session(self, request: OpenRunSessionRequest) -> RunSession: ...

    async def close_run_session(self, request: CloseRunSessionRequest) -> None: ...

    def session_for(self, run: RunSession) -> ComputerUseSessionPort: ...

    async def shutdown(self) -> None: ...
