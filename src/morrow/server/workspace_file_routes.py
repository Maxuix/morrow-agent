"""Controlled WorkspaceFiles HTTP routes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from morrow.application.workspace_files import (
    WorkspaceFilesApplicationService,
    content_disposition,
)
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.server.protocol import CommandRequest


class WorkspaceFileWriteRequest(CommandRequest):
    command_id: str
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=1024 * 1024)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def workspace_file_routes(host, parse):
    def registry():
        return host.context.workspaces

    def service(request: Request) -> WorkspaceFilesApplicationService:
        wid = request.path_params["workspace_id"]
        identity = registry().require_identity(wid)
        return WorkspaceFilesApplicationService(Path(identity.path), wid)

    def service_for(identity, wid: str) -> WorkspaceFilesApplicationService:
        return WorkspaceFilesApplicationService(Path(identity.path), wid)

    async def resolve_identity(request: Request):
        return await host.execute_query(
            lambda: registry().require_identity(request.path_params["workspace_id"])
        )

    async def tree(request: Request):
        try:
            limit = int(request.query_params.get("limit", "200"))
        except ValueError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "query page size is invalid"
            ) from None
        identity = await resolve_identity(request)
        wid = request.path_params["workspace_id"]
        value = await host.execute_query(
            lambda: service_for(identity, wid).tree(
                request.query_params.get("path", "."),
                limit=limit,
                after=request.query_params.get("after"),
            ),
            blocking=True,
        )
        return JSONResponse(value)

    async def info(request: Request):
        identity = await resolve_identity(request)
        wid = request.path_params["workspace_id"]
        value = await host.execute_query(
            lambda: service_for(identity, wid).info(request.query_params.get("path", "")),
            blocking=True,
        )
        return JSONResponse(
            {
                "schema_version": 1,
                "workspace_id": wid,
                "file": value,
            }
        )

    async def content(request: Request):
        identity = await resolve_identity(request)
        wid = request.path_params["workspace_id"]
        value = await host.execute_query(
            lambda: service_for(identity, wid).content(request.query_params.get("path", "")),
            blocking=True,
        )
        return JSONResponse(
            {
                "schema_version": 1,
                "workspace_id": request.path_params["workspace_id"],
                "file": value,
            }
        )

    async def download(request: Request):
        """预览与受控下载共用的有界字节出口。

        每次请求都重新解析路径与工作区身份；响应带 nosniff，文本/HTML/SVG 不会
        被同源页面当作可直接执行的文档加载（GUI 只用它的字节建对象 URL）。
        """

        identity = await resolve_identity(request)
        wid = request.path_params["workspace_id"]
        raw_path = request.query_params.get("path", "")
        disposition = (
            "attachment" if request.query_params.get("disposition") == "attachment" else "inline"
        )
        data, media_type = await host.execute_query(
            lambda: service_for(identity, wid).download(raw_path),
            blocking=True,
        )
        filename = PurePosixPath(raw_path).name or "download"
        return Response(
            data,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": content_disposition(disposition, filename),
            },
        )

    async def replace(request: Request):
        body = await parse(request, WorkspaceFileWriteRequest)

        def mutate():
            current = service(request)
            context = registry().get(request.path_params["workspace_id"])
            operation = "workspace_file_replace"
            payload = {
                "workspace_id": request.path_params["workspace_id"],
                **body.model_dump(mode="json"),
            }
            cid, digest, replay = context.api._prepare(operation, payload, body.command_id)
            if replay is not None:
                return {
                    "schema_version": 1,
                    "workspace_id": request.path_params["workspace_id"],
                    "file": current.content(body.path),
                    "mutation": None,
                    "disposition": "replay",
                }
            value = current.replace(
                path=body.path,
                content=body.content,
                expected_sha256=body.expected_sha256,
                command_id=cid,
            )
            if value.get("disposition") == "reconciled":
                # 已经生效的写入没有新的变更集，也就不写第二份回执。
                return value
            context.api.journal.transact(
                lambda txn: context.api._receipt(
                    txn,
                    command_id=cid,
                    operation=operation,
                    digest=digest,
                    session_id=None,
                    result_kind="workspace_file",
                    result_id=value["mutation"]["change_set_id"],
                    event_cursor=None,
                )
            )
            value["disposition"] = "accepted"
            return value

        return JSONResponse(await host.execute_command(mutate))

    root = "/v1/workspaces/{workspace_id}/files"
    return [
        Route(root + "/tree", tree, methods=["GET"]),
        Route(root + "/info", info, methods=["GET"]),
        Route(root + "/content", content, methods=["GET"]),
        Route(root + "/content", replace, methods=["PUT"]),
        Route(root + "/download", download, methods=["GET"]),
    ]
