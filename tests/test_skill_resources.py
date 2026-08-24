from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.skills.resources import SkillResourceError, SkillResourceService
from morrow.bootstrap import build_application, build_skill_services
from morrow.core.skills.bindings import SkillSelectionMode


def _source(root: Path) -> Path:
    source = root / "resource-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Resource Skill\nversion: 1.0.0\n---\nresource body\n",
        encoding="utf-8",
    )
    (source / "asset.txt").write_text("small asset", encoding="utf-8")
    (source / "asset.bin").write_bytes(b"x" * 17_000)
    return source


def test_frozen_resource_reads_reject_traversal_and_return_artifact_for_binary(
    tmp_path: Path,
) -> None:
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    services = build_skill_services(app)
    installed = services.lifecycle.install(_source(tmp_path), confirmed=True)
    services.lifecycle.enable("resource-skill", selection_mode=SkillSelectionMode.EXPLICIT)
    selection = services.selection.select(
        agent_run_id="arun_resource",
        user_input="@skill:resource-skill",
    ).selections[0]
    published: list[bytes] = []
    resources = SkillResourceService(
        services.packages,
        artifact_publisher=lambda content, **_: published.append(content) or "art_resource",
    )

    inline = resources.read(selection, "asset.txt")
    assert inline.disposition == "inline"
    assert inline.content == b"small asset"
    artifact = resources.read(selection, "asset.bin")
    assert artifact.disposition == "artifact"
    assert artifact.artifact_id == "art_resource"
    assert published == [b"x" * 17_000]
    assert installed.version_id == selection.version_id

    with pytest.raises(SkillResourceError):
        resources.read(selection, "../SKILL.md")
    with pytest.raises(SkillResourceError):
        resources.read(selection, "package/SKILL.md")


def test_frozen_resource_drift_does_not_fallback(tmp_path: Path) -> None:
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    services = build_skill_services(app)
    services.lifecycle.install(_source(tmp_path), confirmed=True)
    services.lifecycle.enable("resource-skill", selection_mode=SkillSelectionMode.EXPLICIT)
    selection = services.selection.select(
        agent_run_id="arun_drift",
        user_input="@skill:resource-skill",
    ).selections[0]
    package_path = (
        services.packages.version_path(
            skill_id=selection.skill_id,
            version_id=selection.version_id,
            source_kind=selection.source_kind,
            scope_id=selection.scope_id,
        )
        / "package"
        / "asset.txt"
    )
    package_path.write_text("drifted", encoding="utf-8")

    with pytest.raises(SkillResourceError, match="unavailable"):
        services.resources.read(selection, "asset.txt")
