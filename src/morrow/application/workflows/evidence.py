"""Safe projections into the existing Artifact and TaskOutcome authorities."""

from morrow.core.domain import (
    TaskOutcome,
    TaskRunStatus,
    TextSafetyProfile,
    redact_workflow_text,
    sha256_digest,
)
from morrow.core.workflows.contracts import TextResult


def text_result_from_assistant(record_id: str, text: str) -> TextResult:
    redacted, changed = redact_workflow_text(text)
    # The runtime may already have removed credentials before committing the Assistant.
    # A retained redaction marker cannot support a claim that the source is complete.
    changed |= "<redacted>" in text
    excerpt = redacted[:4096]
    return TextResult(
        final_assistant_record_id=record_id,
        final_assistant_sha256=sha256_digest(text),
        excerpt=excerpt,
        content_complete=not changed and excerpt == redacted,
    )


def workflow_task_outcome(**fields) -> TaskOutcome:
    """Internal typed projection; no public profile selector or parallel Outcome type."""
    if "text_safety_profile" in fields:
        raise ValueError("Workflow projection owns its text safety profile")
    redacted = False
    for name in (
        "summary",
        "changed_paths",
        "validation_facts",
        "side_effects",
        "unresolved_items",
        "completion_basis",
        "feedback",
    ):
        if name not in fields:
            continue
        values = (fields[name],) if name == "summary" else fields[name]
        projected = []
        for value in values:
            safe, changed = redact_workflow_text(value)
            redacted |= changed or "<redacted>" in value
            # An unsafe path is omitted rather than manufacturing a different file path.
            if name != "changed_paths" or not changed:
                projected.append(safe)
        fields[name] = projected[0] if name == "summary" else tuple(projected)
    if redacted:
        basis = fields.get("completion_basis", ())
        fields["completion_basis"] = (*basis[:63], "workflow_evidence_redacted=true")
    return TaskOutcome(**fields, text_safety_profile=TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE)


def select_workflow_snapshot_carry_forward(transitions, outcomes) -> TaskOutcome | None:
    """The one marked Workflow snapshot bound to the root's latest READY transition.

    An intervening ordinary snapshot carries no markers, and an older Workflow
    snapshot never matches a newer READY transition after resume + Direct work.
    """

    latest_ready = None
    for transition in transitions:
        if transition.to_status is TaskRunStatus.READY_FOR_ACCEPTANCE:
            latest_ready = transition
    if latest_ready is None:
        return None
    selected = None
    for outcome in outcomes:
        if outcome.trigger.value != "snapshot":
            continue
        marker = next(
            (ref for ref in outcome.evidence_refs if ref.role == "workflow_result_snapshot"),
            None,
        )
        ready = next(
            (ref for ref in outcome.evidence_refs if ref.role == "workflow_ready_transition"),
            None,
        )
        if marker is None or ready is None:
            continue
        if ready.reference_id != latest_ready.transition_id:
            continue
        if selected is None or outcome.version > selected.version:
            selected = outcome
    return selected
