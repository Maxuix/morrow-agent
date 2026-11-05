import sqlite3

import pytest

from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import StoreOpenMode


@pytest.mark.parametrize("fail_upgrade", [False, True])
def test_v49_upgrade_preserves_rows_and_rolls_back_before_commit(tmp_path, fail_upgrade):
    store = OperationalStore(tmp_path / "state")
    store.initialize().close()
    with sqlite3.connect(store.layout.database) as db:
        db.execute("DROP TABLE workspace_changes")
        db.execute("DROP TABLE workspace_restore_operations")
        db.execute("PRAGMA user_version=49")
    if fail_upgrade:

        def failure(point):
            if point == "upgrade_before_commit":
                raise RuntimeError("injected")

        store.failure_injector = failure
        with pytest.raises(RuntimeError):
            store.open(StoreOpenMode.READ_WRITE)
        with sqlite3.connect(store.layout.database) as db:
            assert db.execute("PRAGMA user_version").fetchone() == (49,)
            assert (
                db.execute(
                    "SELECT name FROM sqlite_master WHERE name='workspace_changes'"
                ).fetchone()
                is None
            )
        store.failure_injector = None
    with store.open(StoreOpenMode.READ_WRITE) as handle:
        assert handle.schema_version == 50
        assert handle.run_read(lambda ex: ex.execute("SELECT COUNT(*) FROM workspace_changes")) == (
            (0,),
        )
    assert list(store.layout.backups_dir.glob("pre-v50-*.sqlite"))
