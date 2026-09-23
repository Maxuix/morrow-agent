"""Select a TaskOutcome that fits its durable limits before the model is built.

The selector deduplicates references and, only when a limit would otherwise
reject the outcome, keeps the caller's priority order. Omission counts are
part of the outcome. Full records stay in the journal and artifact store.
Validation failures that are not capacity limits still propagate so a task
transition cannot commit without a consistent outcome.
"""

from __future__ import annotations

from morrow.application.workflows.evidence import workflow_task_outcome
from morrow.core.domain import (
    TASK_OUTCOME_ARTIFACT_MAX_REFS,
    TASK_OUTCOME_MAX_BYTES,
    TaskOutcome,
    canonical_json_bytes,
)

_WORKFLOW_PAYLOAD_MARGIN = 2_048
_LINE_FIELDS = (
    "validation_facts",
    "side_effects",
    "unresolved_items",
    "feedback",
)
_LINE_LIMIT = 64
_PATH_LIMIT = 128
_PROTECTED_EVIDENCE_ROLES = frozenset(
    {
        "user_goal",
        "workflow_result_snapshot",
        "workflow_ready_transition",
        "workflow_terminal_run",
        "workflow_input",
        "workflow_leaf_turn",
        "workflow_node_agent_run",
    }
)


def _clean_line(value: str) -> str:
    return " ".join(value.split())[:512]


def _valid_path(value: str) -> bool:
    parts = value.split("/")
    return bool(
        value
        and len(value) <= 512
        and not value.startswith(("/", "\\"))
        and "\\" not in value
        and "\x00" not in value
        and all(part and part not in {".", ".."} for part in parts)
    )


def _dedupe(items, key):
    selected = []
    seen = set()
    for item in items:
        identity = key(item)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(item)
    return selected


def _artifact_key(reference) -> tuple[str, str]:
    return (reference.artifact_id, reference.role)


def _evidence_key(reference) -> tuple[str, str, str]:
    return (reference.kind.value, reference.reference_id, reference.role)


def _payload_limit(*, workflow: bool) -> int:
    if workflow:
        return TASK_OUTCOME_MAX_BYTES - _WORKFLOW_PAYLOAD_MARGIN
    return TASK_OUTCOME_MAX_BYTES


def _payload_size(fields: dict, *, workflow: bool) -> int:
    from morrow.core.domain import TextSafetyProfile

    probe_fields = dict(fields)
    probe_fields["text_safety_profile"] = (
        TextSafetyProfile.WORKFLOW_VALUE_SENSITIVE
        if workflow
        else probe_fields.get("text_safety_profile", TextSafetyProfile.LEGACY_STRICT)
    )
    probe = TaskOutcome.model_construct(**probe_fields)
    return len(canonical_json_bytes(probe.model_dump(mode="json")))


def _basis(fields: dict, omissions: dict[str, int], *, summary_truncated: bool) -> tuple[str, ...]:
    raw = [
        _clean_line(line) for line in fields.get("completion_basis", ()) if isinstance(line, str)
    ]
    raw = [
        line
        for line in raw
        if line and not line.startswith("omitted_") and line != "summary_truncated=true"
    ]
    head = [line for line in raw if line.startswith(("trigger=", "task_status="))]
    tail = [line for line in raw if line not in head]
    notes = [f"omitted_{name}={count}" for name, count in omissions.items() if count > 0]
    if summary_truncated:
        notes.append("summary_truncated=true")
    combined = head + notes + tail
    if len(combined) > _LINE_LIMIT:
        omitted_basis = len(combined) - _LINE_LIMIT
        # Keep the identity lines and the omission notes; drop older detail first.
        keep_tail = max(0, _LINE_LIMIT - len(head) - len(notes))
        combined = head + notes + tail[:keep_tail]
        if omitted_basis and "omitted_completion_basis=" not in " ".join(notes):
            note = f"omitted_completion_basis={omitted_basis}"
            if len(combined) < _LINE_LIMIT:
                combined.append(note)
            elif combined:
                combined[-1] = note
    return tuple(combined[:_LINE_LIMIT])


def _apply_omissions(fields: dict, omissions: dict[str, int], *, summary_truncated: bool) -> dict:
    updated = dict(fields)
    updated["completion_basis"] = _basis(fields, omissions, summary_truncated=summary_truncated)
    return updated


def build_bounded_task_outcome(fields: dict, *, workflow: bool = False) -> TaskOutcome:
    """Return one valid outcome, or raise when the minimum facts still do not fit."""

    summary = fields.get("summary")
    summary_truncated = False
    if isinstance(summary, str):
        cleaned = " ".join(summary.split())
        if len(cleaned) > 2_000:
            cleaned = cleaned[:2_000].rstrip() or cleaned[:2_000]
            summary_truncated = True
        fields = {**fields, "summary": cleaned}

    artifacts = _dedupe(tuple(fields.get("artifact_refs", ())), _artifact_key)
    evidence = _dedupe(tuple(fields.get("evidence_refs", ())), _evidence_key)
    paths_in = tuple(fields.get("changed_paths", ()))
    valid_paths = []
    omitted_paths = 0
    for path in paths_in:
        if isinstance(path, str) and _valid_path(path):
            if path not in valid_paths:
                valid_paths.append(path)
            continue
        omitted_paths += 1

    lines: dict[str, list[str]] = {}
    omitted_lines: dict[str, int] = {}
    for name in _LINE_FIELDS:
        cleaned_lines = []
        for value in tuple(fields.get(name, ())):
            if not isinstance(value, str):
                raise ValueError(f"{name} items must be strings")
            line = _clean_line(value)
            if line and line not in cleaned_lines:
                cleaned_lines.append(line)
        if len(cleaned_lines) > _LINE_LIMIT:
            omitted_lines[name] = len(cleaned_lines) - _LINE_LIMIT
            cleaned_lines = cleaned_lines[:_LINE_LIMIT]
        else:
            omitted_lines[name] = 0
        lines[name] = cleaned_lines

    protected = [item for item in evidence if item.role in _PROTECTED_EVIDENCE_ROLES]
    ordinary = [item for item in evidence if item.role not in _PROTECTED_EVIDENCE_ROLES]
    # Callers pass chronological evidence. Recent ordinary refs outrank older ones.
    ordinary_recent_first = list(reversed(ordinary))

    within_counts = (
        len(artifacts) <= TASK_OUTCOME_ARTIFACT_MAX_REFS
        and len(valid_paths) <= _PATH_LIMIT
        and omitted_paths == 0
        and not summary_truncated
        and all(count == 0 for count in omitted_lines.values())
    )
    light = {
        **fields,
        "artifact_refs": tuple(sorted(artifacts, key=lambda item: (item.artifact_id, item.role))),
        "evidence_refs": tuple(evidence),
        "changed_paths": tuple(sorted(valid_paths)),
        **{name: tuple(values) for name, values in lines.items()},
    }
    light_omissions = {
        "artifact_refs": 0,
        "evidence_refs": 0,
        "changed_paths": omitted_paths,
        **omitted_lines,
    }
    light = _apply_omissions(light, light_omissions, summary_truncated=summary_truncated)
    if within_counts and _payload_size(light, workflow=workflow) <= _payload_limit(
        workflow=workflow
    ):
        return _construct(light, workflow=workflow)

    return _shrink_to_budget(
        fields,
        artifacts=artifacts,
        protected=protected,
        ordinary_recent_first=ordinary_recent_first,
        valid_paths=valid_paths,
        omitted_paths=omitted_paths,
        lines=lines,
        omitted_lines=omitted_lines,
        summary_truncated=summary_truncated,
        workflow=workflow,
    )


def _shrink_to_budget(
    fields: dict,
    *,
    artifacts: list,
    protected: list,
    ordinary_recent_first: list,
    valid_paths: list[str],
    omitted_paths: int,
    lines: dict[str, list[str]],
    omitted_lines: dict[str, int],
    summary_truncated: bool,
    workflow: bool,
) -> TaskOutcome:
    selected_artifacts = artifacts[:TASK_OUTCOME_ARTIFACT_MAX_REFS]
    artifact_omitted = len(artifacts) - len(selected_artifacts)
    selected_paths = valid_paths[:_PATH_LIMIT]
    path_omitted = omitted_paths + (len(valid_paths) - len(selected_paths))
    selected_lines = {name: values[:_LINE_LIMIT] for name, values in lines.items()}
    line_omitted = dict(omitted_lines)
    ordinary = list(ordinary_recent_first)
    kept_protected = list(protected)

    def snapshot() -> dict:
        evidence_refs = _dedupe([*kept_protected, *ordinary], _evidence_key)
        evidence_omitted = (len(protected) - len(kept_protected)) + (
            len(ordinary_recent_first) - len(ordinary)
        )
        current = {
            **fields,
            "artifact_refs": tuple(selected_artifacts),
            "evidence_refs": tuple(evidence_refs),
            "changed_paths": tuple(selected_paths),
            **{name: tuple(values) for name, values in selected_lines.items()},
        }
        omissions = {
            "artifact_refs": artifact_omitted,
            "evidence_refs": evidence_omitted,
            "changed_paths": path_omitted,
            **line_omitted,
        }
        return _apply_omissions(current, omissions, summary_truncated=summary_truncated)

    def drop_one() -> bool:
        nonlocal artifact_omitted, path_omitted
        if ordinary:
            ordinary.pop()
            return True
        for name in ("feedback", "unresolved_items", "side_effects", "validation_facts"):
            if selected_lines[name]:
                selected_lines[name].pop()
                line_omitted[name] = line_omitted.get(name, 0) + 1
                return True
        if selected_paths:
            selected_paths.pop()
            path_omitted += 1
            return True
        if selected_artifacts:
            selected_artifacts.pop()
            artifact_omitted += 1
            return True
        if kept_protected:
            kept_protected.pop()
            return True
        return False

    current = snapshot()
    limit = _payload_limit(workflow=workflow)
    while _payload_size(current, workflow=workflow) > limit:
        if not drop_one():
            raise ValueError("TaskOutcome exceeds the durable payload budget")
        current = snapshot()
    return _construct(current, workflow=workflow)


def _construct(fields: dict, *, workflow: bool) -> TaskOutcome:
    if workflow:
        return workflow_task_outcome(**fields)
    return TaskOutcome(**fields)


__all__ = ["build_bounded_task_outcome"]
