"""Official rewards, trial identity and incomplete evidence remain independent."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_metrics


class CollectorV2Tests(unittest.TestCase):
    def _trial(
        self,
        job: Path,
        name: str,
        *,
        reward: float | None = 1,
        exception: bool = False,
        fingerprint: dict | None = None,
        trial_id: str | None = None,
    ) -> None:
        trial = job / f"{name}__{trial_id or 'one'}"
        logs = trial / "agent" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "id": trial_id or name,
                    "trial_name": trial.name,
                    "task_name": f"terminal-bench/{name}",
                    "finished_at": "2026-09-27T00:00:00Z",
                    "verifier_result": {"rewards": {"reward": reward}}
                    if reward is not None
                    else None,
                    "exception_info": {"exception_type": "AgentTimeoutError"}
                    if exception
                    else None,
                }
            )
        )
        if fingerprint is not None:
            (logs / "morrow-fingerprint.json").write_text(json.dumps(fingerprint))

    def test_job_summary_is_skipped_and_exception_does_not_erase_reward(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "job"
            self._trial(job, "demo", exception=True)
            (job / "result.json").write_text(json.dumps({"id": "job-id", "n_total_trials": 1}))
            with patch.object(collect_metrics, "_task_meta", return_value={}):
                report = collect_metrics.collect_tb2(job)
            self.assertEqual(report["n_tasks"], 1)
            self.assertEqual(report["resolution_rate"], 1)
            self.assertEqual(report["tasks"][0]["agent_status"], "exception")
            self.assertEqual(report["tasks"][0]["exception"], "AgentTimeoutError")
            self.assertEqual(report["failure_taxonomy"], {"agent_deadline": 1})
            self.assertIsNone(report["cost_usd_total"])
            self.assertEqual(report["cost_coverage"], {"known": 0, "expected": 1})

    def test_current_harbor_agent_log_layout_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory) / "job"
            self._trial(job, "demo")
            trial = next(job.glob("*/result.json")).parent
            direct = trial / "agent" / "morrow-terminal-metrics.json"
            direct.write_text(
                json.dumps(
                    {
                        "usage": {
                            "availability": "available",
                            "input_tokens": 10,
                            "output_tokens": 3,
                            "total_tokens": 13,
                        },
                        "cost": {"availability": "unavailable"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(collect_metrics, "_task_meta", return_value={}):
                report = collect_metrics.collect_tb2(job)
            self.assertEqual(report["tasks"][0]["total_tokens"], 13)
            self.assertEqual(report["tasks"][0]["terminal_metrics_file"], str(direct))
            self.assertIsNone(report["cost_usd_total"])

    def test_harbor_nonzero_agent_exit_is_classified(self) -> None:
        self.assertEqual(
            collect_metrics._classify_failure(
                {
                    "exception_type": "NonZeroAgentExitCodeError",
                    "exception_message": "morrow run exited with code 2",
                }
            ),
            "agent_exit",
        )

    def test_full_requires_exact_tasks_reward_and_matching_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "v2"
            config.mkdir(parents=True)
            (config / "protocol.json").write_text(
                json.dumps(
                    {
                        "benchmark": {
                            "task_checksums": {"a": "hash-a", "b": "hash-b"},
                            "harbor_commit": "harbor",
                            "dataset_commit": "dataset",
                        }
                    }
                )
            )
            job = root / "job-full"
            settings = {"run_id": "run", "tasks": {"a": "hash-a", "b": "hash-b"}}
            campaign = {"wheel": {"sha256": "wheel"}, "settings": settings}
            fingerprint = {
                "campaign": campaign,
                "task_checksum_sha256": "hash-a",
                "installed_wheel_sha256": "wheel",
            }
            self._trial(job, "a", fingerprint=fingerprint)
            with (
                patch.object(collect_metrics, "BENCH_DIR", root),
                patch.object(collect_metrics, "_task_meta", return_value={}),
            ):
                report = collect_metrics.collect_tb2(job)
            self.assertEqual(report["report_kind"], "incomplete_full")
            self.assertIsNone(report["resolution_rate"])
            self.assertEqual(report["missing_tasks"], ["b"])
            self.assertEqual(len(report["task_table"]), 2)
            self.assertEqual(report["operational_lower_bound"], 0.5)

            self._trial(job, "a", fingerprint=fingerprint, trial_id="again")
            with (
                patch.object(collect_metrics, "BENCH_DIR", root),
                patch.object(collect_metrics, "_task_meta", return_value={}),
            ):
                repeated = collect_metrics.collect_tb2(job)
            self.assertEqual(repeated["duplicate_tasks"], ["a"])
            self.assertEqual(repeated["report_kind"], "incomplete_full")

    def test_matching_full_can_score_but_model_mismatch_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "v2"
            config.mkdir(parents=True)
            (config / "protocol.json").write_text(
                json.dumps(
                    {
                        "benchmark": {
                            "task_checksums": {"a": "hash-a", "b": "hash-b"},
                            "harbor_commit": "harbor",
                            "dataset_commit": "dataset",
                        }
                    }
                )
            )
            settings = {
                "run_id": "run",
                "tasks": {"a": "hash-a", "b": "hash-b"},
                "provider_adapter": "openai-compatible",
                "provider_id": "bench",
                "provider_base_url_sha256": "url",
                "model_id": "model",
                "api_model_id": "model",
                "reasoning_effort": "high",
                "context_window_tokens": 10000,
                "max_output_tokens": 1000,
                "agent_timeout_multiplier": 1.0,
                "permission_mode": "manual",
                "attempts_per_task": 1,
                "harbor_max_retries": 0,
            }
            campaign = {
                "source": {"commit": "source"},
                "wheel": {"sha256": "wheel"},
                "assets": {"build_manifest_sha256": "assets"},
                "harbor": {"commit": "harbor"},
                "dataset": {"commit": "dataset"},
                "settings": settings,
            }
            settings["job_name"] = "job-full"

            def fingerprint(name: str) -> dict:
                return {
                    "campaign": campaign,
                    "task_checksum_sha256": f"hash-{name}",
                    "installed_wheel_sha256": "wheel",
                    **{
                        field: settings[field]
                        for field in (
                            "provider_adapter",
                            "provider_id",
                            "provider_base_url_sha256",
                            "model_id",
                            "api_model_id",
                            "reasoning_effort",
                            "context_window_tokens",
                            "max_output_tokens",
                            "permission_mode",
                        )
                    },
                }

            job = root / "job-full"
            manifests = root / "runs" / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "run.json").write_text(json.dumps(campaign))
            (root / "runs" / "budget-ledger.json").write_text(
                json.dumps(
                    {
                        "entries": [
                            {"run_key": "tb2:run:a", "status": "admitted"},
                            {"run_key": "tb2:run:b", "status": "admitted"},
                        ]
                    }
                )
            )
            with (
                patch.object(collect_metrics, "BENCH_DIR", root),
                patch.object(collect_metrics, "_task_meta", return_value={}),
            ):
                empty = collect_metrics.collect_tb2(job)
            self.assertEqual(empty["report_kind"], "incomplete_full")
            self.assertEqual(len(empty["task_table"]), 2)
            self.assertTrue(empty["task_table"][0]["admitted"])
            self.assertFalse(empty["task_table"][0]["started"])
            self._trial(job, "a", fingerprint=fingerprint("a"))
            self._trial(job, "b", reward=0.5, fingerprint=fingerprint("b"))
            with (
                patch.object(collect_metrics, "BENCH_DIR", root),
                patch.object(collect_metrics, "_task_meta", return_value={}),
            ):
                complete = collect_metrics.collect_tb2(job)
            self.assertEqual(complete["report_kind"], "fixed_version_full")
            self.assertEqual(complete["resolution_rate"], 0.75)
            broken = fingerprint("b")
            broken["model_id"] = "other"
            self._trial(job, "b", reward=0, fingerprint=broken)
            with (
                patch.object(collect_metrics, "BENCH_DIR", root),
                patch.object(collect_metrics, "_task_meta", return_value={}),
            ):
                mixed = collect_metrics.collect_tb2(job)
            self.assertEqual(mixed["report_kind"], "incomplete_full")
            self.assertIsNone(mixed["resolution_rate"])
            self._trial(job, "b", reward=None, fingerprint=fingerprint("b"))
            with (
                patch.object(collect_metrics, "BENCH_DIR", root),
                patch.object(collect_metrics, "_task_meta", return_value={}),
            ):
                missing_reward = collect_metrics.collect_tb2(job)
            self.assertEqual(missing_reward["outcome_coverage"], {"known": 1, "expected": 2})
            self.assertEqual(missing_reward["report_kind"], "incomplete_full")


if __name__ == "__main__":
    unittest.main()
