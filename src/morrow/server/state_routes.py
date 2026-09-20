"""Data-root maintenance with owner-loop admission and bounded observable jobs."""

import asyncio
import hashlib
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup import OperationalBackupService
from morrow.application.backup_service import _read_regular_file
from morrow.application.cleanup import ArtifactCleanupService, CleanupPreviewChanged
from morrow.application.doctor import OperationalDoctor
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.domain import sha256_digest
from morrow.core.models import ProtocolModel
from morrow.server.protocol import CommandRequest


class StateQuery(ProtocolModel):
    page: int = Field(default=0, ge=0, le=100000)
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class StateAction(CommandRequest):
    action: Literal["doctor", "backup", "verify", "cleanup_preview", "cleanup"]
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    bundle: str | None = Field(default=None, max_length=4096)
    preview_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = False


def _archive_bundle(backup, root):
    verification = backup.verify(root)
    if not verification.ok:
        raise ValueError("backup verification failed")
    # Only manifest-listed files enter the download, after the original service
    # verified every digest and path. Never traverse arbitrary directory entries.
    import json

    raw = _read_regular_file(root / "manifest.json")
    manifest = json.loads(raw)
    target = root.with_suffix(".zip")
    descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=root.parent)
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive,
        ):
            archive.writestr(root.name + "/manifest.json", raw)
            for entry in manifest["files"]:
                descriptor = os.open(root / entry["path"], os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("backup changed during archive")
                    digest = hashlib.sha256()
                    count = 0
                    with archive.open(root.name + "/" + entry["path"], "w") as output:
                        while chunk := source.read(65536):
                            count += len(chunk)
                            if count > entry["byte_size"]:
                                raise ValueError("backup grew during archive")
                            digest.update(chunk)
                            output.write(chunk)
                    if count != entry["byte_size"] or digest.hexdigest() != entry["sha256"]:
                        raise ValueError("backup changed during archive")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target.name


def state_routes(host, parse):
    async def query(request):
        q = StateQuery.model_validate(dict(request.query_params), strict=False)

        def read():
            c = host.context
            registry = c.workspaces
            if request.path_params["kind"] == "events":
                page = c.api.list_events(after_cursor=q.after, limit=q.limit)
                return {
                    "events": [
                        e.model_dump(
                            mode="json",
                            include={
                                "cursor",
                                "event_type",
                                "aggregate_kind",
                                "aggregate_id",
                                "created_at",
                            },
                        )
                        for e in page.items
                    ],
                    "next_cursor": page.next_cursor,
                }
            if request.path_params["kind"] != "status":
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "未知状态查询")
            directory = OperationalStore(c.application.data_root.root).layout.backups_dir
            names = (
                sorted(
                    p.name for p in directory.glob("*.bundle") if p.is_dir() and not p.is_symlink()
                )
                if directory.is_dir()
                else []
            )
            entries = c.application.workspace_service._entries().workspaces
            return {
                "scope": "data-root",
                "maintaining": registry.maintaining,
                "blockers": [
                    wid for wid in entries if registry.busy(wid, pending=True, subscriptions=False)
                ],
                "backups": names[q.page * 20 : (q.page + 1) * 20],
                "next_cursor": str(q.page + 1) if len(names) > (q.page + 1) * 20 else None,
            }

        return JSONResponse(await host.execute_query(read))

    async def job(request):
        def read():
            c = host.context
            cid = request.path_params["command_id"]
            receipt = c.journal.get_application_command_receipt(c.workspace_id, cid)
            if receipt is None or not receipt.operation.startswith("state_"):
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "维护请求不存在")
            return c.workspaces.maintenance_jobs.get(
                (c.workspace_id, cid),
                {
                    "status": "interrupted",
                    "action": receipt.operation[6:],
                    "message": "Core 已重启；检查备份或重新预览后发起新操作，原请求不会自动重放",
                },
            )

        return JSONResponse(await host.execute_query(read))

    async def action(request):
        body = await parse(request, StateAction)

        def accept():
            c, registry = host.context, host.context.workspaces
            api = c.api
            operation = "state_" + body.action
            cid, digest, replay = api._prepare(
                operation, body.model_dump(exclude={"command_id"}), body.command_id
            )
            if replay:
                return {"command_id": cid, "disposition": "replay"}
            if body.action in {"backup", "cleanup"} and not body.confirmed:
                raise ApplicationError(ApplicationErrorCode.INVALID, "请确认数据根维护操作")
            if body.action == "cleanup" and not body.preview_digest:
                raise ApplicationError(ApplicationErrorCode.INVALID, "先预览清理并确认该预览")
            if body.action == "verify" and not body.bundle:
                raise ApplicationError(ApplicationErrorCode.INVALID, "请选择备份或输入其目录路径")
            guard = registry.maintenance()
            guard.__enter__()
            key = (c.workspace_id, cid)
            try:
                api.journal.transact(
                    lambda txn: api._receipt(
                        txn,
                        command_id=cid,
                        operation=operation,
                        digest=digest,
                        session_id=None,
                        result_kind="state_job",
                        result_id=cid,
                        event_cursor=None,
                    )
                )
                while len(registry.maintenance_jobs) >= 128:
                    registry.maintenance_jobs.popitem(last=False)
                registry.maintenance_jobs[key] = {"status": "running", "action": body.action}
            except BaseException:
                guard.__exit__(None, None, None)
                raise

            async def drive():
                try:
                    store = OperationalStore(c.application.data_root.root)
                    backup = OperationalBackupService(store)
                    if body.action == "doctor":
                        result = (
                            await asyncio.to_thread(
                                OperationalDoctor(store).inspect, c.workspace_id
                            )
                        ).model_dump(mode="json")
                    elif body.action in {"cleanup", "cleanup_preview"}:
                        report, preview = await ArtifactCleanupService(c.api.artifacts).run_async(
                            dry_run=body.action == "cleanup_preview",
                            expected_digest=body.preview_digest
                            if body.action == "cleanup"
                            else None,
                            with_digest=True,
                        )
                        result = {**report.model_dump(mode="json"), "preview_digest": preview}
                    elif body.action == "verify":
                        bundle = Path(body.bundle).expanduser()
                        if not bundle.is_absolute():
                            bundle = store.layout.backups_dir / bundle
                        report = await asyncio.to_thread(backup.verify, bundle)
                        result = {**report.model_dump(mode="json"), "ok": report.ok}
                    else:
                        name = body.name or "chat-" + sha256_digest(cid)[:16]

                        def create():
                            report = backup.create(name)
                            download = _archive_bundle(
                                backup, store.layout.backups_dir / report.bundle_name
                            )
                            return {
                                "bundle_name": report.bundle_name,
                                "manifest_sha256": report.manifest_sha256,
                                "schema_version": report.schema_version,
                                "integrity_ok": report.integrity_ok,
                                "artifact_count": len(report.artifacts),
                                "skill_version_count": len(report.skill_versions),
                                "download": download,
                            }

                        result = await asyncio.to_thread(create)
                    registry.maintenance_jobs[key] = {
                        "status": "completed",
                        "action": body.action,
                        "result": result,
                    }
                except CleanupPreviewChanged:
                    registry.maintenance_jobs[key] = {
                        "status": "failed",
                        "action": body.action,
                        "error": "preview_changed",
                        "message": "清理对象或引用已变化，请重新预览",
                    }
                except Exception:
                    registry.maintenance_jobs[key] = {
                        "status": "failed",
                        "action": body.action,
                        "error": "maintenance_failed",
                        "message": "维护未完成。检查工作区占用、备份路径及状态诊断后重试",
                    }
                finally:
                    guard.__exit__(None, None, None)
                    registry.maintenance_task = None

            registry.maintenance_task = asyncio.create_task(drive())
            return {"command_id": cid, "disposition": "accepted"}

        return JSONResponse(await host.execute_command(accept), status_code=202)

    async def download(request):
        name = request.path_params["name"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.zip", name):
            raise ApplicationError(ApplicationErrorCode.INVALID, "下载名称无效")

        def prepare():
            c = host.context
            path = OperationalStore(c.application.data_root.root).layout.backups_dir / name
            if not path.exists() or not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "备份下载不存在")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(descriptor)
                raise ApplicationError(ApplicationErrorCode.INVALID, "备份下载对象已变化")
            return os.fdopen(descriptor, "rb"), info.st_size

        source, size = await host.execute_query(prepare)

        async def chunks():
            try:
                while chunk := await asyncio.to_thread(source.read, 65536):
                    yield chunk
            finally:
                source.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Cache-Control": "no-store",
                "Content-Length": str(size),
                "Content-Disposition": f'attachment; filename="{name}"',
            },
        )

    return [
        Route("/v1/state/{kind}", query),
        Route("/v1/state-actions", action, methods=["POST"]),
        Route("/v1/state-jobs/{command_id}", job),
        Route("/v1/state-downloads/{name}", download),
    ]
