"""Compatibility conversion from the legacy fixed Preference Candidate shape."""

from __future__ import annotations

from morrow.adapters.state.preference_projection import preferences_from_entries
from morrow.core.learning import LearningCandidate, LearningCandidateOperation
from morrow.core.learning_payloads import PreferenceCandidatePayload
from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import (
    PreferenceOperation,
    PreferenceOperationKind,
    PreferenceStatus,
)

_DETAIL_SENTENCES = {
    "concise": "回答默认保持简洁。",
    "balanced": "回答默认在简洁与细节之间保持平衡。",
    "detailed": "回答默认提供详细说明。",
}


def legacy_statement(payload: PreferenceCandidatePayload, value: str | None = None) -> str:
    """Render one old fixed-path value into the generic statement vocabulary."""

    selected = payload.value if value is None else value
    if payload.path == "language":
        assert isinstance(selected, str)
        return f"回答时默认使用 {selected}。"
    if payload.path == "response_detail":
        assert isinstance(selected, str)
        return _DETAIL_SENTENCES[selected]
    if isinstance(selected, tuple):
        if not selected:
            raise ValueError("instruction candidate is empty")
        return selected[0]
    assert isinstance(selected, str)
    return selected


def _matching_entry(document: PreferenceDocument, statement: str):
    key = statement.casefold()
    return next(
        (
            entry
            for entry in document.entries
            if entry.status is not PreferenceStatus.DELETED and entry.statement.casefold() == key
        ),
        None,
    )


def _matching_path_entry(
    document: PreferenceDocument, payload: PreferenceCandidatePayload, statement: str
):
    """Find the old fixed-field target while preserving its generic ID on replace."""

    exact = _matching_entry(document, statement)
    if exact is not None:
        return exact
    if payload.path == "language":
        return next(
            (
                entry
                for entry in document.entries
                if entry.status is not PreferenceStatus.DELETED
                and entry.statement.startswith("回答时默认使用 ")
            ),
            None,
        )
    if payload.path == "response_detail":
        return next(
            (
                entry
                for entry in document.entries
                if entry.status is not PreferenceStatus.DELETED
                and entry.statement in _DETAIL_SENTENCES.values()
            ),
            None,
        )
    return None


def candidate_operations(
    candidate: LearningCandidate,
    payload: PreferenceCandidatePayload,
    document: PreferenceDocument,
) -> tuple[PreferenceOperation, ...]:
    """Convert one accepted legacy Preference Candidate into generic operations.

    Legacy ``lev_*`` evidence IDs deliberately remain attached to the Candidate
    history.  The v2 Preference evidence contract uses a different namespace and
    is not fabricated by this compatibility bridge.
    """

    values = payload.value if payload.path == "instructions" else (payload.value,)
    operation = candidate.operation
    if operation is LearningCandidateOperation.APPEND:
        return tuple(
            PreferenceOperation(
                operation=PreferenceOperationKind.ADD,
                scope=document.scope,
                statement=legacy_statement(payload, value),
            )
            for value in values
            if _matching_path_entry(document, payload, legacy_statement(payload, value)) is None
        )
    if operation in {LearningCandidateOperation.SET, LearningCandidateOperation.REPLACE}:
        if payload.path == "instructions":
            raise ValueError("instruction replacement requires independent append/remove items")
        statement = legacy_statement(payload, values[0])
        target = _matching_path_entry(document, payload, statement)
        if target is None:
            return (
                PreferenceOperation(
                    operation=PreferenceOperationKind.ADD,
                    scope=document.scope,
                    statement=statement,
                ),
            )
        return (
            PreferenceOperation(
                operation=PreferenceOperationKind.REPLACE,
                scope=document.scope,
                preference_id=target.preference_id,
                statement=statement,
            ),
        )
    if operation is LearningCandidateOperation.REMOVE:
        return tuple(
            PreferenceOperation(
                operation=PreferenceOperationKind.REMOVE,
                scope=document.scope,
                preference_id=target.preference_id,
            )
            for value in values
            for target in (
                _matching_path_entry(document, payload, legacy_statement(payload, value)),
            )
            if target is not None
        )
    raise ValueError("unsupported legacy Preference Candidate operation")


__all__ = [
    "candidate_operations",
    "legacy_statement",
    "preferences_from_entries",
]
