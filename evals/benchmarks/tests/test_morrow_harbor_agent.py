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
        self.diagnostics = ""
        self.run_stdout = "MORROW_EXIT=0\n"
        self.download_error: OSError | None = None
        self.block_download = False
        self.download_started = asyncio.Event()

    async def exec(self, command: str, **_kwargs: object) -> SimpleNamespace:
        self.commands.append(command)
        if "platform.libc_ver()" in command:
            return SimpleNamespace(
                stdout=json.dumps(
                    {"python": "3.12.14", "libc": ["glibc", "2.31"], "architecture": "x86_64"}
                ),
                return_code=0,
            )
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
        self.download_started.set()
        if self.block_download:
            await asyncio.Event().wait()
        if self.download_error:
            raise self.download_error
        target.write_text(
            self.diagnostics if _source.endswith("morrow-diagnostics.jsonl") else self.log,
            encoding="utf-8",
        )


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
        self.assertIn(
            "--diagnostic-log /logs/agent/morrow-diagnostics.jsonl", "\n".join(env.commands)
        )
        self.assertEqual(context.n_input_tokens, 11)
        self.assertEqual(context.n_output_tokens, 7)
        self.assertEqual((self.logs_dir / "morrow-run.jsonl").read_text(), env.log)
        self.assertTrue((self.logs_dir / "morrow-terminal-metrics.json").exists())

    async def test_trial_fingerprint_uses_frozen_run_digests(self) -> None:
        env = FakeEnvironment()
        record = json.loads(_record())
        record["fingerprint"] = {
            "tool_schema_digest": "a" * 64,
            "role_prompt_digest": "b" * 64,
            "unexpected": "private content",
        }
        env.log = json.dumps(record) + "\n"
        await self.agent.run("private prompt", env, AgentContext())
        manifest = json.loads((self.logs_dir / "morrow-fingerprint.json").read_text())
        self.assertEqual(manifest["frozen_agent_run"]["tool_schema_digest"], "a" * 64)
        self.assertEqual(manifest["frozen_agent_run"]["role_prompt_digest"], "b" * 64)
        self.assertNotIn("unexpected", manifest["frozen_agent_run"])
        self.assertNotIn("private prompt", json.dumps(manifest))

    async def test_missing_terminal_uses_committed_diagnostic_fingerprint(self) -> None:
        env = FakeEnvironment()
        env.log = ""
        env.diagnostics = json.dumps(
            {
                "kind": "run.fingerprint",
                "digests": {"tool_schema_digest": "c" * 64, "unexpected": "private"},
            }
        )
        await self.agent.run("private prompt", env, AgentContext())
        manifest = json.loads((self.logs_dir / "morrow-fingerprint.json").read_text())
        self.assertEqual(manifest["frozen_agent_run"], {"tool_schema_digest": "c" * 64})

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

    async def test_cancel_exports_known_usage_and_unknown_exposure(self) -> None:
        env = FakeEnvironment(block_run=True)
        env.log = '{"kind":"agent_event"}\n'
        env.diagnostics = (
            "\n".join(
                json.dumps(row)
                for row in (
                    {
                        "kind": "model.request",
                        "request_id": "mreq_one",
                        "state": "completed",
                        "usage": {
                            "availability": "available",
                            "input_tokens": 13,
                            "output_tokens": 5,
                        },
                    },
                    {
                        "kind": "model.request",
                        "request_id": "mreq_two",
                        "state": "admitted",
                        "usage": {
                            "availability": "unavailable",
                            "input_tokens": None,
                            "output_tokens": None,
                        },
                    },
                )
            )
            + "\n"
        )
        context = AgentContext()
        task = asyncio.create_task(self.agent.run("do work", env, context))
        await env.run_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(context.n_input_tokens, 13)
        self.assertEqual(context.n_output_tokens, 5)
        self.assertEqual(
            context.metadata["morrow_partial_usage"],
            {
                "known_request_count": 1,
                "unknown_request_count": 1,
                "complete": False,
                "known_input_tokens": 13,
                "known_output_tokens": 5,
                "known_usd_cost_minor": None,
                "unknown_cost_request_count": 2,
            },
        )
        self.assertTrue((self.logs_dir / "trajectory.json").exists())

    async def test_diagnostic_tool_projection_contains_only_fingerprints(self) -> None:
        env = FakeEnvironment()
        env.log = ""
        env.diagnostics = (
            json.dumps(
                {
                    "kind": "tool.execution",
                    "execution_id": "tex_one",
                    "tool_name": "bash",
                    "state": "closed",
                    "argument_fingerprint": "opaque123",
                    "result_fingerprint": "opaque456",
                    "exit_code": 1,
                }
            )
            + "\n"
        )
        await self.agent.run("do work", env, AgentContext())
        trajectory = json.loads((self.logs_dir / "trajectory.json").read_text())
        self.assertEqual(trajectory["schema_version"], "ATIF-v1.7")
        self.assertEqual(trajectory["steps"][0]["extra"]["morrow_diagnostic"]["exit_code"], 1)

    async def test_download_failure_does_not_mask_agent_exit(self) -> None:
        env = FakeEnvironment()
        env.download_error = OSError("download failed")
        env.run_stdout = "MORROW_EXIT=1\n"
        with self.assertRaises(NonZeroAgentExitCodeError):
            await self.agent.run("do work", env, AgentContext())

    async def test_container_exit_preserves_partial_request_usage(self) -> None:
        env = FakeEnvironment()
        env.log = ""
        env.run_stdout = "container exited"
        env.diagnostics = json.dumps(
            {
                "kind": "model.request",
                "request_id": "mreq_one",
                "state": "completed",
                "usage": {"availability": "available", "input_tokens": 9, "output_tokens": 2},
            }
        )
        context = AgentContext()
        with self.assertRaises(NonZeroAgentExitCodeError):
            await self.agent.run("do work", env, context)
        self.assertEqual(context.n_input_tokens, 9)
        self.assertEqual(context.metadata["morrow_partial_usage"]["unknown_request_count"], 0)

    async def test_second_cancel_keeps_mounted_diagnostics(self) -> None:
        env = FakeEnvironment(block_run=True)
        env.block_download = True
        mounted = self.logs_dir / "morrow-diagnostics.jsonl"
        mounted.write_text('{"kind":"model.request","request_id":"mreq_one"}\n')
        task = asyncio.create_task(self.agent.run("do work", env, AgentContext()))
        await env.run_started.wait()
        task.cancel()
        await env.download_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIn("mreq_one", mounted.read_text())

    async def test_atif_drops_unrecognized_diagnostic_fields(self) -> None:
        env = FakeEnvironment()
        env.diagnostics = json.dumps(
            {
                "kind": "tool.execution",
                "execution_id": "tex_one",
                "tool_name": "bash",
                "raw_command": "private-token",
                "artifact_ids": [],
            }
        )
        await self.agent.run("do work", env, AgentContext())
        assert "private-token" not in (self.logs_dir / "trajectory.json").read_text()

    async def test_total_only_usage_is_not_misreported_as_input(self) -> None:
        env = FakeEnvironment()
        env.log = json.dumps({"kind": "run.completed", "metrics": {"usage": {"total_tokens": 17}}})
        context = AgentContext()
        await self.agent.run("do work", env, context)
        self.assertIsNone(context.n_input_tokens)
        self.assertIsNone(context.n_output_tokens)
        self.assertEqual(context.metadata["morrow_terminal_total_tokens"], 17)

    async def test_partial_one_sided_request_does_not_invent_output(self) -> None:
        env = FakeEnvironment()
        env.log = ""
        env.diagnostics = json.dumps(
            {
                "kind": "model.request",
                "request_id": "mreq_one",
                "state": "completed",
                "usage": {"availability": "available", "input_tokens": 9, "output_tokens": None},
            }
        )
        context = AgentContext()
        await self.agent.run("do work", env, context)
        self.assertEqual(context.n_input_tokens, 9)
        self.assertIsNone(context.n_output_tokens)
        self.assertIsNone(context.model_usage)
        self.assertFalse(context.metadata["morrow_partial_usage"]["complete"])

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
