"""Immutable managed-version.json envelopes: written and verified by Morrow."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from morrow.core.domain import sha256_digest
from morrow.core.skills.identity import (
    SkillIdentityError,
    validate_display_version,
    validate_skv_id,
)
from morrow.core.skills.trust import SourceKind, TrustEvidence, effective_trust

from .tree import CanonicalPackageTree

ENVELOPE_NAME = "managed-version.json"
ENVELOPE_SCHEMA = "managed-version-v1"


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
        "skill_id": skill_id,
        "source_kind": source_kind.value,
        "scope_id": scope_id,
        "tree_digest": tree.tree_digest,
        "file_count": len(tree.entries),
        "total_bytes": tree.total_bytes,
        "effective_trust": trust.value,
        "evidence_refs": list(evidence_refs[:8]),
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
    if not path.is_file():
        raise EnvelopeError("managed-version.json is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EnvelopeError("managed-version.json is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != ENVELOPE_SCHEMA:
        raise EnvelopeError("managed-version.json is not a Morrow envelope")
    expected = payload.get("envelope_sha256")
    if not isinstance(expected, str) or expected != sha256_digest(
        _canonical_envelope_bytes(
            {key: value for key, value in payload.items() if key != "envelope_sha256"}
        )
    ):
        raise EnvelopeError("managed-version.json digest does not verify")
    try:
        validate_skv_id(payload["version_id"])
        validate_display_version(payload.get("display_version"))
    except SkillIdentityError as exc:
        raise EnvelopeError(str(exc)) from exc
    return payload


def verify_envelope_against_tree(payload: dict, tree: CanonicalPackageTree) -> None:
    """Drift detection: envelope file digests and tree digest must match."""
    if payload.get("tree_digest") != tree.tree_digest:
        raise EnvelopeError("envelope tree digest drifted from the package tree")
    by_path = {entry.relative_path: entry.sha256 for entry in tree.entries}
    declared = {entry["path"]: entry["sha256"] for entry in payload.get("files", [])}
    if by_path != declared:
        raise EnvelopeError("envelope file digests drifted from the package tree")
