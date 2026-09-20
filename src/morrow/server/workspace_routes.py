"""Human workspace management; never registered as model tools."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.server.protocol import CommandRequest
from morrow.services.workspace import WorkspaceError, WorkspaceWriterLock


class RegisterWorkspace(CommandRequest):
    command_id: str
    action: Literal["open", "create"]
    path: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class ManageWorkspace(CommandRequest):
    command_id: str
    expected_revision: int = Field(ge=0)
    path: str | None = Field(default=None, max_length=4096)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


def workspace_routes(host, parse):
    def registry():
        return host.context.workspaces

    def safe(call):
        try:
            return call()
        except WorkspaceError as exc:
            # Errors from this human management service contain only path/state
            # facts; never subprocess output, credentials or model payloads.
            raise ApplicationError(ApplicationErrorCode.CONFLICT, str(exc)) from None

    def receipt(operation, body, apply, workspace_id=None):
        api = registry().default.api
        cid, digest, replay = api._prepare(
            operation,
            {"workspace_id": workspace_id, **body.model_dump(mode="json")},
            body.command_id,
        )
        if replay is None:
            wid = safe(apply)
            api.journal.transact(
                lambda txn: api._receipt(
                    txn,
                    command_id=cid,
                    operation=operation,
                    digest=digest,
                    session_id=None,
                    result_kind="workspace",
                    result_id=wid,
                    event_cursor=None,
                )
            )
        else:
            wid = replay.result_id
        service = registry().application.workspace_service
        identity = service.get(wid)
        return {
            "workspace": identity.model_dump(mode="json") if identity else None,
            "revision": service._entries().revision,
            "disposition": "replay" if replay else "accepted",
        }

    async def directories(request):
        value = await host.execute_query(
            lambda: safe(lambda: registry().directories.browse(request.query_params.get("path")))
        )
        return JSONResponse(value)

    async def workspaces(request):
        if request.method == "GET":
            return JSONResponse(
                await host.execute_query(lambda: registry().application.workspace_service.listing())
            )
        body = await parse(request, RegisterWorkspace)
        if (body.action == "create") != (body.name is not None):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Create requires a folder name; Open accepts an existing path",
            )

        def apply():
            reg = registry()
            service = reg.application.workspace_service
            reg.check_admission()
            path = (
                reg.directories.create(body.path, body.name)
                if body.action == "create"
                else body.path
            )
            identity = service.confirm(
                reg.directories.resolve_workspace(service, str(path), include_removed=True),
                display_name=body.display_name,
                validate_path=reg.directories.validate,
            )
            service.update_entry(identity.workspace_id, removed=False, touch=True)
            return identity.workspace_id

        return JSONResponse(
            await host.execute_command(lambda: receipt("workspace_register", body, apply))
        )

    async def workspace(request):
        wid = request.path_params["workspace_id"]

        def query():
            service = registry().application.workspace_service
            identity = service.get(wid)
            if identity is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workspace is missing")
            return {
                "workspace": identity.model_dump(mode="json"),
                "revision": service._entries().revision,
            }

        return JSONResponse(await host.execute_query(query))

    async def manage(request):
        wid = request.path_params["workspace_id"]
        action = request.path_params["action"]
        if action not in {"rename", "relink", "remove", "open", "git-init"}:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Unknown workspace action")
        body = await parse(request, ManageWorkspace)
        if (action == "rename") != (body.display_name is not None) or (action == "relink") != (
            body.path is not None
        ):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Workspace action fields do not match"
            )

        def apply():
            reg = registry()
            service = reg.application.workspace_service
            identity = service.get(wid)
            if identity is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workspace is missing")
            if body.expected_revision != service._entries().revision:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Workspace list changed; refresh and retry"
                )
            if action == "open":
                reg.get(wid)
                service.update_entry(wid, touch=True, expected_revision=body.expected_revision)
                return wid
            if action == "rename":
                service.update_entry(
                    wid, display_name=body.display_name, expected_revision=body.expected_revision
                )
                return wid
            reg.require_idle(wid)
            if action == "relink":
                reg.directories.resolve_workspace(service, body.path)
            # Loaded contexts already own their writer lock; unloaded identities
            # must acquire it before management touches their path binding.
            from contextlib import nullcontext

            lock = (
                nullcontext()
                if wid in reg.contexts
                else WorkspaceWriterLock(reg.application.data_root, wid)
            )
            with lock:
                if action == "remove":
                    service.update_entry(
                        wid, removed=True, expected_revision=body.expected_revision
                    )
                    if wid != reg.default.workspace_id:
                        reg.unload(wid)
                elif action == "git-init":
                    reg.directories.initialize_git(identity.path)
                    service.relink(
                        wid, Path(identity.path), expected_revision=body.expected_revision
                    )
                else:
                    service.relink(
                        wid,
                        Path(body.path),
                        expected_revision=body.expected_revision,
                        validate_path=reg.directories.validate,
                    )
                if action in {"relink", "git-init"} and wid in reg.contexts:
                    if wid != reg.default.workspace_id:
                        reg.unload(wid)
                    else:
                        from .composition import _build_management_context

                        replacement = _build_management_context(
                            reg.application,
                            service.get(wid),
                            reg.permission_profile,
                            handle=reg.default.store_handle,
                            journal=reg.default.journal,
                        )
                        reg.default.__dict__.update(replacement.__dict__)
                        reg.default.workspaces = reg
                        reg.default.close = reg.close
                        reg.roots[wid] = Path(service.get(wid).path).resolve()
                        reg.bind(reg.default, service.get(wid))
            return wid

        return JSONResponse(
            await host.execute_command(lambda: receipt("workspace_" + action, body, apply, wid))
        )

    return [
        Route("/v1/directories", directories),
        Route("/v1/workspaces", workspaces, methods=["GET", "POST"]),
        Route("/v1/workspaces/{workspace_id}", workspace),
        Route("/v1/workspaces/{workspace_id}/{action}", manage, methods=["POST"]),
    ]
