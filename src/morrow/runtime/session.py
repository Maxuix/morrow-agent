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
    ValidationFact,
    WorkspaceCapability,
)
from morrow.core.compaction import CompactionEntry, CompactionSummary
from morrow.core.context import ContextCheckpoint, RunContextProjection
from morrow.core.domain import SessionHealth, SessionLifecycle
from morrow.core.execution import (
    DurableApproval,
    DurableToolExecution,
    ToolExecutionDisposition,
)
from morrow.core.faults import FaultPoint
from morrow.core.models import (
    AgentStopCode,
    AssistantMessage,
    FinishReason,
    Message,
    ModelUsage,
    Preferences,
    Profile,
    StatePresence,
    ToolDefinition,
    UserMessage,
)
from morrow.core.observability import (
    AgentRunObservation,
    AgentRunRetryProgress,
    AgentRunTerminalMetrics,
    ModelRequestObservation,
)
from morrow.core.permissions import PermissionSnapshot
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import PreferenceEntry
from morrow.core.prompt import PromptProjection
from morrow.core.skills.context import SkillContextProjection
from morrow.runtime.conversation import ConversationAppend, ConversationLog

if TYPE_CHECKING:
    from morrow.core.agent_runs import PreparedAgentRunSpec
    from morrow.core.domain import AgentRunSnapshot
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
    current_task_run_id: str | None
    current_agent_run_id: str | None

    def now(self) -> datetime: ...

    def probe(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
    ) -> TurnSubmissionResult: ...

    def submit_user(
        self,
        session: Session,
        user_input: str,
        client_message_id: str,
        *,
        turn_id: str,
        agent_run_id: str,
        tools: tuple[ToolDefinition, ...],
        prepared_spec: PreparedAgentRunSpec | None = None,
        prompt_projection: PromptProjection | None = None,
    ) -> TurnSubmissionResult: ...

    def get_open_run_snapshot(self) -> AgentRunSnapshot | None: ...

    def admit_model_request(self, **kwargs) -> ModelRequestObservation: ...

    def settle_model_request(self, model_request_id: str, **kwargs) -> ModelRequestObservation: ...

    def finalize_agent_run(self, **kwargs) -> AgentRunTerminalMetrics: ...

    def get_agent_run_observation(
        self, agent_run_id: str | None = None
    ) -> AgentRunObservation | None: ...

    def record_retry_progress(self, **kwargs) -> AgentRunRetryProgress: ...

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

    def synchronize_task_projection(self, task_run_id: str | None) -> None: ...

    def persist_compaction_entry(self, entry: CompactionEntry) -> None: ...


@dataclass
class Session:
    session_id: str
    profile: Profile | None = None
    preferences: Preferences = field(default_factory=Preferences)
    global_preferences: Preferences = field(default_factory=Preferences)
    workspace_preferences: Preferences = field(default_factory=Preferences)
    generic_global_preferences: PreferenceDocument | None = None
    generic_workspace_preferences: PreferenceDocument | None = None
    generic_session_preferences: tuple[PreferenceEntry, ...] = ()
    log: ConversationLog = field(default_factory=ConversationLog)
    # Process-local unsaved history, or an in-flight durable turn.
    dirty: bool = False
    read_only: bool = False
    workspace_preferences_read_only: bool = False
    permission_profile: PermissionProfile = field(default_factory=PermissionProfile)
    workspace_capability: WorkspaceCapability | None = None
    latest_run_id: str | None = None
    latest_tool_facts: tuple[ToolFact, ...] = ()
    latest_validation_facts: tuple[ValidationFact, ...] = ()
    metrics_enabled: bool = True
    latest_metrics: RunMetricsSnapshot | None = None
    committer: SessionCommitter | None = None
    durable_runtime: DurableRunCoordinator | None = None
    pending_full_access_grant: bool = False
    health: SessionHealth = SessionHealth.OK
    lifecycle: SessionLifecycle = SessionLifecycle.ACTIVE
    profile_revision: int = 0
    preferences_revision: int = 0
    global_preferences_revision: int = 0
    profile_presence: StatePresence = StatePresence.MISSING
    workspace_preferences_presence: StatePresence = StatePresence.MISSING
    context_checkpoint: ContextCheckpoint | None = None
    run_context_projection: RunContextProjection | None = None
    skill_context_projection: SkillContextProjection | None = None
    # Fresh prompt bodies remain in memory until the matching AgentRun is admitted.
    pending_prompt_projection: PromptProjection | None = None
    # Compaction is a model-context projection; ConversationLog remains authoritative and is not
    # rewritten when these fields advance.
    compaction_entries: tuple[CompactionEntry, ...] = ()
    compaction_boundary_sequence: int = 0
    compaction_summary: CompactionSummary | None = None
    compaction_in_progress: bool = False
    latest_model_usage: ModelUsage = field(default_factory=ModelUsage.unavailable)
    latest_model_usage_context_digest: str | None = None

    def __post_init__(self) -> None:
        # Hand-built Sessions in tests and local integrations may only provide values.  Infer
        # presence for those projections while bootstrap supplies the authoritative tombstone.
        if self.profile is not None and self.profile_presence is StatePresence.MISSING:
            self.profile_presence = StatePresence.PRESENT
        if (
            self.workspace_preferences != Preferences()
            and self.workspace_preferences_presence is StatePresence.MISSING
        ):
            self.workspace_preferences_presence = StatePresence.PRESENT

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
        self,
        reason: FinishReason,
        *,
        interrupted_call_ids: tuple[str, ...] = (),
        stop_code: AgentStopCode | None = None,
    ) -> None:
        self.commit_append(
            self.log.plan_finish_turn(
                reason,
                interrupted_call_ids=interrupted_call_ids,
                stop_code=stop_code,
            )
        )
        if self.persisted:
            self.dirty = False

    def append_compaction_entry(self, entry: CompactionEntry) -> None:
        """Install one immutable context projection boundary after durable validation."""

        if entry.session_id != self.session_id:
            raise ValueError("compaction entry belongs to another Session")
        self._validate_compaction_order(entry)
        persist = (
            getattr(self.durable_runtime, "persist_compaction_entry", None)
            if self.durable_runtime is not None
            else None
        )
        if callable(persist):
            persist(entry)
        self._install_compaction_entry(entry)

    def restore_compaction_entries(self, entries: tuple[CompactionEntry, ...]) -> None:
        """Install persisted projection entries without publishing them again."""

        self.compaction_entries = ()
        self.compaction_boundary_sequence = 0
        self.compaction_summary = None
        for entry in entries:
            if entry.session_id != self.session_id:
                raise ValueError("compaction entry belongs to another Session")
            self._validate_compaction_order(entry)
            self._install_compaction_entry(entry)

    def _validate_compaction_order(self, entry: CompactionEntry) -> None:
        if (
            self.compaction_entries
            and entry.source_end_sequence <= self.compaction_entries[-1].source_end_sequence
        ):
            raise ValueError("compaction entries must advance monotonically")
        if entry.first_retained_sequence <= self.compaction_boundary_sequence:
            raise ValueError("compaction boundary must advance monotonically")

    def _install_compaction_entry(self, entry: CompactionEntry) -> None:
        if (
            self.compaction_entries
            and entry.source_end_sequence <= self.compaction_entries[-1].source_end_sequence
        ):
            raise ValueError("compaction entries must advance monotonically")
        self.compaction_entries = (*self.compaction_entries, entry)
        self.compaction_boundary_sequence = entry.first_retained_sequence
        self.compaction_summary = entry.summary
        self.dirty = self.dirty or self.has_active_turn

    def reset(self, session_id: str) -> None:
        self.session_id = session_id
        self.log.reset()
        self.preferences = Preferences()
        self.generic_session_preferences = ()
        self.dirty = False
        self.health = SessionHealth.OK
        self.latest_run_id = None
        self.latest_tool_facts = ()
        self.latest_validation_facts = ()
        self.latest_metrics = None
        self.context_checkpoint = None
        self.run_context_projection = None
        self.skill_context_projection = None
        self.pending_prompt_projection = None
        self.compaction_entries = ()
        self.compaction_boundary_sequence = 0
        self.compaction_summary = None
        self.compaction_in_progress = False
        self.latest_model_usage = ModelUsage.unavailable()
        self.latest_model_usage_context_digest = None
        self.pending_full_access_grant = False

    def retain_run_facts(
        self, run_context: ToolRunContext, *, finish_reason: str = "unknown"
    ) -> None:
        """Retain only the latest settled run's local facts; never persist them."""
        self.latest_run_id = run_context.run_id
        self.latest_tool_facts = run_context.facts
        self.latest_validation_facts = run_context.validation_facts
        self.latest_metrics = run_context.metrics(finish_reason) if self.metrics_enabled else None
