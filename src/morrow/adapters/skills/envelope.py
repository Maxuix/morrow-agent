"""Immutable managed-version.json envelopes: written and verified by Morrow."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

from morrow.core.domain import sha256_digest
from morrow.core.skills.identity import (
    SkillIdentityError,
    validate_display_version,
    validate_skill_id,
    validate_skv_id,
)
from morrow.core.skills.trust import SourceKind, TrustEvidence, TrustLevel, effective_trust

from .tree import CanonicalPackageTree

ENVELOPE_NAME = "managed-version.json"
ENVELOPE_SCHEMA = "managed-version-v1"
MAX_ENVELOPE_BYTES = 2 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EnvelopeError(ValueError):
    """An envelope is missing, malformed or drifted from the package tree."""


def _canonical_envelope_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_envelope_payload(
    *,
    version_id: str,
    display_version: str | None,
    skill_id: str,
    source_kind: SourceKind,
    scope_id: str | None,
    tree: CanonicalPackageTree,
    evidence_refs: tuple[str, ...],
    local_review_ok: bool,
    controlled_approval_ref: str | None,
    installed_at: datetime,
) -> dict:
    """The only envelope writer; a package never supplies its own envelope."""
    trust = effective_trust(
        TrustEvidence(
            source_kind=source_kind,
            local_review_ok=local_review_ok,
            controlled_approval_ref=controlled_approval_ref,
        )
    )
    payload = {
        "schema": ENVELOPE_SCHEMA,
        "version_id": validate_skv_id(version_id),
        "display_version": validate_display_version(display_version),
        "skill_id": validate_skill_id(skill_id),
        "source_kind": source_kind.value,
        "scope_id": scope_id,
        "tree_digest": tree.tree_digest,
        "file_count": len(tree.entries),
        "total_bytes": tree.total_bytes,
        "effective_trust": trust.value,
        "evidence_refs": list(evidence_refs[:8]),
        "controlled_approval_ref": controlled_approval_ref,
        "files": [entry.canonical() for entry in tree.entries],
        "installed_at_unix": int(installed_at.timestamp()),
    }
    payload["envelope_sha256"] = sha256_digest(_canonical_envelope_bytes(payload))
    return payload


def write_envelope(version_dir: Path, payload: dict) -> Path:
    """Atomically publish an envelope under the Morrow-managed version dir."""
    target = version_dir / ENVELOPE_NAME
    tmp = version_dir / f".{ENVELOPE_NAME}.tmp"
    tmp.write_bytes(_canonical_envelope_bytes(payload))
    tmp.replace(target)
    return target


def read_envelope(version_dir: Path) -> dict:
    """Read and structurally validate a Morrow-written envelope."""
    path = version_dir / ENVELOPE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError as exc:
        raise EnvelopeError("managed-version.json is missing") from exc
    except OSError as exc:
        raise EnvelopeError("managed-version.json could not be read") from exc
    try:
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise EnvelopeError("managed-version.json must be a regular file")
        if info.st_size > MAX_ENVELOPE_BYTES:
            raise EnvelopeError("managed-version.json exceeds the bounded size")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ENVELOPE_BYTES:
                raise EnvelopeError("managed-version.json exceeds the bounded size")
            chunks.append(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        raise EnvelopeError("managed-version.json could not be read") from exc
    finally:
        os.close(fd)
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EnvelopeError("managed-version.json is not valid JSON") from exc
    _validate_envelope_structure(payload, verify_digest=True)
    return payload


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _require_int(payload: dict, key: str, *, minimum: int = 0) -> None:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise EnvelopeError(f"managed-version.json field {key!r} is invalid")


def _validate_envelope_structure(payload: object, *, verify_digest: bool) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != ENVELOPE_SCHEMA:
        raise EnvelopeError("managed-version.json is not a Morrow envelope")
    if verify_digest:
        expected = payload.get("envelope_sha256")
        if not isinstance(expected, str) or not _is_sha256(expected):
            raise EnvelopeError("managed-version.json digest does not verify")
        try:
            actual = sha256_digest(
                _canonical_envelope_bytes(
                    {key: value for key, value in payload.items() if key != "envelope_sha256"}
                )
            )
        except (TypeError, ValueError) as exc:
            raise EnvelopeError("managed-version.json digest does not verify") from exc
        if expected != actual:
            raise EnvelopeError("managed-version.json digest does not verify")

    version_id = payload.get("version_id")
    if not isinstance(version_id, str):
        raise EnvelopeError("managed-version.json version_id is invalid")
    display_version = payload.get("display_version")
    if display_version is not None and not isinstance(display_version, str):
        raise EnvelopeError("managed-version.json display_version is invalid")
    skill_id = payload.get("skill_id")
    if not isinstance(skill_id, str):
        raise EnvelopeError("managed-version.json skill_id is invalid")
    source_kind = payload.get("source_kind")
    if not isinstance(source_kind, str):
        raise EnvelopeError("managed-version.json source_kind is invalid")
    effective = payload.get("effective_trust")
    if not isinstance(effective, str):
        raise EnvelopeError("managed-version.json effective_trust is invalid")
    scope_id = payload.get("scope_id")
    if scope_id is not None and not isinstance(scope_id, str):
        raise EnvelopeError("managed-version.json scope_id is invalid")
    tree_digest = payload.get("tree_digest")
    if not _is_sha256(tree_digest):
        raise EnvelopeError("managed-version.json tree_digest is invalid")
    if not _is_sha256(payload.get("envelope_sha256")):
        raise EnvelopeError("managed-version.json envelope_sha256 is invalid")
    _require_int(payload, "file_count")
    _require_int(payload, "total_bytes")
    installed_at = payload.get("installed_at_unix")
    if not isinstance(installed_at, int) or isinstance(installed_at, bool):
        raise EnvelopeError("managed-version.json installed_at_unix is invalid")
    if not (-62135596800 <= installed_at <= 253402300799):
        raise EnvelopeError("managed-version.json installed_at_unix is out of range")
    try:
        validate_skv_id(version_id)
        validate_display_version(display_version)
        validate_skill_id(skill_id)
        SourceKind(source_kind)
        TrustLevel(effective)
    except (SkillIdentityError, ValueError) as exc:
        raise EnvelopeError(str(exc)) from exc

    evidence_refs = payload.get("evidence_refs")
    if (
        not isinstance(evidence_refs, list)
        or len(evidence_refs) > 8
        or any(not isinstance(item, str) or len(item) > 256 for item in evidence_refs)
    ):
        raise EnvelopeError("managed-version.json evidence_refs is invalid")
    controlled_approval_ref = payload.get("controlled_approval_ref")
    if controlled_approval_ref is not None and (
        not isinstance(controlled_approval_ref, str)
        or not controlled_approval_ref
        or len(controlled_approval_ref) > 256
    ):
        raise EnvelopeError("managed-version.json controlled_approval_ref is invalid")
    files = payload.get("files")
    if not isinstance(files, list) or len(files) != payload["file_count"]:
        raise EnvelopeError("managed-version.json files is invalid")
    for entry in files:
        if not isinstance(entry, dict):
            raise EnvelopeError("managed-version.json files is invalid")
        if (
            not isinstance(entry.get("path"), str)
            or not entry["path"]
            or entry.get("kind") != "file"
            or not isinstance(entry.get("exec"), bool)
            or not isinstance(entry.get("size"), int)
            or isinstance(entry["size"], bool)
            or entry["size"] < 0
            or not _is_sha256(entry.get("sha256"))
        ):
            raise EnvelopeError("managed-version.json files is invalid")


def verify_envelope_against_tree(payload: dict, tree: CanonicalPackageTree) -> None:
    """Drift detection: envelope file metadata and tree digest must match."""
    _validate_envelope_structure(payload, verify_digest=False)
    if payload["tree_digest"] != tree.tree_digest:
        raise EnvelopeError("envelope tree digest drifted from the package tree")
    if payload["total_bytes"] != tree.total_bytes:
        raise EnvelopeError("envelope total byte count drifted from the package tree")
    if payload["file_count"] != tree.file_count:
        raise EnvelopeError("envelope file count drifted from the package tree")
    declared = sorted(payload["files"], key=lambda entry: entry["path"])
    actual = sorted((entry.canonical() for entry in tree.entries), key=lambda entry: entry["path"])
    if actual != declared:
        raise EnvelopeError("envelope file metadata drifted from the package tree")
