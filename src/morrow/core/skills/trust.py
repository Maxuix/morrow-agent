"""Manifest contracts and Trust provenance rules.

Manifest declarations are never authority: ``requested_trust`` and permission
declarations are records of what the package asks for. Effective Trust is
computed purely from local source and lifecycle provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from morrow.core.models import ProtocolModel


class SourceKind(StrEnum):
    BUILTIN = "builtin"
    USER_AUTHORED = "user_authored"
    GENERATED = "generated"
    IMPORTED = "imported"


class TrustLevel(StrEnum):
    """Effective Trust computed from local provenance, never from the package."""

    BUILTIN = "builtin"
    USER = "user"
    GENERATED = "generated"
    IMPORTED = "imported"
    UNKNOWN = "unknown"


class ManifestDocument(ProtocolModel):
    """Decoded, bounded manifest facts; all declarations are non-authoritative."""

    name: str | None = None
    description: str | None = None
    display_version: str | None = None
    requested_trust: TrustLevel | None = None
    requested_permissions: tuple[str, ...] = ()
    raw_morrow_fields: tuple[str, ...] = ()

    def has_identity_fields(self) -> bool:
        return bool(self.name) or bool(self.display_version)


@dataclass(frozen=True, slots=True)
class TrustEvidence:
    """Local evidence used to compute effective Trust (never secrets)."""

    source_kind: SourceKind
    local_review_ok: bool = True
    controlled_approval_ref: str | None = None


def effective_trust(evidence: TrustEvidence) -> TrustLevel:
    """Effective Trust from local provenance and lifecycle evidence only.

    - builtin: shipped with Morrow and provenance-verified;
    - user_authored: the user's own managed directory with local provenance;
    - generated: a controlled Draft/approval record must exist;
    - imported: installed source + tree digest; a local review is required.
    """
    if evidence.source_kind is SourceKind.BUILTIN:
        return TrustLevel.BUILTIN
    if evidence.source_kind is SourceKind.USER_AUTHORED:
        return TrustLevel.USER
    if evidence.source_kind is SourceKind.GENERATED:
        return (
            TrustLevel.GENERATED
            if evidence.controlled_approval_ref is not None
            else TrustLevel.UNKNOWN
        )
    if evidence.source_kind is SourceKind.IMPORTED:
        return TrustLevel.IMPORTED if evidence.local_review_ok else TrustLevel.UNKNOWN
    return TrustLevel.UNKNOWN


def role_label(level: TrustLevel) -> str:
    return {
        TrustLevel.BUILTIN: "随 Morrow 发布",
        TrustLevel.USER: "用户本地来源",
        TrustLevel.GENERATED: "受控 Draft 生成",
        TrustLevel.IMPORTED: "已导入且本地审核",
        TrustLevel.UNKNOWN: "来源未知",
    }[level]


def scope_key(scope_id: str | None) -> str:
    """Deterministic composite-scope key; None means global."""
    return "global" if scope_id is None else scope_id


def composite_identity(scope_id: str | None, source_kind: SourceKind, skill_id: str) -> tuple:
    return (scope_key(scope_id), source_kind.value, skill_id)
