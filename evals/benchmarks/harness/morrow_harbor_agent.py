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
* ``morrow run`` emits versioned JSONL records and a bounded diagnostic sidecar
  in Harbor's agent log directory. Terminal metrics populate ``AgentContext``;
  if the terminal record is missing, settled request observations supply known
  usage with explicit unknown exposure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shlex
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import (
    BaseInstalledAgent,
    NonZeroAgentExitCodeError,
    with_prompt_template,
)
from harbor.agents.options import Cli, InstalledAgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories.agent import Agent
from harbor.models.trajectories.metrics import Metrics
from harbor.models.trajectories.observation import Observation
from harbor.models.trajectories.observation_result import ObservationResult
from harbor.models.trajectories.step import Step
from harbor.models.trajectories.tool_call import ToolCall
from harbor.models.trajectories.trajectory import Trajectory
from pydantic import Field

from harness.fingerprint import run_fingerprint, sha256_file, sha256_tree, write_json

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
    context_window_tokens: Annotated[
        int | None, Cli("--context-window-tokens", fallback="MORROW_BENCH_CONTEXT_WINDOW_TOKENS")
    ] = Field(default=None, gt=0)
    max_output_tokens: Annotated[
        int | None, Cli("--max-output-tokens", fallback="MORROW_BENCH_MAX_OUTPUT_TOKENS")
    ] = Field(default=None, gt=0)
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


def _resolved_agent_timeout_seconds(trial_dir: Path) -> float | None:
    """Mirror Harbor's single-step timeout from this trial's resolved inputs."""
    config_path = trial_dir / "config.json"
    if not config_path.is_file():
        return None
    config = json.loads(config_path.read_text(encoding="utf-8"))
    task = config.get("task") or {}
    task_path = task.get("path")
    if not isinstance(task_path, str):
        return None
    task_config = tomllib.loads((Path(task_path) / "task.toml").read_text(encoding="utf-8"))
    agent_config = config.get("agent") or {}
    base = agent_config.get("override_timeout_sec") or (task_config.get("agent") or {}).get(
        "timeout_sec"
    )
    if base is None:
        return None
    maximum = agent_config.get("max_timeout_sec")
    multiplier = config.get("agent_timeout_multiplier")
    if multiplier is None:
        multiplier = config.get("timeout_multiplier", 1.0)
    seconds = min(float(base), float(maximum) if maximum is not None else math.inf) * float(
        multiplier
    )
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid resolved Harbor agent timeout")
    return seconds


class MorrowAgent(BaseInstalledAgent):
    """Run Morrow (``morrow run`` headless JSONL mode) inside the environment."""

    options_model = MorrowOptions
    capabilities = AgentCapabilities(atif=True)

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

    def _diagnostic_path(self) -> str:
        return (self.environment_logs_dir / "morrow-diagnostics.jsonl").as_posix()

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
        wheels = list(ASSETS_DIR.glob("morrow_agent-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("benchmark assets require exactly one Morrow wheel")
        wheel = wheels[0]
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
                + (
                    f" --context-window-tokens {opts.context_window_tokens}"
                    if opts.context_window_tokens is not None
                    else ""
                )
                + (
                    f" --max-output-tokens {opts.max_output_tokens}"
                    if opts.max_output_tokens is not None
                    else ""
                )
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
        started_at = time.monotonic()
        outer_timeout = _resolved_agent_timeout_seconds(self.logs_dir.parent)
        workspace = await self._workspace_dir(environment)
        state_root = opts.state_root

        trial_config_path = self.logs_dir.parent / "config.json"
        trial_config = (
            json.loads(trial_config_path.read_text(encoding="utf-8"))
            if trial_config_path.is_file()
            else {}
        )
        task_path = (trial_config.get("task") or {}).get("path")
        manifest_path = os.environ.get("MORROW_BENCH_RUN_MANIFEST")
        if manifest_path and not Path(manifest_path).resolve().is_relative_to(
            (ASSETS_DIR.parent / "runs" / "manifests").resolve()
        ):
            raise ValueError("benchmark run manifest must be in the manifest directory")
        campaign = (
            json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            if manifest_path and Path(manifest_path).is_file()
            else run_fingerprint(
                ASSETS_DIR.parent,
                settings={"run_id": self.logs_dir.parent.name, "origin": "direct_harbor"},
            )
        )
        wheel = next(iter(ASSETS_DIR.glob("morrow_agent-*.whl")), None)
        actual_wheel_sha = sha256_file(wheel) if wheel else None
        expected_wheel_sha = (campaign.get("wheel") or {}).get("sha256")
        if expected_wheel_sha and actual_wheel_sha != expected_wheel_sha:
            raise RuntimeError("installed Morrow wheel differs from frozen run fingerprint")
        expected_asset_manifest_sha = (campaign.get("assets") or {}).get("build_manifest_sha256")
        if (
            expected_asset_manifest_sha
            and sha256_file(ASSETS_DIR / "asset-manifest.json") != expected_asset_manifest_sha
        ):
            raise RuntimeError("benchmark assets differ from frozen build manifest")
        actual_task_sha = sha256_tree(Path(task_path)) if isinstance(task_path, str) else None
        expected_task_sha = ((campaign.get("settings") or {}).get("tasks") or {}).get(
            Path(task_path).name if isinstance(task_path, str) else ""
        )
        if expected_task_sha and actual_task_sha != expected_task_sha:
            raise RuntimeError("task contents differ from frozen run fingerprint")
        runtime_result = await environment.exec(
            command=(
                "/opt/morrow/venv/bin/python -c "
                '\'import json,platform,sys; print(json.dumps({"python":sys.version.split()[0],'
                '"libc":platform.libc_ver(),"architecture":platform.machine()}))\''
            ),
            timeout_sec=10,
        )
        try:
            runtime = json.loads(runtime_result.stdout) if runtime_result.return_code == 0 else None
        except (TypeError, json.JSONDecodeError):
            runtime = None
        if not isinstance(runtime, dict) or not all(
            runtime.get(key) for key in ("python", "libc", "architecture")
        ):
            raise RuntimeError("could not fingerprint trial Python/libc/architecture")
        fingerprint = {
            "schema_version": 1,
            "campaign": campaign,
            "task_checksum_sha256": actual_task_sha,
            "installed_wheel_sha256": actual_wheel_sha,
            "prompt_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            "runtime": runtime,
            "resolved_agent_timeout_seconds": outer_timeout,
            "permission_mode": "manual",
            "provider_adapter": opts.provider_adapter,
            "provider_id": opts.provider_id,
            "provider_base_url_sha256": hashlib.sha256(
                opts.provider_base_url.encode("utf-8")
            ).hexdigest(),
            "model_id": opts.model_id,
            "api_model_id": opts.api_model_id,
            "reasoning_effort": opts.reasoning_effort,
            "context_window_tokens": opts.context_window_tokens,
            "max_output_tokens": opts.max_output_tokens,
            "tool_schema_digest": None,
        }
        write_json(self.logs_dir / "morrow-fingerprint.json", fingerprint)
        context.metadata = {
            **(context.metadata or {}),
            "morrow_fingerprint": "morrow-fingerprint.json",
        }

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
        remaining_arg = ""
        if outer_timeout is not None:
            remaining = outer_timeout - (time.monotonic() - started_at) - LOG_COPY_TIMEOUT_SEC
            if remaining <= 0:
                raise TimeoutError("Harbor agent timeout left no time for Morrow execution")
            remaining_arg = f"--run-timeout-seconds {remaining:.3f} "
        command = (
            f"PATH=/opt/morrow/tools:$PATH {self._MORROW_BIN} run "
            f"--state-root {shlex.quote(state_root)} "
            f"--workspace {shlex.quote(workspace)} "
            "--permission-mode manual "
            f"{reasoning_arg}"
            f"{remaining_arg}"
            f"--diagnostic-log {shlex.quote(self._diagnostic_path())} "
            f'--prompt "$(cat {prompt_path})" '
            f"> {RUN_LOG_PATH} 2>&1; echo MORROW_EXIT=$?"
        )
        log_text = ""
        try:
            result = await environment.exec(command=command, env=self._run_env(), timeout_sec=None)
        finally:
            recovery_until = time.monotonic() + LOG_COPY_TIMEOUT_SEC
            # Harbor cancels this coroutine on agent timeout. The container log
            # survives that cancellation until the environment is torn down.
            copy = asyncio.create_task(
                environment.download_file(RUN_LOG_PATH, self.logs_dir / "morrow-run.jsonl")
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(copy), timeout=max(0.01, recovery_until - time.monotonic())
                )
            except asyncio.CancelledError:
                copy.cancel()
                raise
            except (OSError, RuntimeError, TimeoutError) as exc:
                self.logger.warning("could not recover morrow run log: %s", type(exc).__name__)
                copy.cancel()
            else:
                try:
                    log_text = (self.logs_dir / "morrow-run.jsonl").read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    pass
            finally:
                diagnostic_path = self.logs_dir / "morrow-diagnostics.jsonl"
                if not diagnostic_path.exists() and time.monotonic() < recovery_until:
                    try:
                        await asyncio.wait_for(
                            environment.download_file(self._diagnostic_path(), diagnostic_path),
                            timeout=max(0.01, recovery_until - time.monotonic()),
                        )
                    except (OSError, RuntimeError, TimeoutError, asyncio.CancelledError):
                        pass
                try:
                    self._populate_context(context, log_text)
                except Exception as exc:
                    self.logger.warning(
                        "could not project morrow diagnostics: %s", type(exc).__name__
                    )
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
        diagnostic_path = self.logs_dir / "morrow-diagnostics.jsonl"
        diagnostic_text = (
            diagnostic_path.read_text(encoding="utf-8", errors="replace")
            if diagnostic_path.exists()
            else ""
        )
        diagnostic_fingerprint = _last_jsonl_record(diagnostic_text, "run.fingerprint")
        frozen = (completed or {}).get("fingerprint") or (diagnostic_fingerprint or {}).get(
            "digests"
        )
        fingerprint_path = self.logs_dir / "morrow-fingerprint.json"
        if isinstance(frozen, dict) and fingerprint_path.is_file():
            manifest = json.loads(fingerprint_path.read_text(encoding="utf-8"))
            manifest["frozen_agent_run"] = {
                key: value
                for key, value in frozen.items()
                if key
                in {
                    "tool_schema_digest",
                    "run_policy_digest",
                    "provider_config_digest",
                    "generation_digest",
                    "prompt_profile_digest",
                    "role_prompt_digest",
                    "project_instruction_selection_digest",
                }
                and (
                    value is None or isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
                )
            }
            write_json(fingerprint_path, manifest)
        self._write_trajectory(diagnostic_text)
        if not completed:
            self.logger.warning("no run.completed record in morrow JSONL output")
            self._populate_partial_context(context, diagnostic_text)
            return
        metrics = completed.get("metrics") or {}
        if not metrics:
            self._populate_partial_context(context, diagnostic_text)
            return
        usage = metrics.get("usage") or {}
        cost = metrics.get("cost") or {}
        context.n_input_tokens = usage.get("input_tokens")
        context.n_output_tokens = usage.get("output_tokens")
        if isinstance(usage.get("total_tokens"), int):
            context.metadata = {
                **(context.metadata or {}),
                "morrow_terminal_total_tokens": usage["total_tokens"],
            }
        if cost.get("availability") == "available" and cost.get("amount_minor") is not None:
            amount = cost["amount_minor"] / 100.0
            context.cost_usd = amount if cost.get("currency") == "USD" else None
        from harbor.models.agent.context import ModelUsage

        if isinstance(usage.get("input_tokens"), int) and isinstance(
            usage.get("output_tokens"), int
        ):
            context.model_usage = {
                "morrow-terminal-metrics": ModelUsage(
                    n_input_tokens=usage["input_tokens"],
                    n_output_tokens=usage["output_tokens"],
                    cost_usd=context.cost_usd,
                )
            }
        # Full Morrow terminal metrics are preserved next to the JSONL log for
        # the metrics collector (tool calls, rounds, attempts, retries, stops).
        (self.logs_dir / "morrow-terminal-metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if context.n_input_tokens is None and context.n_output_tokens is None:
            self._populate_partial_context(context, diagnostic_text)

    def _populate_partial_context(self, context: AgentContext, diagnostic_text: str) -> None:
        requests = _latest_diagnostic_records(diagnostic_text, "model.request", "request_id")
        if not requests:
            return
        known = [
            row
            for row in requests.values()
            if (row.get("usage") or {}).get("availability") == "available"
        ]
        unknown = len(requests) - len(known)
        input_values = [(row.get("usage") or {}).get("input_tokens") for row in known]
        output_values = [(row.get("usage") or {}).get("output_tokens") for row in known]
        known_input = sum(value for value in input_values if isinstance(value, int))
        known_output = sum(value for value in output_values if isinstance(value, int))
        usd_costs = [
            (row.get("cost") or {}).get("amount_minor")
            for row in requests.values()
            if (row.get("cost") or {}).get("availability") == "available"
            and (row.get("cost") or {}).get("currency") == "USD"
            and isinstance((row.get("cost") or {}).get("amount_minor"), int)
        ]
        known_cost_minor = sum(value for value in usd_costs if isinstance(value, int))
        if any(isinstance(value, int) for value in input_values):
            context.n_input_tokens = known_input
        if any(isinstance(value, int) for value in output_values):
            context.n_output_tokens = known_output
        if len(usd_costs) == len(requests) and usd_costs:
            context.cost_usd = known_cost_minor / 100.0
        if input_values and all(isinstance(value, int) for value in input_values + output_values):
            from harbor.models.agent.context import ModelUsage

            context.model_usage = {
                "morrow-known-requests": ModelUsage(
                    n_input_tokens=known_input,
                    n_output_tokens=known_output,
                    cost_usd=context.cost_usd,
                )
            }
        context.metadata = {
            **(context.metadata or {}),
            "morrow_partial_usage": {
                "known_request_count": len(known),
                "unknown_request_count": unknown,
                "complete": unknown == 0
                and len(input_values) == len(requests)
                and all(isinstance(value, int) for value in input_values + output_values),
                "known_input_tokens": known_input
                if any(isinstance(value, int) for value in input_values)
                else None,
                "known_output_tokens": known_output
                if any(isinstance(value, int) for value in output_values)
                else None,
                "known_usd_cost_minor": known_cost_minor if usd_costs else None,
                "unknown_cost_request_count": len(requests) - len(usd_costs),
            },
        }
        (self.logs_dir / "morrow-partial-metrics.json").write_text(
            json.dumps(context.metadata["morrow_partial_usage"], indent=2), encoding="utf-8"
        )

    def _write_trajectory(self, diagnostic_text: str) -> None:
        requests = _latest_diagnostic_records(diagnostic_text, "model.request", "request_id")
        tools = _latest_diagnostic_records(diagnostic_text, "tool.execution", "execution_id")
        steps = []
        order: dict[tuple[str, str], int] = {}
        events: list[tuple[int, dict]] = []
        for index, line in enumerate(diagnostic_text.splitlines()):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            identifier = item.get("request_id" if kind == "model.request" else "execution_id")
            if isinstance(kind, str) and isinstance(identifier, str):
                order.setdefault((kind, identifier), index)
            if kind == "run.error":
                error = {
                    key: item.get(key)
                    for key in ("error_class", "phase", "correlation_id", "reason_fingerprint")
                }
                if all(value is None or _safe_diagnostic_token(value) for value in error.values()):
                    events.append((index, {"kind": kind, **error}))
            elif kind == "context.compaction" and item.get("boundary") in {
                "compacting",
                "compacted",
            }:
                events.append((index, {"kind": kind, "boundary": item["boundary"]}))
        timeline = [
            (
                order.get((row["kind"], row.get("request_id") or row.get("execution_id")), 10**12),
                row,
            )
            for row in [*requests.values(), *tools.values()]
        ]
        timeline.extend(events)
        timeline.sort(key=lambda pair: pair[0])
        for _, row in timeline:
            kind = row["kind"]
            is_request = kind == "model.request"
            is_tool = kind == "tool.execution"
            call_id = (row.get("call_id") or row["execution_id"]) if is_tool else None
            usage = row.get("usage") or {}
            steps.append(
                Step(
                    step_id=len(steps) + 1,
                    source="agent",
                    timestamp=row.get("admitted_at")
                    if kind == "model.request"
                    else row.get("created_at"),
                    message="[redacted model request]"
                    if is_request
                    else "[redacted tool execution]"
                    if is_tool
                    else "[redacted run diagnostic]",
                    llm_call_count=1 if is_request else 0,
                    metrics=Metrics(
                        prompt_tokens=usage.get("input_tokens"),
                        completion_tokens=usage.get("output_tokens"),
                    )
                    if is_request
                    else None,
                    tool_calls=[
                        ToolCall(
                            tool_call_id=call_id,
                            function_name=row.get("tool_name") or "unknown",
                            arguments={"fingerprint": row.get("argument_fingerprint")},
                            extra={"execution_id": row["execution_id"]},
                        )
                    ]
                    if is_tool
                    else None,
                    observation=Observation(
                        results=[
                            ObservationResult(
                                source_call_id=call_id,
                                content="[redacted tool result]",
                                extra={
                                    "disposition": row.get("disposition"),
                                    "exit_code": row.get("exit_code"),
                                },
                            )
                        ]
                    )
                    if is_tool
                    else None,
                    extra={"morrow_diagnostic": row},
                )
            )
        if not steps:
            return
        trajectory = Trajectory(
            schema_version="ATIF-v1.7",
            agent=Agent(name="morrow", version=self._morrow_version()),
            steps=steps,
            extra={"projection": "redacted journal evidence; no prompts or tool output"},
        )
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory.to_json_dict(), ensure_ascii=False), encoding="utf-8"
        )


def _latest_diagnostic_records(text: str, kind: str, id_field: str) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("kind") != kind:
            continue
        identifier = row.get(id_field)
        if not _safe_diagnostic_token(identifier):
            continue
        if kind == "model.request":
            usage = row.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            cost = row.get("cost") or {}
            if not isinstance(cost, dict):
                cost = {}
            clean = {
                "kind": kind,
                "request_id": identifier,
                "ordinal": _safe_nonnegative_int(row.get("ordinal")),
                "purpose": row.get("purpose")
                if row.get("purpose") in {"agent", "compaction", "outcome_intent"}
                else None,
                "state": row.get("state")
                if row.get("state") in {"admitted", "completed", "failed", "cancelled"}
                else None,
                "usage": {
                    "availability": usage.get("availability")
                    if usage.get("availability") in {"available", "unavailable"}
                    else "unavailable",
                    "input_tokens": _safe_nonnegative_int(usage.get("input_tokens")),
                    "output_tokens": _safe_nonnegative_int(usage.get("output_tokens")),
                },
                "admitted_at": _safe_timestamp(row.get("admitted_at")),
                "settled_at": _safe_timestamp(row.get("settled_at")),
                "compaction_required": row.get("compaction_required")
                if type(row.get("compaction_required")) is bool
                else None,
                "dropped_record_count": _safe_nonnegative_int(row.get("dropped_record_count")),
                "cost": {
                    "availability": cost.get("availability")
                    if cost.get("availability") in {"available", "unavailable"}
                    else "unavailable",
                    "amount_minor": _safe_nonnegative_int(cost.get("amount_minor")),
                    "currency": cost.get("currency")
                    if cost.get("currency") in {"USD", "EUR", "CNY"}
                    else None,
                },
            }
        elif kind == "tool.execution":
            clean = {"kind": kind, "execution_id": identifier}
            clean["created_at"] = _safe_timestamp(row.get("created_at"))
            clean["executing_at"] = _safe_timestamp(row.get("executing_at"))
            clean["closed_at"] = _safe_timestamp(row.get("closed_at"))
            for key in (
                "call_id",
                "tool_name",
                "state",
                "disposition",
                "argument_fingerprint",
                "result_fingerprint",
                "error_code",
                "command_class",
            ):
                clean[key] = row.get(key) if _safe_diagnostic_token(row.get(key)) else None
            cwd = row.get("cwd")
            clean["cwd"] = (
                cwd
                if isinstance(cwd, str)
                and len(cwd) <= 512
                and not cwd.startswith("/")
                and "\x00" not in cwd
                and all(part not in {"", ".."} for part in cwd.split("/"))
                else None
            )
            clean["exit_code"] = _safe_nonnegative_int(row.get("exit_code"))
            clean["duration_ms"] = _safe_nonnegative_int(row.get("duration_ms"))
            clean["artifact_ids"] = (
                [
                    value
                    for value in (row.get("artifact_ids") or [])[:64]
                    if _safe_diagnostic_token(value)
                ]
                if isinstance(row.get("artifact_ids"), list)
                else []
            )
        else:
            continue
        records[identifier] = clean
    return records


def _safe_diagnostic_token(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\-/]{1,128}", value) is not None


def _safe_nonnegative_int(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _safe_timestamp(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _write_temp(text: str) -> Path:
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="morrow-prompt-", delete=False, encoding="utf-8"
    )
    handle.write(text)
    handle.close()
    return Path(handle.name)
