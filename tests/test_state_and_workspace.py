from __future__ import annotations

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
)
from morrow.bootstrap import build_application
from morrow.core.models import Handoff, Preferences, Profile, StateLoadStatus, StateWriteStatus
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock


def test_workspace_confirmation_is_the_only_id_publication(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    resolution = app.workspace_service.resolve(tmp_path)
    assert resolution.status == "candidate"
    assert not (tmp_path / "state" / "workspace-index.yaml").exists()
    identity = app.workspace_service.confirm(resolution)
    assert identity.workspace_id
    assert app.workspace_service.resolve(tmp_path).identity.workspace_id == identity.workspace_id


def test_nested_git_workspace_does_not_reuse_registered_parent(tmp_path):
    parent = tmp_path / "parent"
    nested = parent / "nested-repo"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    app.workspace_service.confirm(app.workspace_service.resolve(parent))

    resolution = app.workspace_service.resolve(nested)

    assert resolution.status == "candidate"
    assert resolution.candidate.path == str(nested)
    assert resolution.candidate.git_root == str(nested)


def test_git_workspace_candidate_uses_repository_root_when_started_below_root(tmp_path):
    repo = tmp_path / "repo"
    subdir = repo / "src"
    subdir.mkdir(parents=True)
    (repo / ".git").mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())

    resolution = app.workspace_service.resolve(subdir)

    assert resolution.status == "candidate"
    assert resolution.candidate.path == str(repo)
    assert resolution.candidate.git_root == str(repo)


def test_two_workspaces_have_isolated_profile_and_handoff(tmp_path):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    a = app.workspace_service.confirm(app.workspace_service.resolve(project_a))
    b = app.workspace_service.confirm(app.workspace_service.resolve(project_b))
    app.project_store.write_profile(a.workspace_id, Profile(name="A"))
    app.project_store.write_handoff(a.workspace_id, Handoff(current_goal="A goal"))
    assert app.project_store.load_profile(b.workspace_id).value is None
    assert app.project_store.load_handoff(b.workspace_id).value is None


def test_revision_conflict_does_not_overwrite_document(tmp_path):
    store = GlobalConfigYamlStore(tmp_path)
    first = store.update(
        lambda value: value.model_copy(update={"preferences": Preferences(language="中文")})
    )
    assert first.status == StateWriteStatus.OK
    before = (tmp_path / "config.yaml").read_bytes()
    conflict = store.update(
        lambda value: value.model_copy(update={"preferences": Preferences(language="English")}),
        expected_revision=0,
    )
    assert conflict.status == StateWriteStatus.REVISION_CONFLICT
    assert (tmp_path / "config.yaml").read_bytes() == before


def test_failed_atomic_replace_preserves_source_and_valid_backup(tmp_path):
    store = GlobalConfigYamlStore(tmp_path)
    assert store.update(lambda value: value).status == StateWriteStatus.OK
    before = (tmp_path / "config.yaml").read_bytes()

    def inject(point):
        if point == "replace":
            raise OSError("injected replacement failure")

    failing = GlobalConfigYamlStore(tmp_path, failure_injector=inject)
    result = failing.update(
        lambda value: value.model_copy(update={"preferences": Preferences(language="中文")})
    )
    assert result.status == StateWriteStatus.FAILED
    assert (tmp_path / "config.yaml").read_bytes() == before
    assert failing.load().status == StateLoadStatus.OK


def test_last_valid_backup_can_be_inspected_without_undo_command(tmp_path):
    store = GlobalConfigYamlStore(tmp_path)
    store.update(lambda value: value)
    store.update(
        lambda value: value.model_copy(update={"preferences": Preferences(language="中文")})
    )
    backup = store.document.load_backup()
    assert backup.status == StateLoadStatus.OK
    assert backup.value.preferences.language is None


def test_corrupt_and_future_documents_are_distinguishable(tmp_path):
    store = GlobalConfigYamlStore(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text("not: [valid", encoding="utf-8")
    assert store.load().status == StateLoadStatus.CORRUPT
    path.write_text("schema_version: 99\nrevision: 5\n", encoding="utf-8")
    assert store.load().status == StateLoadStatus.UNSUPPORTED_SCHEMA


def test_relink_retains_workspace_id_and_state(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(old))
    app.project_store.write_profile(identity.workspace_id, Profile(name="keep"))
    updated = app.workspace_service.relink(identity.workspace_id, new)
    assert updated.workspace_id == identity.workspace_id
    assert app.project_store.load_profile(identity.workspace_id).value.profile.name == "keep"


def test_workspace_writer_lock_is_outside_project_and_rejects_second_writer(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    with WorkspaceWriterLock(app.data_root, identity.workspace_id):
        try:
            with WorkspaceWriterLock(app.data_root, identity.workspace_id, timeout=0):
                raise AssertionError("second writer should not acquire")
        except WorkspaceError:
            pass
    assert not (project / f"{identity.workspace_id}.lock").exists()
