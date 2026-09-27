"""Driver options must reach the installed agent rather than only job metadata."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_tb2

from morrow.core.runtime_policy import (
    AGENT_MAX_CONTEXT_WINDOW_TOKENS,
    AGENT_MAX_RESERVE_TOKENS,
    PI_DEFAULT_RESERVE_TOKENS,
)


class TerminalBenchDriverTests(unittest.TestCase):
    def test_capacity_below_morrow_reserve_is_rejected_before_launch(self) -> None:
        self.assertEqual(run_tb2.MORROW_DEFAULT_RESERVE_TOKENS, PI_DEFAULT_RESERVE_TOKENS)
        self.assertEqual(run_tb2.MORROW_MAX_CONTEXT_TOKENS, AGENT_MAX_CONTEXT_WINDOW_TOKENS)
        self.assertEqual(run_tb2.MORROW_MAX_OUTPUT_TOKENS, AGENT_MAX_RESERVE_TOKENS)
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "MORROW_BENCH_API_KEY": "fake",
                "MORROW_BENCH_PROVIDER_BASE_URL": "https://example.invalid/v1",
                "MORROW_BENCH_MODEL_ID": "model",
                "MORROW_BENCH_API_MODEL_ID": "model",
                "MORROW_BENCH_CONTEXT_WINDOW_TOKENS": "10000",
                "MORROW_BENCH_MAX_OUTPUT_TOKENS": "1000",
            }
            with (
                patch.object(run_tb2, "RUNS_DIR", Path(directory)),
                patch.object(run_tb2, "_load_dotenv", return_value=config),
                patch.dict(run_tb2.os.environ, {}, clear=True),
                patch.object(run_tb2, "_all_tasks", return_value=["demo"]),
                patch.object(run_tb2, "_preflight") as preflight,
                patch.object(run_tb2.subprocess, "run") as launch,
                patch("sys.argv", ["run_tb2.py", "--tasks", "demo"]),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(run_tb2.main(), 2)
            preflight.assert_not_called()
            launch.assert_not_called()

    def test_insufficient_budget_does_not_launch_any_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = run_tb2.TokenBudget(
                root / "budget-ledger.json", budget_total=100, reservation=60
            )
            self.assertTrue(budget.admit("earlier"))
            config = {
                "MORROW_BENCH_API_KEY": "fake",
                "MORROW_BENCH_PROVIDER_BASE_URL": "https://example.invalid/v1",
                "MORROW_BENCH_MODEL_ID": "model",
                "MORROW_BENCH_API_MODEL_ID": "model",
                "MORROW_BENCH_CONTEXT_WINDOW_TOKENS": "65536",
                "MORROW_BENCH_MAX_OUTPUT_TOKENS": "1000",
            }
            with (
                patch.object(run_tb2, "RUNS_DIR", root),
                patch.object(run_tb2, "_load_dotenv", return_value=config),
                patch.object(run_tb2, "_all_tasks", return_value=["demo"]),
                patch.object(run_tb2, "_preflight"),
                patch.object(run_tb2, "run_fingerprint", return_value={"schema_version": 1}),
                patch.object(run_tb2.subprocess, "run") as launch,
                patch(
                    "sys.argv",
                    [
                        "run_tb2.py",
                        "--tasks",
                        "demo",
                        "--budget-total",
                        "100",
                        "--reservation",
                        "60",
                    ],
                ),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(run_tb2.main(), 1)
            launch.assert_not_called()
            self.assertEqual(budget.summary()["admitted"], 1)

    def test_dry_run_validates_tasks_without_writing_budget_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(run_tb2, "RUNS_DIR", root),
                patch.object(run_tb2, "_all_tasks", return_value=["demo"]),
                patch.object(run_tb2, "_load_dotenv", return_value={}),
                patch("sys.argv", ["run_tb2.py", "--tasks", "demo", "--dry-run"]),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(run_tb2.main(), 0)
            self.assertEqual(list(root.iterdir()), [])

    def test_preflight_rejects_missing_task_before_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bench = Path(directory)
            assets = bench / "assets"
            (assets / "wheelhouse").mkdir(parents=True)
            (assets / "uv-x86_64-unknown-linux-gnu").touch()
            (assets / "morrow_agent-1-py3-none-any.whl").touch()
            (assets / "cpython-3.12.14-x86_64-unknown-linux-gnu-install_only.tar.gz").touch()
            harbor = bench / "harbor"
            harbor.touch()
            with (
                patch.object(run_tb2, "BENCH_DIR", bench),
                patch.object(run_tb2, "HARBOR_BIN", harbor),
                patch.object(run_tb2, "VENDOR_TB2", bench / "tasks"),
            ):
                with self.assertRaises(FileNotFoundError):
                    run_tb2._preflight(["missing"])

    def test_finalizes_usage_after_non_object_jsonl_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = run_tb2.TokenBudget(
                root / "ledger.json", budget_total=5_000_000, reservation=1_000_000
            )
            self.assertTrue(budget.admit("tb2:high:job:demo"))
            job_dir = root / "job"
            agent_dir = job_dir / "demo__123" / "agent"
            agent_dir.mkdir(parents=True)
            record = {
                "kind": "run.completed",
                "metrics": {"usage": {"availability": "available", "total_tokens": 2_000_000}},
            }
            (agent_dir / "morrow-run.jsonl").write_text(
                "null\n" + json.dumps(record) + "\n", encoding="utf-8"
            )
            self.assertEqual(run_tb2._finalize_from_job_logs(budget, "tb2:high:job", job_dir), 1)
            self.assertEqual(budget.used_tokens, 2_000_000)

    def test_reasoning_effort_reaches_harbor_agent_kwargs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            captured: list[list[str]] = []

            def run(command, *, env):
                captured.append(command)
                return SimpleNamespace(returncode=0)

            credentials = {
                "MORROW_BENCH_API_KEY": "fake",
                "MORROW_BENCH_PROVIDER_BASE_URL": "https://example.invalid/v1",
                "MORROW_BENCH_MODEL_ID": "model",
                "MORROW_BENCH_API_MODEL_ID": "model",
                "MORROW_BENCH_CONTEXT_WINDOW_TOKENS": "65536",
                "MORROW_BENCH_MAX_OUTPUT_TOKENS": "1000",
            }
            with (
                patch.object(run_tb2, "RUNS_DIR", Path(directory)),
                patch.object(run_tb2, "_load_dotenv", return_value=credentials),
                patch.object(run_tb2, "_all_tasks", return_value=["demo"]),
                patch.object(run_tb2, "_preflight"),
                patch.object(run_tb2, "run_fingerprint", return_value={"schema_version": 1}),
                patch.object(run_tb2, "_finalize_from_job_logs", return_value=0),
                patch.object(run_tb2.subprocess, "run", side_effect=run),
                patch("sys.argv", ["run_tb2.py", "--tasks", "demo", "--reasoning-effort", "high"]),
            ):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(run_tb2.main(), 0)
        self.assertEqual(len(captured), 1)
        command = captured[0]
        self.assertIn("reasoning_effort=high", command)
        self.assertEqual(command[command.index("reasoning_effort=high") - 1], "--ak")
        self.assertEqual(command[command.index("--n-attempts") + 1], "1")
        self.assertEqual(command[command.index("--max-retries") + 1], "0")
        self.assertIn("context_window_tokens=65536", command)
        self.assertIn("max_output_tokens=1000", command)

    def test_finalizes_actual_harbor_log_layout_and_partial_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            budget = run_tb2.TokenBudget(root / "ledger.json", budget_total=100, reservation=60)
            self.assertTrue(budget.admit("tb2:run:demo"))
            logs = root / "job" / "trials" / "demo__123" / "agent" / "logs"
            logs.mkdir(parents=True)
            (logs / "morrow-partial-metrics.json").write_text(
                json.dumps({"known_input_tokens": 70, "unknown_request_count": 1})
            )
            self.assertEqual(run_tb2._finalize_from_job_logs(budget, "tb2:run", root / "job"), 1)
            self.assertEqual(budget.used_tokens, 70)
            self.assertEqual(budget.summary()["unknown_exposure_count"], 1)
