"""Journal-backed ConversationLog appends. Grammar stays in ConversationLog."""

from __future__ import annotations

from pydantic import TypeAdapter

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.core.domain import CONVERSATION_RECORD_ID_PREFIX, DurableConversationRecord
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


def durable_from_conversation_record(
    record, *, record_id: str, session_id: str
) -> DurableConversationRecord:
    if isinstance(record, TurnTerminalRecord):
        payload = {
            "finish_reason": record.finish_reason.value,
            "interrupted_call_ids": list(record.interrupted_call_ids),
        }
        kind = "terminal"
    else:
        payload = record.message.model_dump(mode="json")
        kind = "message"
    return DurableConversationRecord(
        record_id=record_id,
        session_id=session_id,
        conversation_position=record.sequence,
        kind=kind,
        payload=payload,
    )


def restore_conversation_log(
    journal: SqliteOperationalJournal, workspace_id: str, session_id: str
) -> ConversationLog:
    records = tuple(
        conversation_record_from_durable(item)
        for item in journal.load_records(workspace_id, session_id)
    )
    return ConversationLog.from_snapshot(ConversationSnapshot(records=records))


class DurableConversationWriter:
    """Persist a planned append, then replace the live projection from committed rows."""

    def __init__(
        self,
        log: ConversationLog,
        journal: SqliteOperationalJournal,
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

    def commit(self, planned: ConversationAppend) -> None:
        durables = tuple(
            durable_from_conversation_record(
                record,
                record_id=self.id_source.new_id(CONVERSATION_RECORD_ID_PREFIX),
                session_id=self.session_id,
            )
            for record in planned.added
        )
        self.journal.append_records(self.workspace_id, durables)
        restored = restore_conversation_log(self.journal, self.workspace_id, self.session_id)
        current = self.log.snapshot().records
        committed = restored.snapshot()
        self.log.apply_committed(
            ConversationAppend(added=committed.records[len(current) :], snapshot=committed)
        )
