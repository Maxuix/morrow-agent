"""Budget admission and reproducibility evidence for benchmark reruns."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from harness.budget import TokenBudget
from harness.fingerprint import git_fingerprint, run_fingerprint, sha256_tree


def _admit(path: str, run_key: str) -> bool:
    return TokenBudget(Path(path), budget_total=100, reservation=60).admit(run_key)


class BudgetFingerprintTests(unittest.TestCase):
    def test_preview_is_read_only_and_batch_admission_is_all_or_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            self.assertEqual(
                TokenBudget.preview(path, budget_total=100, reservation=60)["remaining"], 100
            )
            self.assertEqual(list(Path(directory).iterdir()), [])
            budget = TokenBudget(path, budget_total=100, reservation=60)
            self.assertFalse(budget.admit_many(["a", "b"]))
            self.assertEqual(budget.summary()["admitted"], 0)
            self.assertTrue(budget.admit_many(["a"]))
            self.assertTrue(budget.admit_many(["a"]))
            self.assertFalse(budget.admit_many(["a", "b"]))
            self.assertEqual(budget.summary()["admitted"], 1)

    def test_parallel_drivers_cannot_oversubscribe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            with ProcessPoolExecutor(max_workers=4) as pool:
                admitted = list(pool.map(_admit, [str(path)] * 8, [f"run:{i}" for i in range(8)]))
            self.assertEqual(sum(admitted), 1)
            budget = TokenBudget(path, budget_total=100, reservation=60)
            self.assertEqual(budget.summary()["used_tokens"], 60)
            self.assertEqual(budget.summary()["refused"], 7)

    def test_new_run_key_and_unknown_usage_keep_exposure_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            budget = TokenBudget(Path(directory) / "ledger.json", budget_total=100, reservation=60)
            self.assertTrue(budget.admit("run-1:task"))
            budget.finalize("run-1:task", usage=None)
            self.assertFalse(budget.admit("run-2:task"))
            self.assertEqual(budget.summary()["unknown_exposure_count"], 1)
            self.assertEqual(budget.summary()["known_actual_tokens"], 0)

    def test_partial_usage_is_lower_bound_and_complete_usage_is_actual(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            budget = TokenBudget(Path(directory) / "ledger.json", budget_total=100, reservation=60)
            self.assertTrue(budget.admit("partial"))
            budget.finalize("partial", usage={"availability": "partial", "input_tokens": 70})
            self.assertEqual(budget.used_tokens, 70)
            self.assertEqual(budget.summary()["known_actual_tokens"], 70)
            self.assertEqual(budget.summary()["unknown_exposure_count"], 1)
            self.assertTrue(budget.admit("complete", reservation=20))
            budget.finalize("complete", usage={"availability": "available", "total_tokens": 5})
            self.assertEqual(budget.used_tokens, 75)
            self.assertEqual(budget.summary()["unknown_exposure_count"], 1)

    def test_resumed_trial_can_replace_unknown_reservation_with_actual(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            first = TokenBudget(path, budget_total=100, reservation=60)
            self.assertTrue(first.admit("same-run:task"))
            first.finalize("same-run:task", usage=None)
            resumed = TokenBudget(path, budget_total=100, reservation=60)
            self.assertTrue(resumed.admit("same-run:task"))
            resumed.finalize(
                "same-run:task", usage={"availability": "available", "total_tokens": 20}
            )
            self.assertEqual(resumed.summary()["used_tokens"], 20)
            self.assertEqual(resumed.summary()["unknown_exposure_count"], 0)

    def test_fingerprint_changes_with_task_or_vendor_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bench = root / "evals" / "benchmarks"
            (bench / "assets").mkdir(parents=True)
            (bench / "vendor" / "harbor").mkdir(parents=True)
            (bench / "assets" / "morrow_agent-1-py3-none-any.whl").write_bytes(b"wheel")
            (root / "uv.lock").write_bytes(b"lock")
            task = bench / "task"
            task.mkdir()
            (task / "task.toml").write_text("a")
            first = sha256_tree(task)
            (task / "task.toml").write_text("b")
            self.assertNotEqual(first, sha256_tree(task))
            manifest = run_fingerprint(bench, settings={"run_id": "one"})
            self.assertEqual(manifest["settings"]["run_id"], "one")
            self.assertIsNotNone(manifest["wheel"]["sha256"])
            self.assertIsNone(git_fingerprint(bench / "vendor" / "harbor")["commit"])
            self.assertEqual(json.loads(json.dumps(manifest)), manifest)
            harbor = bench / "vendor" / "harbor"
            subprocess.run(["git", "init", "-q", str(harbor)], check=True)
            (harbor / "adapter.py").write_text("original\n")
            subprocess.run(["git", "-C", str(harbor), "add", "adapter.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(harbor),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "base",
                ],
                check=True,
            )
            baseline = git_fingerprint(harbor)
            self.assertIsNone(baseline["dirty_patch_sha256"])
            (harbor / "adapter.py").write_text("patched\n")
            patched = git_fingerprint(harbor)
            self.assertEqual(patched["commit"], baseline["commit"])
            self.assertIsNotNone(patched["dirty_patch_sha256"])


if __name__ == "__main__":
    unittest.main()
