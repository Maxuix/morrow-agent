"""Named fault-injection points for durable execution tests.

Production composition injects :class:`NoOpFaultInjector`. Tests may inject
:class:`OnceFaultInjector` to raise at exactly one named point once. Subplan 39
reuses the same points for subprocess ``os._exit`` via ``action``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Protocol


class FaultPoint(StrEnum):
    CONVERSATION_BEFORE_COMMIT = "conversation.before_commit"
    CONVERSATION_AFTER_COMMIT = "conversation.after_commit"
    EXECUTION_INTENT_AFTER_COMMIT = "execution.intent_after_commit"
    APPROVAL_AFTER_CREATE = "approval.after_create"
    APPROVAL_AFTER_CONSUME = "approval.after_consume"
    HANDLER_BEFORE_ENTER = "handler.before_enter"
    HANDLER_AFTER_RETURN = "handler.after_return"
    EXECUTION_AFTER_HANDLER_COMPLETED = "execution.after_handler_completed"
    CONVERSATION_BEFORE_TOOL_MESSAGE_COMMIT = "conversation.before_tool_message_commit"
    CONVERSATION_AFTER_TOOL_MESSAGE_COMMIT = "conversation.after_tool_message_commit"
    TURN_BEFORE_TERMINAL_COMMIT = "turn.before_terminal_commit"
    TURN_AFTER_TERMINAL_COMMIT = "turn.after_terminal_commit"
    ARTIFACT_AFTER_RESERVE = "artifact.after_reserve"
    ARTIFACT_AFTER_TEMP_CREATE = "artifact.after_temp_create"
    ARTIFACT_FILE_FSYNC = "artifact.file_fsync"
    ARTIFACT_BEFORE_RENAME = "artifact.before_rename"
    ARTIFACT_AFTER_RENAME = "artifact.after_rename"
    ARTIFACT_AFTER_PARENT_FSYNC = "artifact.after_parent_fsync"
    ARTIFACT_BEFORE_MARK_AVAILABLE = "artifact.before_mark_available"
    ARTIFACT_AFTER_MARK_AVAILABLE = "artifact.after_mark_available"
    CHECKPOINT_BEFORE_COMMIT = "checkpoint.before_commit"
    CHECKPOINT_AFTER_COMMIT = "checkpoint.after_commit"
    FORK_BEFORE_COMMIT = "fork.before_commit"
    FORK_AFTER_COMMIT = "fork.after_commit"


REQUIRED_FAULT_POINTS = frozenset(FaultPoint)


class InjectedFault(RuntimeError):
    """Logical exception raised by a test-only one-shot injector."""

    def __init__(self, point: FaultPoint) -> None:
        super().__init__(f"injected fault at {point.value}")
        self.point = point


class FaultInjector(Protocol):
    def check(self, point: FaultPoint | str) -> None: ...


class NoOpFaultInjector:
    """Production injector: never raises and never observes a boundary."""

    def check(self, point: FaultPoint | str) -> None:
        return None


class OnceFaultInjector:
    """Test-only injector that fires exactly one named point once."""

    def __init__(
        self,
        point: FaultPoint,
        *,
        action: Callable[[FaultPoint], None] | None = None,
    ) -> None:
        self.point = FaultPoint(point)
        self._action = action
        self._fired = False

    @property
    def fired(self) -> bool:
        return self._fired

    def check(self, point: FaultPoint | str) -> None:
        if self._fired:
            return
        current = point if isinstance(point, FaultPoint) else FaultPoint(point)
        if current is not self.point:
            return
        self._fired = True
        if self._action is not None:
            self._action(self.point)
            return
        raise InjectedFault(self.point)
