"""In-process session state; the ConversationLog is the only history authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from morrow.core.capabilities import (
    PermissionProfile,
    RunMetricsSnapshot,
    ToolFact,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.context import ContextCheckpoint
from morrow.core.domain import SessionHealth, SessionLifecycle
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    ToolExecutionDisposition,
)
from morrow.core.faults import FaultPoint
from morrow.core.models import (
    AssistantMessage,
    FinishReason,
    Message,
    Preferences,
    Profile,
    ToolDefinition,
    UserMessage,
)
from morrow.core.permissions import PermissionSnapshot
from morrow.runtime.conversation import ConversationAppend, ConversationLog

if TYPE_CHECKING:
    from morrow.runtime.tools import ToolExecutionOutcome, ToolExecutor


class SessionCommitter(Protocol):
    def commit(self, planned: ConversationAppend) -> None: ...


class TurnSubmissionResult(Protocol):
    """Outcome shape required by AgentLoop when durable Turn submission is enabled."""

    kind: str
    turn_id: str | None
    assistant_text: str | None


class DurableRunCoordinator(SessionCommitter, Protocol):
    """Explicit durable lifecycle contract consumed by AgentLoop.

    Process-local Sessions leave ``durable_runtime`` unset. Production persistence implements this
    complete contract; AgentLoop never discovers individual durable capabilities dynamically.
    """

    current_turn_id: str | None

    def now(self) -> datetime: ...

    def submit_user(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
        *,
        turn_id: str,
        agent_run_id: str,
        tools: tuple[ToolDefinition, ...],
    ) -> TurnSubmissionResult: ...

    def freeze_permission_snapshot(
        self,
        session: Session,
        *,
        tools: tuple[ToolDefinition, ...] = (),
        now: datetime | None = None,
    ) -> PermissionSnapshot: ...

    def prepare_and_commit_assistant(
        self,
        planned: ConversationAppend,
        message: AssistantMessage,
        *,
        run_context: ToolRunContext,
        tool_executor: ToolExecutor,
    ) -> tuple[DurableToolExecution, ...]: ...

    def execution_is_visible(self, tool_execution_id: str) -> bool: ...

    def get_execution(self, tool_execution_id: str) -> DurableToolExecution | None: ...

    def create_pending_approval(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableApproval: ...

    def consume_and_mark_executing(
        self,
        execution: DurableToolExecution,
        approval: DurableApproval,
        *,
        approved: bool,
        now: datetime | None = None,
        command_id: str | None = None,
    ) -> tuple[DurableToolExecution, DurableApproval, bool]: ...

    def mark_executing(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution: ...

    def deny_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution: ...

    def cancel_execution_before_handler(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution: ...

    def assert_handler_may_enter(
        self, execution: DurableToolExecution, *, now: datetime | None = None
    ) -> DurableToolExecution: ...

    def record_handler_completed(
        self,
        execution: DurableToolExecution,
        result: ToolExecutionOutcome,
        *,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution: ...

    def commit_tool_message(
        self,
        planned: ConversationAppend,
        execution: DurableToolExecution,
        *,
        now: datetime | None = None,
        disposition: ToolExecutionDisposition | None = None,
    ) -> DurableToolExecution: ...

    def check_fault(self, point: FaultPoint) -> None: ...

    def has_active_unconfined_grant(
        self, execution: DurableToolExecution, *, now: datetime
    ) -> bool: ...


@dataclass
class Session:
    session_id: str
    profile: Profile | None = None
    preferences: Preferences = field(default_factory=Preferences)
    global_preferences: Preferences = field(default_factory=Preferences)
    workspace_preferences: Preferences = field(default_factory=Preferences)
    log: ConversationLog = field(default_factory=ConversationLog)
    # Process-local unsaved history, or an in-flight durable turn.
    dirty: bool = False
    read_only: bool = False
    workspace_preferences_read_only: bool = False
    permission_profile: PermissionProfile = field(default_factory=PermissionProfile)
    workspace_capability: WorkspaceCapability | None = None
    latest_run_id: str | None = None
    latest_tool_facts: tuple[ToolFact, ...] = ()
    metrics_enabled: bool = True
    latest_metrics: RunMetricsSnapshot | None = None
    committer: SessionCommitter | None = None
    durable_runtime: DurableRunCoordinator | None = None
    pending_full_access_grant: bool = False
    health: SessionHealth = SessionHealth.OK
    lifecycle: SessionLifecycle = SessionLifecycle.ACTIVE
    profile_revision: int = 0
    preferences_revision: int = 0
    context_checkpoint: ContextCheckpoint | None = None

    @property
    def persisted(self) -> bool:
        return self.committer is not None

    @property
    def messages(self) -> tuple[Message, ...]:
        """Read-only projection of the log; never mutate history through it."""
        return self.log.messages_view()

    def commit_append(self, planned: ConversationAppend) -> None:
        if self.committer is None:
            self.log.apply_committed(planned)
            self.dirty = True
            return
        self.committer.commit(planned)
        self.dirty = self.log.has_active_turn

    def begin_user_turn(self, user: UserMessage) -> None:
        self.commit_append(self.log.plan_begin_turn(user))

    def append_assistant(self, message: AssistantMessage) -> None:
        self.commit_append(self.log.plan_append_assistant(message))

    def append_tool_result(self, tool_call_id: str, content: str) -> None:
        self.commit_append(self.log.plan_append_tool_result(tool_call_id, content))

    def finish_turn(
        self, reason: FinishReason, *, interrupted_call_ids: tuple[str, ...] = ()
    ) -> None:
        self.commit_append(
            self.log.plan_finish_turn(reason, interrupted_call_ids=interrupted_call_ids)
        )
        if self.persisted:
            self.dirty = False

    def reset(self, session_id: str) -> None:
        self.session_id = session_id
        self.log.reset()
        self.preferences = Preferences()
        self.dirty = False
        self.health = SessionHealth.OK
        self.latest_run_id = None
        self.latest_tool_facts = ()
        self.latest_metrics = None
        self.context_checkpoint = None
        self.pending_full_access_grant = False

    def retain_run_facts(
        self, run_context: ToolRunContext, *, finish_reason: str = "unknown"
    ) -> None:
        """Retain only the latest settled run's local facts; never persist them."""
        self.latest_run_id = run_context.run_id
        self.latest_tool_facts = run_context.facts
        self.latest_metrics = run_context.metrics(finish_reason) if self.metrics_enabled else None
