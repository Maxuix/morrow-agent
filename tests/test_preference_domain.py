from __future__ import annotations

from datetime import UTC, datetime

import pytest

from morrow.core.preference_documents import PreferenceDocument
from morrow.core.preference_models import (
    PreferenceEntry,
    PreferenceLifecycleKind,
    PreferenceLifecycleOperation,
    PreferenceOperation,
    PreferenceScope,
    PreferenceStatus,
)
from morrow.core.preference_operations import (
    PreferenceOperationError,
    reduce_preference_document,
    reduce_preference_lifecycle,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _document(*entries: PreferenceEntry, revision: int = 4) -> PreferenceDocument:
    return PreferenceDocument(
        scope="workspace",
        revision=revision,
        updated_at=NOW,
        entries=entries,
    )


def _entry(
    preference_id: str = "pref_one",
    statement: str = "先给代码，再解释。",
    *,
    status: PreferenceStatus = PreferenceStatus.ACTIVE,
) -> PreferenceEntry:
    return PreferenceEntry(
        preference_id=preference_id,
        statement=statement,
        scope=PreferenceScope.WORKSPACE,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def test_statement_normalization_rejects_empty_and_hidden_controls():
    with pytest.raises(ValueError):
        PreferenceOperation(operation="add", scope="workspace", statement="\u200b规则")
    with pytest.raises(ValueError):
        PreferenceOperation(operation="add", scope="workspace", statement="  ")

    operation = PreferenceOperation(
        operation="add", scope="workspace", statement="  先给\t代码，再解释。  "
    )
    assert operation.statement == "先给 代码，再解释。"


def test_same_scope_batch_is_atomic_and_increments_one_document_revision():
    document = _document(_entry())
    operations = (
        PreferenceOperation(
            operation="replace",
            scope="workspace",
            preference_id="pref_one",
            statement="先给可运行代码。",
        ),
        PreferenceOperation(operation="add", scope="workspace", statement="解释关键设计。"),
    )
    result = reduce_preference_document(
        document,
        operations,
        now=NOW,
        allocate_id=lambda _scope, _index, _operation: "pref_two",
    )
    assert result.revision == document.revision + 1
    assert [entry.preference_id for entry in result.entries] == ["pref_one", "pref_two"]
    assert result.entries[0].revision == 2


def test_invalid_later_operation_does_not_mutate_the_input_or_return_partial_state():
    document = _document(_entry())
    operations = (
        PreferenceOperation(
            operation="replace", scope="workspace", preference_id="pref_one", statement="已验证。"
        ),
        PreferenceOperation(operation="remove", scope="workspace", preference_id="pref_missing"),
    )
    with pytest.raises(PreferenceOperationError, match="target does not exist"):
        reduce_preference_document(document, operations, now=NOW)
    assert document.entries[0].statement == "先给代码，再解释。"
    assert document.revision == 4


def test_duplicate_active_and_disabled_rules_are_rejected_but_deleted_tombstones_do_not_block_add():
    active = _entry()
    with pytest.raises(PreferenceOperationError, match="active Preference"):
        reduce_preference_document(
            _document(active),
            (
                PreferenceOperation(
                    operation="add", scope="workspace", statement="先给代码，再解释。"
                ),
            ),
            now=NOW,
        )

    disabled = _entry(status=PreferenceStatus.DISABLED)
    with pytest.raises(PreferenceOperationError, match="disabled Preference"):
        reduce_preference_document(
            _document(disabled),
            (
                PreferenceOperation(
                    operation="add", scope="workspace", statement="先给代码，再解释。"
                ),
            ),
            now=NOW,
        )

    deleted = _entry(status=PreferenceStatus.DELETED)
    result = reduce_preference_document(
        _document(deleted),
        (PreferenceOperation(operation="add", scope="workspace", statement="先给代码，再解释。"),),
        now=NOW,
        allocate_id=lambda _scope, _index, _operation: "pref_two",
    )
    assert result.entries[-1].preference_id == "pref_two"
    assert result.entries[-1].revision == 1


def test_cross_scope_and_duplicate_targets_are_rejected():
    document = _document(_entry())
    with pytest.raises(PreferenceOperationError, match="one durable document scope"):
        reduce_preference_document(
            document,
            (PreferenceOperation(operation="add", scope="global", statement="跨 scope。"),),
        )
    operation = PreferenceOperation(operation="remove", scope="workspace", preference_id="pref_one")
    with pytest.raises(PreferenceOperationError, match="only once"):
        reduce_preference_document(document, (operation, operation))


def test_lifecycle_enable_disable_increments_entry_and_document_revisions():
    document = _document(_entry())
    disabled = reduce_preference_lifecycle(
        document,
        PreferenceLifecycleOperation(
            operation=PreferenceLifecycleKind.DISABLE,
            scope="workspace",
            preference_id="pref_one",
        ),
        now=NOW,
    )
    assert disabled.revision == 5
    assert disabled.entries[0].status is PreferenceStatus.DISABLED
    assert disabled.entries[0].revision == 2

    enabled = reduce_preference_lifecycle(
        disabled,
        PreferenceLifecycleOperation(
            operation=PreferenceLifecycleKind.ENABLE,
            scope="workspace",
            preference_id="pref_one",
        ),
        now=NOW,
    )
    assert enabled.entries[0].status is PreferenceStatus.ACTIVE
    assert enabled.entries[0].revision == 3


def test_deleted_entries_are_terminal_for_lifecycle_and_replace_remove():
    document = _document(_entry(status=PreferenceStatus.DELETED))
    lifecycle = PreferenceLifecycleOperation(
        operation="enable", scope="workspace", preference_id="pref_one"
    )
    with pytest.raises(PreferenceOperationError, match="terminal"):
        reduce_preference_lifecycle(document, lifecycle, now=NOW)
    with pytest.raises(PreferenceOperationError, match="terminal"):
        reduce_preference_document(
            document,
            (PreferenceOperation(operation="remove", scope="workspace", preference_id="pref_one"),),
            now=NOW,
        )
