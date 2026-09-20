import os

import pytest

from morrow.adapters.state.yaml import WorkspaceIndexYamlStore
from morrow.core.models import WorkspaceIndexEntry
from morrow.services.directories import DirectoryService
from morrow.services.workspace import DataRoot, WorkspaceError, WorkspaceService


def test_picker_bounds_creation_aliases_and_identity(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    picker = DirectoryService((root,))
    project = picker.create(str(root), "project")
    (root / "alias").symlink_to(project, target_is_directory=True)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    assert picker.validate(root / "alias") == project
    assert picker.browse(str(root))["items"] == [{"name": "project", "path": str(project)}]
    assert picker.browse(str(root))["parent"] is None
    assert picker.browse()["roots"] == [str(root)]
    for path in (outside, root / "escape", root / ".."):
        with pytest.raises(WorkspaceError):
            picker.validate(path)
    for name in ("", "..", "../escape", "a/b", "a\\b", "\x00"):
        with pytest.raises(WorkspaceError):
            picker.create(str(root), name)
    with pytest.raises(WorkspaceError, match="already exists"):
        picker.create(str(root), "project")
    service = WorkspaceService(
        DataRoot(tmp_path / "data"), WorkspaceIndexYamlStore(tmp_path / "data")
    )
    first = service.confirm(picker.resolve_workspace(service, str(project)))
    assert service.confirm(picker.resolve_workspace(service, str(root / "alias"))) == first
    assert not (project / ".git").exists()


def test_picker_git_boundary_and_bounded_list(tmp_path):
    (tmp_path / ".git").mkdir()
    root = tmp_path / "allowed"
    root.mkdir()
    picker = DirectoryService((root,))
    service = WorkspaceService(
        DataRoot(tmp_path / "data"), WorkspaceIndexYamlStore(tmp_path / "data")
    )
    with pytest.raises(WorkspaceError, match="outside"):
        picker.resolve_workspace(service, str(root))
    for i in range(5):
        (root / str(i)).mkdir()
    result = picker.browse(str(root), limit=2)
    assert len(result["items"]) == 2 and result["truncated"]


def test_picker_rejects_component_replaced_after_validation(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    picker = DirectoryService((root,))
    original = os.open

    def swapped(path, flags, **kwargs):
        if path == "child":
            child.rmdir()
            child.symlink_to(outside, target_is_directory=True)
        return original(path, flags, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    with pytest.raises(WorkspaceError, match="changed"):
        picker.create(str(child), "must-not-exist")
    assert not (outside / "must-not-exist").exists()


def test_workspace_metadata_remove_reopen_relink_and_current_index(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "data"
    store = WorkspaceIndexYamlStore(state)
    assert (
        store.update(
            lambda value: value.model_copy(
                update={
                    "workspaces": {
                        "ws_current": WorkspaceIndexEntry(
                            workspace_id="ws_current",
                            path=str(project),
                            display_name="Current",
                        )
                    }
                }
            ),
            expected_revision=0,
        ).status.value
        == "ok"
    )
    original = (state / "workspace-index.yaml").read_text()
    service = WorkspaceService(DataRoot(state), store)
    assert service.listing()["items"][0]["last_used_at"] is None
    assert (state / "workspace-index.yaml").read_text() == original
    service.update_entry("ws_current", expected_revision=1, display_name="Renamed", touch=True)
    assert store.load().value.schema_version == 2
    assert (state / "workspace-index.yaml.bak").read_text() == original
    with pytest.raises(WorkspaceError, match="changed"):
        service.update_entry("ws_current", expected_revision=1, removed=True)
    service.update_entry("ws_current", expected_revision=2, removed=True)
    assert service.listing()["items"] == []
    assert project.is_dir()
    assert service.confirm(service.resolve(project)).workspace_id == "ws_current"
    service.update_entry("ws_current", removed=False, touch=True)
    moved = tmp_path / "moved"
    project.rename(moved)
    assert not service.listing()["items"][0]["available"]
    service.relink("ws_current", moved, expected_revision=5)
    assert service.get("ws_current").path == str(moved)
    assert service.listing()["items"][0]["display_name"] == "Renamed"


def test_git_initialization_is_explicit_independent_action(tmp_path):
    picker = DirectoryService((tmp_path,))
    project = picker.create(str(tmp_path), "project")
    assert not (project / ".git").exists()
    picker.initialize_git(str(project))
    assert (project / ".git").is_dir()
    with pytest.raises(WorkspaceError, match="already exists"):
        picker.initialize_git(str(project))


def test_registration_revalidates_selection_scope_inside_index_transaction(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    project = root / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    picker = DirectoryService((root,))
    service = WorkspaceService(
        DataRoot(tmp_path / "data"), WorkspaceIndexYamlStore(tmp_path / "data")
    )
    resolution = picker.resolve_workspace(service, str(project))
    project.rmdir()
    project.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="outside"):
        service.confirm(resolution, validate_path=picker.validate)
    assert service.listing()["items"] == []
