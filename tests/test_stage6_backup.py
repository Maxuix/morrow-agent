"""Stage 6 Backup creation, verification and isolated restore."""

from __future__ import annotations

import hashlib
import json
import os
import random
import stat

from typer.testing import CliRunner

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.extension_yaml import ExtensionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.backup import OperationalBackupService
from morrow.application.doctor import OperationalDoctor
from morrow.bootstrap import build_application, build_skill_services
from morrow.core.artifacts import ArtifactKind
from morrow.core.domain import DurableSession
from morrow.core.skills.bindings import GlobalExtensionDocument
from morrow.interfaces.cli import app as cli_app
from morrow.testing import FixedClock, FixedIdSource


def _store(tmp_path):
    store = OperationalStore(
        tmp_path / "state",
        clock=FixedClock(),
        retry_policy=BusyRetryPolicy(
            busy_timeout_ms=0, sleep=lambda _delay: None, rng=random.Random(0)
        ),
        maintenance_timeout=0,
    )
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    return store, handle, journal


def test_stage6_current_round_trip_without_secret_authorities(tmp_path):
    store, handle, journal = _store(tmp_path)
    artifact = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(store.layout),
        workspace_id="ws_1",
        id_source=FixedIdSource(),
    ).publish_bytes(b"stage6 artifact", kind=ArtifactKind.COMMAND_OUTPUT)
    ExtensionYamlStore(store.layout.data_root).write_global(GlobalExtensionDocument())
    backup = OperationalBackupService(store, journal=journal)

    report = backup.create("stage6-fixture")
    bundle = store.layout.backups_dir / report.bundle_name
    assert report.integrity_ok
    assert (
        json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["manifest_version"] == 2
    )
    assert backup.verify(bundle).ok

    restored = backup.restore(bundle, tmp_path / "restored")
    assert restored.ok
    assert (tmp_path / "restored" / "store" / "operational.sqlite").is_file()
    assert (tmp_path / "restored" / "extensions.yaml").is_file()
    assert (bundle / "artifacts" / artifact.filename).read_bytes() == b"stage6 artifact"
    assert not (tmp_path / "restored" / "credentials").exists()
    assert OperationalStore(tmp_path / "restored").classify().ok
    handle.close()


def test_stage6_current_rejects_manifest_file_tampering(tmp_path):
    store, handle, journal = _store(tmp_path)
    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("stage6-tamper")
    bundle = store.layout.backups_dir / report.bundle_name
    manifest = bundle / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["workspace_ids"] = ["ws_2"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert not backup.verify(bundle).ok
    handle.close()


def test_stage6_current_rejects_symlinks_and_unsafe_restore_parents(tmp_path):
    store, handle, journal = _store(tmp_path)
    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("stage6-paths")
    bundle = store.layout.backups_dir / report.bundle_name

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir()
    os.symlink(real_parent, unsafe_parent / "link")
    restored = backup.restore(bundle, unsafe_parent / "link" / "restored")
    assert not restored.ok
    assert restored.issues == ("restore_target_unavailable",)
    assert not (real_parent / "restored").exists()

    os.symlink(bundle / "database.sqlite", bundle / "unexpected-link")
    verification = backup.verify(bundle)
    assert not verification.ok
    assert "symlink_present" in verification.issues
    handle.close()


def test_stage6_current_copies_only_referenced_managed_skill_and_doctor_detects_drift(tmp_path):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    store = OperationalStore(app.data_root.root, maintenance_timeout=0)
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    services = build_skill_services(app, journal=journal, workspace_id="ws_1")
    source = tmp_path / "skill-source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Backup Skill\nversion: 1.0.0\n---\n# backup\n", encoding="utf-8"
    )
    first = services.lifecycle.install(source, confirmed=True)
    installed = services.lifecycle.install(source, confirmed=True)
    services.lifecycle.enable("backup-skill")

    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("stage6-skill")
    bundle = store.layout.backups_dir / report.bundle_name
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert {item["version_id"] for item in manifest["skill_versions"]} == {
        first.version_id,
        installed.version_id,
    }
    assert (bundle / "skills" / "imported" / "backup-skill" / first.version_id).is_dir()
    assert (bundle / "skills" / "imported" / "backup-skill" / installed.version_id).is_dir()
    assert backup.verify(bundle).ok

    doctor = OperationalDoctor(store).inspect("ws_1")
    assert "skill_package_drift" not in {issue.code for issue in doctor.issues}
    package = app.data_root.root / "skills" / "imported" / "backup-skill" / installed.version_id
    (package / "package" / "SKILL.md").write_text("# drift\n", encoding="utf-8")
    drift = OperationalDoctor(store).inspect("ws_1")
    assert any(issue.code == "skill_package_drift" for issue in drift.issues)

    envelope_path = package / "managed-version.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["effective_trust"] = "builtin"
    envelope["source_kind"] = "builtin"
    envelope["envelope_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in envelope.items() if key != "envelope_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    spoofed = OperationalDoctor(store).inspect("ws_1")
    assert any(issue.code == "skill_package_drift" for issue in spoofed.issues)
    handle.close()


def test_stage6_backup_and_restore_preserve_skill_executable_mode(tmp_path):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
        id_source=FixedIdSource(),
    )
    store = OperationalStore(app.data_root.root, maintenance_timeout=0)
    handle = store.initialize()
    journal = SqliteOperationalJournal(handle)
    journal.create_session(DurableSession(session_id="ses_1", workspace_id="ws_1"))
    services = build_skill_services(app, journal=journal, workspace_id="ws_1")
    source = tmp_path / "executable-skill-source"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Executable Skill\nversion: 1.0.0\n---\n# executable\n", encoding="utf-8"
    )
    scripts = source / "scripts"
    scripts.mkdir()
    script = scripts / "report.sh"
    script.write_text("#!/bin/sh\nprintf 'ok\\n'\n", encoding="utf-8")
    script.chmod(0o755)
    installed = services.lifecycle.install(source, confirmed=True)

    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("executable-skill")
    bundle = store.layout.backups_dir / report.bundle_name
    copied = (
        bundle
        / "skills"
        / "imported"
        / "executable-skill"
        / installed.version_id
        / "package"
        / "scripts"
        / "report.sh"
    )
    assert report.integrity_ok
    assert backup.verify(bundle).ok
    assert copied.is_file()
    assert copied.stat().st_mode & stat.S_IXUSR

    restored_root = tmp_path / "restored"
    restored = backup.restore(bundle, restored_root)
    restored_script = (
        restored_root
        / "skills"
        / "imported"
        / "executable-skill"
        / installed.version_id
        / "package"
        / "scripts"
        / "report.sh"
    )
    assert restored.ok
    assert restored_script.is_file()
    assert restored_script.stat().st_mode & stat.S_IXUSR
    handle.close()


def test_current_backup_is_the_only_state_backup_cli_format(tmp_path):
    root = tmp_path / "state"
    OperationalStore(root, maintenance_timeout=0).initialize().close()
    result = CliRunner().invoke(
        cli_app,
        [
            "state",
            "backup",
            "--name",
            "stage6-cli",
            "--state-root",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = root / "backups" / "operational" / "stage6-cli.bundle" / "manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["manifest_version"] == 2
