"""SQLite repository for immutable v12 selections and derived search terms."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from morrow.adapters.state.transaction import SqliteJournalBackend
from morrow.core.domain import canonical_json_bytes
from morrow.core.memory_selection import MemorySearchTerm, MemorySelection, MemorySelectionItem
from morrow.core.store import StorageError, StorageErrorCode

_SELECTION_COLUMNS = (
    "selection_id, workspace_id, query_digest, source_memory_revision, item_count, "
    "omitted_count, rendered_chars, selection_digest, created_at_unix"
)
_ITEM_COLUMNS = (
    "selection_id, workspace_id, ordinal, record_kind, record_id, record_revision_id, "
    "revision, reason_json, reason_bytes, estimated_chars, rendered_content_digest"
)
_TERM_COLUMNS = "workspace_id, knowledge_revision_id, token_kind, token, weight_band"


def _unix(value: datetime) -> int:
    return int(value.timestamp())


def _from_unix(value: object) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


def _workspace_error(label: str) -> StorageError:
    return StorageError(StorageErrorCode.UNAVAILABLE, f"memory {label} is outside the workspace")


def _check_limit(limit: int, label: str) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise StorageError(StorageErrorCode.UNAVAILABLE, f"memory {label} page is invalid")


def _selection_from_row(
    row: tuple[object, ...], items: tuple[MemorySelectionItem, ...]
) -> MemorySelection:
    try:
        return MemorySelection(
            selection_id=str(row[0]),
            workspace_id=str(row[1]),
            query_digest=str(row[2]),
            source_memory_revision=int(row[3]),
            selected_items=items,
            item_count=int(row[4]),
            omitted_count=int(row[5]),
            rendered_chars=int(row[6]),
            selection_digest=str(row[7]),
            created_at=_from_unix(row[8]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "memory selection is invalid") from exc


def _item_from_row(row: tuple[object, ...]) -> MemorySelectionItem:
    try:
        reason_json = str(row[7])
        reason_bytes = int(row[8])
        if reason_bytes != len(reason_json.encode("utf-8")):
            raise ValueError("selection reason byte count is invalid")
        reasons = json.loads(reason_json)
        if not isinstance(reasons, list):
            raise ValueError("selection reasons must be a list")
        item = MemorySelectionItem(
            selection_id=str(row[0]),
            workspace_id=str(row[1]),
            ordinal=int(row[2]),
            record_kind=str(row[3]),
            record_id=str(row[4]),
            record_revision_id=str(row[5]),
            revision=int(row[6]),
            reason_codes=tuple(reasons),
            estimated_chars=int(row[9]),
            rendered_content_digest=str(row[10]),
        )
        canonical_reasons = canonical_json_bytes(
            [reason.value for reason in item.reason_codes]
        ).decode("utf-8")
        if reason_json != canonical_reasons:
            raise ValueError("selection reasons are not canonical")
        return item
    except (TypeError, ValueError, IndexError, OverflowError, json.JSONDecodeError) as exc:
        raise StorageError(
            StorageErrorCode.NEEDS_REPAIR, "memory selection item is invalid"
        ) from exc


def _term_from_row(row: tuple[object, ...]) -> MemorySearchTerm:
    try:
        return MemorySearchTerm(
            workspace_id=str(row[0]),
            knowledge_revision_id=str(row[1]),
            token_kind=str(row[2]),
            token=str(row[3]),
            weight_band=str(row[4]),
        )
    except (TypeError, ValueError, IndexError, OverflowError) as exc:
        raise StorageError(StorageErrorCode.NEEDS_REPAIR, "memory search term is invalid") from exc


class SqliteMemorySelectionJournal:
    """Bounded repository over the shared transaction backend."""

    def __init__(self, backend: SqliteJournalBackend) -> None:
        self.backend = backend

    def put_memory_selection(
        self, workspace_id: str, selection: MemorySelection
    ) -> MemorySelection:
        if selection.workspace_id != workspace_id:
            raise _workspace_error("selection")

        def work() -> MemorySelection:
            if self.get_memory_selection(workspace_id, selection.selection_id) is not None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "memory selection already exists")
            for item in selection.selected_items:
                if item.workspace_id != workspace_id or item.selection_id != selection.selection_id:
                    raise _workspace_error("selection item")
                self._validate_knowledge_reference(workspace_id, item)
            self.backend.executor().execute(
                f"INSERT INTO memory_selections({_SELECTION_COLUMNS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    selection.selection_id,
                    selection.workspace_id,
                    selection.query_digest,
                    selection.source_memory_revision,
                    selection.item_count,
                    selection.omitted_count,
                    selection.rendered_chars,
                    selection.selection_digest,
                    _unix(selection.created_at),
                ),
            )
            for item in selection.selected_items:
                reason_json = canonical_json_bytes(
                    [reason.value for reason in item.reason_codes]
                ).decode("utf-8")
                self.backend.executor().execute(
                    f"INSERT INTO memory_selection_items({_ITEM_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.selection_id,
                        item.workspace_id,
                        item.ordinal,
                        item.record_kind,
                        item.record_id,
                        item.record_revision_id,
                        item.revision,
                        reason_json,
                        len(reason_json.encode("utf-8")),
                        item.estimated_chars,
                        item.rendered_content_digest,
                    ),
                )
            loaded = self.get_memory_selection(workspace_id, selection.selection_id)
            if loaded is None:
                raise StorageError(StorageErrorCode.UNAVAILABLE, "memory selection unavailable")
            return loaded

        return self.backend.transact(work)

    def get_memory_selection(self, workspace_id: str, selection_id: str) -> MemorySelection | None:
        row = self.backend.read_one(
            f"SELECT {_SELECTION_COLUMNS} FROM memory_selections WHERE selection_id = ?",
            (selection_id,),
        )
        if row is None:
            return None
        if str(row[1]) != workspace_id:
            raise _workspace_error("selection")
        items = self._read_items(selection_id)
        try:
            return _selection_from_row(row, items)
        except ValueError as exc:
            raise StorageError(
                StorageErrorCode.NEEDS_REPAIR, "memory selection items are invalid"
            ) from exc

    def list_memory_selections(
        self, workspace_id: str, *, limit: int = 100
    ) -> tuple[MemorySelection, ...]:
        _check_limit(limit, "selection")
        rows = self.backend.read_all(
            f"SELECT {_SELECTION_COLUMNS} FROM memory_selections "
            "WHERE workspace_id = ? ORDER BY created_at_unix ASC, selection_id ASC LIMIT ?",
            (workspace_id, limit),
        )
        return tuple(
            selection
            for row in rows
            if (selection := self.get_memory_selection(workspace_id, str(row[0]))) is not None
        )

    def replace_memory_search_terms(
        self,
        workspace_id: str,
        knowledge_revision_id: str,
        terms: tuple[MemorySearchTerm, ...],
    ) -> tuple[MemorySearchTerm, ...]:
        if any(
            term.workspace_id != workspace_id or term.knowledge_revision_id != knowledge_revision_id
            for term in terms
        ):
            raise _workspace_error("search term")

        def work() -> tuple[MemorySearchTerm, ...]:
            self._validate_revision_workspace(workspace_id, knowledge_revision_id)
            self.backend.executor().execute(
                "DELETE FROM memory_search_terms WHERE workspace_id = ? AND knowledge_revision_id = ?",
                (workspace_id, knowledge_revision_id),
            )
            for term in terms:
                self.backend.executor().execute(
                    f"INSERT INTO memory_search_terms({_TERM_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
                    (
                        term.workspace_id,
                        term.knowledge_revision_id,
                        term.token_kind.value,
                        term.token,
                        term.weight_band.value,
                    ),
                )
            return self.list_memory_search_terms(
                workspace_id, knowledge_revision_id=knowledge_revision_id, limit=500
            )

        return self.backend.transact(work)

    def list_memory_search_terms(
        self,
        workspace_id: str,
        *,
        knowledge_revision_id: str | None = None,
        token: str | None = None,
        limit: int = 500,
    ) -> tuple[MemorySearchTerm, ...]:
        _check_limit(limit, "search term")
        sql = f"SELECT {_TERM_COLUMNS} FROM memory_search_terms WHERE workspace_id = ?"
        parameters: list[object] = [workspace_id]
        if knowledge_revision_id is not None:
            sql += " AND knowledge_revision_id = ?"
            parameters.append(knowledge_revision_id)
        if token is not None:
            sql += " AND token = ?"
            parameters.append(token.casefold())
        sql += " ORDER BY knowledge_revision_id ASC, token_kind ASC, token ASC LIMIT ?"
        parameters.append(limit)
        return tuple(_term_from_row(row) for row in self.backend.read_all(sql, tuple(parameters)))

    def _read_items(self, selection_id: str) -> tuple[MemorySelectionItem, ...]:
        rows = self.backend.read_all(
            f"SELECT {_ITEM_COLUMNS} FROM memory_selection_items "
            "WHERE selection_id = ? ORDER BY ordinal ASC",
            (selection_id,),
        )
        return tuple(_item_from_row(row) for row in rows)

    def _validate_knowledge_reference(self, workspace_id: str, item: MemorySelectionItem) -> None:
        row = self.backend.read_one(
            "SELECT workspace_id, knowledge_id FROM project_knowledge_revisions "
            "WHERE knowledge_revision_id = ?",
            (item.record_revision_id,),
        )
        if row is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "memory selection revision is missing")
        if str(row[0]) != workspace_id or str(row[1]) != item.record_id:
            raise _workspace_error("selection knowledge reference")
        head = self.backend.read_one(
            "SELECT workspace_id FROM project_knowledge_heads WHERE knowledge_id = ?",
            (item.record_id,),
        )
        if head is None or str(head[0]) != workspace_id:
            raise _workspace_error("selection knowledge head")

    def _validate_revision_workspace(self, workspace_id: str, revision_id: str) -> None:
        row = self.backend.read_one(
            "SELECT workspace_id FROM project_knowledge_revisions WHERE knowledge_revision_id = ?",
            (revision_id,),
        )
        if row is None:
            raise StorageError(StorageErrorCode.NOT_FOUND, "memory search revision is missing")
        if str(row[0]) != workspace_id:
            raise _workspace_error("search revision")


__all__ = ["SqliteMemorySelectionJournal"]
