"""Session-scoped permission projections; the existing approval route owns decisions."""

from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.application.chat_permissions import permissions_view, revoke_permission
from morrow.core.models import ProtocolModel


class PermissionRevokeRequest(ProtocolModel):
    command_id: str = Field(pattern=r"^cmd_[A-Za-z0-9_-]+$", max_length=128)
    kind: Literal["grant", "session_scope"]
    subject_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0, strict=True)


def permission_routes(host, parse):
    def context(request):
        return host.context.workspaces.get(request.path_params["workspace_id"])

    async def permissions(request):
        sid = request.path_params["session_id"]
        if request.method == "GET":
            params = request.query_params
            return JSONResponse(
                await host.execute_query(
                    lambda: permissions_view(
                        context(request),
                        sid,
                        run_id=params.get("run_id"),
                        run_cursor=params.get("run_cursor"),
                        grant_cursor=params.get("grant_cursor"),
                        scope_cursor=params.get("scope_cursor", ""),
                    )
                )
            )
        body = await parse(request, PermissionRevokeRequest)
        return JSONResponse(
            await host.execute_command(lambda: revoke_permission(context(request), sid, body))
        )

    return [
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/permissions",
            permissions,
            methods=["GET", "POST"],
        )
    ]
