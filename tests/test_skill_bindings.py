from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.state.extension_yaml import (
    ExtensionYamlConflict,
    ExtensionYamlLoadStatus,
    ExtensionYamlStore,
)
from morrow.application.skills.bindings import SkillBindingError, SkillBindingService
from morrow.core.skills.bindings import (
    GlobalExtensionDocument,
    SkillBinding,
    SkillSelectionMode,
    WorkspaceExtensionDocument,
)
from morrow.core.skills.trust import SourceKind


def test_global_and_workspace_extension_documents_are_independent(tmp_path: Path) -> None:
    store = ExtensionYamlStore(tmp_path)
    global_value = GlobalExtensionDocument(
        bindings=(
            SkillBinding(
                skill_id="global-skill",
                scope="global",
                source_kind=SourceKind.IMPORTED,
            ),
        )
    )
    workspace_value = WorkspaceExtensionDocument(
        scope_id="ws_one",
        bindings=(
            SkillBinding(
                skill_id="workspace-skill",
                scope="workspace",
                scope_id="ws_one",
                selection_mode=SkillSelectionMode.WORKSPACE_DEFAULT,
            ),
        ),
    )
    store.write_global(global_value, expected_revision=0)
    store.write_workspace("ws_one", workspace_value, expected_revision=0)

    assert store.load_global().value.bindings[0].skill_id == "global-skill"
    assert store.load_workspace("ws_one").value.bindings[0].skill_id == "workspace-skill"
    assert "preferences" not in store.global_path.read_text(encoding="utf-8")


def test_extension_yaml_uses_revision_occ_and_last_valid_backup(tmp_path: Path) -> None:
    store = ExtensionYamlStore(tmp_path)
    first = GlobalExtensionDocument(bindings=(SkillBinding(skill_id="demo", scope="global"),))
    second = GlobalExtensionDocument(
        bindings=(SkillBinding(skill_id="demo", scope="global", enabled=True, revision=1),)
    )
    store.write_global(first, expected_revision=0)
    written = store.write_global(second, expected_revision=1)
    assert written.revision == 2
    backup = store.load_global_backup()
    assert backup.status is ExtensionYamlLoadStatus.OK
    assert backup.value.bindings[0].enabled is False

    with pytest.raises(ExtensionYamlConflict):
        store.write_global(first, expected_revision=1)

    store.global_path.write_text("schema_version: 99\nrevision: 9\n", encoding="utf-8")
    future = store.load_global()
    assert future.status is ExtensionYamlLoadStatus.UNSUPPORTED_SCHEMA
    assert future.error == "future_extension_schema"
    assert store.load_global_backup().value.bindings[0].enabled is False


def test_binding_reducer_enforces_scope_and_exact_source(tmp_path: Path) -> None:
    store = ExtensionYamlStore(tmp_path)
    service = SkillBindingService(store, "ws_one")
    with pytest.raises(SkillBindingError):
        service.prepare_change(
            operation="enable",
            skill_id="demo",
            scope="global",
            scope_id="ws_one",
        )

    service.enable(
        "demo",
        scope="workspace",
        scope_id="ws_one",
        source_kind=SourceKind.IMPORTED,
    )
    with pytest.raises(SkillBindingError, match="source"):
        service.enable(
            "demo",
            scope="workspace",
            scope_id="ws_one",
            source_kind=SourceKind.GENERATED,
        )

    binding = service.load("workspace", scope_id="ws_one").value.bindings[0]
    assert binding.enabled is True
    assert binding.source_kind is SourceKind.IMPORTED
    assert binding.revision == 1


def test_workspace_default_is_not_valid_for_global_binding(tmp_path: Path) -> None:
    store = ExtensionYamlStore(tmp_path)
    service = SkillBindingService(store)
    with pytest.raises(ValueError, match="workspace_default"):
        service.enable(
            "demo",
            scope="global",
            selection_mode=SkillSelectionMode.WORKSPACE_DEFAULT,
        )
