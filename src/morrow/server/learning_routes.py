"""Learning management transport; long reviews do not occupy the mutation bus."""

from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.application.context_management import management_wire
from morrow.application.knowledge_management import (
    InboxAdminRequest,
    KnowledgeQuery,
    LearningAdminRequest,
    inbox_admin,
    learning_admin,
    query_knowledge,
    required,
)
from morrow.application.preferences.inbox import PreferenceInboxError
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.server.commands import ServerCommands


def learning_routes(host, parse):
    def jobs(c):
        if c.management_jobs is None:
            c.management_jobs = {}
        return c.management_jobs

    async def query(request):
        q = KnowledgeQuery.model_validate(dict(request.query_params), strict=False)
        try:
            return JSONResponse(
                await host.execute_query(
                    lambda: query_knowledge(host.context.api, request.path_params["kind"], q)
                )
            )
        except PreferenceInboxError as exc:
            code = (
                ApplicationErrorCode.NOT_FOUND
                if exc.code == "not_found"
                else ApplicationErrorCode.INVALID
            )
            raise ApplicationError(code, "Preference preview is unavailable") from None

    async def job(request):
        def read():
            c = host.context
            command = request.path_params["command_id"]
            receipt = c.journal.get_application_command_receipt(c.workspace_id, command)
            if receipt is None or not receipt.operation.startswith("admin_review_"):
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Review command is missing")
            return jobs(c).get(command, {"status": "interrupted", "target": receipt.result_id})

        return JSONResponse(await host.execute_query(read))

    async def command(request):
        kind = request.path_params["kind"]
        if kind not in {"learning", "inbox"}:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Management command is missing")
        body = await parse(
            request, LearningAdminRequest if kind == "learning" else InboxAdminRequest
        )

        def run():
            c = host.context
            long = body.action in (
                {"review", "retry"} if kind == "learning" else {"review", "run_pending"}
            )
            if not long:
                if (kind, body.action) in {
                    ("learning", "promotion"),
                    ("inbox", "retry"),
                    ("inbox", "accept_many"),
                }:

                    def apply():
                        (learning_admin if kind == "learning" else inbox_admin)(c.api, body)
                        return {"status": "applied"}, body.target or "batch"

                    value, receipt = ServerCommands(c)._idempotent(
                        "knowledge_" + kind + "_" + body.action,
                        body.command_id,
                        body.model_dump(mode="json", exclude={"command_id"}),
                        apply,
                        lambda _: {"status": "replayed"},
                        result_kind="knowledge_command",
                    )
                    return {**value, "receipt": receipt.model_dump(mode="json")}
                (learning_admin if kind == "learning" else inbox_admin)(c.api, body)
                return {"status": "applied"}
            values = jobs(c)
            created = False
            api = None

            def accept():
                nonlocal created, api
                if sum(v["status"] in {"queued", "running"} for v in values.values()) >= 4:
                    raise ApplicationError(
                        ApplicationErrorCode.BUSY, "Four review requests are pending"
                    )
                if c.workspaces:
                    c.workspaces.check_admission()
                # Lazy execution composition supplies the same no-tool model reviewers as CLI.
                api = c.products.api
                if body.action != "run_pending":
                    target = required(body.target)
                    required(
                        api.get_learning_review_view(target)
                        if kind == "learning"
                        else api.get_preference_review_job_view(target)
                    )
                created = True
                return {"status": "queued", "command_id": body.command_id}, body.target or "pending"

            value, receipt = ServerCommands(c)._idempotent(
                "admin_review_" + kind,
                body.command_id,
                body.model_dump(mode="json", exclude={"command_id"}),
                accept,
                lambda _: {
                    "status": values.get(body.command_id, {}).get("status", "interrupted"),
                    "command_id": body.command_id,
                },
                result_kind="review_request",
            )
            if created:
                for key in tuple(values):
                    if len(values) < 128:
                        break
                    if values[key]["status"] not in {"queued", "running"}:
                        del values[key]
                values[body.command_id] = {"status": "queued", "target": body.target}

                async def drive():
                    state = values[body.command_id]
                    state["status"] = "running"
                    try:
                        if kind == "learning":
                            method = (
                                api.run_learning_review
                                if body.action == "review"
                                else api.retry_learning_review
                            )
                            result = await method(
                                body.target, expected_row_version=body.expected_row_version
                            )
                        elif body.action == "run_pending":
                            result = await api.run_pending_preference_reviews(limit=body.limit)
                        else:
                            result = await api.run_preference_review(body.target)
                        if kind == "learning":
                            summary = {
                                "review_id": result.review.review_id,
                                "review_status": result.review.status,
                                "candidate_count": len(result.candidate_ids),
                            }
                        else:
                            items = result if isinstance(result, tuple) else (result,)
                            summary = [
                                {
                                    "status": v.status,
                                    "job_id": v.job_id,
                                    "proposal_count": v.proposal_count,
                                    "error_code": v.error_code,
                                }
                                for v in items
                            ]
                        state.update(status="completed", result=management_wire(summary))
                    except ApplicationError as exc:
                        state.update(status="failed", error=exc.code.value)
                    except Exception:
                        state.update(status="failed", error="review_unavailable")
                    finally:
                        if state["status"] == "running":
                            state["status"] = "interrupted"

                c.supervisor.ensure_driver("admin_" + body.command_id, drive)
            return {**value, "receipt": receipt.model_dump(mode="json")}

        try:
            return JSONResponse(await host.execute_command(run))
        except PreferenceInboxError as exc:
            code = (
                ApplicationErrorCode.STALE if exc.code == "stale" else ApplicationErrorCode.INVALID
            )
            raise ApplicationError(
                code, "Preference operation is stale or unavailable; refresh its preview"
            ) from None

    return [
        Route("/v1/knowledge-management/{kind}", query),
        Route("/v1/knowledge-commands/{kind}", command, methods=["POST"]),
        Route("/v1/knowledge-jobs/{command_id}", job),
    ]
