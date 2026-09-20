"""Model-aware image token estimates from sent pixel dimensions.

Token counts follow published vision metering where the exact model is known.
Unknown models use a labeled conservative pixel fallback. File or Base64 size
is never the image-token input; transmission size is a separate budget.
"""

from __future__ import annotations

import base64
import math
from typing import Literal

from morrow.core.attachments import MAX_PIXELS
from morrow.core.models import Message, ModelRef, ProviderInputPart, UserMessage

ImageTokenAlgorithmName = Literal[
    "claude_vision",
    "openai_vision_tiles",
    "openai_gpt4o_mini",
    "conservative_pixel_fallback",
]

# Claude bills (width * height) / 750 after fitting the long edge into 1568px.
# https://platform.claude.com/docs/en/build-with-claude/vision
CLAUDE_LONG_EDGE = 1568
CLAUDE_PIXELS_PER_TOKEN = 750

# OpenAI high-detail tile schedule for GPT-4o / GPT-4.1 / GPT-4 Turbo.
OPENAI_FIT_EDGE = 2048
OPENAI_SHORT_EDGE = 768
OPENAI_TILE = 512
OPENAI_BASE_TOKENS = 85
OPENAI_TILE_TOKENS = 170
OPENAI_MINI_BASE_TOKENS = 2833
OPENAI_MINI_TILE_TOKENS = 5667

# Conservative fallback: no provider-side downscale, 256 pixels/token.
# This overestimates relative to Claude's 750 px/token after 1568 resize and
# typical OpenAI 512-tile counts for screenshots. It is not a per-image flat fee.
CONSERVATIVE_PIXELS_PER_TOKEN = 256

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_JPEG_STANDALONE = frozenset({0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0x01})


def select_image_token_algorithm(model: ModelRef) -> ImageTokenAlgorithmName:
    """Choose a published algorithm from Provider + exact model id."""

    model_id = model.model_id.lower()
    provider_id = model.provider_id.lower()
    if "claude" in model_id or "anthropic" in provider_id:
        return "claude_vision"
    if "gpt-4o-mini" in model_id:
        return "openai_gpt4o_mini"
    if (
        model_id.startswith("gpt-4o")
        or model_id.startswith("gpt-4.1")
        or model_id.startswith("gpt-4-turbo")
        or model_id.startswith("gpt-4-vision")
        or "chatgpt-4o" in model_id
    ):
        return "openai_vision_tiles"
    return "conservative_pixel_fallback"


def estimate_image_tokens(
    width: int,
    height: int,
    *,
    algorithm: ImageTokenAlgorithmName = "conservative_pixel_fallback",
) -> int:
    """Estimate one image from the pixels actually sent to the model."""

    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return _unknown_image_tokens(algorithm)
    if algorithm == "claude_vision":
        width, height = _fit_long_edge(width, height, CLAUDE_LONG_EDGE)
        return max(1, math.ceil((width * height) / CLAUDE_PIXELS_PER_TOKEN))
    if algorithm == "openai_vision_tiles":
        return _openai_tile_tokens(width, height, OPENAI_BASE_TOKENS, OPENAI_TILE_TOKENS)
    if algorithm == "openai_gpt4o_mini":
        return _openai_tile_tokens(width, height, OPENAI_MINI_BASE_TOKENS, OPENAI_MINI_TILE_TOKENS)
    return max(1, math.ceil((width * height) / CONSERVATIVE_PIXELS_PER_TOKEN))


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read width and height from PNG, JPEG, or WebP headers without decoding pixels."""

    if not isinstance(data, (bytes, bytearray)) or len(data) < 24:
        return None
    if data[:8] == _PNG_SIGNATURE:
        return _png_dimensions(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_dimensions(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dimensions(data)
    return None


def decode_image_payload(data: str) -> bytes | None:
    """Decode a Base64 image payload; invalid input yields None."""

    if not data:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        try:
            return base64.b64decode(data)
        except (ValueError, TypeError):
            return None


def resolve_image_dimensions(part: ProviderInputPart) -> tuple[int, int] | None:
    """Prefer explicit send size, then parse the actual payload headers."""

    if part.type != "image":
        return None
    if part.width is not None and part.height is not None:
        return part.width, part.height
    raw = decode_image_payload(part.data)
    return image_dimensions(raw) if raw is not None else None


def estimate_image_part_tokens(
    part: ProviderInputPart,
    *,
    algorithm: ImageTokenAlgorithmName = "conservative_pixel_fallback",
) -> int:
    """Token estimate for one hydrated image part."""

    if part.type != "image":
        return 0
    dimensions = resolve_image_dimensions(part)
    if dimensions is None:
        return _unknown_image_tokens(algorithm)
    return estimate_image_tokens(*dimensions, algorithm=algorithm)


def messages_without_image_payloads(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    """Copy messages with image Base64 removed so text estimators ignore it."""

    result: list[Message] = []
    for message in messages:
        if not isinstance(message, UserMessage) or not message.input_parts:
            result.append(message)
            continue
        parts = tuple(
            part.model_copy(update={"data": ""}) if part.type == "image" else part
            for part in message.input_parts
        )
        result.append(message.model_copy(update={"input_parts": parts}))
    return tuple(result)


def iter_image_parts(messages: tuple[Message, ...]):
    for message in messages:
        if not isinstance(message, UserMessage):
            continue
        for part in message.input_parts:
            if part.type == "image":
                yield part


def _unknown_image_tokens(algorithm: ImageTokenAlgorithmName) -> int:
    """Never underestimate when the sent size cannot be parsed."""

    side = max(1, int(math.isqrt(MAX_PIXELS)))
    return estimate_image_tokens(side, side, algorithm=algorithm)


def _fit_long_edge(width: int, height: int, long_edge: int) -> tuple[int, int]:
    longest = max(width, height)
    if longest <= long_edge:
        return width, height
    scale = long_edge / longest
    return max(1, int(width * scale)), max(1, int(height * scale))


def _openai_tile_tokens(width: int, height: int, base: int, per_tile: int) -> int:
    w, h = float(width), float(height)
    longest = max(w, h)
    if longest > OPENAI_FIT_EDGE:
        scale = OPENAI_FIT_EDGE / longest
        w *= scale
        h *= scale
    shortest = min(w, h)
    if shortest > OPENAI_SHORT_EDGE:
        scale = OPENAI_SHORT_EDGE / shortest
        w *= scale
        h *= scale
    tiles = math.ceil(w / OPENAI_TILE) * math.ceil(h / OPENAI_TILE)
    return base + per_tile * max(1, tiles)


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return _valid_dimensions(width, height)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    index = 2
    length = len(data)
    while index + 8 < length:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < length and data[index] == 0xFF:
            index += 1
        if index >= length:
            return None
        marker = data[index]
        index += 1
        if marker in _JPEG_STANDALONE:
            continue
        if index + 2 > length:
            return None
        segment = int.from_bytes(data[index : index + 2], "big")
        if segment < 2:
            return None
        if marker in _JPEG_SOF and index + 6 <= length:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return _valid_dimensions(width, height)
        index += segment
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    kind = data[12:16]
    if kind == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return _valid_dimensions(width, height)
    if kind == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return _valid_dimensions(width, height)
    if kind == b"VP8 ":
        start = data.find(b"\x9d\x01\x2a", 20, 40)
        if start != -1 and start + 7 <= len(data):
            width = int.from_bytes(data[start + 3 : start + 5], "little") & 0x3FFF
            height = int.from_bytes(data[start + 5 : start + 7], "little") & 0x3FFF
            return _valid_dimensions(width, height)
    return None


def _valid_dimensions(width: int, height: int) -> tuple[int, int] | None:
    if width <= 0 or height <= 0:
        return None
    if width > MAX_PIXELS or height > MAX_PIXELS:
        return None
    return width, height


__all__ = [
    "CLAUDE_LONG_EDGE",
    "CLAUDE_PIXELS_PER_TOKEN",
    "CONSERVATIVE_PIXELS_PER_TOKEN",
    "ImageTokenAlgorithmName",
    "decode_image_payload",
    "estimate_image_part_tokens",
    "estimate_image_tokens",
    "image_dimensions",
    "iter_image_parts",
    "messages_without_image_payloads",
    "resolve_image_dimensions",
    "select_image_token_algorithm",
]
