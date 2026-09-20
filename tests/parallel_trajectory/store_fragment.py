"""Lane test helpers for the current trajectory schema."""

from __future__ import annotations


def assert_current_trajectory_schema(journal) -> None:
    """Assert that a test opened the complete current schema."""

    row = journal._backend.read_one(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_timeline_entries'", ()
    )
    assert row is not None
