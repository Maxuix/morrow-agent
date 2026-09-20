"""Core-owned draft rows and Artifact retention edges."""

import json

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.core.attachments import MAX_STAGED_BYTES, AttachmentReservation, AttachmentState

_ATTACHMENT_COLUMNS = (
    "attachment_id,workspace_id,session_id,command_id,request_json,byte_size,state,"
    "revision,reference_json,reason,blob_refs_json"
)


def _row(row):
    if row is None:
        return None
    data = dict(
        zip(
            (
                "attachment_id",
                "workspace_id",
                "session_id",
                "command_id",
                "request",
                "byte_size",
                "state",
                "revision",
                "reference",
                "reason",
                "blob_refs",
            ),
            row,
            strict=True,
        )
    )
    data["request"] = json.loads(data["request"])
    data["reference"] = json.loads(data["reference"]) if data["reference"] else None
    try:
        refs = json.loads(data["blob_refs"]) if data["blob_refs"] else []
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ApplicationError(
            ApplicationErrorCode.INVALID, "Attachment artifact references are corrupt"
        ) from exc
    if not isinstance(refs, list):
        raise ApplicationError(
            ApplicationErrorCode.INVALID, "Attachment artifact references are invalid"
        )
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("artifact_id"), str)
        or not isinstance(item.get("role"), str)
        for item in refs
    ):
        raise ApplicationError(
            ApplicationErrorCode.INVALID, "Attachment artifact references are invalid"
        )
    data["blob_refs"] = tuple((item["artifact_id"], item["role"]) for item in refs)
    return data


class AttachmentJournal:
    def __init__(self, backend):
        self.backend = backend

    def get(self, workspace_id, attachment_id):
        row = _row(
            self.backend.read_one(
                f"SELECT {_ATTACHMENT_COLUMNS} FROM chat_attachments "
                "WHERE workspace_id=? AND attachment_id=?",
                (workspace_id, attachment_id),
            )
        )
        if row is None:
            raise ApplicationError(
                ApplicationErrorCode.NOT_FOUND, "Attachment is outside this workspace"
            )
        return row

    def reserve(self, workspace_id, request, identity):
        def work():
            existing = _row(
                self.backend.read_one(
                    f"SELECT {_ATTACHMENT_COLUMNS} FROM chat_attachments "
                    "WHERE workspace_id=? AND session_id=? AND command_id=?",
                    (workspace_id, request.session_id, request.command_id),
                )
            )
            if existing is not None:
                if AttachmentReservation.model_validate(existing["request"]) != request:
                    raise ApplicationError(
                        ApplicationErrorCode.CONFLICT, "Attachment key has different content"
                    )
                return existing
            used, count = self.staged_capacity()
            if used + request.byte_size > MAX_STAGED_BYTES or count >= 128:
                raise ApplicationError(
                    ApplicationErrorCode.BUSY,
                    "Attachment staging capacity is full; release unused drafts",
                )
            self.backend.executor().execute(
                "INSERT INTO chat_attachments(attachment_id,workspace_id,session_id,command_id,request_json,byte_size,state) "
                "VALUES(?,?,?,?,?,?,'reserved')",
                (
                    identity,
                    workspace_id,
                    request.session_id,
                    request.command_id,
                    request.model_dump_json(),
                    request.byte_size,
                ),
            )
            return self.get(workspace_id, identity)

        return self.backend.transact(work)

    def transition(self, row, state: AttachmentState | str, *, reference=None, reason=None):
        state = AttachmentState(state)

        def work():
            current = self.get(row["workspace_id"], row["attachment_id"])
            if current["revision"] != row["revision"]:
                raise ApplicationError(
                    ApplicationErrorCode.STALE, "Attachment changed; refresh before retrying"
                )
            self.backend.executor().execute(
                "UPDATE chat_attachments SET state=?,revision=revision+1,reference_json=?,reason=? WHERE attachment_id=?",
                (
                    state.value,
                    json.dumps(reference) if reference else None,
                    reason,
                    row["attachment_id"],
                ),
            )
            return self.get(row["workspace_id"], row["attachment_id"])

        return self.backend.transact(work)

    def link(self, row, artifact_id, role):
        def work():
            current = self.get(row["workspace_id"], row["attachment_id"])
            artifact = self.backend.read_one(
                "SELECT workspace_id FROM artifacts WHERE artifact_id=?", (artifact_id,)
            )
            if artifact is None or str(artifact[0]) != str(row["workspace_id"]):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "Attachment artifact is missing or outside this workspace",
                )
            refs = list(current["blob_refs"])
            if (artifact_id, role) not in refs:
                refs.append((artifact_id, role))
                self.backend.executor().execute(
                    "UPDATE chat_attachments SET blob_refs_json=? WHERE attachment_id=?",
                    (
                        json.dumps(
                            [
                                {"artifact_id": identity, "role": ref_role}
                                for identity, ref_role in refs
                            ],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        row["attachment_id"],
                    ),
                )

        self.backend.transact(work)

    def blob_refs(self, workspace_id, attachment_id):
        return self.get(workspace_id, attachment_id)["blob_refs"]

    def unlink(self, row, artifact_id):
        def work():
            current = self.get(row["workspace_id"], row["attachment_id"])
            refs = [item for item in current["blob_refs"] if item[0] != artifact_id]
            self.backend.executor().execute(
                "UPDATE chat_attachments SET blob_refs_json=? WHERE attachment_id=?",
                (
                    json.dumps(
                        [
                            {"artifact_id": identity, "role": ref_role}
                            for identity, ref_role in refs
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row["attachment_id"],
                ),
            )

        self.backend.transact(work)

    def has_blob_reference(self, artifact_id):
        for (refs_json,) in self.backend.read_all("SELECT blob_refs_json FROM chat_attachments"):
            try:
                refs = json.loads(refs_json) if refs_json else []
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Attachment artifact references are corrupt"
                ) from exc
            if not isinstance(refs, list) or any(
                not isinstance(item, dict) or not item.get("artifact_id") for item in refs
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Attachment artifact references are invalid"
                )
            if any(item.get("artifact_id") == artifact_id for item in refs):
                return True
        return False

    def staged_capacity(self, *, exclude_attachment_id=None):
        sql = "SELECT byte_size, blob_refs_json FROM chat_attachments WHERE state NOT IN ('submitted','released')"
        params = ()
        if exclude_attachment_id is not None:
            sql += " AND attachment_id != ?"
            params = (exclude_attachment_id,)
        rows = self.backend.read_all(sql, params)
        used = 0
        for byte_size, refs_json in rows:
            try:
                refs = json.loads(refs_json) if refs_json else []
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Attachment artifact references are corrupt"
                ) from exc
            artifact_bytes = 0
            if not isinstance(refs, list) or any(
                not isinstance(item, dict) or not item.get("artifact_id") for item in refs
            ):
                raise ApplicationError(
                    ApplicationErrorCode.INVALID, "Attachment artifact references are invalid"
                )
            for item in refs:
                artifact = self.backend.read_one(
                    "SELECT byte_size FROM artifacts WHERE artifact_id=?",
                    (item["artifact_id"],),
                )
                if artifact is not None:
                    artifact_bytes += int(artifact[0])
            used += max(int(byte_size), artifact_bytes)
        count_sql = (
            "SELECT COUNT(*) FROM chat_attachments WHERE state NOT IN ('submitted','released')"
        )
        count_params = ()
        if exclude_attachment_id is not None:
            count_sql += " AND attachment_id != ?"
            count_params = (exclude_attachment_id,)
        count = self.backend.read_one(count_sql, count_params)
        return used, int(count[0]) if count is not None else 0

    def restart(self, workspace_id):
        self.backend.transact(
            lambda: self.backend.executor().execute(
                "UPDATE chat_attachments SET state='failed',reason='Upload interrupted; retry the original file',revision=revision+1 "
                "WHERE workspace_id=? AND state IN ('uploading','processing')",
                (workspace_id,),
            )
        )
