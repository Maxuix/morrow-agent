"""Safe Skill identity rules: stable ids, Morrow-assigned version ids and names.

External manifest values are never trusted as identifiers or paths. ``skill_id``
is a stable identity derived from the normalized manifest name (or assigned by
Morrow); ``skv_`` version ids are always Morrow-generated; the display version is
a separate bounded string.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from morrow.core.models import ProtocolModel, utc_now

SKILL_ID_PREFIX = "skl"
SKV_ID_PREFIX = "skv"

SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SKV_ID_PATTERN = re.compile(rf"^{SKV_ID_PREFIX}_[A-Za-z0-9_-]{{8,64}}$")
DISPLAY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")

RESERVED_PATH_NAMES = frozenset({"package", "managed-version.json", "catalog.json", "."})


class SkillIdentityError(ValueError):
    """A Skill identifier, name or version violates the safe rules."""


def normalize_skill_name(raw: str) -> str:
    """NFC-normalize and collapse whitespace; keeps case for display."""
    normalized = unicodedata.normalize("NFC", " ".join(raw.split()))
    if not normalized or len(normalized) > 128:
        raise SkillIdentityError("Skill name must be a bounded non-empty string")
    return normalized


def skill_id_from_name(name: str) -> str | None:
    """Stable lowercase slug id derived from a normalized name; None if useless."""
    slug = unicodedata.normalize("NFKC", name).casefold()
    slug = re.sub(r"[^a-z0-9_-]+", "-", slug).strip("-")
    if not slug or len(slug) > 64:
        return None
    return slug


def validate_skill_id(value: str) -> str:
    if not value or not SKILL_ID_PATTERN.match(value):
        raise SkillIdentityError("skill_id must match [a-z0-9][a-z0-9_-]{0,63}")
    if value in RESERVED_PATH_NAMES:
        raise SkillIdentityError("skill_id is reserved")
    return value


def validate_skv_id(value: str) -> str:
    if not SKV_ID_PATTERN.match(value):
        raise SkillIdentityError("version id must be a Morrow-assigned skv_ id")
    return value


def validate_display_version(value: str | None) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFC", value)
    if not DISPLAY_VERSION_PATTERN.match(value):
        raise SkillIdentityError(
            "display version must match [A-Za-z0-9._+-]{1,64} and carry no path separators"
        )
    return value


def collides(a: str, b: str) -> bool:
    """Case/Unicode-normalized collision used for path and name safety."""
    return unicodedata.normalize("NFC", a).casefold() == unicodedata.normalize("NFC", b).casefold()


def new_skv_id(id_source) -> str:
    return id_source.new_id(SKV_ID_PREFIX)


class SkillVersionStamp(ProtocolModel):
    """Immutable provenance stamp frozen with a managed version."""

    version_id: str
    display_version: str | None = None
    installed_at: datetime = utc_now()
