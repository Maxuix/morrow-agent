#!/usr/bin/env python3
"""Rebuild disposable development runtime state when its schema is stale."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import StoreHealth


def main(state_root: Path) -> int:
    store = OperationalStore(state_root)
    classification = store.classify()
    if not classification.present:
        return 0

    current = classification.schema_version
    supported = store.registry.supported_version
    if current == supported and classification.health is StoreHealth.OK:
        return 0
    if current is None or current == supported:
        print("development state is unhealthy; run state doctor before startup", file=sys.stderr)
        return 2

    for path in (store.layout.database, store.layout.wal, store.layout.shm):
        path.unlink(missing_ok=True)
    artifacts = store.layout.artifacts_dir
    if artifacts.is_symlink():
        artifacts.unlink()
    elif artifacts.exists():
        shutil.rmtree(artifacts)

    handle = store.initialize()
    handle.close()
    print(
        f"development schema changed v{current} -> v{supported}; "
        "discarded runtime data and rebuilt the current store",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: rebuild-stale-dev-store.py STATE_ROOT", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
