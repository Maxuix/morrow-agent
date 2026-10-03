"""Real synthetic raster validation; never reads a desktop or opens a Driver."""

import io

import pytest
from PIL import Image, PngImagePlugin

from morrow.adapters.computer_use.images import prepare_capture
from morrow.core.computer_use import ComputerUseContractError, TransientCapture


def capture(*, width=8, height=6):
    buffer = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "synthetic password=never-retain-this")
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG", pnginfo=metadata)
    return TransientCapture(buffer.getvalue(), "image/png", width, height)


def test_pixels_are_preserved_and_encoding_metadata_is_normalized():
    result = prepare_capture(capture())
    assert b"never-retain-this" not in result.content
    with Image.open(io.BytesIO(result.content)) as decoded:
        assert decoded.info == {}
        assert decoded.size == (8, 6)
        assert decoded.getextrema() == ((255, 255), (0, 0), (0, 0))


def test_encoded_geometry_mime_and_damage_are_validated():
    source = capture()
    for invalid, code in [
        (TransientCapture(source.content, source.mime, 7, 6), "image_dimensions"),
        (TransientCapture(source.content, "image/jpeg", 8, 6), "image_mime"),
        (TransientCapture(b"fake png", "image/png", 8, 6), "image_decode"),
        (TransientCapture(source.content, source.mime, 1921, 6), "image_bounds"),
    ]:
        with pytest.raises(ComputerUseContractError, match=code):
            prepare_capture(invalid)
