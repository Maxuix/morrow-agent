"""Process-local conversation log; the single chat history authority."""

from __future__ import annotations

from pydantic import field_validator

from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    Message,
    ProtocolModel,
    ToolMessage,
    UserMessage,
)


class MessageRecord(ProtocolModel):
    sequence: int
    message: Message


class TurnTerminalRecord(ProtocolModel):
    sequence: int
    finish_reason: FinishReason
    interrupted_call_ids: tuple[str, ...] = ()

    @field_validator("interrupted_call_ids")
    @classmethod
    def unique_interrupted_call_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not call_id.strip() for call_id in value):
            raise ValueError("interrupted call IDs must be non-empty")
        if len(value) != len(set(value)):
            raise ValueError("interrupted call IDs must be unique")
        return value


ConversationRecord = MessageRecord | TurnTerminalRecord


class ToolCycleView(ProtocolModel):
    """One accepted Assistant call batch and its ordered result records."""

    assistant: MessageRecord
    results: tuple[MessageRecord, ...]
    unresolved_call_ids: tuple[str, ...]

    @property
    def is_closed(self) -> bool:
        return not self.unresolved_call_ids

    @property
    def records(self) -> tuple[MessageRecord, ...]:
        return (self.assistant, *self.results)


class PublicTurnView(ProtocolModel):
    """A User-led public turn derived from an immutable record snapshot."""

    user: MessageRecord
    cycles: tuple[ToolCycleView, ...]
    final_assistant: MessageRecord | None = None
    terminal: TurnTerminalRecord | None = None

    @property
    def is_closed(self) -> bool:
        return self.terminal is not None

    @property
    def unresolved_call_ids(self) -> tuple[str, ...]:
        if not self.cycles:
            return ()
        return self.cycles[-1].unresolved_call_ids

    @property
    def records(self) -> tuple[ConversationRecord, ...]:
        records: list[ConversationRecord] = [self.user]
        for cycle in self.cycles:
            records.extend(cycle.records)
        if self.final_assistant is not None:
            records.append(self.final_assistant)
        if self.terminal is not None:
            records.append(self.terminal)
        return tuple(records)


class ConversationSnapshot(ProtocolModel):
    records: tuple[ConversationRecord, ...]

    def messages(self) -> tuple[Message, ...]:
        return tuple(record.message for record in self.records if isinstance(record, MessageRecord))

    def public_turns(self, *, require_closed: bool = False) -> tuple[PublicTurnView, ...]:
        return _derive_public_turns(self.records, require_closed=require_closed)


class ConversationLogError(RuntimeError):
    """Raised when history would break the public-turn grammar."""


def _derive_public_turns(
    records: tuple[ConversationRecord, ...], *, require_closed: bool
) -> tuple[PublicTurnView, ...]:
    """Validate and derive semantic turns without mutating the snapshot."""
    turns: list[PublicTurnView] = []
    user: MessageRecord | None = None
    cycles: list[ToolCycleView] = []
    final_assistant: MessageRecord | None = None
    terminal: TurnTerminalRecord | None = None
    pending_ids: list[str] = []
    seen_call_ids: set[str] = set()
    cycle_assistant: MessageRecord | None = None
    cycle_results: list[MessageRecord] = []
    previous_sequence = 0

    def close_cycle() -> None:
        nonlocal cycle_assistant, cycle_results
        if cycle_assistant is not None:
            cycles.append(
                ToolCycleView(
                    assistant=cycle_assistant,
                    results=tuple(cycle_results),
                    unresolved_call_ids=tuple(pending_ids),
                )
            )
            cycle_assistant = None
            cycle_results = []

    def close_turn() -> None:
        nonlocal user, cycles, final_assistant, terminal, seen_call_ids
        if user is None:
            raise ConversationLogError("terminal record has no opening User")
        turns.append(
            PublicTurnView(
                user=user,
                cycles=tuple(cycles),
                final_assistant=final_assistant,
                terminal=terminal,
            )
        )
        user = None
        cycles = []
        final_assistant = None
        terminal = None
        seen_call_ids = set()

    for record in records:
        if record.sequence <= previous_sequence:
            raise ConversationLogError("record sequences must be strictly increasing")
        previous_sequence = record.sequence

        if isinstance(record, TurnTerminalRecord):
            if user is None:
                raise ConversationLogError("orphan terminal record")
            if pending_ids:
                raise ConversationLogError("terminal record crosses an open ToolCycle")
            close_cycle()
            if record.finish_reason == FinishReason.STOP and final_assistant is None:
                raise ConversationLogError("completed turn requires a final no-tools Assistant")
            if record.finish_reason == FinishReason.STOP and record.interrupted_call_ids:
                raise ConversationLogError("completed turn cannot contain interrupted call IDs")
            if any(call_id not in seen_call_ids for call_id in record.interrupted_call_ids):
                raise ConversationLogError(
                    "terminal record contains an unknown interrupted call ID"
                )
            terminal = record
            close_turn()
            continue

        message = record.message
        if isinstance(message, UserMessage):
            if user is not None:
                raise ConversationLogError("User cannot cross an active public turn")
            user = record
            continue
        if user is None:
            raise ConversationLogError("conversation message has no opening User")
        if isinstance(message, ToolMessage):
            if not pending_ids or cycle_assistant is None:
                raise ConversationLogError("orphan or duplicate tool result")
            if message.tool_call_id != pending_ids[0]:
                raise ConversationLogError("tool results must arrive in original call order")
            pending_ids.pop(0)
            cycle_results.append(record)
            if not pending_ids:
                close_cycle()
            continue
        if not isinstance(message, AssistantMessage):
            raise ConversationLogError("System messages are not legal conversation records")
        if pending_ids:
            raise ConversationLogError("Assistant cannot cross an open ToolCycle")
        if final_assistant is not None:
            raise ConversationLogError("Assistant cannot follow the final Assistant")
        if message.tool_calls:
            ids = [call.id for call in message.tool_calls]
            if len(ids) != len(set(ids)):
                raise ConversationLogError("tool call IDs must be unique within one ToolCycle")
            seen_call_ids.update(ids)
            cycle_assistant = record
            cycle_results = []
            pending_ids = ids
        else:
            final_assistant = record

    if cycle_assistant is not None:
        close_cycle()
    if user is not None:
        if require_closed:
            raise ConversationLogError("strict conversation view contains an open public turn")
        turns.append(
            PublicTurnView(
                user=user,
                cycles=tuple(cycles),
                final_assistant=final_assistant,
                terminal=None,
            )
        )
    return tuple(turns)


class ConversationLog:
    """Controlled append-only history with one active public turn at a time."""

    def __init__(self) -> None:
        self._records: list[ConversationRecord] = []
        self._sequence = 0
        self._active = False
        self._pending_call_ids: list[str] = []
        self._turn_call_ids: set[str] = set()
        self._has_final_assistant = False

    @property
    def has_active_turn(self) -> bool:
        return self._active

    @property
    def unresolved_call_ids(self) -> tuple[str, ...]:
        return tuple(self._pending_call_ids)

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def begin_turn(self, user: UserMessage) -> None:
        if self._active:
            raise ConversationLogError("a turn is already active")
        self._active = True
        self._pending_call_ids = []
        self._turn_call_ids = set()
        self._has_final_assistant = False
        self._records.append(MessageRecord(sequence=self._next_sequence(), message=user))

    def append_assistant(self, message: AssistantMessage) -> None:
        if not self._active:
            raise ConversationLogError("no active turn")
        if self._pending_call_ids:
            raise ConversationLogError("cannot append Assistant while a ToolCycle is open")
        if self._has_final_assistant:
            raise ConversationLogError("cannot append after the final Assistant")
        ids = [call.id for call in message.tool_calls]
        if len(ids) != len(set(ids)):
            raise ConversationLogError("tool call IDs must be unique within one ToolCycle")
        self._turn_call_ids.update(ids)
        self._records.append(MessageRecord(sequence=self._next_sequence(), message=message))
        self._pending_call_ids = ids
        if not ids:
            self._has_final_assistant = True

    def append_tool_result(self, tool_call_id: str, content: str) -> None:
        if not self._active:
            raise ConversationLogError("no active turn")
        if not self._pending_call_ids:
            raise ConversationLogError("no unresolved tool call for this result")
        if tool_call_id != self._pending_call_ids[0]:
            raise ConversationLogError("tool results must arrive in original call order")
        self._pending_call_ids.pop(0)
        self._records.append(
            MessageRecord(
                sequence=self._next_sequence(),
                message=ToolMessage(tool_call_id=tool_call_id, content=content),
            )
        )

    def finish_turn(
        self, reason: FinishReason, *, interrupted_call_ids: tuple[str, ...] = ()
    ) -> None:
        if not self._active:
            raise ConversationLogError("no active turn")
        if self._pending_call_ids:
            raise ConversationLogError("cannot finish a turn with unresolved tool calls")
        if reason == FinishReason.STOP and not self._has_final_assistant:
            raise ConversationLogError("completed turn requires a final no-tools Assistant")
        if reason == FinishReason.STOP and interrupted_call_ids:
            raise ConversationLogError("completed turn cannot contain interrupted call IDs")
        if len(interrupted_call_ids) != len(set(interrupted_call_ids)):
            raise ConversationLogError("interrupted call IDs must be unique")
        if any(call_id not in self._turn_call_ids for call_id in interrupted_call_ids):
            raise ConversationLogError("interrupted call IDs must belong to the active turn")
        self._active = False
        self._records.append(
            TurnTerminalRecord(
                sequence=self._next_sequence(),
                finish_reason=reason,
                interrupted_call_ids=interrupted_call_ids,
            )
        )

    def snapshot(self) -> ConversationSnapshot:
        snapshot = ConversationSnapshot(records=tuple(self._records))
        snapshot.public_turns(require_closed=not self._active)
        return snapshot

    def messages_view(self) -> tuple[Message, ...]:
        return self.snapshot().messages()

    def reset(self) -> None:
        self._records = []
        self._sequence = 0
        self._active = False
        self._pending_call_ids = []
        self._turn_call_ids = set()
        self._has_final_assistant = False
