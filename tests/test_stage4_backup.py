"""Operational backup bundle and isolated restore verification."""

from __future__ import annotations

import json
import random

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.artifacts import ArtifactService
from morrow.application.backup import OperationalBackupService
from morrow.core.artifacts import ArtifactKind
from morrow.core.domain import DurableSession
from morrow.core.store import OperationalStoreLayout
from morrow.testing import FixedClock, FixedIdSource


def test_backup_contains_database_manifest_artifacts_and_detects_changed_restore_bytes(tmp_path):
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
    artifacts = ArtifactService(
        journal=journal,
        filesystem=FilesystemArtifactStore(OperationalStoreLayout.from_root(tmp_path / "state")),
        workspace_id="ws_1",
        id_source=FixedIdSource(),
    )
    artifact = artifacts.publish_bytes(b"safe output", kind=ArtifactKind.COMMAND_OUTPUT)
    backup = OperationalBackupService(store, journal=journal)
    report = backup.create("fixture")
    bundle = store.layout.backups_dir / report.bundle_name
    assert report.integrity_ok
    assert (bundle / "database.sqlite").is_file()
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "manifest.sha256").is_file()
    assert (bundle / "artifacts" / artifact.filename).read_bytes() == b"safe output"
    assert backup.verify(bundle).ok

    manifest = bundle / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["created_at"] = "2026-08-19T00:00:01Z"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    changed_manifest = backup.verify(bundle)
    assert not changed_manifest.ok
    assert "manifest_changed" in changed_manifest.issues

    target = bundle / "artifacts" / artifact.filename
    target.write_bytes(b"changed")
    verified = backup.verify(bundle)
    assert not verified.ok
    assert "artifact_changed" in verified.issues
    handle.close()
