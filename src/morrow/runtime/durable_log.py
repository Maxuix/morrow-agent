"""Journal-backed ConversationLog appends. Grammar stays in ConversationLog."""

from __future__ import annotations

import re

from pydantic import TypeAdapter

from morrow.core.domain import (
    CONVERSATION_RECORD_ID_PREFIX,
    DurableConversationRecord,
    sha256_digest,
)
from morrow.core.journal import ConversationJournalPort
from morrow.core.models import FinishReason, Message
from morrow.core.ports import IdSource
from morrow.runtime.conversation import (
    ConversationAppend,
    ConversationLog,
    ConversationSnapshot,
    MessageRecord,
    TurnTerminalRecord,
)

_MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)
_DURABLE_CALL_ID_PATTERN = re.compile(r"^call_[0-9a-f]{64}$")


def durable_call_id(call_id: str) -> str:
    """Return a stable, non-sensitive correlation ID for durable projections.

    Provider call IDs are correlation data, not durable secrets.  Keeping them
    verbatim would leak arbitrary provider-controlled values into the
    operational store, while generating an ordinal alias per append can break
    the link between an Assistant tool call, its ToolMessage, and its
    ToolExecution.  A deterministic digest provides one stable ID for every
    projection without retaining the provider value.
    """

    if _DURABLE_CALL_ID_PATTERN.fullmatch(call_id):
        return call_id
    return f"call_{sha256_digest(call_id)}"


def conversation_record_from_durable(record: DurableConversationRecord):
    if record.kind == "terminal":
        interrupted = record.payload.get("interrupted_call_ids") or ()
        return TurnTerminalRecord(
            sequence=record.conversation_position,
            finish_reason=FinishReason(str(record.payload.get("finish_reason", "error"))),
            interrupted_call_ids=tuple(str(item) for item in interrupted),
        )
    return MessageRecord(
        sequence=record.conversation_position,
        message=_MESSAGE_ADAPTER.validate_python(record.payload),
    )


def _redacted_message_payload(record: MessageRecord) -> dict:
    message = record.message
    if message.role == "assistant" and message.tool_calls:
        calls = []
        for call in message.tool_calls:
            calls.append({"id": durable_call_id(call.id), "name": call.name, "arguments": "{}"})
        return {"role": "assistant", "content": message.content, "tool_calls": calls}
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": durable_call_id(message.tool_call_id),
            "content": '{"redacted":true}',
        }
    return message.model_dump(mode="json")


def durable_from_conversation_record(
    record,
    *,
    record_id: str,
    session_id: str,
) -> DurableConversationRecord:
    if isinstance(record, TurnTerminalRecord):
        payload = {
            "finish_reason": record.finish_reason.value,
            "interrupted_call_ids": [
                durable_call_id(call_id) for call_id in record.interrupted_call_ids
            ],
        }
        kind = "terminal"
    else:
        payload = _redacted_message_payload(record)
        kind = "message"
    return DurableConversationRecord(
        record_id=record_id,
        session_id=session_id,
        conversation_position=record.sequence,
        kind=kind,
        payload=payload,
    )


def restore_conversation_log(
    journal: ConversationJournalPort, workspace_id: str, session_id: str
) -> ConversationLog:
    records = tuple(
        conversation_record_from_durable(item)
        for item in journal.load_effective_records(workspace_id, session_id)
    )
    return ConversationLog.from_snapshot(ConversationSnapshot(records=records))


class DurableConversationWriter:
    """Persist a planned append, then replace the live projection from committed rows."""

    def __init__(
        self,
        log: ConversationLog,
        journal: ConversationJournalPort,
        *,
        workspace_id: str,
        session_id: str,
        id_source: IdSource,
    ) -> None:
        self.log = log
        self.journal = journal
        self.workspace_id = workspace_id
        self.session_id = session_id
        self.id_source = id_source

    def persist_with_records(
        self, planned: ConversationAppend
    ) -> tuple[tuple[DurableConversationRecord, ...], ConversationSnapshot]:
        durables = tuple(
            durable_from_conversation_record(
                record,
                record_id=self.id_source.new_id(CONVERSATION_RECORD_ID_PREFIX),
                session_id=self.session_id,
            )
            for record in planned.added
        )
        self.journal.append_records(self.workspace_id, durables)
        snapshot = restore_conversation_log(
            self.journal, self.workspace_id, self.session_id
        ).snapshot()
        return durables, snapshot

    def persist(self, planned: ConversationAppend) -> ConversationSnapshot:
        _durables, snapshot = self.persist_with_records(planned)
        return snapshot

    def apply_persisted(self, committed: ConversationSnapshot) -> None:
        self.log.install_snapshot(committed)

    def commit(self, planned: ConversationAppend) -> None:
        self.persist(planned)
        self.log.apply_committed(planned)
