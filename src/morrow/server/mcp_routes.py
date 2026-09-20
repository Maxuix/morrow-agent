"""Explicit scoped MCP administration, with supervised catalog discovery."""

from typing import Literal

from pydantic import Field, SecretStr
from starlette.responses import JSONResponse
from starlette.routing import Route

from morrow.adapters.mcp.stdio_client import McpAdapterError
from morrow.adapters.state.extension_yaml import ExtensionYamlStore, extension_document_digest
from morrow.application.management_requests import CommandRequest
from morrow.application.mcp.catalog import (
    McpCatalogError,
    McpCatalogService,
    degraded_catalog_snapshot,
)
from morrow.application.mcp.credentials import (
    credential_bindings,
    credential_reference,
    resolve_environment,
)
from morrow.application.mcp.definitions import McpDefinitionService
from morrow.application.mcp.queries import McpQueries
from morrow.core.application import (
    ApplicationCommandReceipt,
    ApplicationError,
    ApplicationErrorCode,
)
from morrow.core.domain import sha256_digest
from morrow.core.mcp import McpServerDefinition, mcp_server_config_digest
from morrow.core.models import ProtocolModel
from morrow.server.commands import ServerCommands, request_digest


class McpQuery(ProtocolModel):
    scope: Literal["workspace", "global"] = "workspace"
    identity: str | None = Field(default=None, max_length=128)
    page: int = Field(default=0, ge=0, le=100)


class McpAction(CommandRequest):
    action: Literal["add", "update", "enable", "disable", "remove", "refresh"]
    scope: Literal["workspace", "global"] = "workspace"
    server_id: str = Field(pattern=r"^mcp_[a-z0-9][a-z0-9_-]{0,63}$")
    expected_revision: int = Field(ge=0)
    expected_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_server_revision: int = Field(default=0, ge=0)
    definition: McpServerDefinition | None = None
    environment_names: tuple[str, ...] | None = Field(default=None, max_length=16)
    keep_arguments: bool = False
    confirmed: bool = False


class McpCredential(ProtocolModel):
    scope: Literal["workspace", "global"] = "workspace"
    server_id: str = Field(pattern=r"^mcp_[a-z0-9][a-z0-9_-]{0,63}$")
    environment_name: str = Field(min_length=1, max_length=64)
    expected_server_revision: int = Field(ge=1)
    secret: SecretStr = Field(min_length=1, max_length=65536)


def _error(exc):
    if isinstance(exc, ApplicationError):
        return exc
    code = str(getattr(exc, "code", "invalid"))
    status = (
        ApplicationErrorCode.INVALID
        if isinstance(exc, ValueError)
        else ApplicationErrorCode.UNAVAILABLE
    )
    if code == "not_found":
        status = ApplicationErrorCode.NOT_FOUND
    if "conflict" in code:
        status = ApplicationErrorCode.CONFLICT
    messages = {
        "catalog_required": "请先刷新MCP目录，再启用服务",
        "catalog_unavailable": "目录尚未就绪，请刷新并检查服务配置",
        "catalog_mismatch": "本地映射已变化，请重新刷新目录",
        "tool_allowlist_required": "请编辑允许的工具列表和本地风险映射",
        "risk_mapping_required": "每个允许的工具都需要本地风险映射",
        "tool_missing": "允许列表中有未发现的工具，请检查目录名称",
        "workspace_visibility_unsupported": "当前stdio只能按读写可见性启动，请核对后修改配置",
    }
    return ApplicationError(status, messages.get(code, "MCP操作未完成；检查状态后可使用原请求重试"))


def mcp_routes(host, parse):
    def services(c, scope):
        sid = c.workspace_id if scope == "workspace" else None
        definitions = McpDefinitionService(
            ExtensionYamlStore(c.application.data_root.root), c.workspace_id
        )
        queries = McpQueries(
            definitions,
            catalogs=lambda identity: c.journal.get_mcp_catalog(scope, identity, scope_id=sid),
        )
        return definitions, queries, sid

    async def query(request):
        q = McpQuery.model_validate(dict(request.query_params), strict=False)

        def read():
            c = host.context
            definitions, queries, sid = services(c, q.scope)
            load = definitions.load(q.scope, scope_id=sid)
            value = {"scope": q.scope, "revision": load.revision, "digest": load.digest}
            if not q.identity:
                rows = queries.list(q.scope, scope_id=sid)
                return {
                    **value,
                    "servers": [
                        r.model_dump(mode="json") for r in rows[q.page * 20 : (q.page + 1) * 20]
                    ],
                    "next_cursor": str((q.page + 1) * 20)
                    if len(rows) > (q.page + 1) * 20
                    else None,
                }
            definition = definitions.show(q.identity, q.scope, scope_id=sid)
            bindings = []
            try:
                for name, reference in credential_bindings(definition).items():
                    try:
                        available = bool(c.application.credentials.get(reference))
                    except Exception:
                        available = False
                    bindings.append({"name": name, "available": available})
                binding_status = "ok"
            except Exception:
                binding_status = "binding_requires_configuration"
            return {
                **value,
                **queries.inspect(q.identity, q.scope, scope_id=sid).model_dump(mode="json"),
                "configuration": {
                    "timeout_ms": definition.timeout_ms,
                    "cwd": definition.cwd,
                    "tool_policy": definition.tool_policy.model_dump(mode="json"),
                },
                "credentials": bindings,
                "credential_status": binding_status,
            }

        try:
            return JSONResponse(
                await host.execute_query(read), headers={"Cache-Control": "no-store"}
            )
        except Exception as exc:
            raise _error(exc) from None

    async def credential(request):
        body = await parse(request, McpCredential)

        def write():
            c = host.context
            definitions, _, sid = services(c, body.scope)
            definition = definitions.show(body.server_id, body.scope, scope_id=sid)
            if definition.revision != body.expected_server_revision:
                raise ApplicationError(ApplicationErrorCode.STALE, "MCP配置已变化，请刷新")
            reference = credential_bindings(definition).get(body.environment_name)
            if reference is None:
                raise ApplicationError(ApplicationErrorCode.INVALID, "先在MCP配置中声明此环境变量")
            value = body.secret.get_secret_value()
            if "\x00" in value:
                raise ApplicationError(ApplicationErrorCode.INVALID, "凭据格式无效")
            c.application.credentials.set(reference, value)

        try:
            await host.execute_command(write)
            return JSONResponse({"saved": True}, headers={"Cache-Control": "no-store"})
        except Exception as exc:
            raise _error(exc) from None

    async def status(request):
        def read():
            c = host.context
            command = request.path_params["command_id"]
            receipt = c.journal.get_application_command_receipt(c.workspace_id, command)
            if receipt is None or receipt.operation != "mcp_refresh":
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "刷新请求不存在")
            return (c.management_jobs or {}).get(
                command, {"status": "interrupted", "target": receipt.result_id}
            )

        return JSONResponse(await host.execute_query(read))

    async def action(request):
        body = await parse(request, McpAction)

        def run():
            c = host.context
            definitions, _, sid = services(c, body.scope)
            created = False
            selected = None
            previous = None
            if c.management_jobs is None:
                c.management_jobs = {}
            jobs = c.management_jobs

            def apply():
                nonlocal created, selected, previous
                load = definitions.load(body.scope, scope_id=sid)
                existing = next(
                    (
                        s
                        for s in definitions.list(body.scope, scope_id=sid)
                        if s.server_id == body.server_id
                    ),
                    None,
                )
                if not body.confirmed:
                    raise ApplicationError(ApplicationErrorCode.INVALID, "请确认具体MCP操作")
                intent_id = "cmd_" + sha256_digest(body.command_id + ":mcp-write")[:48]
                digest = request_digest(
                    "mcp_" + body.action, body.model_dump(mode="json", exclude={"command_id"})
                )
                intent = c.journal.get_application_command_receipt(c.workspace_id, intent_id)
                if intent and intent.request_digest != digest:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT, "MCP请求编号已用于不同内容"
                    )

                def synchronize(server, invalidate):
                    stored = c.journal.get_mcp_server(body.scope, body.server_id, scope_id=sid)
                    if stored is not None and mcp_server_config_digest(
                        stored
                    ) == mcp_server_config_digest(server):
                        return
                    old = c.journal.get_mcp_catalog(body.scope, body.server_id, scope_id=sid)
                    catalog = (
                        degraded_catalog_snapshot(
                            server,
                            revision=old.revision + 1 if old else 1,
                            reason="configuration_changed",
                        )
                        if invalidate
                        else None
                    )
                    c.journal.put_mcp_server(server, catalog=catalog)

                if intent and load.digest == intent.result_id:
                    if existing is not None:
                        synchronize(existing, body.action in {"add", "update"})
                    return {"status": "applied"}, body.server_id
                if load.revision != body.expected_revision or load.digest != body.expected_digest:
                    raise ApplicationError(
                        ApplicationErrorCode.STALE, "Extension配置已变化，请刷新后重试"
                    )

                def persist(change):
                    after = change.after_document.model_copy(update={"revision": load.revision + 1})
                    if intent is None:
                        c.journal.put_application_command_receipt(
                            c.workspace_id,
                            ApplicationCommandReceipt(
                                command_id=intent_id,
                                workspace_id=c.workspace_id,
                                operation="mcp_write_intent",
                                request_digest=digest,
                                result_kind="extension_digest",
                                result_id=extension_document_digest(after),
                            ),
                        )
                    definitions.apply_change(change)
                    if change.after_server is not None:
                        synchronize(change.after_server, body.action in {"add", "update"})

                if body.action in {"add", "update"}:
                    if body.definition is None or body.definition.server_id != body.server_id:
                        raise ApplicationError(ApplicationErrorCode.INVALID, "填写匹配的MCP定义")
                    payload = body.definition.model_dump(mode="python")
                    payload.update(scope=body.scope, scope_id=sid)
                    if body.keep_arguments and existing is not None:
                        payload["argv"] = existing.argv
                    selected = McpServerDefinition.model_validate(payload)
                    if body.environment_names is not None:
                        selected = selected.model_copy(
                            update={
                                "credential_refs": tuple(
                                    credential_reference(selected, n)
                                    for n in body.environment_names
                                )
                            }
                        )
                    elif existing is not None:
                        selected = selected.model_copy(
                            update={"credential_refs": existing.credential_refs}
                        )
                    selected = McpServerDefinition.model_validate(
                        selected.model_dump(mode="python")
                    )
                    change = (
                        definitions.prepare_add
                        if body.action == "add"
                        else definitions.prepare_update
                    )(selected, scope=body.scope, scope_id=sid)
                    persist(change)
                elif body.action == "refresh":
                    if existing is None:
                        raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "MCP服务不存在")
                    if sum(v["status"] in {"queued", "running"} for v in jobs.values()) >= 4:
                        raise ApplicationError(ApplicationErrorCode.BUSY, "已有4个管理请求等待执行")
                    if c.workspaces:
                        c.workspaces.check_admission()
                    selected = existing
                    previous = c.journal.get_mcp_catalog(body.scope, body.server_id, scope_id=sid)
                    created = True
                    return {"status": "queued", "command_id": body.command_id}, body.server_id
                else:
                    previous = c.journal.get_mcp_catalog(body.scope, body.server_id, scope_id=sid)
                    change = definitions.prepare_change(
                        operation=body.action,
                        server_id=body.server_id,
                        scope=body.scope,
                        scope_id=sid,
                        catalog=previous,
                    )
                    persist(change)
                return {"status": "applied"}, body.server_id

            value, receipt = ServerCommands(c)._idempotent(
                "mcp_" + body.action,
                body.command_id,
                body.model_dump(mode="json", exclude={"command_id"}),
                apply,
                lambda _: {
                    "status": jobs.get(body.command_id, {}).get("status", "replayed"),
                    "command_id": body.command_id if body.action == "refresh" else None,
                },
                result_kind="mcp_server",
            )
            if created:
                for key in tuple(jobs):
                    if len(jobs) < 128:
                        break
                    if jobs[key]["status"] not in {"queued", "running"}:
                        del jobs[key]
                jobs[body.command_id] = {"status": "queued", "target": body.server_id}

                async def drive():
                    state = jobs[body.command_id]
                    state["status"] = "running"
                    try:
                        environment = resolve_environment(selected, c.application.credentials)

                        root = c.workspaces.roots[c.workspace_id]
                        catalog = await McpCatalogService(workspace_root=root).refresh(
                            selected, previous=previous, environment=environment
                        )
                        current = definitions.show(body.server_id, body.scope, scope_id=sid)
                        saved = c.journal.get_mcp_catalog(body.scope, body.server_id, scope_id=sid)
                        if mcp_server_config_digest(current) != mcp_server_config_digest(
                            selected
                        ) or (saved.revision if saved else None) != (
                            previous.revision if previous else None
                        ):
                            state.update(status="failed", error="configuration_changed")
                            return
                        c.journal.put_mcp_server(selected, catalog=catalog)
                        state.update(
                            status="completed" if catalog.status.value == "ready" else "failed",
                            catalog_status=catalog.status.value,
                            error=catalog.degraded_reason,
                        )
                    except (McpAdapterError, McpCatalogError) as exc:
                        state.update(status="failed", error=exc.code)
                    except Exception:
                        state.update(status="failed", error="refresh_unavailable")
                    finally:
                        if state["status"] == "running":
                            state["status"] = "interrupted"

                c.supervisor.ensure_driver("mcp_" + body.command_id, drive)
            return {**value, "receipt": receipt.model_dump(mode="json")}

        try:
            return JSONResponse(await host.execute_command(run))
        except Exception as exc:
            raise _error(exc) from None

    return [
        Route("/v1/mcp-management", query),
        Route("/v1/mcp-actions", action, methods=["POST"]),
        Route("/v1/mcp-credentials", credential, methods=["POST"]),
        Route("/v1/mcp-jobs/{command_id}", status),
    ]
