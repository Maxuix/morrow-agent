"""Bounded capture decoding and metadata normalization before publication."""

from __future__ import annotations

import io
import warnings

from PIL import Image, UnidentifiedImageError

from morrow.core.computer_use import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_LONG_EDGE_PX,
    MAX_IMAGE_PIXELS,
    ComputerUseContractError,
    TransientCapture,
)

_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def prepare_capture(capture: TransientCapture) -> TransientCapture:
    """Validate pixels and dimensions and normalize encoding; no content classification."""
    if (
        not capture.content
        or len(capture.content) > MAX_IMAGE_BYTES
        or capture.width < 1
        or capture.height < 1
        or capture.width * capture.height > MAX_IMAGE_PIXELS
        or max(capture.width, capture.height) > MAX_IMAGE_LONG_EDGE_PX
    ):
        raise ComputerUseContractError("image_bounds")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(capture.content), formats=list(_FORMATS)) as image:
                if _FORMATS.get(image.format) != capture.mime:
                    raise ComputerUseContractError("image_mime")
                if image.size != (capture.width, capture.height):
                    raise ComputerUseContractError("image_dimensions")
                if getattr(image, "n_frames", 1) != 1:
                    raise ComputerUseContractError("image_decode")
                image.load()
                # Copy only pixels into a fresh RGB image; EXIF/text/ICC/animation
                # and even transparent hidden pixel channels cannot be retained.
                pixels = Image.new("RGB", image.size, "white")
                rgba = image.convert("RGBA")
                pixels.paste(rgba, mask=rgba.getchannel("A"))
        output = io.BytesIO()
        pixels.save(output, format="PNG")
        content = output.getvalue()
    except ComputerUseContractError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ComputerUseContractError("image_decode") from None
    if len(content) > MAX_IMAGE_BYTES:
        raise ComputerUseContractError("image_bounds")
    return TransientCapture(content, "image/png", capture.width, capture.height)
