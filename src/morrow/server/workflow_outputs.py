"""Bounded inert previews of declared Workflow outputs, never leaf transcripts."""

from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.artifacts import ArtifactError


def workflow_output_routes(host):
    async def content(request):
        def query():
            c = host.context
            view = c.runtime.queries.get_run_view(request.path_params["run_id"])
            identity = request.path_params["artifact_id"]
            artifact = (
                next(
                    (
                        item.artifact
                        for item in view.effective_outputs
                        if item.binding.artifact_id == identity
                    ),
                    None,
                )
                if view
                else None
            )
            if artifact is None:
                raise ApplicationError(
                    ApplicationErrorCode.NOT_FOUND, "Artifact is not an output of this Workflow"
                )
            try:
                value = c.api.artifacts.read(identity, max_bytes=65536).content
            except ArtifactError:
                raise ApplicationError(
                    ApplicationErrorCode.UNAVAILABLE, "Workflow output is unavailable"
                ) from None
            return {
                "content": value.decode("utf-8", errors="replace"),
                "truncated": artifact.byte_size > len(value),
                "byte_size": artifact.byte_size,
            }

        return JSONResponse(await host.execute_query(query))

    return [Route("/v1/workflow-runs/{run_id}/artifacts/{artifact_id}/content", content)]
