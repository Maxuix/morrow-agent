"""Core-owned input references; parser results never write conversation history."""

import asyncio
import base64

from morrow.application.attachment_parsing import AttachmentParsePool
from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.attachments import AttachmentPart, AttachmentRef, AttachmentRepresentation
from morrow.core.domain import canonical_json_bytes
from morrow.core.image_tokens import image_dimensions
from morrow.core.models import ProviderInputPart, UserMessage


def hydrate_attachment_message(artifacts, message):
    """Resolve immutable references only for Provider context, never the Log."""
    if not isinstance(message, UserMessage) or not message.attachments:
        return message
    text, parts = [message.content], []
    for reference in message.attachments:
        metadata = artifacts.get(reference.representation_id)
        if metadata is None or metadata.sha256 != reference.representation_digest:
            raise ValueError("Attachment is missing or damaged")
        representation = AttachmentRepresentation.model_validate_json(
            artifacts.read(reference.representation_id, max_bytes=65536).content
        )
        text.append(
            f"\n[用户附件资料：{representation.source_path or representation.name}；读取方式：{representation.reading}；省略字符：{representation.omitted_chars}]"
        )
        for part in representation.parts:
            metadata = artifacts.get(part.artifact_id)
            if metadata is None or metadata.sha256 != part.digest:
                raise ValueError("Attachment content is missing or damaged")
            data = artifacts.read(part.artifact_id, max_bytes=metadata.byte_size).content
            if part.media_type == "text/plain":
                text.append(f"\n[页 {part.page}]\n" + data.decode() if part.page else data.decode())
            else:
                width, height = part.width, part.height
                if width is None or height is None:
                    parsed = image_dimensions(data)
                    if parsed is not None:
                        width, height = parsed
                parts.append(
                    ProviderInputPart(
                        type="image",
                        media_type=part.media_type,
                        data=base64.b64encode(data).decode(),
                        width=width,
                        height=height,
                    )
                )
    content = "\n".join(text)
    return message.model_copy(
        update={
            "content": content,
            "input_parts": (ProviderInputPart(type="text", text=content), *parts),
        }
    )


class AttachmentService:
    def __init__(self, journal, artifacts, workspace_id, require_session, id_source, *, pool=None):
        self.journal, self.artifacts = journal, artifacts
        self.records = journal.attachments
        self.workspace_id, self.require_session = workspace_id, require_session
        self.id_source = id_source
        self.pool = pool or AttachmentParsePool()
        self.jobs = {}
        self.records.restart(workspace_id)

    def reserve(self, request):
        self.require_session(request.session_id)
        return self.records.reserve(self.workspace_id, request, self.id_source.new_id("att"))

    def get(self, identity):
        return self.records.get(self.workspace_id, identity)

    def require_visible(self, identity, session_id):
        self.require_session(session_id)
        row = self.get(identity)
        if row["session_id"] == session_id:
            return row
        sid, cutoff = session_id, None
        for _ in range(33):
            session = self.journal.get_session(self.workspace_id, sid)
            if session is None:
                break
            if sid == row["session_id"] and cutoff is not None and row["state"] == "submitted":
                found = self.journal._backend.read_one(
                    "SELECT 1 FROM conversation_records c,json_each(c.payload_json,'$.attachments') a "
                    "WHERE c.session_id=? AND c.conversation_position<=? "
                    "AND json_extract(a.value,'$.attachment_id')=? LIMIT 1",
                    (sid, cutoff, identity),
                )
                if found:
                    return row
            if not session.parent_session_id:
                break
            cutoff = min(cutoff or session.parent_cut_position, session.parent_cut_position)
            sid = session.parent_session_id
        if self._leaf_record_references(identity, session_id):
            return row
        raise ApplicationError(
            ApplicationErrorCode.NOT_FOUND, "Attachment is outside this Session history"
        )

    def _leaf_record_references(self, identity, session_id):
        """Whether a visible workflow-leaf record of this view references it.

        Mirrors the index leaf-visibility rule (P06.4/P08.4): the requesting
        session's fork lineage is resolved server side, and leaf sessions of
        runs rooted inside that lineage are authorized as part of the root
        trajectory. A reference in one of their durable conversation records
        grants the same read as the fork-chain rule. No fork cut applies to
        leaf generations, exactly as on the timeline itself.
        """

        lineage = []
        sid = session_id
        for _ in range(33):
            session = self.journal.get_session(self.workspace_id, sid)
            if session is None:
                return False
            lineage.append(sid)
            if not session.parent_session_id:
                break
            sid = session.parent_session_id
        leaves = self.journal._backend.read_all(
            "SELECT DISTINCT json_extract(n.body_json,'$.conversation_session_id') "
            "FROM workflow_node_runs n "
            "JOIN workflow_runs r ON r.workflow_run_id=n.workflow_run_id "
            "JOIN task_runs t ON t.task_run_id=r.root_task_run_id "
            "WHERE n.workspace_id=? AND t.session_id IN (" + ",".join("?" * len(lineage)) + ")",
            (self.workspace_id, *lineage),
        )
        leaf_ids = [leaf[0] for leaf in leaves if leaf[0]]
        if not leaf_ids:
            return False
        return bool(
            self.journal._backend.read_one(
                "SELECT 1 FROM conversation_records c,json_each(c.payload_json,'$.attachments') a "
                "WHERE c.session_id IN (" + ",".join("?" * len(leaf_ids)) + ") "
                "AND json_extract(a.value,'$.attachment_id')=? LIMIT 1",
                (*leaf_ids, identity),
            )
        )

    def clone(self, identity, source_session, target_session, command_id):
        from morrow.core.attachments import MAX_STAGED_BYTES, AttachmentReservation

        source = self.require_visible(identity, source_session)
        if source["state"] != "submitted":
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Only submitted inputs can be copied"
            )
        self.require_session(target_session)
        row = self.reserve(
            AttachmentReservation.model_validate(
                {
                    **source["request"],
                    "session_id": target_session,
                    "command_id": command_id,
                }
            )
        )
        if row["state"] != "reserved":
            return row
        reference = {**source["reference"], "attachment_id": row["attachment_id"]}
        for artifact_id, role in self.records.blob_refs(self.workspace_id, identity):
            self.records.link(row, artifact_id, role)
        used, _ = self.records.staged_capacity()
        if used > MAX_STAGED_BYTES:
            raise ApplicationError(ApplicationErrorCode.BUSY, "Attachment staging capacity is full")
        return self.records.transition(row, "ready", reference=reference)

    def begin_upload(self, identity, revision, media_type):
        row = self.get(identity)
        if row["revision"] != revision:
            raise ApplicationError(ApplicationErrorCode.STALE, "Attachment revision changed")
        if row["state"] not in {"reserved", "failed"} or identity in self.jobs:
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Attachment is not awaiting upload"
            )
        if media_type != row["request"]["media_type"]:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Uploaded media type does not match reservation"
            )
        return self.records.transition(row, "uploading")

    def accept(self, identity, revision, content):
        row = self.get(identity)
        if row["revision"] != revision or row["state"] != "uploading":
            raise ApplicationError(ApplicationErrorCode.STALE, "Upload was cancelled or changed")
        if len(content) != row["byte_size"]:
            self.fail(row, "Upload size does not match reservation")
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Upload size does not match reservation"
            )
        reservations = getattr(self.pool, "reservations", set())
        if max(self.pool.active, len(reservations)) >= self.pool.concurrency:
            self.fail(row, "Parsers busy; retry the file")
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "Attachment parsers are busy; retry shortly"
            )
        row = self.records.transition(row, "processing")
        task = asyncio.create_task(self._process(row, content))
        self.jobs[identity] = task
        reservations.add(task)

        def settled(done):
            self.jobs.pop(identity, None)
            reservations.discard(done)

        task.add_done_callback(settled)
        return row

    def fail(self, row, reason):
        current = self.get(row["attachment_id"])
        if current["revision"] == row["revision"] and current["state"] in {
            "uploading",
            "processing",
        }:
            return self.records.transition(current, "failed", reason=reason)
        return current

    async def _process(self, row, content):
        try:
            parsed = await self.pool.parse(content, row["request"]["media_type"])
            if self.get(row["attachment_id"])["revision"] != row["revision"]:
                return
            from morrow.core.attachments import MAX_PRODUCT_BYTES, MAX_STAGED_BYTES

            required = len(content) + sum(len(p[2]) for p in parsed["parts"]) + 65536
            used, _ = self.records.staged_capacity(exclude_attachment_id=row["attachment_id"])
            if required > MAX_PRODUCT_BYTES or used + required > MAX_STAGED_BYTES:
                raise ValueError("Attachment parsed products exceed staging limits")
            # Publication and retention links run without awaits on the owner.
            raw = self._publish(row, content, "original")
            parts, previews = [], []
            for item in parsed.pop("parts"):
                mime, page, data, preview, *rest = item
                width = rest[0] if rest else None
                height = rest[1] if len(rest) > 1 else None
                if mime != "text/plain" and (width is None or height is None):
                    parsed_size = image_dimensions(data)
                    if parsed_size is not None:
                        width, height = parsed_size
                product = self._publish(row, data, "preview" if preview else "input")
                part = AttachmentPart(
                    artifact_id=product.artifact_id,
                    digest=product.sha256,
                    media_type=mime,
                    page=page,
                    chars=len(data.decode()) if mime == "text/plain" else 0,
                    width=None if mime == "text/plain" else width,
                    height=None if mime == "text/plain" else height,
                )
                (previews if preview else parts).append(part)
            representation = AttachmentRepresentation(
                name=row["request"]["name"],
                source_path=row["request"].get("source_path"),
                parts=tuple(parts),
                previews=tuple(previews),
                **parsed,
            )
            manifest = self._publish(
                row, canonical_json_bytes(representation.model_dump(mode="json")), "representation"
            )
            reference = AttachmentRef(
                attachment_id=row["attachment_id"],
                content_digest=raw.sha256,
                representation_id=manifest.artifact_id,
                representation_digest=manifest.sha256,
            )
            self.records.transition(row, "ready", reference=reference.model_dump(mode="json"))
        except asyncio.CancelledError:
            self.fail(row, "Parsing cancelled; retry the file")
            raise
        except Exception:
            self.fail(
                row, "File is damaged, unsupported, or exceeds parsing limits; remove or retry"
            )

    def _publish(self, row, data, role):
        from morrow.core.domain import sha256_digest

        digest = sha256_digest(data)
        identity = "art_" + sha256_digest(row["attachment_id"] + role + digest)
        artifact = self.artifacts.get(identity)
        if artifact is not None:
            if artifact.state.value == "staging":
                artifact = self.artifacts.restore_staging_bytes(data, artifact_id=identity)
            self.artifacts.read(identity, max_bytes=artifact.byte_size)
        else:
            artifact = self.artifacts.publish_attachment_bytes(
                data, session_id=row["session_id"], artifact_id=identity, role=role
            )
        self.records.link(row, artifact.artifact_id, role)
        return artifact

    def representation(self, reference):
        metadata = self.artifacts.get(reference.representation_id)
        if metadata is None or metadata.sha256 != reference.representation_digest:
            raise ApplicationError(
                ApplicationErrorCode.INVALID, "Attachment representation changed or is missing"
            )
        return AttachmentRepresentation.model_validate_json(
            self.artifacts.read(metadata.artifact_id, max_bytes=65536).content
        )

    def validate_history(self, session_id, *, image_supported):
        from morrow.application.chat_runtime import MAX_RUNTIME_HISTORY_BYTES

        if (
            self.journal.chat_timeline.history_bytes(self.workspace_id, session_id)
            > MAX_RUNTIME_HISTORY_BYTES
        ):
            raise ApplicationError(
                ApplicationErrorCode.BUSY, "Session history exceeds runtime capacity"
            )
        for record in self.journal.load_records(self.workspace_id, session_id):
            for raw in record.payload.get("attachments", ()):
                reference = AttachmentRef.model_validate(raw)
                representation = self.representation(reference)
                if not image_supported and any(
                    p.media_type.startswith("image/") for p in representation.parts
                ):
                    raise ApplicationError(
                        ApplicationErrorCode.INVALID,
                        "历史包含图像附件；请选择支持图像的模型或新建对话",
                    )

    def validate(self, session_id, references, *, image_supported):
        for reference in references:
            row = self.get(reference.attachment_id)
            if row["session_id"] != session_id or row["state"] not in {"ready", "submitted"}:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Attachment is not ready in this Session"
                )
            if row["reference"] != reference.model_dump(mode="json"):
                raise ApplicationError(ApplicationErrorCode.CONFLICT, "Attachment content changed")
            representation = self.representation(reference)
            if not image_supported and any(
                p.media_type.startswith("image/") for p in representation.parts
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "This model cannot read images; select an image-capable model or remove the attachment",
                )
        return references

    def submit(self, references):
        for reference in references:
            row = self.get(reference.attachment_id)
            if row["state"] == "ready":
                self.records.transition(row, "submitted", reference=row["reference"])

    def _discard_released_blobs(self, row):
        from morrow.application.cleanup_fs import TrustedArtifactLayout

        released = []

        def forget(_):
            ids = self.records.blob_refs(row["workspace_id"], row["attachment_id"])
            for identity, _role in ids:
                metadata = self.artifacts.get(identity)
                if metadata is None or metadata.retention.value == "pinned":
                    continue
                if any(
                    self.journal.list_artifact_references(wid, identity)
                    for wid in self.journal.list_workspace_ids()
                ):
                    continue
                self.records.unlink(row, identity)
                if self.records.has_blob_reference(identity):
                    continue
                self.journal._backend.executor().execute(
                    "DELETE FROM artifacts WHERE artifact_id=?", (identity,)
                )
                released.append(metadata)

        self.journal.transact(forget)
        if not released:
            return
        # A crash after metadata commit leaves ordinary reclaimable orphan files.
        with TrustedArtifactLayout.open(self.artifacts.filesystem) as layout:
            ids = {a.artifact_id for a in released}
            for candidate in layout.scan(tuple(released), frozenset()):
                if candidate.artifact_id not in ids or not candidate.is_private_regular_file:
                    continue
                attempt = layout.quarantine(candidate, on_moved=lambda _: None)
                if attempt.status == "quarantined":
                    layout.discard_released_attachment(attempt.quarantine)

    def release(self, identity, revision, *, purge=True):
        row = self.get(identity)
        if row["revision"] != revision:
            raise ApplicationError(ApplicationErrorCode.STALE, "Attachment revision changed")
        if row["state"] == "submitted":
            raise ApplicationError(
                ApplicationErrorCode.CONFLICT, "Submitted content is retained by chat history"
            )
        if row["state"] == "released":
            if purge:
                self._discard_released_blobs(row)
            return row
        task = self.jobs.get(identity)
        if task:
            task.cancel()
        row = self.records.transition(row, "released")
        if purge:
            self._discard_released_blobs(row)
        return row

    async def shutdown(self):
        tasks = tuple(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.jobs.clear()
