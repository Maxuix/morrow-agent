"""Builder for the Subplan 37 v2 upgrade fixture reused by Subplan 45."""

from __future__ import annotations

from pathlib import Path

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.core.domain import DurableSession
from morrow.testing import FixedClock


def write_v2_store(data_root: Path) -> Path:
    """Create a v2 operational database with one empty active Session."""
    store = OperationalStore(data_root, clock=FixedClock(), maintenance_timeout=0)
    session = store.initialize()
    try:
        journal = SqliteOperationalJournal(session)
        journal.create_session(DurableSession(session_id="ses_v2fixture", workspace_id="ws_stage3"))
    finally:
        session.close()
    return store.layout.database


def copy_stage3_yaml(destination: Path, source: Path) -> None:
    import shutil

    shutil.copytree(
        source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("README.md")
    )
