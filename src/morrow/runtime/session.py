"""In-process session state; the ConversationLog is the only history authority."""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.capabilities import (
    PermissionProfile,
    RunMetricsSnapshot,
    ToolFact,
    ToolRunContext,
    WorkspaceCapability,
)
from morrow.core.models import Message, Preferences, Profile
from morrow.runtime.conversation import ConversationLog


@dataclass
class Session:
    session_id: str
    profile: Profile | None = None
    preferences: Preferences = field(default_factory=Preferences)
    global_preferences: Preferences = field(default_factory=Preferences)
    workspace_preferences: Preferences = field(default_factory=Preferences)
    log: ConversationLog = field(default_factory=ConversationLog)
    # Process-local content that requires explicit discard confirmation.
    dirty: bool = False
    read_only: bool = False
    workspace_preferences_read_only: bool = False
    permission_profile: PermissionProfile = field(default_factory=PermissionProfile)
    workspace_capability: WorkspaceCapability | None = None
    latest_run_id: str | None = None
    latest_tool_facts: tuple[ToolFact, ...] = ()
    metrics_enabled: bool = True
    latest_metrics: RunMetricsSnapshot | None = None

    @property
    def messages(self) -> tuple[Message, ...]:
        """Read-only projection of the log; never mutate history through it."""
        return self.log.messages_view()

    def reset(self, session_id: str) -> None:
        self.session_id = session_id
        self.log.reset()
        self.preferences = Preferences()
        self.dirty = False
        self.latest_run_id = None
        self.latest_tool_facts = ()
        self.latest_metrics = None

    def retain_run_facts(
        self, run_context: ToolRunContext, *, finish_reason: str = "unknown"
    ) -> None:
        """Retain only the latest settled run's local facts; never persist them."""
        self.latest_run_id = run_context.run_id
        self.latest_tool_facts = run_context.facts
        self.latest_metrics = run_context.metrics(finish_reason) if self.metrics_enabled else None
