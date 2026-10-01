"""Session settings with independently explicit workspace/global default writes."""

from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.application.computer_selection import LocalWindowSelectionRequest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import session_can_start_work
from morrow.core.models import ChatSettings, ProtocolModel
from morrow.core.runtime_policy import ComputerUseSettings


class SettingsRequest(ProtocolModel):
    scope: Literal["session", "workspace", "global"] = "session"
    expected_revision: int = Field(ge=0, strict=True)
    settings: ChatSettings


class ComputerSettingsRequest(ProtocolModel):
    expected_revision: int = Field(ge=0, strict=True)
    settings: ComputerUseSettings


class LocalCandidatesRequest(ProtocolModel):
    pass


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

    async def computer_settings(request):
        sid = request.path_params["session_id"]

        def view():
            c = context(request)
            c.chat.require_session(sid)
            effective, _ = c.chat.settings.resolve(sid)
            return c.chat.computer_settings.view(effective.model)

        if request.method == "GET":
            return JSONResponse(await host.execute_query(view))
        body = await parse(request, ComputerSettingsRequest)

        def apply():
            c = context(request)
            c.chat.require_session(sid)
            c.chat.computer_settings.put(body.settings, expected_revision=body.expected_revision)
            return view()

        return JSONResponse(await host.execute_command(apply))

    def picker_context(request):
        c = context(request)
        sid = request.path_params["session_id"]
        session = c.chat.require_session(sid)
        if not session_can_start_work(session.lifecycle, session.health):
            raise ApplicationError(
                ApplicationErrorCode.NEEDS_RECOVERY, "Session必须活跃且健康才能选择桌面"
            )
        effective, _ = c.chat.settings.resolve(sid)
        return c, effective

    async def computer_candidates(request):
        await parse(request, LocalCandidatesRequest)
        sid = request.path_params["session_id"]

        async def prepare():
            c, effective = picker_context(request)
            return await c.chat.computer_selection.prepare_catalog(permission=effective.permission)

        prepared = await host.execute_preparation(prepare)

        def accept():
            c, effective = picker_context(request)
            return c.chat.computer_selection.accept_catalog(
                sid, prepared, permission=effective.permission
            )

        return JSONResponse(await host.execute_command(accept))

    async def computer_selection(request):
        body = await parse(request, LocalWindowSelectionRequest)
        sid = request.path_params["session_id"]

        def select():
            c, effective = picker_context(request)
            return c.chat.computer_selection.select(
                sid, body, permission=effective.permission, model=effective.model
            )

        return JSONResponse(await host.execute_command(select))

    return [
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/computer-use/candidates",
            computer_candidates,
            methods=["POST"],
        ),
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/computer-use/selection",
            computer_selection,
            methods=["POST"],
        ),
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/computer-use/settings",
            computer_settings,
            methods=["GET", "POST"],
        ),
        Route(
            "/v1/workspaces/{workspace_id}/sessions/{session_id}/settings",
            settings,
            methods=["GET", "POST"],
        ),
    ]
