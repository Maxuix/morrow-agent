"""Read-only desktop Activity facts from the original durable execution owner."""

from morrow.application.computer_visuals import validate_visual_source
from morrow.core.computer_use import COMPUTER_TOOL_NAMES, ComputerUseContractError


def computer_activity_projection(
    journal, workspace_id, execution_id, source_session_id, view_session_id
):
    execution = journal.get_execution(workspace_id, execution_id)
    if (
        execution is None
        or execution.tool_name not in COMPUTER_TOOL_NAMES
        or execution.session_id != source_session_id
    ):
        return {}
    evidence = execution.facts.computer if execution.facts is not None else None
    projection = {"turn_id": execution.turn_id, "agent_run_id": execution.agent_run_id}
    if evidence is not None:
        projection["computer"] = evidence.model_dump(mode="json")
    envelope = execution.result_envelope
    if envelope is not None and envelope.visual_refs:
        reference = envelope.visual_refs[0]
        try:
            validate_visual_source(journal, workspace_id, reference)
        except ComputerUseContractError:
            return projection
        projection["preview_ref"] = (
            f"/v1/workspaces/{workspace_id}/sessions/{view_session_id}"
            f"/artifacts/{reference.artifact_id}/content"
        )
    return projection
