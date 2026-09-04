#!/usr/bin/env python3
"""Align the persistent development store before invoking a source checkout.

This helper is intentionally outside ``src/morrow`` and is used only by the
repository's Mimo development wrapper. Installed Morrow builds do not call it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import StorageError, StoreHealth


def main(state_root: Path) -> int:
    store = OperationalStore(state_root)
    classification = store.classify()
    if not classification.present:
        return 0
    if classification.health is not StoreHealth.OK or classification.schema_version is None:
        print("development state is not healthy; run state doctor before startup", file=sys.stderr)
        return 2

    current = classification.schema_version
    supported = store.registry.supported_version
    if current == supported:
        return 0
    if current > supported:
        print(
            f"development state v{current} is newer than this checkout v{supported}; "
            "select the current checkout or a separate MORROW_MIMO_STATE_ROOT",
            file=sys.stderr,
        )
        return 2

    try:
        report = store.migrate()
    except StorageError as exc:
        print(f"development state alignment failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"development state aligned v{report.from_version} -> v{report.to_version}; "
        f"backup: {report.backup_name}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: ensure-current-dev-state.py STATE_ROOT", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
