"""Typed retention, task and recovery operations using existing application owners."""

from datetime import datetime
from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.models import ProtocolModel
from morrow.core.permissions import (
    UNCONFINED_HOST_WARNING,
    UNCONFINED_HOST_WARNING_DIGEST,
    CapabilityName,
)
from morrow.core.recovery import RecoveryResolution
from morrow.server.projections import artifact_wire, task_wire
from morrow.server.protocol import CommandRequest


class OperationQuery(ProtocolModel):
    session_id: str = Field(min_length=1, max_length=128)
    identity: str | None = Field(default=None, max_length=128)
    page: int = Field(default=0, ge=0, le=100000)


class RetentionRequest(CommandRequest):
    action: Literal["pin", "release"]
    expected_row_version: int = Field(ge=1)
    confirmed: bool = False


class RecoveryDecision(CommandRequest):
    session_id: str = Field(min_length=1, max_length=128)
    report_id: str = Field(min_length=1, max_length=128)
    resolution: RecoveryResolution
    item_id: str | None = Field(default=None, max_length=128)
    confirmed: bool = False


class TaskAction(CommandRequest):
    action: Literal["accept", "cancel", "abandon", "resume"]
    expected_row_version: int = Field(ge=1)
    confirmed: bool = False


class GrantQuery(ProtocolModel):
    identity: str | None = Field(default=None, max_length=128)
    agent_run_id: str | None = Field(default=None, max_length=128)
    cursor: str | None = Field(default=None, max_length=32)


class GrantAction(CommandRequest):
    action: Literal["create", "revoke"]
    task_run_id: str | None = Field(default=None, max_length=128)
    agent_run_id: str | None = Field(default=None, max_length=128)
    grant_id: str | None = Field(default=None, max_length=128)
    expected_row_version: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=512)
    preview_digest: str | None = Field(default=None, max_length=64)
    expires_at: datetime | None = None
    confirmed: bool = False


def operation_routes(host, parse):
    def idle(c, sid):
        c.chat.require_session(sid)
        if sid in c.chat.drivers or c.chat.interactions.records.pending(c.workspace_id, sid):
            raise ApplicationError(ApplicationErrorCode.BUSY, "先停止运行并撤回此对话的排队输入")

    def confirm(body):
        if not body.confirmed:
            raise ApplicationError(ApplicationErrorCode.INVALID, "请确认所选对象与具体操作")

    async def grants(request):
        if request.method == "GET":
            q = GrantQuery.model_validate(dict(request.query_params), strict=False)

            def read():
                api = host.context.api
                if q.identity:
                    grant = api.get_grant(q.identity)
                    if grant is None:
                        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "授权不存在")
                    return {"grant": grant.model_dump(mode="json")}
                page = api.list_grants(agent_run_id=q.agent_run_id, cursor=q.cursor, limit=20)
                return {
                    "grants": [g.model_dump(mode="json") for g in page.items],
                    "next_cursor": page.next_cursor,
                    "warning": UNCONFINED_HOST_WARNING,
                    "preview_digest": UNCONFINED_HOST_WARNING_DIGEST,
                }

            return JSONResponse(await host.execute_query(read))
        body = await parse(request, GrantAction)

        def apply():
            confirm(body)
            c = host.context
            if body.action == "create":
                if (
                    not body.task_run_id
                    or not body.agent_run_id
                    or not body.expires_at
                    or body.preview_digest != UNCONFINED_HOST_WARNING_DIGEST
                ):
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID, "请核对任务、运行、到期时间和标准 Host 提示"
                    )
                result = c.api.create_grant(
                    task_run_id=body.task_run_id,
                    agent_run_id=body.agent_run_id,
                    capabilities=(CapabilityName.UNCONFINED_HOST_PROCESS,),
                    reason=body.reason,
                    preview_digest=body.preview_digest,
                    expires_at=body.expires_at,
                    command_id=body.command_id,
                )
            else:
                if not body.grant_id or body.expected_row_version is None:
                    raise ApplicationError(ApplicationErrorCode.INVALID, "请先检查授权及其版本")
                result = c.api.revoke_grant(
                    body.grant_id,
                    expected_row_version=body.expected_row_version,
                    reason=body.reason,
                    command_id=body.command_id,
                )
                for approval in c.journal.list_approvals_for_grant(c.workspace_id, body.grant_id):
                    if approval.revoked_at and c.approval_waiters.claim(approval.approval_id):
                        c.approval_waiters.deliver_claimed(approval.approval_id, approved=False)
            return {
                "grant": result.value.model_dump(mode="json"),
                "disposition": result.receipt.disposition.value,
            }

        return JSONResponse(await host.execute_command(apply))

    async def retention(request):
        body = await parse(request, RetentionRequest)

        def apply():
            confirm(body)
            c = host.context
            result = getattr(c.api, body.action + "_artifact")(
                request.path_params["artifact_id"],
                command_id=body.command_id,
                expected_row_version=body.expected_row_version,
            )
            return {
                "artifact": artifact_wire(result.value),
                "disposition": result.receipt.disposition.value,
            }

        return JSONResponse(await host.execute_command(apply))

    async def task(request):
        body = await parse(request, TaskAction)

        def apply():
            confirm(body)
            c = host.context
            target = c.api.get_task(request.path_params["task_id"])
            if target is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "任务不存在")
            idle(c, target.session_id)
            result = getattr(c.api, "task_" + body.action)(
                target.task_run_id,
                command_id=body.command_id,
                expected_row_version=body.expected_row_version,
            )
            products = c.chat.runtimes.get(target.session_id)
            if products:
                products.persistence.synchronize_task_projection(result.value.task_run_id)
            c.chat.streams.changed(target.session_id)
            return {
                "task": task_wire(result.value),
                "disposition": result.receipt.disposition.value,
            }

        return JSONResponse(await host.execute_command(apply))

    async def recovery(request):
        if request.method == "GET":
            q = OperationQuery.model_validate(dict(request.query_params), strict=False)

            def read():
                c = host.context
                c.chat.require_session(q.session_id)
                if q.identity:
                    report = c.api.get_recovery(q.identity)
                    if report is None or report.session_id != q.session_id:
                        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "报告不属于此对话")
                    return {"report": report.model_dump(mode="json")}
                reports = c.api.list_recovery(q.session_id)
                return {
                    "reports": [
                        r.model_dump(mode="json") for r in reports[q.page * 20 : (q.page + 1) * 20]
                    ],
                    "next_cursor": str(q.page + 1) if len(reports) > (q.page + 1) * 20 else None,
                }

            return JSONResponse(await host.execute_query(read))
        body = await parse(request, RecoveryDecision)

        def decide():
            confirm(body)
            c = host.context
            c.chat.require_session(body.session_id)
            if body.session_id in c.chat.drivers or c.chat.recovery_lock.locked():
                raise ApplicationError(ApplicationErrorCode.BUSY, "等待活动执行结束后再处理恢复")
            report = c.api.get_recovery(body.report_id)
            if report is None or report.session_id != body.session_id:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "报告不属于此对话")
            if body.resolution is RecoveryResolution.RETRY:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "当前不支持无损 linked retry；请选择报告允许的中止或隔离",
                )
            values = {
                "command_id": body.command_id,
                "resolution": body.resolution,
                "item_id": body.item_id,
            }
            products = c.chat.runtimes.get(body.session_id)
            if products:
                result = products.api.resolve_recovery(
                    report, log=products.session.log, writer=products.persistence.writer, **values
                )
            else:
                # No resident owner exists: the same standalone CLI service owns
                # this Session's temporary log/writer for the synchronous decision.
                result = c.api.resolve_recovery_by_id(body.report_id, **values)
            c.chat.interactions.records.pause(body.session_id)
            c.chat.streams.changed(body.session_id)
            return {
                "report": result.value.model_dump(mode="json"),
                "disposition": result.receipt.disposition.value,
                "execution_started": False,
            }

        return JSONResponse(await host.execute_command(decide))

    return [
        Route("/v1/grant-management", grants, methods=["GET", "POST"]),
        Route("/v1/artifacts/{artifact_id}/retention", retention, methods=["POST"]),
        Route("/v1/task-actions/{task_id}", task, methods=["POST"]),
        Route("/v1/recovery-management", recovery, methods=["GET", "POST"]),
    ]
