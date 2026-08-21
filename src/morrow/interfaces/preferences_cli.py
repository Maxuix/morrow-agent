"""Dedicated Typer surface for the semantic Preference Inbox."""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

import typer

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.preference_persistence_models import (
    PreferenceProposalStatus,
    PreferenceReviewJobStatus,
)

preference_inbox_app = typer.Typer(help="Semantic Preference proposals: list, preview, and decide.")


def _cli_helpers():
    from morrow.interfaces.cli import (
        _cli_error,
        _close_state,
        _state_services,
    )

    return _cli_error, _close_state, _state_services


def _run_state_command(
    *,
    state_root: Path | None,
    workspace_id: str | None,
    directory: Path,
    write: bool,
    with_reviewer: bool = False,
    action,
) -> None:
    cli_error, close_state, state_services = _cli_helpers()
    handle = None
    try:
        _application, handle, api, _doctor, _backup = state_services(
            state_root=state_root,
            workspace_id=workspace_id,
            directory=directory,
            write=write,
            with_reviewer=with_reviewer,
        )
        action(api)
    except typer.Exit:
        raise
    except Exception as exc:
        cli_error(exc)
        raise typer.Exit(code=2) from None
    finally:
        close_state(handle)


def _command_id(api, command_id: str | None) -> str:
    return command_id or api.id_source.new_id("cmd")


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _emit(value, *, as_json: bool = False) -> None:
    payload = _jsonable(value)
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key, item in payload.items():
            if isinstance(item, (dict, list)):
                item = json.dumps(item, ensure_ascii=False, sort_keys=True)
            typer.echo(f"{key}: {item}")
    else:
        typer.echo(str(payload))


def _emit_page(page, *, as_json: bool) -> None:
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "items": [_jsonable(item) for item in page.items],
                    "next_cursor": page.next_cursor,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    for item in page.items:
        stale = f"stale={item.stale}" if item.stale else "ready"
        typer.echo(
            f"{item.proposal_id}\t{item.operation.operation.value}\t"
            f"{item.operation.scope.value}\t{item.status.value}\t{stale}"
        )
    if page.next_cursor is not None:
        typer.echo(f"next_cursor: {page.next_cursor}")


def _emit_review_job_page(page, *, as_json: bool) -> None:
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "items": [_jsonable(item) for item in page.items],
                    "next_cursor": page.next_cursor,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    for item in page.items:
        failure = item.failure_code.value if item.failure_code is not None else "-"
        typer.echo(
            f"{item.job_id}\t{item.status.value}\tattempt={item.attempt_count}\tfailure={failure}"
        )
    if page.next_cursor is not None:
        typer.echo(f"next_cursor: {page.next_cursor}")


def _review_result_payload(result) -> dict[str, object]:
    return {
        "status": result.status,
        "job_id": result.job_id,
        "learning_review_id": result.learning_review_id,
        "proposal_count": result.proposal_count,
        "duplicate_count": result.duplicate_count,
        "suppressed_count": result.suppressed_count,
        "error_code": result.error_code,
    }


@preference_inbox_app.command("jobs")
def preference_inbox_jobs(
    status: PreferenceReviewJobStatus | None = typer.Option(None, "--status"),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=lambda api: _emit_review_job_page(
            api.list_preference_review_jobs(status=status, cursor=cursor, limit=limit),
            as_json=as_json,
        ),
    )


@preference_inbox_app.command("job")
def preference_inbox_job(
    job_id: str,
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        value = api.get_preference_review_job_view(job_id)
        if value is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Preference Review job is missing"
            )
        _emit(value, as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


@preference_inbox_app.command("status")
def preference_inbox_status(
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _emit(api.preference_review_status(), as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


@preference_inbox_app.command("retry")
def preference_inbox_retry(
    job_id: str,
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _emit(api.retry_preference_review_job(job_id), as_json=as_json),
    )


@preference_inbox_app.command("run-pending")
def preference_inbox_run_pending(
    limit: int = typer.Option(100, "--limit", min=1, max=500),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        results = asyncio.run(api.run_pending_preference_reviews(limit=limit))
        status = api.preference_review_status()
        payload = {
            "attempted": len(results),
            "results": [_review_result_payload(result) for result in results],
            "status": status,
            "daemon": False,
        }
        if as_json:
            _emit(payload, as_json=True)
            return
        typer.echo(f"attempted: {len(results)}")
        for result in results:
            summary = _review_result_payload(result)
            typer.echo(
                f"{summary['status']}\t{summary['job_id'] or summary['learning_review_id'] or '-'}"
                f"\tproposals={summary['proposal_count']}"
                f"\terror={summary['error_code'] or '-'}"
            )
        typer.echo(
            f"pending: {status.pending}\trunning: {status.running}\t"
            f"exhausted: {status.exhausted}\tdaemon: false"
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        with_reviewer=True,
        action=action,
    )


def _confirm_or_exit(question: str, *, yes: bool) -> None:
    if yes or typer.confirm(question, default=False):
        return
    typer.echo("已取消，未写入状态。")
    raise typer.Exit(code=2)


@preference_inbox_app.command("list")
def preference_inbox_list(
    status: PreferenceProposalStatus | None = typer.Option(
        PreferenceProposalStatus.PROPOSED, "--status"
    ),
    job_id: str | None = typer.Option(None, "--job-id"),
    cursor: str | None = typer.Option(None, "--cursor"),
    limit: int = typer.Option(50, "--limit", min=1, max=100),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _emit_page(
            api.list_preference_proposal_views(
                status=status, job_id=job_id, cursor=cursor, limit=limit
            ),
            as_json=as_json,
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


def _proposal_or_error(api, proposal_id: str):
    value = api.get_preference_proposal_view(proposal_id)
    if value is None:
        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Preference proposal is missing")
    return value


@preference_inbox_app.command("show")
def preference_inbox_show(
    proposal_id: str,
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _emit(_proposal_or_error(api, proposal_id), as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


@preference_inbox_app.command("preview")
def preference_inbox_preview(
    proposal_id: str,
    statement: str | None = typer.Option(None, "--statement"),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _emit(
            api.preview_preference_proposal(
                proposal_id, edit=statement if statement is not None else None
            ),
            as_json=as_json,
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=False,
        action=action,
    )


def _accept(
    api,
    proposal_id: str,
    *,
    statement: str | None,
    command_id: str | None,
    expected_row_version: int | None,
    expected_document_revision: int | None,
    expected_target_revision: int | None,
    yes: bool,
    as_json: bool,
) -> None:
    edit = statement if statement is not None else None
    preview = api.preview_preference_proposal(proposal_id, edit=edit)
    _emit(preview, as_json=as_json)
    _confirm_or_exit("确认接受这项 Preference proposal？", yes=yes)
    result = api.accept_preference_proposal(
        proposal_id,
        command_id=_command_id(api, command_id),
        expected_row_version=(
            preview.expected_row_version if expected_row_version is None else expected_row_version
        ),
        expected_document_revision=(
            preview.expected_document_revision
            if expected_document_revision is None
            else expected_document_revision
        ),
        expected_target_revision=(
            preview.expected_target_revision
            if expected_target_revision is None
            else expected_target_revision
        ),
        edit=edit,
    )
    _emit(result, as_json=as_json)


@preference_inbox_app.command("accept")
def preference_inbox_accept(
    proposal_id: str,
    statement: str | None = typer.Option(None, "--statement"),
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    expected_document_revision: int | None = typer.Option(
        None, "--expected-document-revision", min=0
    ),
    expected_target_revision: int | None = typer.Option(None, "--expected-target-revision", min=1),
    yes: bool = typer.Option(False, "--yes", help="跳过确认提示。"),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _accept(
            api,
            proposal_id,
            statement=statement,
            command_id=command_id,
            expected_row_version=expected_row_version,
            expected_document_revision=expected_document_revision,
            expected_target_revision=expected_target_revision,
            yes=yes,
            as_json=as_json,
        ),
    )


@preference_inbox_app.command("edit-and-accept")
def preference_inbox_edit_and_accept(
    proposal_id: str,
    statement: str,
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    expected_document_revision: int | None = typer.Option(
        None, "--expected-document-revision", min=0
    ),
    expected_target_revision: int | None = typer.Option(None, "--expected-target-revision", min=1),
    yes: bool = typer.Option(False, "--yes", help="跳过确认提示。"),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=lambda api: _accept(
            api,
            proposal_id,
            statement=statement,
            command_id=command_id,
            expected_row_version=expected_row_version,
            expected_document_revision=expected_document_revision,
            expected_target_revision=expected_target_revision,
            yes=yes,
            as_json=as_json,
        ),
    )


@preference_inbox_app.command("accept-many")
def preference_inbox_accept_many(
    proposal_ids: list[str],
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_document_revision: int | None = typer.Option(
        None, "--expected-document-revision", min=0
    ),
    yes: bool = typer.Option(False, "--yes", help="跳过确认提示。"),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        previews = tuple(api.preview_preference_proposal(item) for item in proposal_ids)
        for preview in previews:
            _emit(preview, as_json=as_json)
        _confirm_or_exit("确认批量接受这些 Preference proposals？", yes=yes)
        result = api.accept_preference_proposals(
            proposal_ids,
            command_id=_command_id(api, command_id),
            expected_row_versions={
                preview.proposal.proposal_id: preview.expected_row_version for preview in previews
            },
            expected_document_revision=expected_document_revision,
        )
        _emit(result, as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


@preference_inbox_app.command("review")
def preference_inbox_review(
    job_id: str,
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        result = asyncio.run(api.run_preference_review(job_id))
        _emit(
            {
                "job_id": result.job.job_id,
                "proposal_ids": result.pipeline.proposal_ids,
                "proposal_count": len(result.proposals),
            },
            as_json=as_json,
        )

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        with_reviewer=True,
        action=action,
    )


@preference_inbox_app.command("reject")
def preference_inbox_reject(
    proposal_id: str,
    reason: str | None = typer.Option(None, "--reason"),
    suppress: bool = typer.Option(False, "--suppress"),
    command_id: str | None = typer.Option(None, "--command-id"),
    expected_row_version: int | None = typer.Option(None, "--expected-row-version", min=1),
    yes: bool = typer.Option(False, "--yes", help="跳过确认提示。"),
    as_json: bool = typer.Option(False, "--json"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    directory: Path = typer.Option(Path("."), "--dir", exists=True, file_okay=False),
    state_root: Path | None = typer.Option(None, "--state-root", hidden=True),
) -> None:
    def action(api) -> None:
        _emit(_proposal_or_error(api, proposal_id), as_json=as_json)
        _confirm_or_exit("确认拒绝这项 Preference proposal？", yes=yes)
        result = api.reject_preference_proposal(
            proposal_id,
            command_id=_command_id(api, command_id),
            expected_row_version=expected_row_version,
            reason=reason,
            suppress=suppress,
        )
        _emit(result, as_json=as_json)

    _run_state_command(
        state_root=state_root,
        workspace_id=workspace_id,
        directory=directory,
        write=True,
        action=action,
    )


__all__ = ["preference_inbox_app"]
