"""Dry-run and exact-target managed orphan cleanup coverage."""

from __future__ import annotations

import random

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import BusyRetryPolicy, OperationalStore
from morrow.application.api import OperationalApplicationService
from morrow.application.artifacts import ArtifactService
from morrow.core.artifacts import ArtifactKind
from morrow.core.domain import DurableSession
from morrow.core.store import OperationalStoreLayout
from morrow.testing import FixedClock, FixedIdSource


def test_orphan_cleanup_is_dry_run_by_default_and_only_removes_unmanaged_files(tmp_path):
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
    files = FilesystemArtifactStore(OperationalStoreLayout.from_root(tmp_path / "state"))
    artifacts = ArtifactService(
        journal=journal, filesystem=files, workspace_id="ws_1", id_source=FixedIdSource()
    )
    metadata = artifacts.publish_bytes(b"kept", kind=ArtifactKind.COMMAND_OUTPUT)
    unmanaged = files.artifacts_dir / "art_unmanaged.artifact"
    unmanaged.write_bytes(b"orphan")
    unmanaged.chmod(0o600)
    api = OperationalApplicationService(
        journal=journal, workspace_id="ws_1", id_source=FixedIdSource(), artifacts=artifacts
    )
    preview = api.cleanup_orphans()
    assert preview.dry_run and preview.eligible == 1 and unmanaged.exists()
    result = api.cleanup_orphans(dry_run=False)
    assert result.removed == 1 and not unmanaged.exists()
    assert files.final_path(metadata.artifact_id).exists()
    handle.close()
