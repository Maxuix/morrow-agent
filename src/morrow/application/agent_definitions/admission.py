"""Atomic new-run checks; ordinary disable is never a recovery check."""

from morrow.application.agent_definitions.errors import (
    AgentDefinitionAdmissionError,
    DefinitionFailure,
)
from morrow.core.domain import session_can_start_work, sha256_digest


def require_definition_admission(journal, workspace_id, spec, session_id):
    ref = spec.definition_ref
    repo = journal.agent_definitions
    version = repo.get_version(workspace_id, ref.version_id)
    if (
        version is None
        or version.content_hash != ref.content_hash
        or version.source.definition_id != ref.definition_id
        or spec.conversation_session_id != session_id
        or spec.role_prompt_digest != sha256_digest(version.source.role_prompt)
        or spec.max_agent_generation_requests != version.source.max_agent_generation_requests
    ):
        raise AgentDefinitionAdmissionError(DefinitionFailure.EVIDENCE)
    head = repo.get_head(workspace_id, ref.definition_id)
    if head is None or not head.enabled:
        raise AgentDefinitionAdmissionError(DefinitionFailure.DISABLED)
    if repo.get_revocation(workspace_id, ref.version_id) is not None:
        raise AgentDefinitionAdmissionError(DefinitionFailure.REVOKED)
    session = journal.get_session(workspace_id, session_id)
    if (
        session is None
        or session.parent_session_id is not None
        or session.conversation_position != 0
        or not session_can_start_work(session.lifecycle, session.health)
    ):
        raise AgentDefinitionAdmissionError(DefinitionFailure.NONEMPTY)
    return version
