"""Authenticated bounded upload exception; decoding stays off the mutation bus."""

import asyncio

from pydantic import Field
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.attachments import AttachmentRef, AttachmentReservation, attachment_limits
from morrow.core.models import ProtocolModel


class AttachmentMutation(ProtocolModel):
    command_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]+$")
    expected_revision: int = Field(ge=1, strict=True)


class AttachmentClone(ProtocolModel):
    command_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    source_session: str = Field(pattern=r"^ses_[A-Za-z0-9_-]+$")
    target_session: str = Field(pattern=r"^ses_[A-Za-z0-9_-]+$")


class FileReferenceRequest(ProtocolModel):
    command_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    session_id: str = Field(pattern=r"^ses_[A-Za-z0-9_-]+$")
    path: str = Field(min_length=1, max_length=1024)


def attachment_routes(host, parse):
    file_jobs = set()

    async def bounded_file_read(fn, *args, **kwargs):
        if len(file_jobs) >= 2:
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "File readers are busy; retry shortly"
            )
        task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
        file_jobs.add(task)

        def settled(done):
            file_jobs.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(settled)
        return await asyncio.shield(task)

    def context(request):
        wid = request.path_params["workspace_id"]
        if host.context.workspaces is not None:
            return host.context.workspaces.get(wid)
        if wid != host.context.workspace_id:
            raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Workspace is unavailable")
        return host.context

    def service(request):
        return context(request).chat.attachments

    def scoped(request, session_id=None):
        s = service(request)
        sid = session_id or request.query_params.get("session_id", "")
        s.require_visible(request.path_params["attachment_id"], sid)
        return s

    def wire(s, row):
        result = {
            k: row[k]
            for k in (
                "attachment_id",
                "session_id",
                "state",
                "revision",
                "reference",
                "reason",
                "byte_size",
            )
        }
        result["name"] = row["request"]["name"]
        result["media_type"] = row["request"]["media_type"]
        result["limits"] = attachment_limits()
        if row["reference"]:
            result["representation"] = s.representation(
                AttachmentRef.model_validate(row["reference"])
            ).model_dump(mode="json")
        return result

    async def reserve(request):
        body = await parse(request, AttachmentReservation)
        if body.source != "upload" or body.source_path is not None:
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Use workspace file selection for workspace references",
            )

        def run():
            c = context(request)
            if c.workspaces:
                c.workspaces.check_admission()
            return wire(c.chat.attachments, c.chat.attachments.reserve(body))

        return JSONResponse(await host.execute_command(run), status_code=201)

    async def metadata(request):
        return JSONResponse(
            await host.execute_query(
                lambda: wire(
                    scoped(request), scoped(request).get(request.path_params["attachment_id"])
                )
            ),
            headers={"Cache-Control": "no-store"},
        )

    async def upload(request):
        identity = request.path_params["attachment_id"]
        try:
            revision = int(request.query_params.get("revision", ""))
        except ValueError:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Upload revision is required"
            ) from None
        mime = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        row = await host.execute_command(
            lambda: scoped(request).begin_upload(identity, revision, mime)
        )
        accepted = False
        try:
            content = bytearray()
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    if len(content) + len(chunk) > row["byte_size"]:
                        raise ApplicationError(
                            ApplicationErrorCode.INVALID, "Upload exceeds reserved bytes"
                        )
                    content.extend(chunk)
            result = await host.execute_command(
                lambda: wire(
                    service(request),
                    service(request).accept(identity, row["revision"], bytes(content)),
                )
            )
            accepted = True
            return JSONResponse(result, status_code=202)
        except (TimeoutError, ClientDisconnect):
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Upload interrupted; retry the file"
            ) from None
        finally:
            if not accepted:
                await host.execute_command(
                    lambda: service(request).fail(row, "Upload interrupted; retry the file")
                )

    async def release(request):
        from morrow.server.commands import ServerCommands

        body = await parse(request, AttachmentMutation)

        def run():
            s = scoped(request, body.session_id)
            identity = request.path_params["attachment_id"]
            c = context(request)
            commands = ServerCommands(c)
            row, _ = c.journal.transact(
                lambda _: commands._idempotent(
                    "attachment_release",
                    body.command_id,
                    {"attachment_id": identity, **body.model_dump(mode="json")},
                    lambda: (s.release(identity, body.expected_revision, purge=False), identity),
                    lambda _: s.get(identity),
                    result_kind="attachment",
                )
            )
            s._discard_released_blobs(row)
            return wire(s, row)

        return JSONResponse(await host.execute_command(run))

    async def clone(request):
        from morrow.server.commands import ServerCommands

        body = await parse(request, AttachmentClone)

        def run():
            c = context(request)
            c.workspaces.check_admission()
            s = c.chat.attachments
            identity = request.path_params["attachment_id"]

            def apply():
                row = s.clone(identity, body.source_session, body.target_session, body.command_id)
                return row, row["attachment_id"]

            row, _ = c.journal.transact(
                lambda _: ServerCommands(c)._idempotent(
                    "attachment_clone",
                    body.command_id,
                    {"attachment_id": identity, **body.model_dump()},
                    apply,
                    lambda receipt: s.get(receipt.result_id),
                    result_kind="attachment",
                )
            )
            return wire(s, row)

        return JSONResponse(await host.execute_command(run))

    async def content(request):
        def run():
            s = scoped(request)
            row = s.get(request.path_params["attachment_id"])
            if row["reference"] is None:
                raise ApplicationError(ApplicationErrorCode.CONFLICT, "Attachment is not ready")
            rep = s.representation(AttachmentRef.model_validate(row["reference"]))
            artifact_id = request.query_params.get("artifact_id")
            part = next(
                (p for p in (*rep.parts, *rep.previews) if p.artifact_id == artifact_id), None
            )
            if part is None:
                raise ApplicationError(ApplicationErrorCode.NOT_FOUND, "Attachment part is missing")
            metadata = s.artifacts.get(part.artifact_id)
            if metadata is None or metadata.sha256 != part.digest:
                raise ApplicationError(ApplicationErrorCode.INVALID, "Attachment part is damaged")
            return s.artifacts.read(
                part.artifact_id, max_bytes=metadata.byte_size
            ).content, part.media_type

        data, mime = await host.execute_query(run)
        return Response(
            data,
            media_type=mime,
            headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
        )

    async def files(request):
        from morrow.application.attachment_files import search_selected

        root = await host.execute_query(
            lambda: str(context(request).workspaces.roots[context(request).workspace_id])
        )
        try:
            result = await bounded_file_read(
                search_selected,
                root,
                request.query_params.get("query", ""),
                directory=request.query_params.get("directory", "."),
            )
        except (OSError, ValueError):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "Workspace path is unavailable or outside the allowed scope",
            ) from None
        return JSONResponse(result)

    async def reference(request):
        from pathlib import PurePosixPath

        from morrow.application.attachment_files import read_selected, selected_media_type

        body = await parse(request, FileReferenceRequest)

        def prepare():
            c = context(request)
            c.chat.require_session(body.session_id)
            c.workspaces.check_admission()
            return str(c.workspaces.roots[c.workspace_id])

        root = await host.execute_query(prepare)
        try:
            data = await bounded_file_read(read_selected, root, body.path)
        except (OSError, ValueError):
            raise ApplicationError(
                ApplicationErrorCode.INVALID,
                "File is unavailable, too large, or outside the allowed workspace scope",
            ) from None

        def finish():
            c = context(request)
            if str(c.workspaces.roots[c.workspace_id]) != root:
                raise ApplicationError(
                    ApplicationErrorCode.CONFLICT, "Workspace root changed; select the file again"
                )
            s = service(request)
            spec = AttachmentReservation(
                command_id=body.command_id,
                session_id=body.session_id,
                name=PurePosixPath(body.path).name,
                media_type=selected_media_type(body.path),
                byte_size=len(data),
                source="workspace",
                source_path=body.path,
            )
            row = s.reserve(spec)
            if row["state"] in {"ready", "submitted", "processing"}:
                return wire(s, row)
            row = s.begin_upload(row["attachment_id"], row["revision"], spec.media_type)
            return wire(s, s.accept(row["attachment_id"], row["revision"], data))

        return JSONResponse(await host.execute_command(finish), status_code=202)

    base = "/v1/workspaces/{workspace_id}/attachments"
    return [
        Route("/v1/workspaces/{workspace_id}/files/search", files),
        Route("/v1/workspaces/{workspace_id}/attachments/reference", reference, methods=["POST"]),
        Route(base, reserve, methods=["POST"]),
        Route(base + "/{attachment_id}", metadata),
        Route(base + "/{attachment_id}/content", upload, methods=["PUT"]),
        Route(base + "/{attachment_id}/content", content),
        Route(base + "/{attachment_id}/clone", clone, methods=["POST"]),
        Route(base + "/{attachment_id}/release", release, methods=["POST"]),
        Route(base + "/{attachment_id}/cancel", release, methods=["POST"]),
    ]
