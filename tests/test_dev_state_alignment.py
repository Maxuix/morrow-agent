"""Repository-only development-state alignment checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from morrow.adapters.state.migrations import MigrationRegistry, production_registry
from morrow.adapters.state.operational import OperationalStore
from morrow.core.store import SUPPORTED_SCHEMA_VERSION

SCRIPT = Path(__file__).parents[1] / "scripts" / "ensure-current-dev-state.py"


def test_dev_wrapper_alignment_migrates_once_and_is_idempotent(tmp_path):
    state_root = tmp_path / "state"
    current = production_registry()
    legacy = MigrationRegistry(supported_version=22)
    for version in range(1, 23):
        legacy.add(current.get(version))
    OperationalStore(state_root, registry=legacy).initialize().close()

    first = subprocess.run(
        [sys.executable, str(SCRIPT), str(state_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, str(SCRIPT), str(state_root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0
    assert "development state aligned v22 -> v26" in first.stderr
    assert second.returncode == 0
    assert second.stderr == ""
    assert OperationalStore(state_root).classify().schema_version == SUPPORTED_SCHEMA_VERSION
    assert len(tuple((state_root / "backups" / "operational").glob("*.sqlite"))) == 1
