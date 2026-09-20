"""Immutable, bounded references to user supplied Artifact content."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from morrow.core.models import AttachmentRef as AttachmentRef
from morrow.core.models import ProtocolModel


class AttachmentState(StrEnum):
    RESERVED = "reserved"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    SUBMITTED = "submitted"
    FAILED = "failed"
    RELEASED = "released"


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_STAGED_BYTES = 64 * 1024 * 1024
MAX_PRODUCT_BYTES = 32 * 1024 * 1024
MAX_TEXT_CHARS = 32768
MAX_PIXELS = 16_000_000
MAX_PAGES = 20
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MEDIA_TYPES = IMAGE_TYPES | {"text/plain", "application/pdf"}


class AttachmentReservation(ProtocolModel):
    command_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]+$", max_length=128)
    name: str = Field(min_length=1, max_length=255)
    media_type: Literal["text/plain", "image/png", "image/jpeg", "image/webp", "application/pdf"]
    byte_size: int = Field(ge=1, le=MAX_FILE_BYTES, strict=True)
    source: Literal["upload", "workspace"] = "upload"
    source_path: str | None = Field(default=None, max_length=1024)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value):
        if any(ord(c) < 32 for c in value) or "/" in value or "\\" in value:
            raise ValueError("Attachment name must be a plain filename")
        return value


class AttachmentPart(ProtocolModel):
    artifact_id: str = Field(pattern=r"^art_[A-Za-z0-9_-]+$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: Literal["text/plain", "image/png", "image/jpeg", "image/webp"]
    page: int | None = Field(default=None, ge=1, le=MAX_PAGES)
    chars: int = Field(default=0, ge=0, le=MAX_TEXT_CHARS)
    width: int | None = Field(default=None, ge=1, le=MAX_PIXELS)
    height: int | None = Field(default=None, ge=1, le=MAX_PIXELS)

    @model_validator(mode="after")
    def image_dimensions(self):
        if (self.width is None) != (self.height is None):
            raise ValueError("Image width and height must be provided together")
        if self.media_type == "text/plain" and self.width is not None:
            raise ValueError("Text parts cannot carry image dimensions")
        if self.width is not None and self.width * self.height > MAX_PIXELS:
            raise ValueError("Image pixel limit exceeded")
        return self


class AttachmentRepresentation(ProtocolModel):
    version: Literal[1] = 1
    name: str = Field(max_length=255)
    source_path: str | None = Field(default=None, max_length=1024)
    parts: tuple[AttachmentPart, ...] = Field(min_length=1, max_length=MAX_PAGES * 2)
    previews: tuple[AttachmentPart, ...] = Field(default=(), max_length=MAX_PAGES)
    reading: Literal["text", "image", "pdf_text", "pdf_images", "pdf_mixed"]
    omitted_chars: int = Field(default=0, ge=0)
    pages: int = Field(default=0, ge=0, le=MAX_PAGES)


def attachment_limits():
    return {
        "file_bytes": MAX_FILE_BYTES,
        "message_files": MAX_ATTACHMENTS,
        "staged_bytes": MAX_STAGED_BYTES,
        "product_bytes": MAX_PRODUCT_BYTES,
        "text_chars": MAX_TEXT_CHARS,
        "pixels": MAX_PIXELS,
        "pdf_pages": MAX_PAGES,
        "workers": 2,
        "media_types": sorted(MEDIA_TYPES),
    }
