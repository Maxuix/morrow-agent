"""Per-instance SWE-bench Lite runner: Morrow inside the official task image.

Uses SWE-bench's official evaluation path end to end:

1. Resolve the instance image (``ghcr.io/swe-bench/...`` built from the
   official specs, or prebuilt community mirrors) and start a container with
   the repo checked out at ``base_commit``.
2. Install Morrow offline from the shared asset bundle (same bundle as the
   Harbor adapter) and run ``morrow run`` with the instance ``problem_statement``.
3. Collect ``git diff`` from the container as the model patch in the official
   predictions format (``instance_id,model_patch,model_name_or_path``).
4. After all patches are collected, the driver invokes the *official*
   ``swebench.harness.run_evaluation`` (no custom result interpretation) to
   compute ``resolved`` / ``% Resolved``.

The runner never writes credentials anywhere except per-exec environment.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
CONTAINER_WORKSPACE = "/testbed"
RUN_LOG = "/tmp/morrow-run.jsonl"


@dataclass
class InstanceResult:
    instance_id: str
    status: str  # "patched" | "empty-patch" | "error"
    duration_sec: float = 0.0
    patch_file: Path | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


def _docker(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
    )


def image_for_instance(instance: dict[str, Any], template: str | None = None) -> str:
    """Official-style image tag used by the SWE-bench harness.

    ``template`` may point at a reachable registry mirror, e.g.
    ``"docker.m.daocloud.io/ghcr.io/swe-bench/{repo}:{version}"``.
    """
    repo = instance["repo"].replace("/", "_").lower()
    version = instance["instance_id"].split("__")[-1]
    template = template or "ghcr.io/swe-bench/{repo}:{version}"
    return template.format(repo=repo, version=version)


class SweLiteRunner:
    def __init__(
        self,
        *,
        assets_dir: Path = ASSETS_DIR,
        workspace: Path,
        provider_env: dict[str, str],
        morrow_env: dict[str, str],
        image_template: str | None = None,
        timeout_sec: int = 1800,
    ) -> None:
        self.assets_dir = Path(assets_dir)
        self.workspace = Path(workspace)
        self.provider_env = provider_env  # secret-bearing; exec-env only
        self.morrow_env = morrow_env  # provider/model ids, base url
        self.image_template = image_template
        self.timeout_sec = timeout_sec
        self._ensure_assets()

    def _ensure_assets(self) -> None:
        for pattern in ("morrow_agent-*.whl", "cpython-3.12*-x86_64*.tar.gz"):
            if not next(self.assets_dir.glob(pattern), None):
                raise FileNotFoundError(
                    f"asset {pattern} missing under {self.assets_dir}; "
                    "run scripts/prepare_assets.sh first"
                )
        if not (self.assets_dir / "wheelhouse").is_dir():
            raise FileNotFoundError(f"wheelhouse missing under {self.assets_dir}")

    # -- container helpers ------------------------------------------------

    def _exec(self, name: str, command: str, *, env: dict[str, str] | None = None) -> str:
        args = ["exec"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args += [name, "bash", "-lc", command]
        result = _docker(*args)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker exec failed ({result.returncode}): {command}\n{result.stderr}"
            )
        return result.stdout

    def _install_morrow(self, name: str) -> None:
        wheel = next(self.assets_dir.glob("morrow_agent-*.whl")).name
        py_tarball = next(self.assets_dir.glob("cpython-3.12*-x86_64*.tar.gz")).name
        self._exec(name, "mkdir -p /opt/morrow /opt/bench")
        uv_bin = self.assets_dir / "uv-x86_64-unknown-linux-gnu"
        if uv_bin.exists():
            _docker("cp", str(uv_bin), f"{name}:/opt/bench/uv")
        _docker("cp", str(self.assets_dir / py_tarball), f"{name}:/opt/bench/python.tar.gz")
        _docker("cp", str(self.assets_dir / wheel), f"{name}:/opt/bench/morrow_agent.whl")
        _docker("cp", str(self.assets_dir / "wheelhouse"), f"{name}:/opt/bench/wheelhouse")
        self._exec(
            name,
            "set -e; "
            "mkdir -p /opt/python && tar -xzf /opt/bench/python.tar.gz -C /opt/python --strip-components=1; "
            "/opt/python/bin/python3 -m venv /opt/morrow/venv; "
            "/opt/morrow/venv/bin/pip install --no-index --find-links /opt/bench/wheelhouse "
            "/opt/bench/morrow_agent-*.whl",
        )

    def _configure_morrow(self, name: str) -> None:
        env = self.morrow_env
        setup_src = Path(__file__).resolve().parent / "bench_setup.py"
        _docker("cp", str(setup_src), f"{name}:/opt/bench/bench_setup.py")
        self._exec(
            name,
            "/opt/morrow/venv/bin/python /opt/bench/bench_setup.py "
            "--state-root /tmp/morrow-state "
            f"--workspace {CONTAINER_WORKSPACE} "
            f"--provider-id {shlex.quote(env['provider_id'])} "
            f"--adapter {shlex.quote(env['adapter'])} "
            f"--base-url {shlex.quote(env['base_url'])} "
            f"--model-id {shlex.quote(env['model_id'])} "
            f"--api-model-id {shlex.quote(env['api_model_id'])}",
        )

    # -- main entry --------------------------------------------------------

    def run_instance(self, instance: dict[str, Any]) -> InstanceResult:
        instance_id = instance["instance_id"]
        image = image_for_instance(instance, self.image_template)
        name = f"morrow-swe-{instance_id.split('__')[-1].lower()}"
        started = time.monotonic()
        try:
            # Clean up a leftover container from a previously killed run so the
            # campaign can resume without manual docker housekeeping.
            _docker("rm", "-f", name, check=False, capture=False)
            _docker("run", "-d", "--name", name, "--network", "host", image, "sleep", "infinity")
            try:
                self._install_morrow(name)
                self._configure_morrow(name)

                prompt_file = self.workspace / f"{instance_id}__prompt.txt"
                prompt_file.write_text(instance["problem_statement"], encoding="utf-8")
                _docker("cp", str(prompt_file), f"{name}:/tmp/prompt.txt")

                provider_id = self.morrow_env["provider_id"]
                run_env = {
                    f"MORROW_{provider_id.upper().replace('-', '_')}_API_KEY": self.provider_env[
                        "api_key"
                    ]
                }
                reasoning_arg = (
                    f"--reasoning-effort {shlex.quote(self.morrow_env['reasoning_effort'])} "
                    if self.morrow_env.get("reasoning_effort")
                    else ""
                )
                base = "/opt/morrow/venv/bin/morrow run --state-root /tmp/morrow-state"
                self._exec(
                    name,
                    f"timeout {self.timeout_sec} {base} "
                    f"{reasoning_arg}"
                    f"--workspace {CONTAINER_WORKSPACE} "
                    "--permission-mode manual "
                    '--prompt "$(cat /tmp/prompt.txt)" '
                    f"> {RUN_LOG} 2>&1; echo MORROW_EXIT=$?",
                    env=run_env,
                )

                log_local = self.workspace / f"{instance_id}__morrow-run.jsonl"
                _docker("cp", f"{name}:{RUN_LOG}", str(log_local))
                metrics = self._extract_metrics(log_local)

                patch = self._exec(
                    name,
                    f"git -C {CONTAINER_WORKSPACE} diff",
                )
                patch_file = self.workspace / f"{instance_id}__patch.diff"
                patch_file.write_text(patch, encoding="utf-8")
                status = "patched" if patch.strip() else "empty-patch"
                return InstanceResult(
                    instance_id=instance_id,
                    status=status,
                    duration_sec=time.monotonic() - started,
                    patch_file=patch_file if patch.strip() else None,
                    usage=metrics.get("usage", {}),
                    metrics=metrics,
                )
            finally:
                _docker("rm", "-f", name, check=False)
        except Exception as exc:  # noqa: BLE001 - record and continue the campaign
            return InstanceResult(
                instance_id=instance_id,
                status="error",
                duration_sec=time.monotonic() - started,
                error=str(exc)[:500],
            )

    @staticmethod
    def _extract_metrics(log_file: Path) -> dict[str, Any]:
        if not log_file.exists():
            return {}
        record = None
        for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("kind") == "run.completed":
                record = item
        return (record or {}).get("metrics") or {}
