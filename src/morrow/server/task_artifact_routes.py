"""Session-scoped TaskArtifacts and command-output read routes."""

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from morrow.application.task_artifacts import TaskArtifactsService
from morrow.application.workspace_files import content_disposition
from morrow.core.application import ApplicationError, ApplicationErrorCode


def _download_name(name: str) -> str:
    """A header-safe real file name; control characters never reach the header."""

    cleaned = "".join(
        char if char.isprintable() and char not in '"\\' else "_" for char in name
    ).strip()
    return cleaned or "artifact"


def task_artifact_routes(host):
    def context(request):
        workspace_id = request.path_params.get("workspace_id", host.context.workspace_id)
        registry = host.context.workspaces
        if registry is not None:
            current = registry.get(workspace_id)
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope"
                )
            return current
        if workspace_id != host.context.workspace_id:
            raise ApplicationError(ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope")
        return host.context

    def service(request):
        current = context(request)
        return TaskArtifactsService(
            current.journal,
            artifacts=current.api.artifacts,
            workflow_queries=getattr(current.runtime, "queries", None),
            workspace_id=current.workspace_id,
        )

    def selectors(request):
        return {
            "task_run_id": request.query_params.get("task_run_id"),
            "workflow_run_id": request.query_params.get("workflow_run_id"),
            "node_run_id": request.query_params.get("node_run_id"),
        }

    async def listing(request):
        sid = request.path_params["session_id"]

        def query():
            return service(request).view(sid, **selectors(request)).model_dump(mode="json")

        return JSONResponse(await host.execute_query(query))

    async def content(request):
        sid = request.path_params["session_id"]
        artifact_id = request.path_params["artifact_id"]

        def query():
            return service(request).read_content(
                sid,
                artifact_id,
                **selectors(request),
            )

        return JSONResponse(await host.execute_query(query))

    async def download(request: Request):
        """历史 Artifact 的受控下载；作用域校验与正文读取完全一致。"""

        sid = request.path_params["session_id"]
        artifact_id = request.path_params["artifact_id"]

        def query():
            return service(request).download(sid, artifact_id, **selectors(request))

        # Scope checks and the bounded byte read both touch Core-owned state:
        # they stay on the owner loop, exactly like the content route above.
        data, name, mime = await host.execute_query(query)
        return Response(
            data,
            media_type=mime,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": content_disposition("attachment", _download_name(name)),
            },
        )

    root = "/v1/workspaces/{workspace_id}/sessions/{session_id}/task-artifacts"
    return [
        Route(root, listing),
        Route(root + "/{artifact_id}/content", content),
        Route(root + "/{artifact_id}/download", download),
    ]
