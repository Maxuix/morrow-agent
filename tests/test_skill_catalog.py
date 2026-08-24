"""Subplan 65: Skill identity, manifest, tree, envelope, discovery and catalog."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from morrow.adapters.skills.discovery import (
    PackageLoadError,
    scan_source_root,
)
from morrow.adapters.skills.envelope import (
    EnvelopeError,
    build_envelope_payload,
    read_envelope,
    verify_envelope_against_tree,
    write_envelope,
)
from morrow.adapters.skills.manifest_parser import ManifestError, load_manifest
from morrow.adapters.skills.tree import (
    PackageTreeError,
    build_canonical_tree,
)
from morrow.application.skills.catalog import SkillCatalogService
from morrow.core.domain import sha256_digest
from morrow.core.skills.catalog import (
    SkillAvailability,
    SkillConflictStatus,
)
from morrow.core.skills.identity import (
    SkillIdentityError,
    collides,
    skill_id_from_name,
    validate_display_version,
    validate_skill_id,
    validate_skv_id,
)
from morrow.core.skills.trust import (
    SourceKind,
    TrustEvidence,
    TrustLevel,
    effective_trust,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

SKV_ID = "skv_0123456789abcdef"
SKILL_ID = "search-tool"


def _write(root: Path, relative: str, content: str = "content") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_package(
    root: Path,
    *,
    skill_id: str = SKILL_ID,
    source_kind: SourceKind = SourceKind.IMPORTED,
    display_version: str = "1.2.3",
    extra_files: tuple[str, ...] = (),
    manifest_body: str = "name: Search Tool\ndescription: Search helper\n",
) -> tuple[Path, dict]:
    """Build one managed version dir and return (version_dir, envelope)."""
    version_dir = root / skill_id / SKV_ID
    package_root = version_dir / "package"
    _write(package_root, "SKILL.md", f"---\n{manifest_body}---\n\n# {skill_id}\n")
    _write(package_root, "tools.py", "def run():\n    return 'ok'\n")
    for extra in extra_files:
        _write(package_root, extra)
    tree = build_canonical_tree(package_root)
    payload = build_envelope_payload(
        version_id=SKV_ID,
        display_version=display_version,
        skill_id=skill_id,
        source_kind=source_kind,
        scope_id=None,
        tree=tree,
        evidence_refs=(),
        local_review_ok=True,
        controlled_approval_ref=None,
        installed_at=NOW,
    )
    write_envelope(version_dir, payload)
    return version_dir, payload


def _resign_envelope(payload: dict) -> dict:
    signed = dict(payload)
    signed["envelope_sha256"] = sha256_digest(
        json.dumps(
            {key: value for key, value in signed.items() if key != "envelope_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return signed


# --- identity -----------------------------------------------------------------


def test_identity_rules_are_safe() -> None:
    assert validate_skill_id("search-tool") == "search-tool"
    assert skill_id_from_name("Search Tool!") == "search-tool"
    with pytest.raises(SkillIdentityError):
        validate_skill_id("Search Tool")
    with pytest.raises(SkillIdentityError):
        validate_skill_id("a" * 65)
    assert validate_skv_id(SKV_ID) == SKV_ID
    with pytest.raises(SkillIdentityError):
        validate_skv_id("1.2.3")
    with pytest.raises(SkillIdentityError):
        validate_skv_id("../../etc")
    assert validate_display_version("1.2.3") == "1.2.3"
    with pytest.raises(SkillIdentityError):
        validate_display_version("v1/../x")
    with pytest.raises(SkillIdentityError):
        validate_display_version("a\x00b")
    assert collides("A/b", "a/B")
    assert not collides("a-b", "a b")


# --- manifest -----------------------------------------------------------------


def test_manifest_decodes_frontmatter_and_morrow_yaml(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write(
        root,
        "SKILL.md",
        "---\nname: Search Tool\ndescription: A tool\nversion: 1.0.0\nmorrow.requested_trust: imported\nmorrow.requested_permissions: [run_command]\n---\n",
    )
    manifest = load_manifest(root)
    assert manifest.name == "Search Tool"
    assert manifest.display_version == "1.0.0"
    assert manifest.requested_trust is TrustLevel.IMPORTED
    assert manifest.requested_permissions == ("run_command",)

    _write(root, "morrow.yaml", "morrow.name: Search Tool v2\nmorrow.description: override\n")
    merged = load_manifest(root)
    assert merged.name == "Search Tool v2"
    assert merged.description == "override"


def test_manifest_rejects_unknown_keys_and_bad_versions(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write(root, "SKILL.md", "---\nallows: [run_command]\n---\n")
    with pytest.raises(ManifestError):
        load_manifest(root)

    _write(root, "SKILL.md", "---\nname: yes\n---\n")
    with pytest.raises(ManifestError, match="name must be a string"):
        load_manifest(root)
    _write(root, "morrow.yaml", "morrow.version: '../etc'\n")
    with pytest.raises(ManifestError):
        load_manifest(root)


# --- canonical tree ----------------------------------------------------------


def test_canonical_tree_hashes_regular_files_only(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write(root, "SKILL.md", "# hello\n")
    _write(root, "scripts/run.sh", "#!/bin/sh\necho ok\n")
    os.chmod(root / "scripts/run.sh", 0o755)
    tree = build_canonical_tree(root)
    assert tree.file_count == 2
    assert tree.total_bytes > 0
    run = next(entry for entry in tree.entries if entry.relative_path == "scripts/run.sh")
    assert run.exec_mode is True
    assert run.sha256
    # deterministic across scans
    assert build_canonical_tree(root).tree_digest == tree.tree_digest


def test_canonical_tree_rejects_symlinks_and_nonregular_entries(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write(root, "SKILL.md", "# x\n")
    os.symlink(root / "SKILL.md", root / "link.md")
    with pytest.raises(PackageTreeError, match="symlink"):
        build_canonical_tree(root)

    fifo_root = tmp_path / "pkg2"
    _write(fifo_root, "SKILL.md", "# x\n")
    os.mkfifo(fifo_root / "pipe")
    with pytest.raises(PackageTreeError, match="non-regular"):
        build_canonical_tree(fifo_root)

    dir_link = tmp_path / "pkg3"
    _write(dir_link, "SKILL.md", "# x\n")
    os.symlink(dir_link, dir_link / "loop")
    with pytest.raises(PackageTreeError, match="real directories"):
        build_canonical_tree(dir_link)


def test_canonical_tree_rejects_hardlinks(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _write(root, "SKILL.md", "# x\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    os.link(outside, root / "linked.txt")
    with pytest.raises(PackageTreeError, match="hard links"):
        build_canonical_tree(root)


def test_canonical_tree_rejects_collisions_reserved_and_oversize(tmp_path: Path) -> None:
    # Case/Unicode collision detection is a pure rule: the host filesystem may
    # be case-insensitive (macOS/Windows) and would mask the colliding names.
    from morrow.adapters.skills.tree import _reject_normalization_collisions

    with pytest.raises(PackageTreeError, match="colliding"):
        _reject_normalization_collisions(["A.md", "a.md"])
    with pytest.raises(PackageTreeError, match="colliding"):
        _reject_normalization_collisions(["caf\u00e9.md", "cafe\u0301.md"])

    reserved = tmp_path / "reserved"
    _write(reserved, "managed-version.json", "{}")
    with pytest.raises(PackageTreeError, match="reserved"):
        build_canonical_tree(reserved)

    spoof = tmp_path / "spoof"
    _write(spoof, "package", "{}")
    with pytest.raises(PackageTreeError, match="reserved"):
        build_canonical_tree(spoof)


def test_canonical_tree_rejects_non_utf8(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    path = _write(root, "SKILL.md", "# x\n")
    path.write_bytes(b"# \xff\xfe binary\n")
    with pytest.raises(PackageTreeError, match="UTF-8"):
        build_canonical_tree(root)


# --- envelope ----------------------------------------------------------------


def test_envelope_round_trip_and_spoof_rejection(tmp_path: Path) -> None:
    version_dir, payload = _make_package(tmp_path)
    stored = read_envelope(version_dir)
    assert stored["version_id"] == SKV_ID
    assert stored["effective_trust"] == TrustLevel.IMPORTED.value
    tree = build_canonical_tree(version_dir / "package")
    verify_envelope_against_tree(stored, tree)

    tampered = dict(stored)
    tampered["tree_digest"] = "x" * 64
    with pytest.raises(EnvelopeError):
        verify_envelope_against_tree(tampered, tree)


def test_envelope_drift_is_detected(tmp_path: Path) -> None:
    version_dir, _payload = _make_package(tmp_path)
    _write(version_dir / "package", "SKILL.md", "# changed\n")
    tree = build_canonical_tree(version_dir / "package")
    with pytest.raises(EnvelopeError, match="drift"):
        verify_envelope_against_tree(read_envelope(version_dir), tree)


def test_envelope_never_trusts_package_provided_content(tmp_path: Path) -> None:
    version_dir, _payload = _make_package(tmp_path)
    spoof = version_dir / "package" / "managed-version.json"
    spoof.write_text('{"schema": "managed-version-v1"}', encoding="utf-8")
    with pytest.raises(PackageTreeError, match="reserved"):
        build_canonical_tree(version_dir / "package")


# --- discovery ---------------------------------------------------------------


def test_discovery_isolates_scope_and_reports_bounded_failures(tmp_path: Path) -> None:
    global_root = tmp_path / "global"
    ws_root = tmp_path / "ws"
    _make_package(global_root, skill_id="search-tool")
    _make_package(ws_root, skill_id="parse-tool", source_kind=SourceKind.USER_AUTHORED)
    scan = scan_source_root(global_root, source_kind=SourceKind.IMPORTED)
    assert len(scan.packages) == 1
    assert scan.packages[0].scope_id is None

    ws_scan = scan_source_root(ws_root, source_kind=SourceKind.USER_AUTHORED, scope_id="ws_1")
    assert ws_scan.packages[0].scope_id == "ws_1"

    broken = tmp_path / "broken"
    broken.mkdir()
    version_dir = broken / "bad-id" / "skv_11111111"
    version_dir.mkdir(parents=True)
    failures = scan_source_root(broken, source_kind=SourceKind.IMPORTED).failures
    assert len(failures) == 1
    assert isinstance(failures[0], PackageLoadError)
    assert "managed-version.json is missing" in failures[0].errors[0]


def test_discovery_rejects_skill_id_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _make_package(root, skill_id="search-tool")
    # Directory renamed after install: envelope and dir name disagree.
    (root / "search-tool").rename(root / "renamed-tool")
    failures = scan_source_root(root, source_kind=SourceKind.IMPORTED).failures
    assert len(failures) == 1
    assert "does not match envelope" in failures[0].errors[0]


def test_discovery_rejects_directory_symlinks_at_every_package_boundary(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    version_dir, _payload = _make_package(outside)

    skill_link_root = tmp_path / "skill-link-root"
    skill_link_root.mkdir()
    (skill_link_root / SKILL_ID).symlink_to(outside / SKILL_ID, target_is_directory=True)
    result = scan_source_root(skill_link_root, source_kind=SourceKind.IMPORTED)
    assert not result.packages
    assert any("skill directory symlinks" in error.errors[0] for error in result.failures)

    version_link_root = tmp_path / "version-link-root"
    (version_link_root / SKILL_ID).mkdir(parents=True)
    (version_link_root / SKILL_ID / SKV_ID).symlink_to(version_dir, target_is_directory=True)
    result = scan_source_root(version_link_root, source_kind=SourceKind.IMPORTED)
    assert not result.packages
    assert any("version directory symlinks" in error.errors[0] for error in result.failures)

    package_link_root = tmp_path / "package-link-root"
    linked_version, _payload = _make_package(package_link_root)
    real_package = tmp_path / "real-package"
    linked_version.joinpath("package").rename(real_package)
    linked_version.joinpath("package").symlink_to(real_package, target_is_directory=True)
    result = scan_source_root(package_link_root, source_kind=SourceKind.IMPORTED)
    assert not result.packages
    assert any(
        "package directory must be a real directory" in error.errors[0] for error in result.failures
    )


def test_discovery_rejects_version_id_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "root"
    version_dir, _payload = _make_package(root)
    renamed = version_dir.with_name("skv_99999999")
    version_dir.rename(renamed)
    result = scan_source_root(root, source_kind=SourceKind.IMPORTED)
    assert not result.packages
    assert "does not match envelope version_id" in result.failures[0].errors[0]


def test_discovery_recomputes_effective_trust_from_local_source(tmp_path: Path) -> None:
    root = tmp_path / "root"
    version_dir, payload = _make_package(root)
    spoofed = dict(payload)
    spoofed["effective_trust"] = TrustLevel.BUILTIN.value
    spoofed = _resign_envelope(spoofed)
    write_envelope(version_dir, spoofed)
    result = scan_source_root(root, source_kind=SourceKind.IMPORTED)
    assert not result.packages
    assert "effective_trust does not match local provenance" in result.failures[0].errors[0]


def test_discovery_reports_structurally_malformed_envelopes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    version_dir, payload = _make_package(root)
    malformed = dict(payload)
    malformed.pop("version_id")
    write_envelope(version_dir, _resign_envelope(malformed))

    result = scan_source_root(root, source_kind=SourceKind.IMPORTED)
    assert not result.packages
    assert "version_id is invalid" in result.failures[0].errors[0]


def test_discovery_parses_manifest_from_canonical_tree_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    _make_package(root)
    original_read_bytes = Path.read_bytes

    def reject_manifest_reopen(path: Path) -> bytes:
        if path.name in {"SKILL.md", "morrow.yaml"}:
            raise AssertionError("manifest was reopened after tree hashing")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_manifest_reopen)
    result = scan_source_root(root, source_kind=SourceKind.IMPORTED)
    assert len(result.packages) == 1


# --- catalog conflicts and trust ---------------------------------------------


def _catalog(root: Path, *, source_kind: SourceKind = SourceKind.IMPORTED) -> SkillCatalogService:
    return SkillCatalogService({source_kind: (root,)})


def test_catalog_folds_identical_digests_and_blocks_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _make_package(root, skill_id="search-tool", display_version="1.0.0")
    # Second version with identical content: folds into one visible definition.
    other_skv = "skv_99999999"
    version_dir = root / "search-tool" / other_skv
    shutil.copytree(root / "search-tool" / SKV_ID / "package", version_dir / "package")
    tree = build_canonical_tree(version_dir / "package")
    write_envelope(
        version_dir,
        build_envelope_payload(
            version_id=other_skv,
            display_version="1.0.1",
            skill_id="search-tool",
            source_kind=SourceKind.IMPORTED,
            scope_id=None,
            tree=tree,
            evidence_refs=(),
            local_review_ok=True,
            controlled_approval_ref=None,
            installed_at=NOW,
        ),
    )
    view = _catalog(root).scan()
    entry = view.entry("search-tool")
    assert entry is not None
    assert len(entry.versions) == 2
    assert entry.definition.availability is SkillAvailability.AVAILABLE
    assert entry.definition.conflict_status is SkillConflictStatus.NONE

    # Different (valid) content for the same id: identity conflict, blocked.
    changed = root / "search-tool" / other_skv / "package"
    (changed / "tools.py").write_text("def run():\n    return 'changed'\n", encoding="utf-8")
    changed_tree = build_canonical_tree(changed)
    write_envelope(
        root / "search-tool" / other_skv,
        build_envelope_payload(
            version_id=other_skv,
            display_version="1.0.1",
            skill_id="search-tool",
            source_kind=SourceKind.IMPORTED,
            scope_id=None,
            tree=changed_tree,
            evidence_refs=(),
            local_review_ok=True,
            controlled_approval_ref=None,
            installed_at=NOW,
        ),
    )
    conflicting = _catalog(root).scan()
    conflicted = conflicting.entry("search-tool")
    assert conflicted.definition.availability is SkillAvailability.CONFLICTED
    assert conflicted.definition.conflict_status is SkillConflictStatus.IDENTITY_CONFLICT
    assert conflicted.newest_available is None


def test_catalog_flags_name_conflicts_across_ids(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _make_package(root, skill_id="search-tool", manifest_body="name: Search Tool\n")
    _make_package(root, skill_id="search-tool-2", manifest_body="name: search tool\n")
    view = _catalog(root).scan()
    first = view.entry("search-tool")
    assert first.definition.conflict_status is SkillConflictStatus.NAME_CONFLICT
    assert first.definition.availability is SkillAvailability.CONFLICTED


def test_effective_trust_from_local_provenance_only() -> None:
    assert effective_trust(TrustEvidence(source_kind=SourceKind.BUILTIN)) is TrustLevel.BUILTIN
    assert effective_trust(TrustEvidence(source_kind=SourceKind.USER_AUTHORED)) is TrustLevel.USER
    assert (
        effective_trust(
            TrustEvidence(source_kind=SourceKind.GENERATED, controlled_approval_ref=None)
        )
        is TrustLevel.UNKNOWN
    )
    assert (
        effective_trust(
            TrustEvidence(source_kind=SourceKind.GENERATED, controlled_approval_ref="drf_x")
        )
        is TrustLevel.GENERATED
    )
    assert (
        effective_trust(TrustEvidence(source_kind=SourceKind.IMPORTED, local_review_ok=False))
        is TrustLevel.UNKNOWN
    )


def test_catalog_exposes_requested_and_effective_trust_separately(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _make_package(
        root,
        source_kind=SourceKind.GENERATED,
        manifest_body="name: Search Tool\nmorrow.requested_trust: builtin\n",
    )
    entry = _catalog(root, source_kind=SourceKind.GENERATED).scan().entry("search-tool")
    # The manifest ask (builtin) is a hint; local evidence (generated, no
    # approval ref) yields UNKNOWN effective Trust.
    assert entry.requested_trust is TrustLevel.BUILTIN
    assert entry.effective_trust is TrustLevel.UNKNOWN
    assert entry.definition.effective_trust is TrustLevel.UNKNOWN


def test_catalog_reports_invalid_packages_without_enabling(tmp_path: Path) -> None:
    root = tmp_path / "root"
    version_dir = root / "search-tool" / SKV_ID
    package_root = version_dir / "package"
    _write(package_root, "SKILL.md", "# invalid skill\n")
    _write(package_root, "morrow.yaml", "morrow.version: '../x'\n")
    tree = build_canonical_tree(package_root)
    write_envelope(
        version_dir,
        build_envelope_payload(
            version_id=SKV_ID,
            display_version="1.0.0",
            skill_id="search-tool",
            source_kind=SourceKind.IMPORTED,
            scope_id=None,
            tree=tree,
            evidence_refs=(),
            local_review_ok=True,
            controlled_approval_ref=None,
            installed_at=NOW,
        ),
    )
    view = _catalog(root).scan()
    assert view.entries == ()
    failures = view.failures
    assert len(failures) == 1
    assert "manifest invalid" in failures[0].errors[0]
