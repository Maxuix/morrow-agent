"""Real synthetic raster validation; never reads a desktop or opens a Driver."""

import io

import pytest
from PIL import Image, PngImagePlugin

from morrow.adapters.computer_use.images import CaptureMask, prepare_capture
from morrow.core.computer_use import ComputerUseContractError, TransientCapture


def capture(*, width=8, height=6):
    buffer = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "synthetic password=never-retain-this")
    Image.new("RGB", (width, height), "red").save(buffer, format="PNG", pnginfo=metadata)
    return TransientCapture(buffer.getvalue(), "image/png", width, height)


def test_masked_pixels_and_metadata_cannot_recover_synthetic_secret():
    result = prepare_capture(capture(), masks=(CaptureMask(2, 1, 5, 4),))
    assert b"never-retain-this" not in result.content
    with Image.open(io.BytesIO(result.content)) as decoded:
        assert decoded.info == {}
        assert decoded.size == (8, 6)
        for x in range(8):
            for y in range(6):
                assert decoded.getpixel((x, y)) == (
                    (0, 0, 0) if 2 <= x < 5 and 1 <= y < 4 else (255, 0, 0)
                )


@pytest.mark.parametrize(
    "mask",
    [
        CaptureMask(-1, 0, 1, 1),
        CaptureMask(0, 0, 9, 1),
        CaptureMask(0, 0, 1, 7),
        CaptureMask(1, 1, 1, 2),
        CaptureMask(True, 0, 2, 2),
        CaptureMask(0, 0, 1.0, 1),
    ],
)
def test_invalid_masks_refuse_publication_instead_of_leaving_sensitive_pixels(mask):
    with pytest.raises(ComputerUseContractError, match="out_of_bounds"):
        prepare_capture(capture(), masks=(mask,))


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
