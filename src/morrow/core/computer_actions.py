"""Action grammar and the pure execute-request mapping. No device or SDK calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from morrow.core.computer_use import (
    _CODE,
    MAX_HOTKEY_KEYS,
    MAX_SCROLL_UNITS,
    MAX_TEXT_CHARS,
    AxElement,
    ComputerUseContractError,
    ComputerUseDelivery,
    ComputerUseImageShare,
    ComputerUseModel,
    ComputerUseOperation,
    Observation,
    SelectedWindowScope,
    TargetRef,
    _as_tuple,
    images_allowed,
    map_image_point,
    reject_untrusted_computer_use_authority,
    scope_grants_window,
)
from morrow.core.domain import (
    COMPUTER_ELEMENT_ID_PREFIX,
    refuse_secret_material,
    validate_prefixed_id,
)
from morrow.core.runtime_policy import ComputerUseSettings

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
EDITABLE_ROLES = frozenset(
    {
        "axtextfield",
        "axtextarea",
        "axcombobox",
        "axsearchfield",
        "axsecuretextfield",
        "axpasswordfield",
    }
)


class PostconditionSelector(ComputerUseModel):
    """Exact safe labels/roles in a fresh tree, never an old snapshot token."""

    role: str | None = None
    label: str | None = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, value):
        return None if value is None else AxElement.valid_role(value)

    @field_validator("label")
    @classmethod
    def valid_label(cls, value):
        return AxElement.valid_label(value)

    @model_validator(mode="after")
    def bounded_selector(self):
        if self.role is None and self.label is None:
            raise ValueError("rejected_action")
        return self


class ElementPostconditionTarget(ComputerUseModel):
    element_ref: str | None = None
    selector: PostconditionSelector | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        return None if value is None else validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)

    @model_validator(mode="after")
    def exact_target(self):
        if (self.element_ref is None) == (self.selector is None):
            raise ValueError("mixed_target")
        return self


class ElementExistsPostcondition(ElementPostconditionTarget):
    type: Literal["element_exists"]


class AttributeEqualsPostcondition(ElementPostconditionTarget):
    type: Literal["attribute_equals"]
    attribute: Literal["enabled", "focused", "checked", "expanded"]
    value: Literal["true", "false"]


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
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, repr=False)
    element_ref: str | None = None
    postcondition: Postcondition | None = None

    @field_validator("element_ref")
    @classmethod
    def valid_element(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_prefixed_id(value, COMPUTER_ELEMENT_ID_PREFIX)


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
    verification_error: Literal["verification_failed", "verification_unavailable"] | None = None

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
    scope: SelectedWindowScope
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
    if not scope_grants_window(scope, target.app, target.window_identity):
        raise ComputerUseContractError("window_not_granted")
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
    if request.include_image and not images_allowed(resolved, scope):
        if scope.image_share is not ComputerUseImageShare.CONTROLLED_WINDOW:
            raise ComputerUseContractError("image_share_not_granted")
        raise ComputerUseContractError("images_not_allowed")
    if request.include_image and observation.image is not None:
        long_edge = max(observation.image.width, observation.image.height)
        if long_edge > resolved.image_long_edge_px:
            raise ComputerUseContractError("image_budget")
    action = request.action
    element = _matching_element(observation, action.element_ref)
    if isinstance(action, (TypeTextAction, PressKeyAction, HotkeyAction)):
        if element is None:
            raise ComputerUseContractError("element_required")
        if isinstance(action, TypeTextAction) and element.role not in EDITABLE_ROLES:
            raise ComputerUseContractError("not_editable")
    if isinstance(action, ScrollAction) and element is None and action.x is None:
        raise ComputerUseContractError("element_required")
    point = None
    if isinstance(action, (ClickAction, ScrollAction)) and (
        action.x is not None or action.y is not None
    ):
        if action.x is None or action.y is None:
            raise ComputerUseContractError("mixed_target")
        if observation.frame.target_space != "window":
            raise ComputerUseContractError("desktop_coordinates")
        point = map_image_point(observation.frame, action.x, action.y)
    postcondition = action.postcondition
    if (
        isinstance(postcondition, ElementPostconditionTarget)
        and postcondition.element_ref is not None
    ):
        _matching_element(observation, postcondition.element_ref)
    return PreparedComputerAction(action=action, window_point=point)
