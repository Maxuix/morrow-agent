"""Thin CLI projection for governed Skill lifecycle services."""

from __future__ import annotations

from pathlib import Path

import typer

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.skills.drafts import SkillDraftServiceError
from morrow.application.skills.lifecycle import SkillLifecycleError
from morrow.application.skills.queries import SkillQueryError
from morrow.application.skills.usage import SkillUsageServiceError
from morrow.bootstrap import build_application, build_skill_services
from morrow.core.skills.trust import SourceKind
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode

skill_app = typer.Typer(help="Skill 验证、安装与 Binding 生命周期管理。")


def _open_journal(application):
    store = OperationalStore(application.data_root.root)
    try:
        handle = store.open(StoreOpenMode.READ_WRITE)
    except StorageError as exc:
        if exc.code is not StorageErrorCode.NOT_FOUND:
            raise
        handle = store.initialize()
    return handle, SqliteOperationalJournal(handle)


def _source(value: str) -> SourceKind:
    try:
        return SourceKind(value)
    except ValueError as exc:
        raise typer.BadParameter("source must be imported or generated") from exc


def _error(exc: Exception) -> None:
    if isinstance(exc, (SkillLifecycleError, SkillDraftServiceError)):
        typer.echo(f"Skill 操作失败（{exc.code}）：{exc.message}", err=True)
    else:
        typer.echo(f"Skill 操作失败：{str(exc) or type(exc).__name__}", err=True)


@skill_app.command("list")
def skill_list(
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    all_scopes: bool = typer.Option(False, "--all-scopes"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    services = build_skill_services(application, workspace_id=workspace)
    try:
        scopes = (None, workspace) if all_scopes and workspace else (workspace,)
        for scope_id in scopes:
            for item in services.queries.list(scope_id=scope_id):
                binding = "enabled" if item.enabled else "disabled"
                pin = item.pinned_version_id or "latest"
                typer.echo(
                    f"{item.skill_id}\t{item.availability.value}\t{binding}\t{pin}\t"
                    f"{item.effective_trust.value}"
                )
    except (SkillQueryError, ValueError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc


@skill_app.command("show")
def skill_show(
    skill_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    services = build_skill_services(application, workspace_id=workspace)
    try:
        item = services.queries.show(skill_id, scope_id=workspace)
    except (SkillQueryError, ValueError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    typer.echo(f"id: {item.skill_id}")
    typer.echo(f"name: {item.name}")
    typer.echo(f"availability: {item.availability.value}")
    typer.echo(f"conflict: {item.conflict_status.value}")
    typer.echo(f"effective_trust: {item.effective_trust.value}")
    typer.echo(f"requested_trust: {item.requested_trust.value if item.requested_trust else 'none'}")
    typer.echo(f"binding: {'enabled' if item.enabled else 'disabled'}")
    typer.echo(f"pinned_version: {item.pinned_version_id or 'none'}")
    for version in item.versions:
        typer.echo(
            f"version: {version.version_id}\tdisplay={version.display_version or 'none'}\t"
            f"digest={version.tree_digest}\tsource={version.source_kind.value}"
        )


@skill_app.command("validate")
def skill_validate(
    path: Path,
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    services = build_skill_services(application)
    report = services.lifecycle.validate(path)
    typer.echo(f"valid: {'yes' if report.valid and not report.conflicts else 'no'}")
    if report.skill_id:
        typer.echo(f"skill_id: {report.skill_id}")
    if report.tree_digest:
        typer.echo(f"tree_digest: {report.tree_digest}")
    typer.echo(f"permissions: {', '.join(report.requested_permissions) or 'none'}")
    typer.echo(f"scripts: {len(report.scripts)}")
    typer.echo(f"dependencies: {', '.join(report.dependencies) or 'none'}")
    for error in (*report.conflicts, *report.errors):
        typer.echo(f"error: {error}", err=True)
    if not report.valid or report.conflicts:
        raise typer.Exit(code=2)


@skill_app.command("install")
def skill_install(
    path: Path,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    source: str = typer.Option("imported", "--source"),
    yes: bool = typer.Option(False, "--yes", help="确认安装；默认只显示预览。"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, journal = _open_journal(application)
        services = build_skill_services(application, workspace_id=workspace, journal=journal)
        prepared, report = services.lifecycle.preview_install(
            path,
            source_kind=_source(source),
            scope_id=workspace,
        )
        typer.echo(f"skill_id: {prepared.skill_id}")
        typer.echo(f"tree_digest: {prepared.tree.tree_digest}")
        typer.echo(f"permissions: {', '.join(report.requested_permissions) or 'none'}")
        typer.echo(f"scripts: {len(report.scripts)}")
        typer.echo(f"dependencies: {', '.join(report.dependencies) or 'none'}")
        if report.conflicts:
            raise SkillLifecycleError(
                "conflict", "Skill package conflicts with an existing identity"
            )
        if not yes and not typer.confirm("确认安装（安装后仍保持 disabled）？"):
            raise typer.Exit(code=2)
        result = services.lifecycle.install_prepared(
            prepared,
            report=report,
            confirmed=True,
        )
        typer.echo(f"已安装 {result.skill_id}；Binding: disabled；version: {result.version_id}")
    except (SkillLifecycleError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


def _binding_command(
    operation: str, skill_id: str, workspace: str | None, state_root: Path | None, **kwargs
):
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, journal = _open_journal(application)
        services = build_skill_services(application, workspace_id=workspace, journal=journal)
        result = getattr(services.lifecycle, operation)(skill_id, scope_id=workspace, **kwargs)
        typer.echo(f"{operation}: {result.skill_id} ({result.status})")
        if result.pinned_version_id:
            typer.echo(f"pinned_version: {result.pinned_version_id}")
    except (SkillLifecycleError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("enable")
def skill_enable(
    skill_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    source: str | None = typer.Option(None, "--source"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _binding_command(
        "enable", skill_id, workspace, state_root, source_kind=_source(source) if source else None
    )


@skill_app.command("disable")
def skill_disable(
    skill_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    source: str | None = typer.Option(None, "--source"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _binding_command(
        "disable", skill_id, workspace, state_root, source_kind=_source(source) if source else None
    )


@skill_app.command("pin")
def skill_pin(
    skill_id: str,
    version_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    source: str | None = typer.Option(None, "--source"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _binding_command(
        "pin",
        skill_id,
        workspace,
        state_root,
        version_id=version_id,
        source_kind=_source(source) if source else None,
    )


@skill_app.command("rollback")
def skill_rollback(
    skill_id: str,
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    source: str | None = typer.Option(None, "--source"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _binding_command(
        "rollback",
        skill_id,
        workspace,
        state_root,
        source_kind=_source(source) if source else None,
    )


@skill_app.command("remove")
def skill_remove(
    skill_id: str,
    version_id: str | None = typer.Option(None, "--version"),
    workspace: str | None = typer.Option(None, "--workspace", "--workspace-id"),
    source: str | None = typer.Option(None, "--source"),
    yes: bool = typer.Option(False, "--yes"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _binding_command(
        "remove",
        skill_id,
        workspace,
        state_root,
        version_id=version_id,
        source_kind=_source(source) if source else None,
        confirmed=yes,
    )


def _draft_services(application, workspace: str):
    if not workspace:
        raise typer.BadParameter("Draft 操作需要 --workspace")
    handle, journal = _open_journal(application)
    services = build_skill_services(application, workspace_id=workspace, journal=journal)
    if services.drafts is None:
        handle.close()
        raise SkillDraftServiceError("unavailable", "Draft 服务尚未就绪")
    return handle, services


@skill_app.command("draft-list")
def skill_draft_list(
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, services = _draft_services(application, workspace)
        for draft in services.drafts.list():
            typer.echo(
                f"{draft.draft_id}\t{draft.status.value}\t{draft.skill_id}\t"
                f"revision={draft.revision}\tversion={draft.accepted_version_id or 'none'}"
            )
    except (SkillDraftServiceError, SkillQueryError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("draft-create")
def skill_draft_create(
    candidate_id: str,
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, services = _draft_services(application, workspace)
        draft = services.drafts.create_from_candidate(candidate_id)
        typer.echo(f"draft_id: {draft.draft_id}")
        typer.echo(f"status: {draft.status.value}")
        typer.echo(f"skill_id: {draft.skill_id}")
        typer.echo(f"revision: {draft.revision}")
    except (SkillDraftServiceError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("draft-show")
def skill_draft_show(
    draft_id: str,
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, services = _draft_services(application, workspace)
        view = services.drafts.show(draft_id)
        draft = view.draft
        typer.echo(f"draft_id: {draft.draft_id}")
        typer.echo(f"status: {draft.status.value}")
        typer.echo(f"skill_id: {draft.skill_id}")
        typer.echo(f"revision: {draft.revision}")
        typer.echo(f"tree_digest: {draft.tree_digest}")
        if view.validation is not None:
            typer.echo(f"valid: {'yes' if view.validation.valid else 'no'}")
            for finding in view.validation.findings:
                typer.echo(f"finding: {finding.severity.value}:{finding.code}")
        if view.diff is not None:
            typer.echo(
                f"diff: added={len(view.diff.added)} removed={len(view.diff.removed)} "
                f"changed={len(view.diff.changed)}"
            )
    except (SkillDraftServiceError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("draft-validate")
def skill_draft_validate(
    draft_id: str,
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, services = _draft_services(application, workspace)
        report = services.drafts.revalidate(draft_id)
        typer.echo(f"validation_id: {report.validation_id}")
        typer.echo(f"valid: {'yes' if report.valid else 'no'}")
        for finding in report.findings:
            typer.echo(f"finding: {finding.severity.value}:{finding.code}")
        if not report.valid:
            raise typer.Exit(code=2)
    except (SkillDraftServiceError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("draft-accept")
def skill_draft_accept(
    draft_id: str,
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    yes: bool = typer.Option(False, "--yes", help="确认发布 immutable version；不会启用 Binding。"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        if not yes and not typer.confirm("确认接受 Draft 并发布版本（Binding 保持不变）？"):
            raise typer.Exit(code=2)
        handle, services = _draft_services(application, workspace)
        result = services.drafts.accept(draft_id)
        typer.echo(f"accepted: {result.skill_id}")
        typer.echo(f"version: {result.version_id}")
        typer.echo("binding: unchanged")
    except (SkillDraftServiceError, SkillLifecycleError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("draft-reject")
def skill_draft_reject(
    draft_id: str,
    reason: str = typer.Option(..., "--reason"),
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, services = _draft_services(application, workspace)
        draft = services.drafts.reject(draft_id, reason=reason)
        typer.echo(f"rejected: {draft.draft_id}")
    except (SkillDraftServiceError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


@skill_app.command("usage")
def skill_usage(
    workspace: str = typer.Option(..., "--workspace", "--workspace-id"),
    skill_id: str | None = typer.Option(None, "--skill-id"),
    version_id: str | None = typer.Option(None, "--version"),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    application = build_application(state_root=state_root)
    handle = None
    try:
        handle, services = _draft_services(application, workspace)
        if services.usage is None:
            raise SkillUsageServiceError("Usage 服务尚未就绪")
        for usage in services.usage.list(skill_id=skill_id, version_id=version_id):
            typer.echo(
                f"{usage.usage_id}\t{usage.skill_id}\t{usage.version_id}\t"
                f"{usage.status.value}\ttools={usage.tool_call_count}"
            )
    except (SkillDraftServiceError, SkillUsageServiceError, ValueError, StorageError) as exc:
        _error(exc)
        raise typer.Exit(code=2) from exc
    finally:
        if handle is not None:
            handle.close()


__all__ = ["skill_app"]
