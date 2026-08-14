"""In-process session state; no persistent chat history is introduced."""

from __future__ import annotations

from dataclasses import dataclass, field

from morrow.core.models import Handoff, Message, Preferences, Profile


@dataclass
class Session:
    session_id: str
    profile: Profile | None = None
    preferences: Preferences = field(default_factory=Preferences)
    global_preferences: Preferences = field(default_factory=Preferences)
    workspace_preferences: Preferences = field(default_factory=Preferences)
    loaded_handoff: Handoff | None = None
    handoff_source_revision: int | None = None
    messages: list[Message] = field(default_factory=list)
    dirty: bool = False
    read_only: bool = False
    workspace_preferences_read_only: bool = False

    @property
    def is_continuation(self) -> bool:
        return self.handoff_source_revision is not None

    def accept_user(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))
        self.dirty = True

    def accept_assistant(self, content: str) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def reset(self, session_id: str) -> None:
        self.session_id = session_id
        self.loaded_handoff = None
        self.handoff_source_revision = None
        self.messages.clear()
        self.preferences = Preferences()
        self.dirty = False
