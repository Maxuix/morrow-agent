"""Offline regression tests for the Harbor adapter (run with benchmark venv)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.models.agent.context import AgentContext
from harness import morrow_harbor_agent as adapter


def _record() -> str:
    return (
        json.dumps(
            {
                "kind": "run.completed",
                "metrics": {"usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}},
            }
        )
        + "\n"
    )


class FakeEnvironment:
    def __init__(self, workdir: str | None = None, *, block_run: bool = False) -> None:
        self.task_env_config = SimpleNamespace(workdir=workdir)
        self.block_run = block_run
        self.run_started = asyncio.Event()
        self.commands: list[str] = []
        self.uploads: list[str] = []
        self.log = _record()
        self.run_stdout = "MORROW_EXIT=0\n"
        self.download_error: OSError | None = None

    async def exec(self, command: str, **_kwargs: object) -> SimpleNamespace:
        self.commands.append(command)
        if command == "pwd":
            return SimpleNamespace(stdout="/app\n", return_code=0)
        if "morrow run" in command:
            self.run_started.set()
            if self.block_run:
                await asyncio.Event().wait()
            return SimpleNamespace(stdout=self.run_stdout, return_code=0)
        return SimpleNamespace(stdout="", return_code=0)

    async def upload_file(self, _source: Path, target: str) -> None:
        self.uploads.append(target)

    async def upload_dir(self, _source: Path, target: str) -> None:
        self.uploads.append(target)

    async def download_file(self, _source: str, target: Path) -> None:
        if self.download_error:
            raise self.download_error
        target.write_text(self.log, encoding="utf-8")


class MorrowAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs_dir = Path(self.temp.name) / "logs"
        self.logs_dir.mkdir()
        self.agent = adapter.MorrowAgent(
            logs_dir=self.logs_dir,
            provider_base_url="https://example.invalid/v1",
            model_id="model",
            api_model_id="model",
            extra_env={"MORROW_BENCH_API_KEY": "fake"},
        )

    async def test_workspace_uses_task_workdir_or_container_pwd(self) -> None:
        declared = FakeEnvironment("/repo")
        self.assertEqual(await self.agent._workspace_dir(declared), "/repo")
        self.assertEqual(declared.commands, [])
        inherited = FakeEnvironment()
        self.assertEqual(await self.agent._workspace_dir(inherited), "/app")
        self.assertEqual(inherited.commands, ["pwd"])

    async def test_install_keeps_tools_out_of_verifier_path(self) -> None:
        asset_dir = Path(self.temp.name) / "assets"
        (asset_dir / "wheelhouse").mkdir(parents=True)
        for name in (
            "uv-x86_64-unknown-linux-gnu",
            "uvx-x86_64-unknown-linux-gnu",
            "cpython-3.12.14-x86_64-unknown-linux-gnu-install_only.tar.gz",
            "morrow_agent-0.1.0-py3-none-any.whl",
        ):
            (asset_dir / name).touch()
        env = FakeEnvironment()
        with patch.object(adapter, "ASSETS_DIR", asset_dir):
            with patch.object(self.agent, "exec_as_root", new_callable=AsyncMock) as root_exec:
                await self.agent.install(env)
        commands = "\n".join(call.kwargs["command"] for call in root_exec.await_args_list)
        self.assertIn("--workspace /app", commands)
        self.assertNotIn("/usr/local/bin", commands)
        self.assertNotIn("uvx-wrapper", commands)
        self.assertIn("/opt/morrow/tools/uvx", env.uploads)

    async def test_run_copies_log_and_metrics(self) -> None:
        env = FakeEnvironment()
        env.log = "null\n" + _record()
        context = AgentContext()
        await self.agent.run("do work", env, context)
        self.assertIn("PATH=/opt/morrow/tools:$PATH", "\n".join(env.commands))
        self.assertIn("--workspace /app", "\n".join(env.commands))
        self.assertEqual(context.n_input_tokens, 11)
        self.assertEqual(context.n_output_tokens, 7)
        self.assertEqual((self.logs_dir / "morrow-run.jsonl").read_text(), env.log)
        self.assertTrue((self.logs_dir / "morrow-terminal-metrics.json").exists())

    async def test_resolved_harbor_timeout_reaches_morrow_run(self) -> None:
        task_dir = Path(self.temp.name) / "task"
        task_dir.mkdir()
        (task_dir / "task.toml").write_text("[agent]\ntimeout_sec = 100\n", encoding="utf-8")
        (self.logs_dir.parent / "config.json").write_text(
            json.dumps(
                {
                    "task": {"path": str(task_dir)},
                    "agent": {"max_timeout_sec": 80},
                    "agent_timeout_multiplier": 2,
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(adapter._resolved_agent_timeout_seconds(self.logs_dir.parent), 160)
        env = FakeEnvironment()
        await self.agent.run("work", env, AgentContext())
        command = next(item for item in env.commands if "morrow run" in item)
        self.assertIn("--run-timeout-seconds ", command)

    async def test_cancel_recovers_partial_log_and_preserves_cancellation(self) -> None:
        env = FakeEnvironment(block_run=True)
        env.log = '{"kind":"turn.completed"}\n'
        context = AgentContext()
        task = asyncio.create_task(self.agent.run("do work", env, context))
        await env.run_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((self.logs_dir / "morrow-run.jsonl").read_text(), env.log)
        self.assertIsNone(context.n_input_tokens)

    async def test_nonzero_process_exit_is_an_agent_error_after_log_recovery(self) -> None:
        env = FakeEnvironment()
        env.run_stdout = "MORROW_EXIT=1\n"
        context = AgentContext()
        with self.assertRaises(NonZeroAgentExitCodeError):
            await self.agent.run("do work", env, context)
        self.assertEqual((self.logs_dir / "morrow-run.jsonl").read_text(), env.log)
        self.assertEqual(context.n_input_tokens, 11)

    async def test_missing_log_does_not_mask_cancellation(self) -> None:
        env = FakeEnvironment(block_run=True)
        env.download_error = FileNotFoundError("no log")
        task = asyncio.create_task(self.agent.run("do work", env, AgentContext()))
        await env.run_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
