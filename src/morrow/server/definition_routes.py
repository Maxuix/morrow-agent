"""Definition lifecycle delegates to the shared publication/source owners."""

from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.workflows.definitions import WorkflowDefinitionSource
from morrow.server.commands import ServerCommands
from morrow.server.protocol import CommandRequest


class DefinitionAction(CommandRequest):
    command_id: str
    action: Literal["validate", "enable", "disable", "revoke", "publish", "clone"]
    expected_head_revision: int = Field(default=0, ge=0)
    version_id: str | None = Field(default=None, max_length=128)
    reason: str = Field(
        default="User requested definition revocation", min_length=1, max_length=512
    )
    new_definition_id: str | None = Field(default=None, max_length=128)
    expected_source_revision: int = Field(default=0, ge=0)
    name: str | None = Field(default=None, max_length=128)


class WorkflowSourceWrite(CommandRequest):
    command_id: str
    source: WorkflowDefinitionSource
    expected_source_revision: int = Field(ge=0)


def definition_routes(host, parse):
    def view(c, kind, identity):
        cmd = ServerCommands(c)
        return (
            cmd.catalog_agent_definition if kind == "agent" else cmd.catalog_workflow_definition
        )(identity)

    async def action(request):
        kind = request.path_params["kind"]
        identity = request.path_params["definition_id"]
        if kind not in {"agent", "workflow"}:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Definition kind is unavailable")
        body = await parse(request, DefinitionAction)

        def run():
            c = host.context
            service = c.management
            current = view(c, kind, identity)
            if body.action == "validate":
                validation = getattr(service, "validate_" + kind)(identity)
                diagnostics = getattr(validation, "diagnostics", ())
                return {
                    "definition": current,
                    "diagnostics": [
                        d.model_dump(mode="json") if hasattr(d, "model_dump") else str(d)
                        for d in diagnostics
                    ],
                }

            def apply():
                target = identity
                if body.action in {"enable", "disable"}:
                    getattr(service, "set_" + kind + "_enabled")(
                        identity,
                        enabled=body.action == "enable",
                        expected_head_revision=body.expected_head_revision,
                    )
                elif body.action == "revoke":
                    # Any exact historical revision may be revoked, after checking its definition.
                    version = (
                        c.journal.agent_definitions.get_version(
                            c.workspace_id, body.version_id or ""
                        )
                        if kind == "agent"
                        else c.journal.workflows.get_revision(c.workspace_id, body.version_id or "")
                    )
                    owner = (
                        (
                            version.source.definition_id
                            if kind == "agent"
                            else version.workflow_definition_id
                        )
                        if version
                        else None
                    )
                    if owner != identity or body.version_id is None:
                        raise ApplicationError(
                            ApplicationErrorCode.INVALID,
                            "Select an exact version belonging to this definition",
                        )
                    getattr(
                        service,
                        "revoke_agent_version" if kind == "agent" else "revoke_workflow_revision",
                    )(body.version_id, reason=body.reason, command_id=body.command_id)
                elif body.action == "publish":
                    getattr(service, "publish_" + kind)(
                        identity,
                        expected_head_revision=body.expected_head_revision,
                        command_id=body.command_id,
                    )
                elif body.action == "clone":
                    if not body.new_definition_id:
                        raise ApplicationError(
                            ApplicationErrorCode.INVALID, "A new definition ID is required"
                        )
                    target = body.new_definition_id
                    if kind == "workflow":
                        service.clone_workflow_source(
                            identity,
                            new_definition_id=target,
                            name=body.name,
                            expected_source_revision=body.expected_source_revision,
                        )
                    else:
                        source = service._agent_source(identity)[0]
                        source = source.model_copy(
                            update={"definition_id": target, "name": body.name or source.name}
                        )
                        service.create_agent_source(
                            source, expected_source_revision=body.expected_source_revision
                        )
                return view(c, kind, target), target

            value, receipt = ServerCommands(c)._idempotent(
                "definition_" + kind + "_" + body.action,
                body.command_id,
                {"definition_id": identity, **body.model_dump(exclude={"command_id"})},
                apply,
                lambda receipt: view(c, kind, receipt.result_id),
                result_kind=kind + "_definition",
            )
            return {**value, "receipt": receipt.model_dump(mode="json")}

        return JSONResponse(await host.execute_command(run))

    async def source(request):
        body = await parse(request, WorkflowSourceWrite)
        identity = request.path_params.get("definition_id", body.source.workflow_definition_id)
        if identity != body.source.workflow_definition_id:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Definition path and source differ"
            )

        def run():
            c = host.context

            def apply():
                operation = (
                    c.management.create_workflow_source
                    if request.method == "POST"
                    else c.management.update_workflow_source
                )
                operation(body.source, expected_source_revision=body.expected_source_revision)
                return view(c, "workflow", identity), identity

            value, receipt = ServerCommands(c)._idempotent(
                "workflow_source_" + request.method,
                body.command_id,
                body.model_dump(mode="json", exclude={"command_id"}),
                apply,
                lambda _: view(c, "workflow", identity),
                result_kind="workflow_definition",
            )
            return {**value, "receipt": receipt.model_dump(mode="json")}

        return JSONResponse(await host.execute_command(run))

    return [
        Route("/v1/definition-actions/{kind}/{definition_id}", action, methods=["POST"]),
        Route("/v1/workflow-definitions", source, methods=["POST"]),
        Route("/v1/workflow-definitions/{definition_id}", source, methods=["PUT"]),
    ]
