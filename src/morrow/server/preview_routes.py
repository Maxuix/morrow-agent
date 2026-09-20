"""预览创建与释放操作。

预览不是持久状态：这里只做窄的创建/释放，走现有 loopback + Origin 认证边界，
不写 journal、不新增表。构建好的集合由 PreviewRegistry 持有，服务端只按身份
提供固定字节。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.application.html_preview import (
    HtmlPreviewBuilder,
    registered_preview_bundle,
)
from morrow.application.task_artifacts import TaskArtifactsService
from morrow.application.workspace_files import WorkspaceFilesApplicationService
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.server.preview_server import PreviewHttpServer


class PreviewCreateRequest(BaseModel):
    """One preview source: the current workspace file, or a registered snapshot."""

    path: str | None = Field(default=None, min_length=1, max_length=512)
    """入口文件当时的版本；不一致时拒绝建立预览，避免预览与已保存内容不符。"""

    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_id: str | None = Field(default=None, pattern=r"^art_[A-Za-z0-9_-]+$")
    """历史来源：交付快照 Artifact 及其原始 session/task 选择器。"""

    session_id: str | None = Field(default=None, max_length=128)
    task_run_id: str | None = Field(default=None, max_length=128)
    workflow_run_id: str | None = Field(default=None, max_length=128)
    node_run_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def one_source(self):
        if (self.path is None) == (self.artifact_id is None):
            raise ValueError("exactly one of path or artifact_id is required")
        selectors = (self.session_id, self.task_run_id, self.workflow_run_id, self.node_run_id)
        if self.artifact_id is None and any(value is not None for value in selectors):
            raise ValueError("session selectors only apply to an artifact preview")
        if self.artifact_id is not None and self.session_id is None:
            raise ValueError("an artifact preview requires its session selector")
        return self


def preview_routes(host, parse, previews: PreviewHttpServer):
    def registry():
        return host.context.workspaces

    async def create(request: Request):
        body = await parse(request, PreviewCreateRequest)
        wid = request.path_params["workspace_id"]
        identity = await host.execute_query(lambda: registry().require_identity(wid))
        origin = previews.origin
        if origin is None:
            raise ApplicationError(ApplicationErrorCode.UNAVAILABLE, "预览监听端口当前不可用")

        if body.artifact_id is not None:
            # Historical branch: every byte comes from an already registered
            # snapshot, authorized again for the original session/task.
            current = await host.execute_query(lambda: registry().get(wid))
            if current is None:
                raise ApplicationError(
                    ApplicationErrorCode.CROSS_WORKSPACE, "Unknown workspace scope"
                )

            def build_registered():
                service = TaskArtifactsService(
                    current.journal,
                    artifacts=current.api.artifacts,
                    workflow_queries=getattr(current.runtime, "queries", None),
                    workspace_id=wid,
                )
                source = service.delivery_bundle_source(
                    body.session_id,
                    body.artifact_id,
                    task_run_id=body.task_run_id,
                    workflow_run_id=body.workflow_run_id,
                    node_run_id=body.node_run_id,
                )
                suffix = source["entry_path"].rsplit(".", 1)[-1].lower()
                if not source["mime"].startswith("text/html") and suffix not in {"html", "htm"}:
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID, "只有 HTML 交付件可以建立运行预览"
                    )
                bundle = registered_preview_bundle(
                    workspace_id=wid,
                    entry_path=source["entry_path"],
                    revision=source["revision"],
                    files=source["files"],
                    missing=source["missing"],
                )
                preview_id = previews.registry.register(bundle)
                return {
                    "preview_id": preview_id,
                    "url": previews.url_for(preview_id, bundle.entry_path),
                    **bundle.wire(),
                }

            value = await host.execute_query(build_registered)
            return JSONResponse({"schema_version": 1, "workspace_id": wid, **value})

        def build():
            service = WorkspaceFilesApplicationService(Path(identity.path), wid)
            bundle = HtmlPreviewBuilder(service.files, workspace_id=wid).build(body.path)
            if body.revision is not None and bundle.revision != body.revision:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "入口文件已变化，请保存后重新打开预览"
                )
            preview_id = previews.registry.register(bundle)
            return {
                "preview_id": preview_id,
                "url": previews.url_for(preview_id, bundle.entry_path),
                **bundle.wire(),
            }

        value = await host.execute_query(build, blocking=True)
        return JSONResponse({"schema_version": 1, "workspace_id": wid, **value})

    async def release(request: Request):
        preview_id = request.path_params["preview_id"]
        return JSONResponse(
            {"schema_version": 1, "released": previews.registry.release(preview_id)}
        )

    return [
        Route("/v1/workspaces/{workspace_id}/previews", create, methods=["POST"]),
        Route(
            "/v1/workspaces/{workspace_id}/previews/{preview_id}",
            release,
            methods=["DELETE"],
        ),
    ]
