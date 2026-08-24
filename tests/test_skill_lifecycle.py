from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.skills.lifecycle import SkillLifecycleError, SkillLifecycleNeedsResolution
from morrow.bootstrap import build_application, build_skill_services
from morrow.core.skills.trust import SourceKind


def _source(root: Path, *, name: str = "Demo Skill", body: str = "# demo\n") -> Path:
    source = root / name.casefold().replace(" ", "-")
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "version: 1.0.0\n"
        "morrow.requested_permissions: [read_workspace]\n"
        "morrow.required_tools: [search]\n"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )
    scripts = source / "scripts"
    scripts.mkdir()
    runner = scripts / "run.sh"
    runner.write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8")
    runner.chmod(0o700)
    return source


def _services(tmp_path: Path):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    return app, build_skill_services(app)


def test_validate_and_install_are_bounded_and_install_stays_disabled(tmp_path: Path) -> None:
    app, services = _services(tmp_path)
    source = _source(tmp_path)

    report = services.lifecycle.validate(source)
    assert report.valid is True
    assert report.skill_id == "demo-skill"
    assert report.scripts == ("scripts/run.sh",)
    assert report.dependencies == ("tool:search",)
    assert report.requested_permissions == ("read_workspace",)

    with pytest.raises(SkillLifecycleError, match="confirmation"):
        services.lifecycle.install(source)
    result = services.lifecycle.install(source, confirmed=True)
    assert result.version_id and result.version_id.startswith("skv_")
    assert services.queries.show("demo-skill").binding is None
    assert (app.data_root.root / "skills" / "imported" / "demo-skill").is_dir()


def test_enable_disable_pin_and_replay_use_yaml_binding_authority(tmp_path: Path) -> None:
    _, services = _services(tmp_path)
    source = _source(tmp_path)
    first = services.lifecycle.install(source, confirmed=True)
    second = services.lifecycle.install(source, confirmed=True)

    enabled = services.lifecycle.enable("demo-skill")
    assert enabled.enabled is True
    pinned = services.lifecycle.pin("demo-skill", first.version_id)
    assert pinned.pinned_version_id == first.version_id
    rolled = services.lifecycle.rollback("demo-skill")
    assert rolled.pinned_version_id == second.version_id
    disabled = services.lifecycle.disable("demo-skill")
    assert disabled.enabled is False

    replay = services.lifecycle.enable("demo-skill", command_id=enabled.command_id)
    assert replay.status == "replayed"


def test_install_rejects_identity_conflict_and_protected_sources(tmp_path: Path) -> None:
    _, services = _services(tmp_path)
    first = _source(tmp_path)
    services.lifecycle.install(first, confirmed=True)
    changed = _source(tmp_path, name="Changed Skill", body="# changed\n")
    changed_manifest = changed / "SKILL.md"
    changed_manifest.write_text(
        changed_manifest.read_text(encoding="utf-8").replace("Changed Skill", "Demo Skill"),
        encoding="utf-8",
    )
    with pytest.raises(SkillLifecycleError, match="conflicts"):
        services.lifecycle.install(changed, confirmed=True)

    report = services.lifecycle.validate(first, source_kind=SourceKind.BUILTIN)
    assert report.valid is False
    assert report.errors


def test_remove_requires_confirmation_and_refuses_referenced_versions(tmp_path: Path) -> None:
    _, services = _services(tmp_path)
    source = _source(tmp_path)
    installed = services.lifecycle.install(source, confirmed=True)

    with pytest.raises(SkillLifecycleError, match="confirmation"):
        services.lifecycle.remove("demo-skill", version_id=installed.version_id)

    services.lifecycle.reference_checker = lambda version_id: ("run:one",)
    with pytest.raises(SkillLifecycleError, match="referenced"):
        services.lifecycle.remove(
            "demo-skill",
            version_id=installed.version_id,
            confirmed=True,
        )

    services.lifecycle.reference_checker = lambda version_id: ()
    unbound = services.lifecycle.remove("demo-skill")
    assert unbound.status == "applied"
    services.lifecycle.remove("demo-skill", version_id=installed.version_id, confirmed=True)
    assert not (tmp_path / "state" / "skills" / "imported" / "demo-skill").exists()


def test_recovery_finishes_after_managed_package_publish(tmp_path: Path, monkeypatch) -> None:
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    handle = OperationalStore(app.data_root.root, maintenance_timeout=0).initialize()
    journal = SqliteOperationalJournal(handle)
    services = build_skill_services(app, journal=journal)
    source = _source(tmp_path)

    original_transact = journal.transact

    def fail_once(work):
        monkeypatch.setattr(journal, "transact", original_transact)
        raise RuntimeError("injected finalize boundary")

    monkeypatch.setattr(journal, "transact", fail_once)
    with pytest.raises(SkillLifecycleNeedsResolution):
        services.lifecycle.install(source, command_id="cmd_recover_import", confirmed=True)

    pending = services.lifecycle.operations.load("cmd_recover_import")
    assert pending is not None
    assert pending.phase == "package_applied"
    recovered = services.lifecycle.recover("cmd_recover_import")
    assert recovered.status == "applied"
    assert services.queries.show("demo-skill").versions
    handle.close()


def test_recovery_finishes_after_yaml_publish_without_revision_drift(
    tmp_path: Path, monkeypatch
) -> None:
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    handle = OperationalStore(app.data_root.root, maintenance_timeout=0).initialize()
    journal = SqliteOperationalJournal(handle)
    services = build_skill_services(app, journal=journal)
    source = _source(tmp_path)
    services.lifecycle.install(source, confirmed=True)

    original_transact = journal.transact

    def fail_once(work):
        monkeypatch.setattr(journal, "transact", original_transact)
        raise RuntimeError("injected finalize boundary")

    monkeypatch.setattr(journal, "transact", fail_once)
    with pytest.raises(SkillLifecycleNeedsResolution):
        services.lifecycle.enable("demo-skill", command_id="cmd_recover_yaml")

    pending = services.lifecycle.operations.load("cmd_recover_yaml")
    assert pending is not None
    assert pending.phase == "yaml_applied"
    recovered = services.lifecycle.recover("cmd_recover_yaml")
    assert recovered.status == "applied"
    assert services.extensions.load_global().value.bindings[0].enabled is True
    handle.close()


def test_recovery_finishes_after_package_remove_boundary(tmp_path: Path, monkeypatch) -> None:
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    handle = OperationalStore(app.data_root.root, maintenance_timeout=0).initialize()
    journal = SqliteOperationalJournal(handle)
    services = build_skill_services(app, journal=journal)
    source = _source(tmp_path)
    installed = services.lifecycle.install(source, confirmed=True)

    original_apply = services.lifecycle.bindings.apply_change

    def fail_once(change):
        monkeypatch.setattr(services.lifecycle.bindings, "apply_change", original_apply)
        raise RuntimeError("injected YAML boundary")

    monkeypatch.setattr(services.lifecycle.bindings, "apply_change", fail_once)
    with pytest.raises(SkillLifecycleNeedsResolution):
        services.lifecycle.remove(
            "demo-skill",
            version_id=installed.version_id,
            command_id="cmd_recover_remove",
            confirmed=True,
        )

    pending = services.lifecycle.operations.load("cmd_recover_remove")
    assert pending is not None
    assert pending.phase == "package_applied"
    recovered = services.lifecycle.recover("cmd_recover_remove")
    assert recovered.status == "applied"
    assert services.extensions.load_global().value.bindings == ()
    assert services.catalog.scan_scope().entry("demo-skill") is None
    handle.close()
