import pytest

from morrow.core.artifacts import ArtifactError, ArtifactKind
from morrow.core.workspace_changes import FileManifest, WorkspaceChange
from test_stage7_multi_agent_pipeline import WS, PipelineFixture


@pytest.fixture
def fx(tmp_path):
    value = PipelineFixture(tmp_path)
    yield value
    value.close()


@pytest.mark.parametrize("raw", [b"", b"\xef\xbb\xbftext\r\n", b"no newline"])
def test_snapshot_roundtrip_is_internal_and_retained(fx, raw):
    snapshot = fx.artifacts.publish_workspace_snapshot(
        raw, session_id="ses_root", task_run_id="task_root"
    )
    assert snapshot.kind is ArtifactKind.WORKSPACE_SNAPSHOT
    assert fx.artifacts.read_workspace_snapshot(snapshot.artifact_id) == raw
    with pytest.raises(ArtifactError):
        fx.artifacts.read(snapshot.artifact_id, max_bytes=1024)
    manifest = FileManifest(
        exists=True,
        sha256=snapshot.sha256,
        byte_size=len(raw),
        mode=0o755,
        snapshot_id=snapshot.artifact_id,
    )
    row = fx.journal.workspace_changes.insert(
        WorkspaceChange(
            change_id="chg_one",
            workspace_id=WS,
            session_id="ses_root",
            task_run_id="task_root",
            mutation_key="tex_one:1",
            path="a.py",
            operation="replace",
            before=manifest,
            after=manifest,
        )
    )
    assert fx.journal.workspace_changes.get(WS, row.change_id).before.mode == 0o755
    assert fx.journal.list_artifact_references(WS, snapshot.artifact_id)
    assert snapshot.artifact_id in fx.artifacts.retention_report().referenced


def test_sensitive_snapshot_is_rejected_without_modified_bytes(fx):
    with pytest.raises(ArtifactError):
        fx.artifacts.publish_workspace_snapshot(
            b"OPENAI_API_KEY=sk-test-abcdefghijklmnopqrstuv123456",
            session_id="ses_root",
            task_run_id="task_root",
        )
    assert fx.journal.list_artifacts(WS) == ()


def test_snapshot_refs_survive_backup_restore_and_doctor_is_read_only(fx, tmp_path):
    from morrow.adapters.state.journal import SqliteOperationalJournal
    from morrow.adapters.state.operational import OperationalStore
    from morrow.application.backup import OperationalBackupService
    from morrow.application.workspace_change_doctor import inspect_workspace_changes
    from morrow.core.store import StoreOpenMode

    raw = b"user original\r\n"
    snapshot = fx.artifacts.publish_workspace_snapshot(
        raw, session_id="ses_root", task_run_id="task_root"
    )
    manifest = FileManifest(
        exists=True,
        sha256=snapshot.sha256,
        byte_size=len(raw),
        mode=0o755,
        snapshot_id=snapshot.artifact_id,
    )
    fx.journal.workspace_changes.insert(
        WorkspaceChange(
            change_id="chg_backup",
            workspace_id=WS,
            session_id="ses_root",
            task_run_id="task_root",
            mutation_key="key_backup",
            path="a.py",
            operation="replace",
            before=manifest,
            after=manifest,
        )
    )
    counts, issues = {}, []
    inspect_workspace_changes(fx.journal, WS, counts, issues, issue_factory=lambda *args: args)
    assert not issues
    backup = OperationalBackupService(fx.store)
    report = backup.create("changes")
    bundle = fx.store.layout.backups_dir / report.bundle_name
    assert backup.verify(bundle).ok
    target = tmp_path / "restored"
    restored = backup.restore(bundle, target)
    assert restored.target_root == str(target)
    store = OperationalStore(target)
    with store.open(StoreOpenMode.READ_ONLY) as handle:
        journal = SqliteOperationalJournal(handle)
        row = journal.workspace_changes.get(WS, "chg_backup")
        assert row.before.mode == 0o755
        assert journal.list_artifact_references(WS, snapshot.artifact_id)
        assert (store.layout.artifacts_dir / snapshot.filename).read_bytes() == raw
