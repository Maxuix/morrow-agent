"""Strict manifest decoding for SKILL.md frontmatter and morrow.yaml.

Basic Agent Skills compatibility is retained (SKILL.md frontmatter with
``name``/``description``); Morrow extensions live under ``morrow.*`` inside the
frontmatter or a dedicated ``morrow.yaml``. Every declaration (requested Trust,
permissions) is recorded but never authoritative.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
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
        "morrow.required_tools",
        "morrow.required_mcp_servers",
        "morrow.entry_resources",
        "morrow.context_budget",
        "morrow.platform_constraints",
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
    if any(not isinstance(key, str) for key in parsed):
        raise ManifestError(f"{label} keys must be strings")
    return parsed


def _decode_trust(value) -> TrustLevel | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManifestError("morrow.requested_trust must be a string")
    normalized = value.strip().casefold()
    for level in TrustLevel:
        if level.value == normalized:
            return level
    raise ManifestError(f"unknown requested_trust value: {value!r}")


def _decode_string_list(value, *, label: str, limit: int, item_limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ManifestError(f"{label} must be a list of strings")
    if len(value) > limit or any(not isinstance(item, str) for item in value):
        raise ManifestError(f"{label} must be a bounded list of strings")
    if any(not item.strip() or len(item) > item_limit for item in value):
        raise ManifestError(f"{label} contains an invalid item")
    return tuple(item.strip() for item in value)


def _read_manifest_text(
    root: Path,
    filename: str,
    file_bytes: Mapping[str, bytes] | None,
) -> str | None:
    if file_bytes is not None:
        raw = file_bytes.get(filename)
        if raw is None:
            return None
    else:
        path = root / filename
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ManifestError(f"{filename} could not be read") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ManifestError(f"{filename} must be a regular file")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ManifestError(f"{filename} could not be read") from exc
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{filename} is not valid UTF-8") from exc


def _frontmatter_document(root: Path, file_bytes: Mapping[str, bytes] | None) -> dict:
    text = _read_manifest_text(root, FRONTMATTER_FILE, file_bytes)
    if text is None:
        return {}
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


def _morrow_yaml_document(root: Path, file_bytes: Mapping[str, bytes] | None) -> dict:
    text = _read_manifest_text(root, MORROW_YAML_FILE, file_bytes)
    if text is None:
        return {}
    payload = _parse_yaml(text, label=MORROW_YAML_FILE)
    unknown = set(payload) - _MORROW_KEYS
    if unknown:
        raise ManifestError(
            f"{MORROW_YAML_FILE} contains unsupported keys: {', '.join(sorted(unknown))}"
        )
    return payload


def load_manifest(
    package_root: Path,
    *,
    file_bytes: Mapping[str, bytes] | None = None,
) -> ManifestDocument:
    """Decode morrow.yaml merged over SKILL.md frontmatter; strict and bounded."""
    frontmatter = _frontmatter_document(package_root, file_bytes)
    morrow_yaml = _morrow_yaml_document(package_root, file_bytes)

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
    if name is not None and not isinstance(name, str):
        raise ManifestError("skill name must be a string")
    if description is not None and not isinstance(description, str):
        raise ManifestError("skill description must be a string")
    if raw_version is not None and not isinstance(raw_version, str):
        raise ManifestError("skill version must be a string")
    if requested_permissions is None:
        requested_permissions = ()
    if not isinstance(requested_permissions, (list, tuple)):
        raise ManifestError("morrow.requested_permissions must be a list of strings")
    if any(not isinstance(item, str) for item in requested_permissions):
        raise ManifestError("morrow.requested_permissions must be a list of strings")
    normalized_permissions = tuple(requested_permissions)
    required_tools = _decode_string_list(
        pick(None, "morrow.required_tools"),
        label="morrow.required_tools",
        limit=16,
        item_limit=128,
    )
    required_mcp_servers = _decode_string_list(
        pick(None, "morrow.required_mcp_servers"),
        label="morrow.required_mcp_servers",
        limit=16,
        item_limit=128,
    )
    entry_resources = _decode_string_list(
        pick(None, "morrow.entry_resources"),
        label="morrow.entry_resources",
        limit=32,
        item_limit=256,
    )
    raw_context_budget = pick(None, "morrow.context_budget")
    if raw_context_budget is not None:
        if (
            not isinstance(raw_context_budget, int)
            or isinstance(raw_context_budget, bool)
            or not 1 <= raw_context_budget <= 16 * 1024
        ):
            raise ManifestError("morrow.context_budget must be between 1 and 16384")
    platform_constraints = _decode_string_list(
        pick(None, "morrow.platform_constraints"),
        label="morrow.platform_constraints",
        limit=16,
        item_limit=128,
    )
    raw_fields = tuple(f"{key}: {value!r}" for key, value in {**frontmatter, **morrow_yaml}.items())
    try:
        display_version = validate_display_version(raw_version) if raw_version is not None else None
        normalized_name = normalize_skill_name(name) if name is not None else None
    except SkillIdentityError as exc:
        raise ManifestError(str(exc)) from exc
    return ManifestDocument(
        name=normalized_name,
        description=description[:2048] if description is not None else None,
        display_version=display_version,
        requested_trust=requested_trust,
        requested_permissions=normalized_permissions[:16],
        required_tools=required_tools,
        required_mcp_servers=required_mcp_servers,
        entry_resources=entry_resources,
        context_budget=raw_context_budget,
        platform_constraints=platform_constraints,
        raw_morrow_fields=raw_fields[:16],
    )
