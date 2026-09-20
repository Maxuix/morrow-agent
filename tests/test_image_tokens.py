"""Published image-token algorithms and header parsing without a tokenizer."""

from __future__ import annotations

import io
import math

from PIL import Image

from morrow.core.image_tokens import (
    CLAUDE_PIXELS_PER_TOKEN,
    CONSERVATIVE_PIXELS_PER_TOKEN,
    estimate_image_tokens,
    image_dimensions,
    select_image_token_algorithm,
)
from morrow.core.models import ModelRef


def _png(width: int, height: int, *, color=(12, 34, 56)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def test_same_dimensions_do_not_follow_file_bytes():
    solid = estimate_image_tokens(800, 640)
    noisy = estimate_image_tokens(800, 640)
    assert solid == noisy == math.ceil((800 * 640) / CONSERVATIVE_PIXELS_PER_TOKEN)


def test_claude_and_openai_and_fallback_differ_for_the_same_size():
    claude = estimate_image_tokens(800, 640, algorithm="claude_vision")
    openai = estimate_image_tokens(800, 640, algorithm="openai_vision_tiles")
    fallback = estimate_image_tokens(800, 640, algorithm="conservative_pixel_fallback")
    assert claude == math.ceil((800 * 640) / CLAUDE_PIXELS_PER_TOKEN)
    assert openai == 85 + 170 * 4
    assert fallback == math.ceil((800 * 640) / CONSERVATIVE_PIXELS_PER_TOKEN)
    assert len({claude, openai, fallback}) == 3


def test_claude_resizes_long_edge_before_metering():
    tokens = estimate_image_tokens(4000, 4000, algorithm="claude_vision")
    assert tokens == math.ceil((1568 * 1568) / CLAUDE_PIXELS_PER_TOKEN)


def test_algorithm_selection_uses_provider_and_exact_model():
    assert (
        select_image_token_algorithm(ModelRef(provider_id="anthropic", model_id="claude-sonnet-4"))
        == "claude_vision"
    )
    assert (
        select_image_token_algorithm(ModelRef(provider_id="openai", model_id="gpt-4o"))
        == "openai_vision_tiles"
    )
    assert (
        select_image_token_algorithm(ModelRef(provider_id="openai", model_id="gpt-4o-mini"))
        == "openai_gpt4o_mini"
    )
    assert (
        select_image_token_algorithm(ModelRef(provider_id="test", model_id="test"))
        == "conservative_pixel_fallback"
    )


def test_png_and_jpeg_headers_report_sent_dimensions():
    png = _png(320, 240)
    jpeg_buf = io.BytesIO()
    Image.new("RGB", (64, 48), "green").save(jpeg_buf, format="JPEG")
    assert image_dimensions(png) == (320, 240)
    assert image_dimensions(jpeg_buf.getvalue()) == (64, 48)
    assert image_dimensions(b"not-an-image") is None
