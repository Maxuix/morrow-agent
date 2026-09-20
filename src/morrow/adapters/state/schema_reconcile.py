"""Small, additive reconciliation for the operational SQLite catalog.

The current schema statements remain the source of truth. Reconciliation is
one-way: it creates missing objects and appends safe columns, but never guesses
renames, drops, or constraint changes.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from morrow.adapters.state.schema import CURRENT_SCHEMA_STATEMENTS

_CREATE_TABLE = re.compile(
    r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[^\s(]+)\s*\(",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SchemaReconcileReport:
    """Objects observed or added by one reconciliation pass."""

    added_tables: tuple[str, ...] = ()
    added_columns: tuple[tuple[str, str], ...] = ()
    added_indexes: tuple[str, ...] = ()
    pending_changes: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added_tables or self.added_columns or self.added_indexes)


def reconcile_schema(connection: sqlite3.Connection, *, apply: bool) -> SchemaReconcileReport:
    """Diff the live catalog against ``CURRENT_SCHEMA_STATEMENTS``.

    ``apply=False`` has no side effects and is suitable for diagnostics.
    ``apply=True`` is called by the store while its maintenance lock and outer
    transaction are held.
    """

    desired = sqlite3.connect(":memory:")
    try:
        desired.execute("PRAGMA trusted_schema = ON")
        for statement in CURRENT_SCHEMA_STATEMENTS:
            desired.execute(statement)
        desired_tables = {
            str(row[0])
            for row in desired.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        desired_indexes = {
            str(row[0]): str(row[1])
            for row in desired.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }
        actual_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        actual_indexes = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }

        added_tables: list[str] = []
        added_columns: list[tuple[str, str]] = []
        added_indexes: list[str] = []
        pending: list[str] = []
        for statement in CURRENT_SCHEMA_STATEMENTS:
            match = _CREATE_TABLE.match(statement)
            if match is None:
                continue
            table = _unquote_identifier(match.group("name"))
            if table not in desired_tables:
                continue
            if table not in actual_tables:
                if apply:
                    connection.execute(statement)
                    added_tables.append(table)
                else:
                    pending.append(f"table:{table}")
                continue

            actual_columns = {
                str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            for column, definition in _table_columns(statement):
                if column in actual_columns:
                    continue
                if not _safe_add_column(definition):
                    pending.append(f"column:{table}.{column}")
                    continue
                if apply:
                    connection.execute(f'ALTER TABLE "{table}" ADD COLUMN {definition}')
                    added_columns.append((table, column))
                else:
                    pending.append(f"column:{table}.{column}")

        for name, sql in desired_indexes.items():
            if name in actual_indexes:
                continue
            if not apply:
                pending.append(f"index:{name}")
                continue
            try:
                connection.execute(sql)
            except sqlite3.Error:
                pending.append(f"index:{name}")
            else:
                added_indexes.append(name)

        return SchemaReconcileReport(
            added_tables=tuple(added_tables),
            added_columns=tuple(added_columns),
            added_indexes=tuple(added_indexes),
            pending_changes=tuple(pending),
        )
    finally:
        desired.close()


def _table_columns(statement: str) -> tuple[tuple[str, str], ...]:
    opening = statement.find("(")
    closing = statement.rfind(")")
    if opening < 0 or closing <= opening:
        return ()
    columns: list[tuple[str, str]] = []
    for part in _split_sql_list(statement[opening + 1 : closing]):
        stripped = part.strip()
        if not stripped or _is_table_constraint(stripped):
            continue
        first, _, remainder = stripped.partition(" ")
        if not remainder:
            continue
        columns.append((_unquote_identifier(first), stripped))
    return tuple(columns)


def _split_sql_list(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in "'\"`[":
            quote = "]" if char == "[" else char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    parts.append(value[start:])
    return tuple(parts)


def _is_table_constraint(definition: str) -> bool:
    return bool(
        re.match(
            r"^(?:CONSTRAINT\s+\S+\s+)?(?:PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY)\b",
            definition,
            re.IGNORECASE,
        )
    )


def _safe_add_column(definition: str) -> bool:
    upper = definition.upper()
    if "PRIMARY KEY" in upper or re.search(r"\bUNIQUE\b", upper):
        return False
    if "GENERATED ALWAYS" in upper and " VIRTUAL" not in upper:
        return False
    if " NOT NULL" in f" {upper}" and " DEFAULT " not in f" {upper}":
        return False
    if re.search(r"DEFAULT\s+(?:CURRENT_TIME|CURRENT_DATE|CURRENT_TIMESTAMP)\b", upper):
        return False
    return True


def _unquote_identifier(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"`':
        return value[1:-1].replace(value[0] * 2, value[0])
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1]
    return value


__all__ = ["SchemaReconcileReport", "reconcile_schema"]
