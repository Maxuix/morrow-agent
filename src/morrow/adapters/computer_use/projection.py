"""Project SDK observations and action results into core values. Tokens stay in the registry."""

from __future__ import annotations

import base64
import math
import re
from typing import Any

from morrow.adapters.computer_use.census import bounds_geometry
from morrow.adapters.computer_use.registry import TrustedDesktopRegistry, WindowGeometry
from morrow.core.computer_use import (
    MAX_AX_DEPTH,
    MAX_AX_ELEMENTS,
    MAX_AX_TEXT_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_LONG_EDGE_PX,
    MAX_IMAGE_PIXELS,
    MAX_TEXT_CHARS,
    ActionOutcome,
    AxElement,
    ComputerUseContractError,
    ComputerUseDelivery,
    CoordinateFrame,
    TransientCapture,
)

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MIME = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/webp": "image/webp",
}


def outcome_from_action(result: Any) -> ActionOutcome:
    effect = _enum_name(getattr(result, "effect", None))
    delivery = _actual_delivery(getattr(result, "delivery", None))
    if effect == "REFUSED":
        code = _stable_code(getattr(getattr(result, "error", None), "code", None), "refused")
        return ActionOutcome(status="not_started", error_code=code)
    if effect == "CONFIRMED" and delivery is not None:
        return ActionOutcome(status="completed", delivery=delivery)
    if effect == "PARTIAL" and delivery is not None:
        return ActionOutcome(status="completed", delivery=delivery, error_code="partial_effect")
    if effect == "SUSPECTED_NOOP":
        return ActionOutcome(status="unknown", delivery=delivery, error_code="suspected_noop")
    if effect in {"CONFIRMED", "PARTIAL", "UNVERIFIABLE"}:
        code = "unknown_delivery" if delivery is None else "unverified_action"
        return ActionOutcome(status="unknown", delivery=delivery, error_code=code)
    return ActionOutcome(status="unknown", error_code="driver_error")


def outcome_from_tool(result: Any) -> ActionOutcome:
    action = getattr(result, "action", None)
    if action is not None:
        return outcome_from_action(action)
    if bool(getattr(result, "degraded", False)):
        return ActionOutcome(status="unknown", error_code="degraded_result")
    if bool(getattr(result, "is_error", False)):
        return ActionOutcome(
            status="not_started",
            error_code=_stable_code(getattr(result, "error_code", None), "tool_error"),
        )
    return ActionOutcome(status="unknown", error_code="unverified_action")


def project_elements(
    state: Any,
    registry: TrustedDesktopRegistry,
    window_identity: str,
) -> tuple[list[AxElement], int, bool]:
    raw_elements = getattr(state, "elements", None) or ()
    total = getattr(state, "total_element_count", None)
    returned = getattr(state, "returned_element_count", None)
    omitted = 0
    if isinstance(total, int) and isinstance(returned, int) and not isinstance(total, bool):
        omitted = max(0, total - returned)
    truncated = bool(getattr(state, "truncated", False))
    candidates: list[tuple[Any, str]] = []
    text_bytes = 0
    for raw in raw_elements:
        if len(candidates) >= MAX_AX_ELEMENTS:
            omitted += 1
            truncated = True
            continue
        depth = getattr(raw, "depth", None)
        if (
            isinstance(depth, bool)
            or not isinstance(depth, int)
            or depth < 0
            or depth > MAX_AX_DEPTH
        ):
            omitted += 1
            truncated = True
            continue
        role = _role(getattr(raw, "role", ""))
        role_bytes = len(role.encode())
        if text_bytes + role_bytes > MAX_AX_TEXT_BYTES:
            omitted += 1
            truncated = True
            continue
        text_bytes += role_bytes
        candidates.append((raw, role))

    # Reserve every retained node's role and label before spending the shared
    # display budget on values. Long content must not retire actionable tokens.
    labels: list[tuple[str | None, bool]] = []
    for raw, _ in candidates:
        label = _element_label(raw)
        raw_label = getattr(raw, "label", None)
        cut = isinstance(raw_label, str) and bool(raw_label.strip()) and label is None
        addition = len((label or "").encode())
        if text_bytes + addition > MAX_AX_TEXT_BYTES:
            label, cut = None, True
        else:
            text_bytes += addition
        labels.append((label, cut))

    kept: list[AxElement] = []
    for (raw, role), (label, cut) in zip(candidates, labels, strict=True):
        value, value_cut = _bounded_value(
            getattr(raw, "value", None), byte_budget=MAX_AX_TEXT_BYTES - text_bytes
        )
        text_bytes += len((value or "").encode())
        description, description_cut = _bounded_value(
            getattr(raw, "value_description", None), byte_budget=MAX_AX_TEXT_BYTES - text_bytes
        )
        text_bytes += len((description or "").encode())
        cut = cut or value_cut or description_cut
        truncated = truncated or cut
        token = getattr(raw, "element_token", None)
        element_ref = registry.remember_element(
            window_identity=window_identity,
            token=token if isinstance(token, str) and token else None,
            center=_center(getattr(raw, "frame", None)),
            role=role,
        )
        kept.append(
            AxElement(
                element_ref=element_ref,
                depth=depth,
                role=role,
                label=label,
                value=value,
                value_description=description,
                text_truncated=cut,
                enabled=(
                    getattr(raw, "enabled", None)
                    if type(getattr(raw, "enabled", None)) is bool
                    else None
                ),
            )
        )
    return kept, omitted, truncated


def project_frame(state: Any, *, geometry: WindowGeometry) -> CoordinateFrame:
    width = _positive_int(getattr(state, "screenshot_width", None))
    height = _positive_int(getattr(state, "screenshot_height", None))
    scale = _positive_float(getattr(state, "screenshot_scale", None))
    crop_width = None
    crop_height = None
    if width is None or height is None:
        width = math.ceil(geometry.width)
        height = math.ceil(geometry.height)
        scale = None
    elif getattr(state, "screenshot_frame_valid", None) is True and scale is not None:
        # The pinned SDK accepts pixels of its delivered (possibly resized)
        # window screenshot. Its session cache undoes resizing and the native
        # backend applies backing scale. Do not divide by Retina scale here.
        scale = 1.0
        crop_width = width
        crop_height = height
    else:
        scale = None
    if width is None or height is None:
        raise ComputerUseContractError("unknown_scale")
    try:
        return CoordinateFrame(
            width=width,
            height=height,
            scale_x=scale,
            scale_y=scale,
            crop_width=crop_width,
            crop_height=crop_height,
        )
    except ValueError:
        raise ComputerUseContractError("unknown_scale") from None


def project_capture(state: Any) -> tuple[TransientCapture | None, str | None]:
    images = getattr(state, "images", ()) or ()
    if not images:
        return None, "image_missing"
    if getattr(state, "screenshot_frame_valid", None) is not True:
        return None, "unknown_scale"
    image = images[0]
    mime = _MIME.get(str(getattr(image, "mime_type", "")).casefold())
    if mime is None:
        return None, "image_mime"
    encoded = getattr(image, "data_base64", "")
    if not isinstance(encoded, str) or not encoded:
        return None, "image_decode"
    if len(images) != 1 or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        return None, "image_bounds"
    try:
        content = base64.b64decode(encoded, validate=True)
    except Exception:
        return None, "image_decode"
    width = _positive_int(getattr(state, "screenshot_width", None))
    height = _positive_int(getattr(state, "screenshot_height", None))
    if width is None or height is None:
        return None, "unknown_scale"
    if (
        not content
        or len(content) > MAX_IMAGE_BYTES
        or width * height > MAX_IMAGE_PIXELS
        or max(width, height) > MAX_IMAGE_LONG_EDGE_PX
    ):
        return None, "image_bounds"
    return TransientCapture(content=content, mime=mime, width=width, height=height), None


def reported_window_geometry(state: Any) -> WindowGeometry:
    return bounds_geometry(getattr(state, "window_bounds", None))


def _actual_delivery(delivery: Any) -> ComputerUseDelivery | None:
    name = _enum_name(getattr(delivery, "mode", None))
    if name == "FOREGROUND":
        return ComputerUseDelivery.FOREGROUND
    if name == "BACKGROUND":
        return ComputerUseDelivery.BACKGROUND
    return None


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else None


def _stable_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and _CODE.fullmatch(value):
        return value
    return fallback


def _role(value: object) -> str:
    text = value if isinstance(value, str) else ""
    pieces = [char if char.isalnum() else "_" for char in text.casefold()]
    token = "_".join(part for part in "".join(pieces).split("_") if part)
    if not token or not token[0].isalpha():
        token = f"ax_{token}" if token else "ax_element"
    return token[:64]


def _element_label(raw: Any) -> str | None:
    label = getattr(raw, "label", None)
    if not isinstance(label, str):
        return None
    cleaned = " ".join(label.split())
    return cleaned if cleaned and len(cleaned) <= 200 else None


def _bounded_value(value: object, *, byte_budget: int) -> tuple[str | None, bool]:
    # A missing SDK field remains unavailable. Do not infer or read private AX data.
    if not isinstance(value, str):
        return None, False
    prefix = value[:MAX_TEXT_CHARS].encode()[:byte_budget].decode("utf-8", errors="ignore")
    return prefix, len(prefix) != len(value)


def _center(frame: Any) -> tuple[float, float] | None:
    if frame is None:
        return None
    numbers = [getattr(frame, name, None) for name in ("x", "y", "w", "h")]
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in numbers):
        return None
    x, y, width, height = (float(item) for item in numbers)
    if width <= 0 or height <= 0 or not math.isfinite(x + y + width + height):
        return None
    return x + width / 2, y + height / 2


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    number = int(value)
    return number if number >= 1 else None


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number
