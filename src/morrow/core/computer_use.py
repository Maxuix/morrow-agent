"""SDK-agnostic desktop observe/act contracts.

This module is the permission and device boundary for computer use. It does not
import a native driver, construct a session, or register model tools.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator, model_serializer, model_validator

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


class LocalComputerUseCandidate(ComputerUseModel):
    """Local picker display only; native identities remain in the owner registry."""

    candidate_id: str
    app: ComputerUseAppIdentity
    display_label: str | None = None

    @field_validator("candidate_id")
    @classmethod
    def valid_candidate_id(cls, value: str) -> str:
        from morrow.core.domain import COMPUTER_CANDIDATE_ID_PREFIX

        return validate_prefixed_id(value, COMPUTER_CANDIDATE_ID_PREFIX)

    @field_validator("display_label")
    @classmethod
    def valid_display_label(cls, value: str | None) -> str | None:
        if value is not None:
            if len(value) > 120 or not value.strip():
                raise ValueError("invalid_display_label")
            refuse_secret_material(value, label="computer use label")
        return value


class LocalComputerUseCandidates(ComputerUseModel):
    candidates: tuple[LocalComputerUseCandidate, ...] = Field(max_length=MAX_DISCOVERED_TARGETS)
    expires_at: datetime


class ComputerUseWindowIdentity(ComputerUseModel):
    """Opaque selected window identity; native process facts stay in the adapter."""

    app: ComputerUseAppIdentity
    window_identity: str

    @field_validator("window_identity")
    @classmethod
    def valid_window(cls, value: str) -> str:
        return validate_prefixed_id(value, COMPUTER_WINDOW_ID_PREFIX)


class _ComputerUseScope(ComputerUseModel):
    """Shared fields for historical evidence and selected-window runtime scopes."""

    schema_version: int
    generation: int = Field(ge=1)
    workspace_id: str
    task_run_id: str
    agent_run_id: str
    apps: tuple[ComputerUseAppIdentity, ...]
    windows: tuple[ComputerUseWindowIdentity, ...] = ()
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

    @field_validator("apps", "operations", "windows", mode="before")
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
    def enforce_range(self) -> _ComputerUseScope:
        if not 1 <= len(self.apps) <= MAX_APPS:
            raise ValueError("app_bounds")
        if len({item.bundle_id for item in self.apps}) != len(self.apps):
            raise ValueError("duplicate_app")
        if not self.operations:
            raise ValueError("empty_operations")
        return self


class AppWindowScope(_ComputerUseScope):
    """Historical app-wide evidence. Device requests cannot contain this type."""

    schema_version: Literal[1] = 1
    windows: tuple[()] = ()

    @model_serializer(mode="wrap")
    def serialize_scope(self, handler):
        payload = handler(self)
        # Preserve historical scope JSON and permission digests byte-for-byte.
        payload.pop("windows", None)
        return payload

    @model_validator(mode="after")
    def reject_desktop_actions(self) -> AppWindowScope:
        if (
            self.window_boundary is ComputerUseWindowBoundary.EXPLICIT_DESKTOP_DIAGNOSTIC
            and ComputerUseOperation.ACTION in self.operations
        ):
            raise ValueError("desktop_action_rejected")
        return self


class SelectedWindowScope(_ComputerUseScope):
    """The only runtime scope: a nonempty, canonical set of concrete windows."""

    schema_version: Literal[2] = 2
    windows: tuple[ComputerUseWindowIdentity, ...] = Field(
        min_length=1, max_length=MAX_DISCOVERED_TARGETS
    )
    window_boundary: Literal[ComputerUseWindowBoundary.WINDOW] = ComputerUseWindowBoundary.WINDOW

    @model_validator(mode="after")
    def enforce_windows(self) -> SelectedWindowScope:
        if {item.app.bundle_id for item in self.windows} != {
            item.bundle_id for item in self.apps
        } or len({item.window_identity for item in self.windows}) != len(self.windows):
            raise ValueError("invalid_window_scope")
        if tuple(sorted(self.windows, key=lambda item: item.window_identity)) != self.windows:
            raise ValueError("noncanonical_window_scope")
        return self


ComputerUseScope = Annotated[
    AppWindowScope | SelectedWindowScope, Field(discriminator="schema_version")
]
_SCOPE_EVIDENCE = TypeAdapter(ComputerUseScope)


def decode_computer_use_scope(payload: object) -> ComputerUseScope:
    """Decode persisted evidence; callers must narrow before preparing a new run."""

    return _SCOPE_EVIDENCE.validate_python(payload)


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
    return type(scope).model_validate(payload)


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
    # A selected-window grant is not an app-wide grant. Downgrade and any window
    # outside the current set are expansion; dropping windows is a restriction.
    if isinstance(current, SelectedWindowScope) and isinstance(proposed, AppWindowScope):
        raise ComputerUseContractError("scope_expansion")
    if isinstance(current, SelectedWindowScope) and isinstance(proposed, SelectedWindowScope):
        allowed_windows = {(item.app.bundle_id, item.window_identity) for item in current.windows}
        if any(
            (item.app.bundle_id, item.window_identity) not in allowed_windows
            for item in proposed.windows
        ):
            raise ComputerUseContractError("scope_expansion")


def scope_grants_window(
    scope: SelectedWindowScope, app: ComputerUseAppIdentity, window_identity: str
) -> bool:
    """Window membership is independent of merely having a non-empty selection."""

    return any(
        item.app == app and item.window_identity == window_identity for item in scope.windows
    )


def images_allowed(settings: ComputerUseSettings, scope: ComputerUseScope) -> bool:
    return (
        settings.enabled
        and settings.mode is ComputerUseMode.HYBRID
        and scope.image_share is ComputerUseImageShare.CONTROLLED_WINDOW
    )


class ComputerUseRuntimeStatus(ComputerUseModel):
    """Local owner's operational state; this does not declare native availability."""

    state: Literal[
        "not_activated", "idle", "active", "quarantined", "stopping", "closed", "unknown"
    ]
    native_pending: bool | None


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
    enabled: bool | None = None
    focused: bool | None = None
    checked: bool | None = None
    expanded: bool | None = None

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
        if self.sensitive and (
            self.label is not None
            or any(
                getattr(self, key) is not None
                for key in ("enabled", "focused", "checked", "expanded")
            )
        ):
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


class OpenRunSessionRequest(ComputerUseModel):
    authority: str
    agent_run_id: str
    scope: SelectedWindowScope
    settings: ComputerUseSettings | None = None

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
    scope: SelectedWindowScope
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
    scope: SelectedWindowScope
    target: TargetRef
    delivery: ComputerUseDelivery
    include_image: bool = False


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


_LAZY_EXPORTS = {
    "PostconditionSelector": "morrow.core.computer_actions",
    "ElementPostconditionTarget": "morrow.core.computer_actions",
    "ElementExistsPostcondition": "morrow.core.computer_actions",
    "AttributeEqualsPostcondition": "morrow.core.computer_actions",
    "TextAppearsPostcondition": "morrow.core.computer_actions",
    "Postcondition": "morrow.core.computer_actions",
    "ClickAction": "morrow.core.computer_actions",
    "TypeTextAction": "morrow.core.computer_actions",
    "ScrollAction": "morrow.core.computer_actions",
    "PressKeyAction": "morrow.core.computer_actions",
    "HotkeyAction": "morrow.core.computer_actions",
    "ComputerUseAction": "morrow.core.computer_actions",
    "parse_computer_action": "morrow.core.computer_actions",
    "ActionOutcome": "morrow.core.computer_actions",
    "ComputerActionResult": "morrow.core.computer_actions",
    "outcome_for_rejection": "morrow.core.computer_actions",
    "PreparedComputerAction": "morrow.core.computer_actions",
    "ExecuteRequest": "morrow.core.computer_actions",
    "prepare_execute_request": "morrow.core.computer_actions",
    "EDITABLE_ROLES": "morrow.core.computer_actions",
    "AdmittedDiscover": "morrow.core.computer_admission",
    "AdmittedObserve": "morrow.core.computer_admission",
    "AdmittedExecute": "morrow.core.computer_admission",
    "admit_discover": "morrow.core.computer_admission",
    "admit_observe": "morrow.core.computer_admission",
    "admit_execute": "morrow.core.computer_admission",
    "ComputerUseSessionPort": "morrow.core.computer_admission",
    "ComputerUseLifecyclePort": "morrow.core.computer_admission",
}


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value
