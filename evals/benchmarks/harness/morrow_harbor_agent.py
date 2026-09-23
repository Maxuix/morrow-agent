"""Harbor installed-agent adapter that runs Morrow inside the task container.

Design notes
------------
* Morrow is installed fully offline from a pre-built asset bundle
  (``assets/`` next to this file): a static ``uv`` binary, a CPython 3.12
  ``python-build-standalone`` tarball, a Morrow wheel, and a pip wheelhouse
  for linux x86_64. ``scripts/prepare_assets.sh`` builds that bundle once on
  the host; trials never touch the network for installation.
* The model credential is forwarded only through per-exec environment
  variables (Morrow's ``MORROW_<PROVIDER>_API_KEY`` convention). It never
  lands in config files, command lines, or logs.
* ``morrow run`` emits versioned JSONL records; the final ``run.completed``
  record carries Morrow's terminal metrics (token usage, cost, tool calls,
  retries, attempts). The adapter parses that record and populates the
  Harbor ``AgentContext`` so trial results carry the same numbers.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path
from typing import Annotated, Any, Literal

from harbor.agents.installed.base import (
    BaseInstalledAgent,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.agents.options import Cli, InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
RUN_LOG_PATH = "/tmp/morrow-run.jsonl"
SETUP_MARKER = "/opt/morrow/.bench-setup-ok"
LOG_COPY_TIMEOUT_SEC = 15


class MorrowOptions(InstalledAgentOptions):
    """User-facing kwargs; every field also falls back to a MORROW_BENCH_* env var."""

    provider_adapter: Annotated[
        str, Cli("--provider-adapter", fallback="MORROW_BENCH_PROVIDER_ADAPTER")
    ] = Field(default="openai-compatible")
    provider_base_url: Annotated[
        str, Cli("--provider-base-url", fallback="MORROW_BENCH_PROVIDER_BASE_URL")
    ] = Field(...)
    provider_id: Annotated[str, Cli("--provider-id", fallback="MORROW_BENCH_PROVIDER_ID")] = Field(
        default="bench"
    )
    model_id: Annotated[str, Cli("--model-id", fallback="MORROW_BENCH_MODEL_ID")] = Field(...)
    api_model_id: Annotated[str, Cli("--api-model-id", fallback="MORROW_BENCH_API_MODEL_ID")] = (
        Field(...)
    )
    workspace_dir: Annotated[
        str | None, Cli("--workspace-dir", fallback="MORROW_BENCH_WORKSPACE_DIR")
    ] = Field(default=None)
    state_root: Annotated[str, Cli("--state-root", fallback="MORROW_BENCH_STATE_ROOT")] = Field(
        default="/tmp/morrow-state"
    )
    api_key_env: Annotated[str, Cli("--api-key-env", fallback="MORROW_BENCH_API_KEY_ENV")] = Field(
        default="MORROW_BENCH_API_KEY"
    )
    reasoning_effort: Annotated[
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None,
        Cli("--reasoning-effort", fallback="MORROW_BENCH_REASONING_EFFORT"),
    ] = Field(default=None)


def _last_jsonl_record(text: str, kind: str) -> dict[str, Any] | None:
    record = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("kind") == kind:
            record = item
    return record


class MorrowAgent(BaseInstalledAgent):
    """Run Morrow (``morrow run`` headless JSONL mode) inside the environment."""

    options_model = MorrowOptions

    _MORROW_VENV = "/opt/morrow/venv"
    _MORROW_BIN = "/opt/morrow/venv/bin/morrow"

    @staticmethod
    def name() -> str:
        return "morrow"

    def version(self) -> str | None:
        return self._morrow_version()

    # -- helpers ---------------------------------------------------------

    def _morrow_version(self) -> str:
        wheel = next(ASSETS_DIR.glob("morrow_agent-*.whl"), None)
        if wheel is None:
            return "unknown"
        match = re.match(r"morrow_agent-([^-]+)-", wheel.name)
        return match.group(1) if match else "unknown"

    def _secret(self) -> str:
        value = self._get_env(self.options.api_key_env) if self.options else None
        if not value:
            raise ValueError(
                f"agent env {self.options.api_key_env} is required (--ae "
                f"{self.options.api_key_env}=... or host environment)"
            )
        return value

    def _run_env(self) -> dict[str, str]:
        assert self.options is not None
        provider_key = f"MORROW_{self.options.provider_id.upper().replace('-', '_')}_API_KEY"
        return {provider_key: self._secret()}

    async def _workspace_dir(self, environment: BaseEnvironment) -> str:
        assert self.options is not None
        workdir = self.options.workspace_dir or environment.task_env_config.workdir
        if not workdir:
            result = await environment.exec("pwd", timeout_sec=10)
            if result.return_code != 0:
                raise RuntimeError("could not determine task working directory")
            workdir = result.stdout.strip()
        if not workdir.startswith("/"):
            raise ValueError("task working directory must be an absolute path")
        return workdir

    # -- lifecycle -------------------------------------------------------

    async def install(self, environment: BaseEnvironment) -> None:
        opts = self.options
        assert opts is not None
        await environment.exec("mkdir -p /opt/morrow /opt/bench /opt/morrow/tools", user="root")

        uv_bin = ASSETS_DIR / "uv-x86_64-unknown-linux-gnu"
        if uv_bin.exists():
            await environment.upload_file(uv_bin, "/opt/morrow/tools/uv")
        uvx_bin = ASSETS_DIR / "uvx-x86_64-unknown-linux-gnu"
        if uvx_bin.exists():
            await environment.upload_file(uvx_bin, "/opt/morrow/tools/uvx")
        py_tarball = next(
            ASSETS_DIR.glob("cpython-3.12*-x86_64-unknown-linux-gnu-install_only.tar.gz")
        )
        await environment.upload_file(py_tarball, "/opt/bench/python.tar.gz")
        await environment.upload_dir(ASSETS_DIR / "wheelhouse", "/opt/bench/wheelhouse")
        wheel = next(ASSETS_DIR.glob("morrow_agent-*.whl"))
        await environment.upload_file(wheel, f"/opt/bench/{wheel.name}")

        await self.exec_as_root(
            environment,
            command=(
                "set -e; "
                "mkdir -p /opt/python && tar -xzf /opt/bench/python.tar.gz -C /opt/python --strip-components=1; "
                "chmod +x /opt/morrow/tools/* 2>/dev/null || true; "
                "/opt/python/bin/python3 -m venv /opt/morrow/venv; "
                "/opt/morrow/venv/bin/pip install --no-index --find-links /opt/bench/wheelhouse "
                "/opt/bench/morrow_agent-*.whl; "
                f"touch {SETUP_MARKER}"
            ),
            timeout_sec=600,
        )

        # Non-interactive workspace + provider + model bootstrap. bench_setup
        # creates the provider with secret=None; the real key only exists in
        # per-exec env and is resolved via MORROW_<PROVIDER>_API_KEY at runtime.
        setup_src = Path(__file__).resolve().parent / "bench_setup.py"
        await environment.upload_file(setup_src, "/opt/bench/bench_setup.py")
        await self.exec_as_root(
            environment,
            command=(
                f"{self._MORROW_VENV}/bin/python /opt/bench/bench_setup.py "
                f"--state-root {shlex.quote(opts.state_root)} "
                f"--workspace {shlex.quote(await self._workspace_dir(environment))} "
                f"--provider-id {shlex.quote(opts.provider_id)} "
                f"--adapter {shlex.quote(opts.provider_adapter)} "
                f"--base-url {shlex.quote(opts.provider_base_url)} "
                f"--model-id {shlex.quote(opts.model_id)} "
                f"--api-model-id {shlex.quote(opts.api_model_id)}"
            ),
            timeout_sec=300,
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        opts = self.options
        assert opts is not None
        workspace = await self._workspace_dir(environment)
        state_root = opts.state_root

        prompt_path = "/tmp/morrow-prompt.txt"
        local_prompt = _write_temp(instruction)
        try:
            await environment.upload_file(local_prompt, prompt_path)
        finally:
            local_prompt.unlink(missing_ok=True)
        # Capture the process status separately so its log remains available
        # before Harbor classifies an agent execution failure.
        reasoning_arg = (
            f"--reasoning-effort {shlex.quote(opts.reasoning_effort)} "
            if opts.reasoning_effort
            else ""
        )
        command = (
            f"PATH=/opt/morrow/tools:$PATH {self._MORROW_BIN} run "
            f"--state-root {shlex.quote(state_root)} "
            f"--workspace {shlex.quote(workspace)} "
            "--permission-mode manual "
            f"{reasoning_arg}"
            f'--prompt "$(cat {prompt_path})" '
            f"> {RUN_LOG_PATH} 2>&1; echo MORROW_EXIT=$?"
        )
        try:
            result = await environment.exec(command=command, env=self._run_env(), timeout_sec=None)
        finally:
            # Harbor cancels this coroutine on agent timeout. The container log
            # survives that cancellation until the environment is torn down.
            copy = asyncio.create_task(
                environment.download_file(RUN_LOG_PATH, self.logs_dir / "morrow-run.jsonl")
            )
            try:
                await asyncio.wait_for(asyncio.shield(copy), timeout=LOG_COPY_TIMEOUT_SEC)
            except (OSError, RuntimeError, TimeoutError) as exc:
                self.logger.warning("could not recover morrow run log: %s", type(exc).__name__)
                copy.cancel()
            else:
                log_text = (self.logs_dir / "morrow-run.jsonl").read_text(
                    encoding="utf-8", errors="replace"
                )
                self._populate_context(context, log_text)
        status = re.search(r"(?:^|\n)MORROW_EXIT=(\d+)(?:\n|$)", result.stdout or "")
        if result.return_code != 0 or status is None:
            raise NonZeroAgentExitCodeError("morrow run did not report a successful exit")
        exit_code = int(status.group(1))
        self.logger.info("morrow run finished with exit code %d", exit_code)
        if exit_code != 0:
            raise NonZeroAgentExitCodeError(f"morrow run exited with code {exit_code}")

    # -- metrics ---------------------------------------------------------

    def _populate_context(self, context: AgentContext, log_text: str) -> None:
        completed = _last_jsonl_record(log_text, "run.completed")
        if not completed:
            self.logger.warning("no run.completed record in morrow JSONL output")
            return
        metrics = completed.get("metrics") or {}
        usage = metrics.get("usage") or {}
        cost = metrics.get("cost") or {}
        context.n_input_tokens = usage.get("input_tokens")
        context.n_output_tokens = usage.get("output_tokens")
        if usage.get("total_tokens") is not None:
            # Harbor sums input+output; keep totals consistent when both absent.
            if context.n_input_tokens is None and context.n_output_tokens is None:
                context.n_input_tokens = usage["total_tokens"]
        if cost.get("availability") == "available" and cost.get("amount_minor") is not None:
            amount = cost["amount_minor"] / 100.0
            context.cost_usd = amount if cost.get("currency") == "USD" else None
        from harbor.models.agent.context import ModelUsage

        context.model_usage = {
            "morrow-terminal-metrics": ModelUsage(
                n_input_tokens=usage.get("input_tokens") or 0,
                n_output_tokens=usage.get("output_tokens") or 0,
                cost_usd=context.cost_usd,
            )
        }
        # Full Morrow terminal metrics are preserved next to the JSONL log for
        # the metrics collector (tool calls, rounds, attempts, retries, stops).
        (self.logs_dir / "morrow-terminal-metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _write_temp(text: str) -> Path:
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="morrow-prompt-", delete=False, encoding="utf-8"
    )
    handle.write(text)
    handle.close()
    return Path(handle.name)
