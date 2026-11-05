"""Session/task scoped review and restore commands on the Core owner."""

from pathlib import Path

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.application.workspace_restore import WorkspaceRestoreService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.server.protocol import CommandRequest


class ChangeSelectionRequest(CommandRequest):
    command_id: str
    change_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    task_run_id: str | None = None
    workflow_run_id: str | None = None
    node_run_id: str | None = None


class ChangeReviewRequest(ChangeSelectionRequest):
    versions: dict[str, int] = Field(max_length=1000)


class RestoreApplyRequest(CommandRequest):
    command_id: str
    preview_id: str = Field(min_length=1, max_length=128)
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RestoreReconcileRequest(CommandRequest):
    command_id: str


def workspace_change_routes(host, parse):
    def context(request):
        wid = request.path_params["workspace_id"]
        if host.context.workspaces is not None:
            return host.context.workspaces.get(wid)
        if wid != host.context.workspace_id:
            raise ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope")
        return host.context

    def service(c):
        identity = c.application.workspace_service.get(c.workspace_id)

        def busy():
            registry = c.workspaces
            if registry is not None and registry.busy(
                c.workspace_id, pending=True, subscriptions=False
            ):
                return True
            products = [c.products, *c.chat.runtimes.values()]
            return any(
                not getattr(p, "_execution_proxy", False) and p.process.tracked.has_running()
                for p in products
            )

        def writable():
            return not c.application.workspace_state_service.inspect(c.workspace_id).read_only

        return WorkspaceRestoreService(
            c.journal,
            c.api.artifacts,
            Path(identity.path),
            c.workspace_id,
            workflow_queries=getattr(c.runtime, "queries", None),
            busy=busy,
            writable=writable,
        )

    def selectors(request):
        return {
            key: request.query_params[key]
            for key in ("task_run_id", "workflow_run_id", "node_run_id")
            if request.query_params.get(key)
        }

    def selection(body):
        return {
            key: getattr(body, key)
            for key in ("task_run_id", "workflow_run_id", "node_run_id")
            if getattr(body, key)
        }

    async def listing(request):
        def query():
            try:
                limit = int(request.query_params.get("limit", "50"))
            except ValueError:
                raise ApplicationError(ApplicationErrorCode.INVALID, "分页大小无效") from None
            c = context(request)
            return service(c).listing(
                request.path_params["session_id"],
                selectors=selectors(request),
                limit=limit,
                cursor=request.query_params.get("cursor"),
            )

        return JSONResponse(await host.execute_query(query))

    async def detail(request):
        def query():
            try:
                offset = int(request.query_params.get("offset", "0"))
            except ValueError:
                raise ApplicationError(ApplicationErrorCode.INVALID, "Diff 位置无效") from None
            c = context(request)
            return service(c).detail(
                request.path_params["session_id"],
                request.path_params["change_id"],
                selectors=selectors(request),
                offset=offset,
            )

        return JSONResponse(await host.execute_query(query))

    async def operation(request):
        return JSONResponse(
            await host.execute_query(
                lambda: (
                    service(context(request))
                    .get_operation(
                        request.path_params["session_id"], request.path_params["operation_id"]
                    )
                    .model_dump(mode="json")
                )
            )
        )

    async def command(request, kind, model):
        body = await parse(request, model)

        def mutate():
            c = context(request)
            sid = request.path_params["session_id"]
            api = c.api
            name = "workspace_" + kind
            payload = {"session_id": sid, **body.model_dump(mode="json")}
            cid, request_digest, replay = api._prepare(name, payload, body.command_id)
            app = service(c)
            if replay is not None:
                if kind == "change_review":
                    return {"disposition": "replay", "reviewed": list(body.change_ids)}
                return app.get_operation(sid, replay.result_id).model_dump(mode="json")

            def receipt(result_id, row_version=None):
                event = c.emitter.emit(
                    "workspace_change_reviewed"
                    if kind == "change_review"
                    else "workspace_restore_updated",
                    "workspace_change" if kind == "change_review" else "workspace_restore",
                    result_id,
                    {"session_id": sid, "row_version": row_version},
                )
                api._receipt(
                    c.journal,
                    command_id=cid,
                    operation=name,
                    digest=request_digest,
                    session_id=sid,
                    result_kind="workspace_change"
                    if kind == "change_review"
                    else "workspace_restore",
                    result_id=result_id,
                    event_cursor=event.cursor,
                    row_version=row_version,
                )

            if kind == "change_review":

                def review(_):
                    rows = app.review(
                        sid, body.change_ids, body.versions, selectors=selection(body)
                    )
                    receipt(rows[0].change_id, rows[0].row_version)
                    return {"disposition": "accepted", "reviewed": [r.change_id for r in rows]}

                return c.journal.transact(review)
            if kind == "restore_preview":
                op = app.preview(sid, body.change_ids, command_id=cid, selectors=selection(body))
            elif kind == "restore_apply":
                op = app.apply(
                    sid, body.preview_id, preview_digest=body.preview_digest, command_id=cid
                )
            else:
                op = app.reconcile(sid, request.path_params["operation_id"])
            c.journal.transact(lambda _: receipt(op.operation_id, op.row_version))
            return op.model_dump(mode="json")

        return JSONResponse(await host.execute_command(mutate))

    async def review(request):
        return await command(request, "change_review", ChangeReviewRequest)

    async def preview(request):
        return await command(request, "restore_preview", ChangeSelectionRequest)

    async def apply(request):
        return await command(request, "restore_apply", RestoreApplyRequest)

    async def reconcile(request):
        return await command(request, "restore_reconcile", RestoreReconcileRequest)

    root = "/v1/workspaces/{workspace_id}/sessions/{session_id}"
    return [
        Route(root + "/changes", listing),
        Route(root + "/changes/review", review, methods=["POST"]),
        Route(root + "/changes/{change_id}", detail),
        Route(root + "/changes/{change_id}/diff", detail),
        Route(root + "/restores/preview", preview, methods=["POST"]),
        Route(root + "/restores", apply, methods=["POST"]),
        Route(root + "/restores/{operation_id}", operation),
        Route(root + "/restores/{operation_id}/reconcile", reconcile, methods=["POST"]),
    ]
