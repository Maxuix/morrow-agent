"""Strict manifest decoding for SKILL.md frontmatter and morrow.yaml.

Basic Agent Skills compatibility is retained (SKILL.md frontmatter with
``name``/``description``); Morrow extensions live under ``morrow.*`` inside the
frontmatter or a dedicated ``morrow.yaml``. Every declaration (requested Trust,
permissions) is recorded but never authoritative.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from morrow.core.skills.identity import (
    SkillIdentityError,
    normalize_skill_name,
    validate_display_version,
)
from morrow.core.skills.trust import ManifestDocument, TrustLevel

FRONTMATTER_FILE = "SKILL.md"
MORROW_YAML_FILE = "morrow.yaml"

_FRONTMATTER_DELIMITER = "---"

_MORROW_KEYS = frozenset(
    {
        "morrow.name",
        "morrow.description",
        "morrow.version",
        "morrow.requested_trust",
        "morrow.requested_permissions",
    }
)
_FRONTMATTER_KEYS = frozenset({"name", "description", "version", *_MORROW_KEYS})


class ManifestError(ValueError):
    """A manifest cannot be decoded safely."""


def _parse_yaml(text: str, *, label: str) -> dict:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{label} is not valid YAML") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ManifestError(f"{label} must be a mapping")
    return parsed


def _decode_trust(value) -> TrustLevel | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    for level in TrustLevel:
        if level.value == normalized:
            return level
    raise ManifestError(f"unknown requested_trust value: {value!r}")


def _frontmatter_document(root: Path) -> dict:
    path = root / FRONTMATTER_FILE
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_FRONTMATTER_DELIMITER):
        return {}
    parts = text.split(_FRONTMATTER_DELIMITER, 2)
    if len(parts) < 3:
        raise ManifestError(f"{FRONTMATTER_FILE} has unbalanced frontmatter delimiters")
    payload = _parse_yaml(parts[1], label=FRONTMATTER_FILE)
    unknown = set(payload) - _FRONTMATTER_KEYS
    if unknown:
        raise ManifestError(
            f"{FRONTMATTER_FILE} contains unsupported keys: {', '.join(sorted(unknown))}"
        )
    return payload


def _morrow_yaml_document(root: Path) -> dict:
    path = root / MORROW_YAML_FILE
    if not path.is_file():
        return {}
    payload = _parse_yaml(path.read_text(encoding="utf-8"), label=MORROW_YAML_FILE)
    unknown = set(payload) - _MORROW_KEYS
    if unknown:
        raise ManifestError(
            f"{MORROW_YAML_FILE} contains unsupported keys: {', '.join(sorted(unknown))}"
        )
    return payload


def load_manifest(package_root: Path) -> ManifestDocument:
    """Decode morrow.yaml merged over SKILL.md frontmatter; strict and bounded."""
    frontmatter = _frontmatter_document(package_root)
    morrow_yaml = _morrow_yaml_document(package_root)

    def pick(front_key: str, morrow_key: str, default=None):
        if morrow_key in morrow_yaml:
            return morrow_yaml[morrow_key]
        if morrow_key in frontmatter:
            return frontmatter[morrow_key]
        if front_key in frontmatter:
            return frontmatter[front_key]
        return default

    name = pick("name", "morrow.name")
    description = pick("description", "morrow.description")
    raw_version = pick("version", "morrow.version")
    requested_trust = _decode_trust(pick(None, "morrow.requested_trust"))
    requested_permissions = pick(None, "morrow.requested_permissions", ())
    if requested_permissions is None:
        requested_permissions = ()
    if not isinstance(requested_permissions, (list, tuple)):
        raise ManifestError("morrow.requested_permissions must be a list of strings")
    normalized_permissions = tuple(
        str(item) for item in requested_permissions if isinstance(item, str)
    )
    raw_fields = tuple(f"{key}: {value!r}" for key, value in {**frontmatter, **morrow_yaml}.items())
    try:
        display_version = (
            validate_display_version(str(raw_version)) if raw_version is not None else None
        )
        normalized_name = normalize_skill_name(name) if name is not None else None
    except SkillIdentityError as exc:
        raise ManifestError(str(exc)) from exc
    return ManifestDocument(
        name=normalized_name,
        description=str(description)[:2048] if description is not None else None,
        display_version=display_version,
        requested_trust=requested_trust,
        requested_permissions=normalized_permissions[:16],
        raw_morrow_fields=raw_fields[:16],
    )
