"""Offline checks for the benchmark asset preparation transaction."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evals/benchmarks/scripts/prepare_assets.sh"


@pytest.mark.parametrize("smoke_fails", [False, True])
def test_prepare_assets_replaces_bundle_only_after_compatible_smoke(
    tmp_path: Path, smoke_fails: bool
):
    script_dir = tmp_path / "repo/evals/benchmarks/scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "prepare_assets.sh"
    shutil.copyfile(SCRIPT, script)
    assets = script_dir.parent / "assets"
    (assets / "wheelhouse").mkdir(parents=True)
    stale_wheel = assets / "morrow_agent-old-py3-none-any.whl"
    stale_wheel.touch()
    stale_dep = assets / "wheelhouse/cryptography-old-manylinux_2_34_x86_64.whl"
    stale_dep.touch()
    (assets / "cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only.tar.gz").touch()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(
        "#!/bin/sh\n"
        'while [ "$1" != --out-dir ]; do shift; done\n'
        'shift; touch "$1/morrow_agent-0.1.0-py3-none-any.whl"\n'
    )
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$*" >> "$FAKE_DOCKER_CALLS"\n'
        'case " $* " in\n'
        "  *' --network none '*) [ \"$FAKE_SMOKE_FAILS\" = 0 ] || exit 17 ;;\n"
        '  *) for arg in "$@"; do\n'
        '       case "$arg" in *:/candidate)\n'
        "         dir=${arg%:/candidate};\n"
        '         touch "$dir/wheelhouse/cryptography-new-manylinux_2_28_x86_64.whl";;\n'
        "       esac\n"
        "     done ;;\n"
        "esac\n"
    )
    for executable in (bin_dir / "uv", bin_dir / "docker"):
        executable.chmod(0o755)

    calls = tmp_path / "docker-calls.txt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_CALLS": str(calls),
        "FAKE_SMOKE_FAILS": str(int(smoke_fails)),
    }
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    call_text = calls.read_text()
    assert "manylinux_2_28_x86_64" in call_text
    assert "--network none" in call_text
    assert "debian:11" in call_text
    if smoke_fails:
        assert result.returncode != 0
        assert stale_wheel.exists()
        assert stale_dep.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert not stale_wheel.exists()
        assert not stale_dep.exists()
        assert (assets / "morrow_agent-0.1.0-py3-none-any.whl").exists()
        assert (assets / "wheelhouse/cryptography-new-manylinux_2_28_x86_64.whl").exists()
