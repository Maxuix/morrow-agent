"""In-process session state; the ConversationLog is the only history authority."""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.models import Handoff, Message, Preferences, Profile
from morrow.runtime.conversation import ConversationLog


@dataclass
class Session:
    session_id: str
    profile: Profile | None = None
    preferences: Preferences = field(default_factory=Preferences)
    global_preferences: Preferences = field(default_factory=Preferences)
    workspace_preferences: Preferences = field(default_factory=Preferences)
    loaded_handoff: Handoff | None = None
    handoff_source_revision: int | None = None
    log: ConversationLog = field(default_factory=ConversationLog)
    dirty: bool = False
    read_only: bool = False
    workspace_preferences_read_only: bool = False

    @property
    def messages(self) -> tuple[Message, ...]:
        """Read-only projection of the log; never mutate history through it."""
        return self.log.messages_view()

    @property
    def is_continuation(self) -> bool:
        return self.handoff_source_revision is not None

    def reset(self, session_id: str) -> None:
        self.session_id = session_id
        self.loaded_handoff = None
        self.handoff_source_revision = None
        self.log.reset()
        self.preferences = Preferences()
        self.dirty = False
