"""Bounded definition diagnostics shared by preparation and atomic admission."""

from enum import StrEnum

from morrow.core.application import ApplicationError, ApplicationErrorCode


class DefinitionFailure(StrEnum):
    MISSING = "published Agent version is missing"
    DISABLED = "Agent definition is disabled for new admissions"
    REVOKED = "policy_revoked: publish a changed Agent definition to supersede"
    SCOPE = "isolated scope requires a distinct standalone Session/Task pair"
    NONEMPTY = "isolated admission requires an empty ConversationLog"
    TOOLS = "required tool backend is unavailable or denied by the capability ceiling"
    SKILLS = "exact Skill version is disabled, unavailable or incompatible with the effective tools"
    EVIDENCE = "Agent definition frozen evidence mismatch"


class AgentDefinitionAdmissionError(ApplicationError):
    def __init__(self, reason: DefinitionFailure):
        super().__init__(ApplicationErrorCode.INVALID, reason.value)
