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


class TerminalBenchDriverTests(unittest.TestCase):
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
            }
            with (
                patch.object(run_tb2, "RUNS_DIR", Path(directory)),
                patch.object(run_tb2, "_load_dotenv", return_value=credentials),
                patch.object(run_tb2, "_all_tasks", return_value=["demo"]),
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
