"""Scoped Skill package administration using the existing immutable package saga."""

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.adapters.skills.managed_store import prepare_local_skill
from morrow.application.context_management import management_wire
from morrow.application.management_requests import CommandRequest
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import sha256_digest
from morrow.core.models import ProtocolModel
from morrow.core.skills.trust import SourceKind
from morrow.server.commands import ServerCommands


class SkillQuery(ProtocolModel):
    scope: Literal["workspace", "global"] = "workspace"
    identity: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=32)
    version_id: str | None = Field(default=None, max_length=128)
    agent_run_id: str | None = Field(default=None, max_length=128)
    page: int = Field(default=0, ge=0, le=20000)


class SkillAction(CommandRequest):
    action: Literal["validate", "install", "remove"]
    scope: Literal["workspace", "global"] = "workspace"
    path: str | None = Field(default=None, max_length=4096)
    source: Literal["imported", "generated", "builtin", "user_authored"] = "imported"
    skill_id: str | None = Field(default=None, max_length=128)
    version_id: str | None = Field(default=None, max_length=128)
    expected_tree_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_binding_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False


def _safe_error(exc):
    if isinstance(exc, ApplicationError):
        return exc
    code = str(getattr(exc, "code", "invalid"))
    selected = (
        ApplicationErrorCode.CONFLICT
        if code in {"conflict", "referenced", "needs_resolution"}
        else ApplicationErrorCode.INVALID
    )
    return ApplicationError(
        selected, "Skill operation is unavailable; check its scope, source and preview"
    )


def skill_routes(host, parse):
    async def query(request):
        q = SkillQuery.model_validate(dict(request.query_params), strict=False)
        kind = request.path_params["kind"]

        def read():
            service = host.context.context_management
            if kind == "catalog":
                return service.skill_queries.catalog(q.scope, q.page, q.identity)
            if kind == "drafts":
                return service.skill_queries.drafts(q.page, q.identity, q.status)
            if kind == "usage":
                rows = service.skills.usage.list(
                    skill_id=q.identity,
                    version_id=q.version_id,
                    agent_run_id=q.agent_run_id,
                    limit=51,
                    offset=q.page * 50,
                )
                return management_wire(
                    {
                        "items": rows[:50],
                        "next_cursor": str((q.page + 1) * 50) if len(rows) > 50 else None,
                    }
                )
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Skill query is missing")

        try:
            return JSONResponse(await host.execute_query(read))
        except Exception as exc:
            raise _safe_error(exc) from None

    async def action(request):
        body = await parse(request, SkillAction)
        prepared = None
        try:
            if body.action in {"validate", "install"}:
                existing, scope_id = await host.execute_query(
                    lambda: (
                        host.context.journal.get_application_command_receipt(
                            host.context.workspace_id, body.command_id
                        ),
                        host.context.context_management.skill_queries.scope_id(body.scope),
                    )
                )
                if existing is None or body.action == "validate":
                    if not body.path or not Path(body.path).is_absolute():
                        raise ApplicationError(
                            ApplicationErrorCode.INVALID, "Choose an absolute local Skill directory"
                        )
                    # Parsing is a pure, bounded file capture; journal/catalog writes remain on Core.
                    prepared = await host.execute_preparation(
                        lambda: asyncio.to_thread(
                            prepare_local_skill,
                            Path(body.path),
                            source_kind=SourceKind(body.source),
                            scope_id=scope_id,
                        )
                    )

            def run():
                c = host.context
                service = c.context_management
                lifecycle = service.skills.lifecycle
                scope_id = service.skill_queries.scope_id(body.scope)
                if body.action == "validate":
                    return management_wire(lifecycle._with_conflicts(prepared))

                def apply():
                    if not body.confirmed:
                        raise ApplicationError(
                            ApplicationErrorCode.INVALID, "Confirm the concrete Skill preview first"
                        )
                    if body.action == "install":
                        if prepared.tree.tree_digest != body.expected_tree_digest:
                            raise ApplicationError(
                                ApplicationErrorCode.STALE,
                                "Skill directory changed; validate again",
                            )
                        result = lifecycle.install_prepared(
                            prepared,
                            confirmed=True,
                            command_id="cmd_" + sha256_digest(body.command_id + ":skill")[:48],
                        )
                    else:
                        native_receipt = c.journal.get_application_command_receipt(
                            c.workspace_id, "cmd_" + sha256_digest(body.command_id + ":skill")[:48]
                        )
                        if native_receipt is None and (
                            service.skill_queries.binding_digest(body.scope)
                            != body.expected_binding_digest
                        ):
                            raise ApplicationError(
                                ApplicationErrorCode.STALE, "Skill binding changed; refresh first"
                            )
                        result = lifecycle.remove(
                            body.skill_id or "",
                            scope_id=scope_id,
                            version_id=body.version_id,
                            source_kind=SourceKind(body.source),
                            confirmed=True,
                            command_id="cmd_" + sha256_digest(body.command_id + ":skill")[:48],
                        )
                    return management_wire(result), result.skill_id

                value, receipt = ServerCommands(c)._idempotent(
                    "skill_admin_" + body.action,
                    body.command_id,
                    body.model_dump(mode="json", exclude={"command_id"}),
                    apply,
                    lambda receipt: {"status": "replayed", "skill_id": receipt.result_id},
                    result_kind="skill",
                )
                return {"result": value, "receipt": receipt.model_dump(mode="json")}

            return JSONResponse(await host.execute_command(run))
        except Exception as exc:
            raise _safe_error(exc) from None

    return [
        Route("/v1/skill-management/{kind}", query),
        Route("/v1/skill-actions", action, methods=["POST"]),
    ]
