"""Session settings with independently explicit workspace/global default writes."""

from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.models import ChatSettings, ProtocolModel


class SettingsRequest(ProtocolModel):
    scope: Literal["session", "workspace", "global"] = "session"
    expected_revision: int = Field(ge=0, strict=True)
    settings: ChatSettings


def settings_routes(host, parse):
    def context(request):
        return host.context.workspaces.get(request.path_params["workspace_id"])

    async def settings(request):
        sid = request.path_params["session_id"]
        if request.method == "GET":
            return JSONResponse(
                await host.execute_query(lambda: context(request).chat.settings.view(sid))
            )
        body = await parse(request, SettingsRequest)

        def apply():
            c = context(request)
            value = c.chat.settings.put(sid, body.scope, body.settings, body.expected_revision)
            if body.scope == "session":
                c.chat.streams.changed(sid)
            else:
                contexts = c.workspaces.contexts.values() if body.scope == "global" else (c,)
                for ctx in contexts:
                    for subscribed in tuple(ctx.chat.streams.states):
                        ctx.chat.streams.changed(subscribed)
            return value

        return JSONResponse(await host.execute_command(apply))

    return [
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/settings",
            settings,
            methods=["GET", "POST"],
        )
    ]
