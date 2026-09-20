"""Session display/lifecycle commands delegate to the existing application owners."""

from datetime import datetime

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.server.projections import session_wire
from morrow.server.protocol import CommandRequest


class MetadataRequest(CommandRequest):
    command_id: str
    expected_revision: int = Field(ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None


class LifecycleRequest(CommandRequest):
    command_id: str
    expected_updated_at: datetime


class ForkRequest(CommandRequest):
    command_id: str
    checkpoint_id: str | None = Field(default=None, max_length=128)
    edit_record_id: str | None = Field(default=None, max_length=128)


def session_routes(host, parse):
    def context(request):
        return host.context.workspaces.get(request.path_params["workspace_id"])

    def idle(manager, sid):
        manager.require_session(sid)
        if sid in manager.drivers or manager.interactions.records.pending(
            manager.workspace_id, sid
        ):
            raise ApplicationError(
                ApplicationErrorCode.BUSY,
                "Stop and withdraw queued work before managing this Session",
            )

    async def metadata(request):
        sid = request.path_params["session_id"]
        if request.method == "GET":
            return JSONResponse(
                await host.execute_query(lambda: context(request).api.get_session_metadata(sid))
            )
        body = await parse(request, MetadataRequest)

        def apply():
            c = context(request)
            result = c.api.update_session_metadata(sid, **body.model_dump())
            c.chat.streams.changed(sid)
            return {"metadata": result.value, "receipt": result.receipt.model_dump(mode="json")}

        return JSONResponse(await host.execute_command(apply))

    async def lifecycle(request):
        sid = request.path_params["session_id"]
        body = await parse(request, LifecycleRequest)

        def apply():
            c = context(request)
            idle(c.chat, sid)
            action = (
                c.api.archive_session
                if request.url.path.endswith("/archive")
                else c.api.unarchive_session
            )
            result = action(sid, **body.model_dump())
            c.chat.runtimes.pop(sid, None)
            c.chat.streams.changed(sid)
            return {
                "session": session_wire(result.value),
                "receipt": result.receipt.model_dump(mode="json"),
            }

        return JSONResponse(await host.execute_command(apply))

    async def checkpoints(request):
        return JSONResponse(
            await host.execute_query(
                lambda: {
                    "checkpoints": [
                        {
                            "checkpoint_id": c.checkpoint_id,
                            "source_end_position": c.source_end_position,
                        }
                        for c in context(request).api.list_checkpoints(
                            request.path_params["session_id"]
                        )
                    ]
                }
            )
        )

    async def fork(request):
        sid = request.path_params["session_id"]
        body = await parse(request, ForkRequest)
        if body.checkpoint_id and body.edit_record_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Choose a checkpoint or an edit source"
            )

        def apply():
            c = context(request)
            idle(c.chat, sid)
            api = c.api
            cid, digest, replay = api._prepare(
                "chat_fork", {"session_id": sid, **body.model_dump()}, body.command_id
            )
            if replay is not None:
                return {
                    "session": session_wire(api._require_session(replay.result_id)),
                    "disposition": "replay",
                }

            def work(txn):
                checkpoint = body.checkpoint_id
                empty = False
                if body.edit_record_id:
                    # Validate ancestry/cutoff before inspecting the immutable source.
                    c.chat.timeline.repository.content(c.workspace_id, sid, body.edit_record_id)
                    records = txn.load_effective_records(c.workspace_id, sid)
                    source = next(
                        (
                            r
                            for r in records
                            if r.record_id == body.edit_record_id
                            and r.payload.get("role") == "user"
                        ),
                        None,
                    )
                    if source is None:
                        raise ApplicationError(
                            ApplicationErrorCode.INVALID, "Edit source must be a user message"
                        )
                    cut = max(
                        (
                            r.conversation_position
                            for r in records
                            if r.kind == "terminal"
                            and r.conversation_position < source.conversation_position
                        ),
                        default=0,
                    )
                    if cut:
                        checkpoint = api.checkpoints.create(
                            sid, source_end_position=cut + 1
                        ).checkpoint_id
                    else:
                        empty = True
                elif checkpoint is None:
                    checkpoint = api.checkpoints.create(sid).checkpoint_id
                # The first message has no closed prefix and cannot form a valid
                # checkpoint. Explicitly start an empty Session in that case.
                child = (
                    api.create_session().value
                    if empty
                    else api.fork_session(sid, checkpoint_id=checkpoint).value
                )
                api._receipt(
                    txn,
                    command_id=cid,
                    operation="chat_fork",
                    digest=digest,
                    session_id=sid,
                    result_kind="session",
                    result_id=child.session_id,
                    event_cursor=None,
                )
                return {
                    "session": session_wire(child),
                    "disposition": "accepted",
                    "empty_prefix": empty,
                }

            return api._translate(lambda: c.journal.transact(work))

        return JSONResponse(await host.execute_command(apply))

    root = "/v1/workspaces/{workspace_id}/sessions/{session_id}"
    return [
        Route(root + "/metadata", metadata, methods=["GET", "PATCH"]),
        Route(root + "/archive", lifecycle, methods=["POST"]),
        Route(root + "/unarchive", lifecycle, methods=["POST"]),
        Route(root + "/checkpoints", checkpoints),
        Route(root + "/fork", fork, methods=["POST"]),
    ]
