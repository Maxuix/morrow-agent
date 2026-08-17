from __future__ import annotations

import multiprocessing
import os

import pytest
import yaml

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.adapters.state.yaml import (
    GlobalConfigYamlStore,
    ProjectStateYamlStore,
    WorkspaceIndexYamlStore,
)
from morrow.bootstrap import build_application
from morrow.core.models import Preferences, Profile, StateLoadStatus, StateWriteStatus
from morrow.services.workspace import (
    DataRoot,
    WorkspaceError,
    WorkspaceService,
    WorkspaceWriterLock,
)


def _confirm_workspace_in_process(state_root, project, ready, start, results):
    class ProcessIdSource:
        def new_id(self, prefix):
            return f"{prefix}_process_{os.getpid()}"

    service = WorkspaceService(
        DataRoot(state_root),
        WorkspaceIndexYamlStore(state_root),
        id_source=ProcessIdSource(),
    )
    resolution = service.resolve(project)
    ready.put(True)
    start.wait(timeout=10)
    try:
        identity = service.confirm(resolution)
        results.put(("ok", identity.workspace_id))
    except Exception as exc:  # pragma: no cover - asserted through the parent process
        results.put(("error", type(exc).__name__))


def _hold_workspace_writer_lock(state_root, workspace_id, acquired, release):
    with WorkspaceWriterLock(DataRoot(state_root), workspace_id):
        acquired.set()
        release.wait(timeout=10)


def _mutate_profile_in_process(
    state_root,
    workspace_id,
    action,
    expected_revision,
    ready,
    start,
    results,
):
    store = ProjectStateYamlStore(state_root)
    ready.put(True)
    start.wait(timeout=10)
    if action == "write":
        result = store.write_profile(
            workspace_id,
            Profile(name=f"profile-{os.getpid()}"),
            expected_revision=expected_revision,
        )
    else:
        result = store.clear_profile(workspace_id, expected_revision=expected_revision)
    results.put((result.status.value, result.revision))


def test_workspace_confirmation_is_the_only_id_publication(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    resolution = app.workspace_service.resolve(tmp_path)
    assert resolution.status == "candidate"
    assert not (tmp_path / "state" / "workspace-index.yaml").exists()
    identity = app.workspace_service.confirm(resolution)
    assert identity.workspace_id
    assert app.workspace_service.resolve(tmp_path).identity.workspace_id == identity.workspace_id


def test_repeated_confirmation_of_one_stale_candidate_is_idempotent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    stale_candidate = app.workspace_service.resolve(project)

    first = app.workspace_service.confirm(stale_candidate)
    repeated = app.workspace_service.confirm(stale_candidate)

    assert repeated.workspace_id == first.workspace_id
    index = app.index_store.load().value
    assert list(index.workspaces) == [first.workspace_id]


def test_concurrent_confirmation_returns_one_authoritative_workspace_id(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_confirm_workspace_in_process,
            args=(state_root, project, ready, start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for _ in processes:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert [status for status, _ in outcomes] == ["ok", "ok"]
    assert len({workspace_id for _, workspace_id in outcomes}) == 1
    index = WorkspaceIndexYamlStore(state_root).load().value
    assert len(index.workspaces) == 1


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


def test_registered_exact_path_remains_authoritative_after_git_init(tmp_path):
    project = tmp_path / "project"
    child = project / "src"
    child.mkdir(parents=True)
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    (project / ".git").mkdir()

    assert app.workspace_service.resolve(child).identity.workspace_id == identity.workspace_id


def test_existing_path_alias_resolves_to_registered_identity(tmp_path):
    project = tmp_path / "project"
    alias = tmp_path / "project-alias"
    project.mkdir()
    alias.symlink_to(project, target_is_directory=True)
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))

    assert app.workspace_service.resolve(alias).identity.workspace_id == identity.workspace_id


def test_non_git_child_reuses_nearest_registered_parent(tmp_path):
    project = tmp_path / "project"
    child = project / "src" / "package"
    child.mkdir(parents=True)
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))

    assert app.workspace_service.resolve(child).identity.workspace_id == identity.workspace_id


def test_distinct_git_worktree_roots_receive_distinct_identities(tmp_path):
    first = tmp_path / "worktree-a"
    second = tmp_path / "worktree-b"
    first.mkdir()
    second.mkdir()
    (first / ".git").write_text("gitdir: ../repo/.git/worktrees/a\n", encoding="utf-8")
    (second / ".git").write_text("gitdir: ../repo/.git/worktrees/b\n", encoding="utf-8")
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())

    first_identity = app.workspace_service.confirm(app.workspace_service.resolve(first))
    second_identity = app.workspace_service.confirm(app.workspace_service.resolve(second))

    assert first_identity.workspace_id != second_identity.workspace_id


def test_two_workspaces_have_isolated_profile_and_preferences(tmp_path):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    a = app.workspace_service.confirm(app.workspace_service.resolve(project_a))
    b = app.workspace_service.confirm(app.workspace_service.resolve(project_b))
    app.project_store.write_profile(a.workspace_id, Profile(name="A"))
    app.project_store.write_preferences(a.workspace_id, Preferences(language="中文"))
    assert app.project_store.load_profile(b.workspace_id).value is None
    assert app.project_store.load_preferences(b.workspace_id).value is None


@pytest.mark.parametrize(
    ("document_name", "write_method", "clear_method", "first_value", "second_value"),
    [
        (
            "preferences.yaml",
            "write_preferences",
            "clear_preferences",
            Preferences(language="中文"),
            Preferences(language="English"),
        ),
        (
            "profile.yaml",
            "write_profile",
            "clear_profile",
            Profile(name="first"),
            Profile(name="second"),
        ),
    ],
)
def test_clear_persists_revision_and_rejects_stale_recreation(
    tmp_path,
    document_name,
    write_method,
    clear_method,
    first_value,
    second_value,
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    workspace_id = "ws_revision"
    write = getattr(app.project_store, write_method)
    clear = getattr(app.project_store, clear_method)
    path = app.data_root.workspaces_path / workspace_id / document_name

    assert clear(workspace_id, expected_revision=0).revision == 0
    assert not path.exists()
    assert write(workspace_id, first_value, expected_revision=0).revision == 1
    assert write(workspace_id, second_value, expected_revision=1).revision == 2

    cleared = clear(workspace_id, expected_revision=2)
    assert cleared.status == StateWriteStatus.OK
    assert cleared.revision == 3
    assert path.exists()
    primary = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert primary["schema_version"] == 2
    assert primary["state"] == "cleared"
    assert write_method.removeprefix("write_") not in primary
    backup = yaml.safe_load(path.with_suffix(path.suffix + ".bak").read_text(encoding="utf-8"))
    assert backup["revision"] == 2
    assert backup["state"] == "present"
    backup_load = getattr(
        app.project_store,
        f"load_{write_method.removeprefix('write_')}_backup",
    )(workspace_id)
    assert backup_load.status == StateLoadStatus.OK
    assert backup_load.presence.value == "present"
    assert backup_load.revision == 2
    cleared_bytes = path.read_bytes()
    cleared_load = getattr(app.project_store, f"load_{write_method.removeprefix('write_')}")(
        workspace_id
    )
    assert cleared_load.status == StateLoadStatus.OK
    assert cleared_load.presence.value == "cleared"
    assert cleared_load.value is None
    assert cleared_load.revision == 3

    stale = write(workspace_id, first_value, expected_revision=0)
    assert stale.status == StateWriteStatus.REVISION_CONFLICT
    assert stale.revision == 3
    assert path.read_bytes() == cleared_bytes
    idempotent = clear(workspace_id, expected_revision=3)
    assert idempotent.status == StateWriteStatus.OK
    assert idempotent.revision == 3
    assert path.read_bytes() == cleared_bytes

    recreated = write(workspace_id, first_value, expected_revision=3)
    assert recreated.status == StateWriteStatus.OK
    assert recreated.revision == 4


@pytest.mark.parametrize(
    "load_method",
    ["load_preferences", "load_profile"],
)
def test_missing_workspace_document_has_explicit_successful_presence(tmp_path, load_method):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())

    loaded = getattr(app.project_store, load_method)("ws_missing")

    assert loaded.status == StateLoadStatus.OK
    assert loaded.presence.value == "missing"
    assert loaded.value is None
    assert loaded.revision == 0


def test_missing_workspace_backup_is_distinct_from_missing_primary(tmp_path):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())

    primary = app.project_store.load_profile("ws_missing")
    backup = app.project_store.load_profile_backup("ws_missing")

    assert primary.status == StateLoadStatus.OK
    assert primary.presence.value == "missing"
    assert backup.status == StateLoadStatus.CORRUPT
    assert backup.error == "backup_missing"


@pytest.mark.parametrize(
    ("document_name", "load_method", "write_method", "legacy_payload", "replacement"),
    [
        (
            "preferences.yaml",
            "load_preferences",
            "write_preferences",
            {"preferences": {"language": "中文"}},
            Preferences(language="English"),
        ),
        (
            "profile.yaml",
            "load_profile",
            "write_profile",
            {"profile": {"name": "legacy"}},
            Profile(name="updated"),
        ),
    ],
)
def test_version_one_workspace_document_is_read_without_rewrite_and_upgraded_on_mutation(
    tmp_path,
    document_name,
    load_method,
    write_method,
    legacy_payload,
    replacement,
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    path = app.data_root.workspaces_path / "ws_legacy" / document_name
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "revision": 7,
                "updated_at": "2026-08-14T00:00:00+00:00",
                **legacy_payload,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()

    loaded = getattr(app.project_store, load_method)("ws_legacy")

    assert loaded.status == StateLoadStatus.OK
    assert loaded.presence.value == "present"
    assert loaded.revision == 7
    assert path.read_bytes() == before
    written = getattr(app.project_store, write_method)(
        "ws_legacy", replacement, expected_revision=7
    )
    assert written.status == StateWriteStatus.OK
    upgraded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert upgraded["schema_version"] == 2
    assert upgraded["state"] == "present"
    assert upgraded["revision"] == 8


@pytest.mark.parametrize(
    ("document_name", "write_method", "clear_method", "value"),
    [
        (
            "preferences.yaml",
            "write_preferences",
            "clear_preferences",
            Preferences(language="中文"),
        ),
        ("profile.yaml", "write_profile", "clear_profile", Profile(name="valid")),
    ],
)
@pytest.mark.parametrize(
    "invalid_bytes",
    [b"not: [valid", b"schema_version: 99\nrevision: 5\n"],
)
def test_invalid_workspace_documents_are_never_overwritten_by_write_or_clear(
    tmp_path,
    document_name,
    write_method,
    clear_method,
    value,
    invalid_bytes,
):
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    path = app.data_root.workspaces_path / "ws_invalid" / document_name
    path.parent.mkdir(parents=True)
    path.write_bytes(invalid_bytes)

    written = getattr(app.project_store, write_method)("ws_invalid", value, expected_revision=5)
    cleared = getattr(app.project_store, clear_method)("ws_invalid", expected_revision=5)

    assert written.status == StateWriteStatus.FAILED
    assert cleared.status == StateWriteStatus.FAILED
    assert path.read_bytes() == invalid_bytes


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


@pytest.mark.parametrize("action", ["write", "clear"])
@pytest.mark.parametrize(
    "failure_point",
    ["temporary_write", "fsync", "replace", "directory_fsync"],
)
def test_workspace_publication_failures_leave_deterministic_valid_state(
    tmp_path, action, failure_point
):
    state_root = tmp_path / "state"
    workspace_id = "ws_failure"
    baseline = ProjectStateYamlStore(state_root)
    assert (
        baseline.write_profile(workspace_id, Profile(name="before"), expected_revision=0).status
        == StateWriteStatus.OK
    )
    path = state_root / "workspaces" / workspace_id / "profile.yaml"
    before = path.read_bytes()

    def inject(point):
        if point == failure_point:
            raise OSError(f"injected {point}")

    failing = ProjectStateYamlStore(state_root, failure_injector=inject)
    if action == "write":
        result = failing.write_profile(workspace_id, Profile(name="after"), expected_revision=1)
    else:
        result = failing.clear_profile(workspace_id, expected_revision=1)

    assert result.status == StateWriteStatus.FAILED
    loaded = baseline.load_profile(workspace_id)
    assert loaded.status == StateLoadStatus.OK
    if failure_point == "directory_fsync":
        assert loaded.revision == 2
        assert loaded.presence.value == ("present" if action == "write" else "cleared")
    else:
        assert path.read_bytes() == before
        assert loaded.revision == 1
        assert loaded.presence.value == "present"
    backup_path = path.with_suffix(path.suffix + ".bak")
    if backup_path.exists():
        backup = yaml.safe_load(backup_path.read_text(encoding="utf-8"))
        assert backup["schema_version"] == 2
        assert backup["revision"] == 1
        assert backup["state"] == "present"


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


def test_relink_rejects_effective_git_root_owned_by_another_workspace(tmp_path):
    repo = tmp_path / "repo"
    subdirectory = repo / "src"
    other = tmp_path / "other"
    subdirectory.mkdir(parents=True)
    other.mkdir()
    (repo / ".git").mkdir()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    owner = app.workspace_service.confirm(app.workspace_service.resolve(repo))
    second = app.workspace_service.confirm(app.workspace_service.resolve(other))
    before = app.data_root.index_path.read_bytes()

    try:
        app.workspace_service.relink(second.workspace_id, subdirectory)
        raise AssertionError("relink should reject a Git root owned by another workspace")
    except WorkspaceError:
        pass

    assert app.data_root.index_path.read_bytes() == before
    assert app.workspace_service.resolve(repo).identity.workspace_id == owner.workspace_id


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


def test_workspace_writer_lock_rejects_a_separate_process_without_project_writes(tmp_path):
    state_root = tmp_path / "state"
    project = tmp_path / "project"
    project.mkdir()
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_workspace_writer_lock,
        args=(state_root, "ws_process_lock", acquired, release),
    )
    process.start()
    assert acquired.wait(timeout=10)
    try:
        with pytest.raises(WorkspaceError):
            with WorkspaceWriterLock(DataRoot(state_root), "ws_process_lock", timeout=0):
                pass
    finally:
        release.set()
        process.join(timeout=10)

    assert process.exitcode == 0
    assert list(project.iterdir()) == []


@pytest.mark.parametrize(
    ("action", "starting_revision", "expected_success_revision", "expected_presence"),
    [
        ("write", 1, 2, "present"),
        ("clear", 2, 3, "cleared"),
    ],
)
def test_competing_workspace_publications_are_serialized_across_processes(
    tmp_path,
    action,
    starting_revision,
    expected_success_revision,
    expected_presence,
):
    state_root = tmp_path / "state"
    workspace_id = "ws_competing"
    store = ProjectStateYamlStore(state_root)
    assert (
        store.write_profile(
            workspace_id, Profile(name="revision one"), expected_revision=0
        ).revision
        == 1
    )
    if starting_revision == 2:
        assert (
            store.write_profile(
                workspace_id, Profile(name="revision two"), expected_revision=1
            ).revision
            == 2
        )
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_mutate_profile_in_process,
            args=(
                state_root,
                workspace_id,
                action,
                starting_revision,
                ready,
                start,
                results,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for _ in processes:
        assert ready.get(timeout=10) is True
    start.set()
    outcomes = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert sorted(status for status, _ in outcomes) == ["ok", "revision_conflict"]
    assert {revision for _, revision in outcomes} == {expected_success_revision}
    loaded = store.load_profile(workspace_id)
    assert loaded.revision == expected_success_revision
    assert loaded.presence.value == expected_presence
