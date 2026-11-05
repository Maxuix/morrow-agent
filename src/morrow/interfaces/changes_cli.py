"""Review/restore through the existing Core owner, never a second filesystem writer."""

import json
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

import typer

from morrow.interfaces.core_client import CoreClient, CoreConnectionError

changes_app = typer.Typer(help="查看任务文件变更，预览并撤销受控修改。")


def _client(state_root, workspace_id, session_id):
    client = CoreClient.discover(state_root, workspace_id)
    wid = workspace_id or client.workspace_id
    return client, f"/v1/workspaces/{quote(wid, safe='')}/sessions/{quote(session_id, safe='')}"


def _run(call):
    try:
        typer.echo(json.dumps(call(), ensure_ascii=False))
    except (CoreConnectionError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None


@changes_app.command("list")
def listing(
    session: str = typer.Option(..., "--session"),
    task: str = typer.Option(..., "--task"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    state_root: Path = typer.Option(Path.home() / ".morrow", "--state-root"),
    cursor: str | None = typer.Option(None, "--cursor"),
):
    def run():
        client, root = _client(state_root, workspace_id, session)
        return client.request(
            "GET",
            root
            + "/changes?"
            + urlencode({"task_run_id": task, **({"cursor": cursor} if cursor else {})}),
        )

    _run(run)


@changes_app.command("diff")
def diff(
    session: str = typer.Option(..., "--session"),
    task: str = typer.Option(..., "--task"),
    change: str = typer.Option(..., "--change"),
    offset: int = typer.Option(0, "--offset"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    state_root: Path = typer.Option(Path.home() / ".morrow", "--state-root"),
):
    def run():
        client, root = _client(state_root, workspace_id, session)
        return client.request(
            "GET",
            root
            + "/changes/"
            + quote(change, safe="")
            + "/diff?"
            + urlencode({"task_run_id": task, "offset": offset}),
        )

    _run(run)


@changes_app.command("restore-preview")
def preview(
    session: str = typer.Option(..., "--session"),
    task: str = typer.Option(..., "--task"),
    change: list[str] = typer.Option(..., "--change"),
    command_id: str | None = typer.Option(None, "--command-id"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    state_root: Path = typer.Option(Path.home() / ".morrow", "--state-root"),
):
    cid = command_id or "cmd_" + uuid4().hex
    typer.echo("command_id: " + cid, err=True)

    def run():
        client, root = _client(state_root, workspace_id, session)
        return client.request(
            "POST",
            root + "/restores/preview",
            {"command_id": cid, "change_ids": change, "task_run_id": task},
        )

    _run(run)


@changes_app.command("restore")
def restore(
    session: str = typer.Option(..., "--session"),
    preview: str = typer.Option(..., "--preview"),
    preview_digest: str = typer.Option(..., "--preview-digest"),
    command_id: str = typer.Option(..., "--command-id"),
    yes: bool = typer.Option(False, "--yes"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    state_root: Path = typer.Option(Path.home() / ".morrow", "--state-root"),
):
    def run():
        client, root = _client(state_root, workspace_id, session)
        plan = client.request("GET", root + "/restores/" + quote(preview, safe=""))
        if plan["preview_digest"] != preview_digest:
            raise ValueError("预览摘要不一致，请重新预览")
        if not yes:
            typer.echo(json.dumps(plan, ensure_ascii=False, indent=2))
            typer.confirm("确认按此预览恢复这些文件？", abort=True)
        typer.echo("command_id: " + command_id, err=True)
        return client.request(
            "POST",
            root + "/restores",
            {"command_id": command_id, "preview_id": preview, "preview_digest": preview_digest},
        )

    _run(run)


@changes_app.command("restore-status")
def status(
    session: str = typer.Option(..., "--session"),
    operation: str = typer.Option(..., "--operation"),
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    state_root: Path = typer.Option(Path.home() / ".morrow", "--state-root"),
):
    def run():
        client, root = _client(state_root, workspace_id, session)
        return client.request("GET", root + "/restores/" + quote(operation, safe=""))

    _run(run)
