"""CLI connection to an existing foreground Core, without another writer."""

import json
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import typer

from morrow.interfaces.core_client import CoreClient, CoreConnectionError


def attach(
    workspace_id: str | None = typer.Option(None, "--workspace-id"),
    session_id: str | None = typer.Option(None, "--session-id"),
    core_workspace_id: str | None = typer.Option(None, "--core-workspace-id"),
    message: str | None = typer.Option(None, "--message", "-m"),
    client_message_id: str | None = typer.Option(None, "--client-message-id"),
    action: str | None = typer.Option(
        None, "--action", help="history/rename/pin/unpin/archive/unarchive/fork/new-task/stop"
    ),
    title: str | None = typer.Option(None, "--title"),
    checkpoint_id: str | None = typer.Option(None, "--checkpoint-id"),
    command_id: str | None = typer.Option(None, "--command-id"),
    new_session: bool = typer.Option(False, "--new-session"),
    open_gui: bool = typer.Option(False, "--open-gui"),
    list_sessions: bool = typer.Option(False, "--list"),
    state_root: Path = typer.Option(Path.home() / ".morrow", "--state-root", hidden=True),
):
    """连接已有 Core；/exit 仅断开，/stop 才停止当前运行。"""
    try:
        client = CoreClient.discover(state_root, core_workspace_id)
        if open_gui:
            webbrowser.open(client.base_url.rstrip("/") + "/")
            return
        workspace_id = workspace_id or client.workspace_id
        root = "/v1/workspaces/" + quote(workspace_id, safe="") + "/sessions"
        if list_sessions or (session_id is None and not new_session):
            typer.echo(
                json.dumps(client.request("GET", root + "?archived=false"), ensure_ascii=False)
            )
            return
        if new_session:
            session_id = client.request("POST", root, {"command_id": "cmd_" + uuid4().hex})[
                "result"
            ]["session"]["session_id"]
        path = root + "/" + quote(session_id, safe="")
        session = client.request("GET", path)["session"]
        if action is not None:
            cid = command_id or "cmd_" + uuid4().hex
            if action == "history":
                result = client.request("GET", path + "/timeline")
            elif action in {"rename", "pin", "unpin"}:
                metadata = client.request("GET", path + "/metadata")
                if action == "rename" and not title:
                    raise CoreConnectionError("rename requires --title")
                result = client.request(
                    "PATCH",
                    path + "/metadata",
                    {
                        "command_id": cid,
                        "expected_revision": metadata["revision"],
                        **({"title": title} if action == "rename" else {"pinned": action == "pin"}),
                    },
                )
            elif action in {"archive", "unarchive"}:
                result = client.request(
                    "POST",
                    path + "/" + action,
                    {"command_id": cid, "expected_updated_at": session["updated_at"]},
                )
            elif action == "fork":
                result = client.request(
                    "POST",
                    path + "/fork",
                    {
                        "command_id": cid,
                        **({"checkpoint_id": checkpoint_id} if checkpoint_id else {}),
                    },
                )
            elif action == "new-task":
                result = client.request(
                    "POST", path + "/commands", {"command_id": cid, "action": "task"}
                )
            elif action == "stop":
                queue = client.request("GET", path + "/queue")
                result = client.request(
                    "POST",
                    path + "/control",
                    {
                        "command_id": cid,
                        "action": "stop",
                        "target_agent_run_id": queue["active_agent_run_id"],
                        "expected_revision": queue["revision"],
                    },
                )
            else:
                raise CoreConnectionError("Unsupported attach action")
            typer.echo(json.dumps(result, ensure_ascii=False))
            return
        if message is not None:
            key = client_message_id or "cli." + uuid4().hex
            # Print the non-secret retry identity before sending, so an uncertain
            # connection can be reconciled with exactly the same request.
            typer.echo("client_message_id: " + key, err=True)
            typer.echo(
                json.dumps(
                    client.request(
                        "POST", path + "/interactions", {"client_message_id": key, "text": message}
                    ),
                    ensure_ascii=False,
                )
            )
            return
        typer.echo(f"Connected: {workspace_id} / {session_id}. /exit disconnects; /stop cancels.")
        while True:
            text = typer.prompt("You", prompt_suffix="> ")
            if text == "/exit":
                break
            if text == "/stop":
                queue = client.request("GET", path + "/queue")
                client.request(
                    "POST",
                    path + "/control",
                    {
                        "command_id": "cmd_" + uuid4().hex,
                        "action": "stop",
                        "target_agent_run_id": queue["active_agent_run_id"],
                        "expected_revision": queue["revision"],
                    },
                )
                continue
            key = "cli." + uuid4().hex
            typer.echo("client_message_id: " + key, err=True)
            client.request("POST", path + "/interactions", {"client_message_id": key, "text": text})
            while True:
                receipt = client.request("GET", path + "/interactions/" + key)["receipt"]
                if receipt["status"] in {"settled", "blocked", "withdrawn"}:
                    break
                time.sleep(0.1)
            page = client.request("GET", path + "/timeline")
            for item in page["items"]:
                if item["kind"] == "assistant_message" and item["source"].get(
                    "turn_id"
                ) == receipt.get("turn_id"):
                    typer.echo(item.get("content") or "[正文需从历史详情读取]")
    except (CoreConnectionError, ValueError) as exc:
        typer.echo(
            str(exc)
            if isinstance(exc, CoreConnectionError)
            else "Invalid Core connection arguments",
            err=True,
        )
        raise typer.Exit(2) from None
    except (KeyboardInterrupt, EOFError):
        typer.echo("Disconnected; Core work continues.")


def register(app):
    app.command("attach")(attach)
