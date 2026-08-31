"""Safe projections into the existing Artifact and TaskOutcome authorities."""

from morrow.core.domain import TaskOutcome, TextSafetyProfile, redact_workflow_text, sha256_digest
from morrow.core.workflows.contracts import TextResult


def text_result_from_assistant(record_id: str, text: str) -> TextResult:
    redacted, changed = redact_workflow_text(text)
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
            redacted |= changed
            # An unsafe path is omitted rather than manufacturing a different file path.
            if name != "changed_paths" or not changed:
                projected.append(safe)
        fields[name] = projected[0] if name == "summary" else tuple(projected)
    if redacted:
        basis = fields.get("completion_basis", ())
        fields["completion_basis"] = (*basis[:63], "workflow_evidence_redacted=true")
    return TaskOutcome(**fields, text_safety_profile=TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE)
