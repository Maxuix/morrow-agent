"""Closed adapters for fields absent from the pinned generated action inputs.

These four fixed SDK calls stay in the same Session/owner/runtime. Native IDs,
tokens and text are transient. No caller supplies a tool name or argument dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from morrow.core.computer_use import ComputerUseContractError
from morrow.core.domain import canonical_json_bytes, refuse_secret_material


@dataclass(frozen=True, slots=True, repr=False)
class ElementSafetySubject:
    """Private native binding for a read-only secure-subrole proof."""

    pid: int
    window_id: int
    token: str | None
    role: str
    center: tuple[float, float] | None

    def __repr__(self) -> str:
        return "ElementSafetySubject()"


class _WindowInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    pid: int = Field(gt=0, le=2**31 - 1, repr=False)
    window_id: int = Field(gt=0, le=2**32 - 1, repr=False)
    session: str = Field(min_length=1, max_length=128, repr=False)
    delivery_mode: Literal["background", "foreground"]


class NativeTextInput(_WindowInput):
    element_token: str = Field(min_length=1, max_length=1024, repr=False)
    text: str = Field(min_length=1, max_length=4096, repr=False)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value):
        refuse_secret_material(value, label="computer use text")
        return value


NativeKey = Literal[
    "ctrl",
    "option",
    "shift",
    "cmd",
    "return",
    "tab",
    "escape",
    "space",
    "backspace",
    "forward_delete",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pageup",
    "pagedown",
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "i",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "v",
    "w",
    "x",
    "y",
    "z",
    "0",
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
]


class NativeKeyInput(_WindowInput):
    element_token: str = Field(min_length=1, max_length=1024, repr=False)
    key: NativeKey


class NativeHotkeyInput(_WindowInput):
    element_token: str = Field(min_length=1, max_length=1024, repr=False)
    keys: tuple[NativeKey, ...] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def one_base_key(self):
        modifiers = {"ctrl", "option", "shift", "cmd"}
        if len(set(self.keys)) != len(self.keys) or sum(k not in modifiers for k in self.keys) != 1:
            raise ValueError("rejected_action")
        return self


class NativeScrollInput(_WindowInput):
    element_token: str | None = Field(default=None, min_length=1, max_length=1024, repr=False)
    x: float | None = Field(default=None, ge=0, repr=False)
    y: float | None = Field(default=None, ge=0, repr=False)
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(ge=1, le=50)
    by: Literal["line"] = "line"

    @model_validator(mode="after")
    def exact_position(self):
        if (self.x is None) != (self.y is None):
            raise ValueError("mixed_target")
        if (self.element_token is not None) == (self.x is not None):
            raise ValueError("mixed_target")
        return self


def native_key(key: str) -> str:
    return {"meta": "cmd", "alt": "option", "enter": "return", "delete": "forward_delete"}.get(
        key, key
    )


async def invoke_fixed_action(session, payload):
    if type(payload) not in {NativeTextInput, NativeKeyInput, NativeHotkeyInput, NativeScrollInput}:
        raise ComputerUseContractError("rejected_action")
    arguments = canonical_json_bytes(payload.model_dump(mode="json", exclude_none=True)).decode()
    if type(payload) is NativeTextInput:
        return await session.call_tool("type_text", arguments)
    if type(payload) is NativeKeyInput:
        return await session.call_tool("press_key", arguments)
    if type(payload) is NativeHotkeyInput:
        return await session.call_tool("hotkey", arguments)
    return await session.call_tool("scroll", arguments)
