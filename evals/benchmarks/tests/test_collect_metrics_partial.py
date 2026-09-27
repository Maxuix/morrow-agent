"""Partial request usage stays separate from complete terminal totals."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collect_metrics


class PartialMetricsTests(unittest.TestCase):
    def test_missing_terminal_keeps_known_lower_bound_and_unknown_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory)
            trial = jobs / "job" / "trials" / "sample__one"
            logs = trial / "agent" / "logs"
            logs.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "id": "trial-one",
                        "trial_name": "sample__one",
                        "task_name": "sample",
                        "agent_result": {
                            "n_input_tokens": 13,
                            "metadata": {
                                "morrow_partial_usage": {
                                    "known_input_tokens": 13,
                                    "known_output_tokens": 5,
                                    "unknown_request_count": 1,
                                    "complete": False,
                                }
                            },
                        },
                        "verifier_result": {"rewards": {"reward": 0}},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(collect_metrics, "_task_meta", return_value={}):
                report = collect_metrics.collect_tb2(jobs)
            self.assertIsNone(report["tasks"][0]["input_tokens"])
            self.assertEqual(report["tasks"][0]["partial_usage"]["known_input_tokens"], 13)
            self.assertEqual(report["partial_usage"]["unknown_request_count"], 1)
            self.assertEqual(report["partial_usage"]["known_input_tokens"], 13)
            self.assertEqual(report["report_kind"], "mixed_campaign")
            with patch.object(collect_metrics, "_task_meta", return_value={}):
                single_job = collect_metrics.collect_tb2(jobs / "job")
            self.assertEqual(single_job["report_kind"], "diagnostic_subset")
