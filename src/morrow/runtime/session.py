"""In-process session state; the ConversationLog is the only history authority."""

from __future__ import annotations

from dataclasses import dataclass, field

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

    @property
    def messages(self) -> tuple[Message, ...]:
        """Read-only projection of the log; never mutate history through it."""
        return self.log.messages_view()

    def reset(self, session_id: str) -> None:
        self.session_id = session_id
        self.log.reset()
        self.preferences = Preferences()
        self.dirty = False
