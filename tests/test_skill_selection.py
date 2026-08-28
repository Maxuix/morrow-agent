from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.application.learning.memory_run_projection import build_run_context_projection
from morrow.application.skills.selection import SkillSelectionService
from morrow.bootstrap import build_application, build_session_application, build_skill_services
from morrow.core.models import ModelRef
from morrow.core.skills.bindings import SkillSelectionMode
from morrow.testing import ScriptedModelProvider


def _source(root: Path, *, name: str = "Writer Skill", description: str = "") -> Path:
    source = root / name.casefold().replace(" ", "-")
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: 1.0.0\n"
        "---\n"
        "Use the supplied workspace context to draft a concise report.\n",
        encoding="utf-8",
    )
    return source


def _services(tmp_path: Path, *, workspace_id: str | None = None):
    app = build_application(
        state_root=tmp_path / "state",
        credentials=MemoryCredentialStore(),
    )
    return app, build_skill_services(app, workspace_id=workspace_id)


def test_explicit_and_workspace_default_selection_freezes_context(tmp_path: Path) -> None:
    _, services = _services(tmp_path)
    source = _source(tmp_path, description="draft a concise report")
    installed = services.lifecycle.install(source, confirmed=True)
    services.lifecycle.enable("writer-skill", selection_mode=SkillSelectionMode.EXPLICIT)

    plan = services.selection.select(
        agent_run_id="arun_selection",
        user_input="@skill:writer-skill draft this",
    )

    assert [item.version_id for item in plan.selections] == [installed.version_id]
    assert plan.selections[0].activation_reason == "explicit"
    assert plan.contexts[0].content.startswith("Use the supplied")
    assert plan.selection_digest
    assert plan.context_digest

    # The process-local plan is the exact run evidence. A later Binding change
    # cannot rewrite this selection or its context.
    services.lifecycle.disable("writer-skill")
    assert plan.selections[0].version_id == installed.version_id
    assert plan.contexts[0].version_id == installed.version_id


def test_description_fallback_is_conservative_and_can_be_disabled(tmp_path: Path) -> None:
    _, services = _services(tmp_path)
    source = _source(tmp_path, description="draft a concise report")
    services.lifecycle.install(source, confirmed=True)
    services.lifecycle.enable(
        "writer-skill",
        selection_mode=SkillSelectionMode.DESCRIPTION_MATCH,
    )

    matched = services.selection.select(
        agent_run_id="arun_description",
        user_input="Please draft a concise report",
    )
    assert [item.skill_id for item in matched.selections] == ["writer-skill"]
    assert matched.selections[0].activation_reason == "description_match"

    disabled = SkillSelectionService(
        services.catalog,
        services.bindings,
        services.packages,
        id_source=services.selection.id_source,
        description_fallback_enabled=False,
    ).select(agent_run_id="arun_disabled", user_input="draft a concise report")
    assert not disabled.selections
    assert any(item.reason == "description_fallback_disabled" for item in disabled.omissions)


def test_missing_dependencies_and_unknown_explicit_skill_are_omitted(tmp_path: Path) -> None:
    _, services = _services(tmp_path)
    source = _source(tmp_path)
    manifest = source / "SKILL.md"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "version: 1.0.0", "version: 1.0.0\nmorrow.required_tools: [not_available]"
        ),
        encoding="utf-8",
    )
    services.lifecycle.install(source, confirmed=True)
    services.lifecycle.enable("writer-skill", selection_mode=SkillSelectionMode.EXPLICIT)
    strict_selection = SkillSelectionService(
        services.catalog,
        services.bindings,
        services.packages,
        id_source=services.selection.id_source,
        available_tools=(),
    )
    plan = strict_selection.select(
        agent_run_id="arun_missing",
        user_input="@skill:writer-skill @skill:does-not-exist",
    )

    assert not plan.selections
    assert any(item.reason.startswith("missing_dependency:") for item in plan.omissions)


@pytest.mark.asyncio
async def test_admission_persists_skill_rows_and_injects_low_authority_context(
    tmp_path: Path,
) -> None:
    app, _ = _services(tmp_path)
    source = _source(tmp_path, description="draft a concise report")
    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(["ordinary answer"])
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="test", model_id="test"),
    )
    services = build_skill_services(
        app,
        journal=session_app.persistence.journal,
        workspace_id=identity.workspace_id,
    )
    installed = services.lifecycle.install(
        source,
        scope_id=identity.workspace_id,
        confirmed=True,
    )

    ordinary = await session_app.orchestrator.dispatch("draft this without a Skill")
    assert ordinary.events[-1].payload["finish_reason"] == "stop"
    first_run = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )[0]
    assert first_run.snapshot.skill_selected_count == 0

    services.lifecycle.enable(
        "writer-skill",
        scope_id=identity.workspace_id,
        selection_mode=SkillSelectionMode.EXPLICIT,
    )

    selected_provider = ScriptedModelProvider(["skill answer"])
    selected_session_app = build_session_application(
        app,
        identity,
        provider=selected_provider,
        model=ModelRef(provider_id="test", model_id="test"),
    )
    result = await selected_session_app.orchestrator.dispatch("@skill:writer-skill draft this")
    assert result.events[-1].payload["finish_reason"] == "stop", [
        event.payload for event in result.events
    ]
    runs = selected_session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, selected_session_app.session.session_id
    )
    assert len(runs) == 1
    snapshot = runs[0].snapshot
    assert snapshot.skill_selected_count == 1
    assert snapshot.skill_selection_ids
    assert snapshot.skill_context_ids
    assert snapshot.skill_selection_digest
    assert snapshot.skill_context_digest
    assert snapshot.model_dump_json().__len__() < 64 * 1024
    assert any(
        "Skill context" in (message.content or "")
        and "用于提供与当前任务相关的方法和参考" in (message.content or "")
        for message in selected_provider.stream_calls[0]
    )
    selection = selected_session_app.persistence.journal.get_skill_selection(
        identity.workspace_id, snapshot.skill_selection_ids[0]
    )
    assert selection is not None
    assert selection.version_id == installed.version_id

    # The envelope projection written at install time is exactly the same
    # immutable row used by repeated selected-run synchronization.
    selected_session_app.persistence.journal.transact(
        lambda txn: services.selection.sync_catalog(txn, (selection,))
    )
    selected_session_app.persistence.journal.transact(
        lambda txn: services.selection.sync_catalog(txn, (selection,))
    )
    catalog_version = next(
        item
        for item in services.catalog.scan_scope(identity.workspace_id)
        .entry("writer-skill", scope_id=identity.workspace_id)
        .versions
        if item.version_id == installed.version_id
    )
    stored_version = next(
        item
        for item in selected_session_app.persistence.journal.list_skill_versions(
            workspace_id=identity.workspace_id
        )
        if item.version_id == installed.version_id
    )
    assert stored_version == catalog_version

    # Rebuilding the projection is journal-only; live Catalog/Binding changes
    # are intentionally outside the historical path.
    services.catalog.scan_scope = lambda *_args, **_kwargs: pytest.fail("Catalog was consulted")
    projection = build_run_context_projection(
        selected_session_app.persistence.journal,
        identity.workspace_id,
        snapshot,
        prompt_assembler=session_app.persistence.prompt_assembler,
    )
    assert projection.skill_context is not None
    assert projection.skill_context.entries[0].version_id == installed.version_id


@pytest.mark.asyncio
async def test_unselected_inconsistent_skill_version_does_not_block_run(tmp_path: Path) -> None:
    app, package_services = _services(tmp_path)
    source = _source(tmp_path, name="Unused Skill")
    installed = package_services.lifecycle.install(source, confirmed=True)
    entry = package_services.catalog.scan_scope().entry("unused-skill")
    assert entry is not None
    catalog_version = next(
        item for item in entry.versions if item.version_id == installed.version_id
    )

    project = tmp_path / "project"
    project.mkdir()
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    provider = ScriptedModelProvider(["ordinary answer"])
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="test", model_id="test"),
    )
    session_app.persistence.journal.transact(
        lambda txn: (
            txn.put_skill_definition(entry.definition, updated_at=txn.now()),
            txn.put_skill_version(
                catalog_version.model_copy(update={"provenance": "imported:managed"})
            ),
        )
    )

    result = await session_app.orchestrator.dispatch("ordinary task")

    assert result.events[-1].payload["finish_reason"] == "stop"
    runs = session_app.persistence.journal.list_session_agent_runs(
        identity.workspace_id, session_app.session.session_id
    )
    assert len(runs) == 1
    assert runs[0].snapshot.skill_selected_count == 0
