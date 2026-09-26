"""Aggregate benchmark run artifacts into the required metrics report.

Inputs
------
* Terminal-Bench 2.0: ``runs/jobs/<job>/trials/**/result.json`` (official Harbor
  trial results, verifier rewards untouched) + per-trial terminal and partial
  metrics for Morrow-internal counters and incomplete usage coverage.
* SWE-bench Lite: official ``run_evaluation`` report under ``results/`` plus
  per-instance runner records ``runs/swebench-lite/instances-*.jsonl``.

Output
------
* ``results/<name>-metrics.json`` with resolution rates, per-category and
  per-difficulty breakdowns, p50/p95 durations, token totals, tool/model
  counters, cost figures, and a failure taxonomy.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
TB2_DIR = BENCH_DIR / "vendor" / "terminal-bench-2"
RUNS_DIR = BENCH_DIR / "runs"
RESULTS_DIR = BENCH_DIR / "results"


def _p50_p95(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None}
    ordered = sorted(values)

    def pct(p: float) -> float:
        idx = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
        return ordered[idx]

    return {"p50": pct(50), "p95": pct(95)}


def _task_meta() -> dict[str, dict]:
    meta = {}
    for task_dir in sorted(TB2_DIR.iterdir()):
        toml = task_dir / "task.toml"
        if toml.exists():
            data = tomllib.loads(toml.read_text(encoding="utf-8"))
            meta[task_dir.name] = {
                "difficulty": data["metadata"].get("difficulty") or "unknown",
                "category": data["metadata"].get("category") or "unknown",
            }
    return meta


def collect_tb2(jobs_dir: Path) -> dict:
    meta = _task_meta()
    tasks: list[dict] = []
    for result_path in sorted(jobs_dir.rglob("result.json")):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task_name = str(result.get("task_name", ""))
        short = task_name.split("/")[-1]
        verifier = result.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        reward = rewards.get("reward", rewards.get("verify")) if rewards else None
        agent_ctx = result.get("agent_result") or {}
        timing = result.get("agent_execution") or {}
        exception = result.get("exception_info") or {}

        metrics_file = result_path.parent / "agent" / "logs" / "morrow-terminal-metrics.json"
        partial_file = result_path.parent / "agent" / "logs" / "morrow-partial-metrics.json"
        fingerprint_file = result_path.parent / "agent" / "logs" / "morrow-fingerprint.json"
        fingerprint = None
        if fingerprint_file.is_file():
            try:
                fingerprint = json.loads(fingerprint_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        morrow_metrics: dict = {}
        if metrics_file.exists():
            try:
                morrow_metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                morrow_metrics = {}
        partial_usage = (agent_ctx.get("metadata") or {}).get("morrow_partial_usage")
        if not isinstance(partial_usage, dict) and partial_file.exists():
            try:
                partial_usage = json.loads(partial_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                partial_usage = None
        if not isinstance(partial_usage, dict):
            partial_usage = None

        status = "resolved" if reward == 1 else "unresolved"
        failure_kind = None
        if exception:
            status = "exception"
            failure_kind = _classify_failure(exception)
        elif reward == 0:
            status = "failed"
            failure_kind = "model_failure"

        duration = _duration_sec(timing)
        usage = morrow_metrics.get("usage") or {}
        tasks.append(
            {
                "task": short,
                "difficulty": (meta.get(short) or {}).get("difficulty", "unknown"),
                "category": (meta.get(short) or {}).get("category", "unknown"),
                "status": status,
                "failure_kind": failure_kind,
                "reward": reward,
                "duration_sec": duration,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "tool_calls": morrow_metrics.get("tool_calls"),
                "tool_rounds": morrow_metrics.get("tool_rounds"),
                "model_attempts": morrow_metrics.get("model_attempts"),
                "retry_count": morrow_metrics.get("retry_count"),
                "context_compactions": (morrow_metrics.get("dropped_cycle_count") or 0)
                + (morrow_metrics.get("cleared_cycle_count") or 0),
                "cost_usd": _cost_usd(morrow_metrics.get("cost") or {}),
                "exception": (exception.get("type") or "")[:120] or None,
                "agent_input_tokens": agent_ctx.get("n_input_tokens"),
                "agent_output_tokens": agent_ctx.get("n_output_tokens"),
                "agent_cost_usd": agent_ctx.get("cost_usd"),
                "partial_usage": partial_usage,
                "run_id": (((fingerprint or {}).get("campaign") or {}).get("settings") or {}).get(
                    "run_id"
                ),
                "fingerprint_file": str(fingerprint_file) if fingerprint is not None else None,
            }
        )

    resolved = [t for t in tasks if t["status"] == "resolved"]
    n = len(tasks)
    one_job = (jobs_dir / "trials").is_dir()
    complete_fingerprints = all(task["fingerprint_file"] is not None for task in tasks)
    unique_tasks = len({task["task"] for task in tasks}) == n
    report_kind = (
        "fixed_version_full"
        if one_job
        and n == 89
        and unique_tasks
        and complete_fingerprints
        and len({task["run_id"] for task in tasks}) == 1
        and tasks[0]["run_id"] is not None
        else "diagnostic_subset"
        if one_job
        else "mixed_campaign"
    )
    report = {
        "benchmark": "Terminal-Bench 2.0",
        "report_kind": report_kind,
        "fingerprinted_trials": sum(t["fingerprint_file"] is not None for t in tasks),
        "run_ids": sorted({t["run_id"] for t in tasks if t["run_id"]}),
        "n_tasks": n,
        "resolution_rate": (len(resolved) / n) if n else None,
        "by_difficulty": _breakdown(tasks, "difficulty"),
        "by_category": _breakdown(tasks, "category"),
        "duration_sec": _p50_p95([t["duration_sec"] for t in tasks if t["duration_sec"]]),
        "tokens": _token_totals(tasks),
        "partial_usage": {
            "tasks_with_partial_evidence": sum(t["partial_usage"] is not None for t in tasks),
            "known_input_tokens": sum(
                (t["partial_usage"] or {}).get("known_input_tokens") or 0 for t in tasks
            ),
            "known_output_tokens": sum(
                (t["partial_usage"] or {}).get("known_output_tokens") or 0 for t in tasks
            ),
            "unknown_request_count": sum(
                (t["partial_usage"] or {}).get("unknown_request_count") or 0 for t in tasks
            ),
        },
        "tool_calls_total": sum(t["tool_calls"] or 0 for t in tasks),
        "model_requests_total": sum(t["model_attempts"] or 0 for t in tasks),
        "retry_total": sum(t["retry_count"] or 0 for t in tasks),
        "context_compaction_total": sum(t["context_compactions"] or 0 for t in tasks),
        "cost_usd_total": sum(t["cost_usd"] or 0 for t in tasks),
        "cost_per_task": (sum(t["cost_usd"] or 0 for t in tasks) / n) if n else None,
        "cost_per_resolved": (sum(t["cost_usd"] or 0 for t in tasks) / len(resolved))
        if resolved
        else None,
        "failure_taxonomy": _failure_counts(tasks),
        "tasks": tasks,
    }
    return report


def collect_swebench(results_dir: Path, runs_dir: Path) -> dict:
    """Read the official run_evaluation report + per-instance Morrow usage."""
    report_files = sorted(results_dir.rglob("results.json")) + sorted(
        results_dir.rglob("*report*.json")
    )
    official: dict = {}
    for path in report_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and (
            "resolved" in data or "no_generation" in data or "no_logs" in data
        ):
            official = {"path": str(path), **data}
            break

    instances: list[dict] = []
    for path in sorted(runs_dir.glob("instances-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                instances.append(json.loads(line))

    resolved_ids = set()
    per_instance_eval = sorted(results_dir.rglob("*results*.jsonl"))
    eval_records = []
    for path in per_instance_eval:
        try:
            eval_records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            break
        except (OSError, json.JSONDecodeError):
            continue
    for rec in eval_records:
        if rec.get("resolved"):
            resolved_ids.add(rec.get("instance_id"))

    n = len(instances)
    n_resolved = len(resolved_ids & {i["instance_id"] for i in instances}) if instances else None
    by_repo: dict[str, dict] = {}
    for inst in instances:
        repo = inst["instance_id"].split("__")[0]
        bucket = by_repo.setdefault(repo, {"n": 0, "resolved": 0})
        bucket["n"] += 1
        if inst["instance_id"] in resolved_ids:
            bucket["resolved"] += 1

    patches = [i for i in instances if i.get("status") == "patched"]
    durations = [i["duration_sec"] for i in instances if i.get("duration_sec")]
    tokens = [i for i in instances if (i.get("usage") or {}).get("total_tokens")]
    return {
        "benchmark": "SWE-bench Lite",
        "n_instances": n,
        "n_resolved": n_resolved,
        "percent_resolved": (n_resolved / n * 100) if (n and n_resolved is not None) else None,
        "official_report": official or None,
        "by_repo": {
            repo: {"n": b["n"], "resolved": b["resolved"], "rate": b["resolved"] / b["n"]}
            for repo, b in sorted(by_repo.items())
        },
        "duration_sec": _p50_p95(durations),
        "tokens": {
            "total": sum((i.get("usage") or {}).get("total_tokens") or 0 for i in tokens),
            "p50_per_instance": _p50_p95(
                [(i.get("usage") or {}).get("total_tokens") or 0 for i in tokens]
            )["p50"],
        },
        "patch_stats": {
            "patched": len(patches),
            "empty_patch": sum(1 for i in instances if i.get("status") == "empty-patch"),
            "errors": sum(1 for i in instances if i.get("status") == "error"),
        },
        "instances": instances,
    }


def _duration_sec(timing: dict) -> float | None:
    start, end = timing.get("started_at"), timing.get("finished_at")
    if not start or not end:
        return None
    try:
        from datetime import datetime

        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        return (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds()
    except (TypeError, ValueError):
        return None


def _cost_usd(cost: dict) -> float | None:
    if cost.get("availability") == "available" and cost.get("amount_minor") is not None:
        if cost.get("currency") == "USD":
            return cost["amount_minor"] / 100.0
    return None


def _classify_failure(exception: dict) -> str:
    text = f"{exception.get('type', '')} {exception.get('message', '')}".lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "rate" in text or "usage limit" in text or "quota" in text:
        return "budget_exhausted"
    if "docker" in text or "image" in text or "environment" in text:
        return "environment_failure"
    if "tool" in text:
        return "tool_failure"
    return "model_failure"


def _breakdown(tasks: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for task in tasks:
        groups.setdefault(task[key], []).append(task)
    return {
        name: {
            "n": len(items),
            "resolved": sum(1 for t in items if t["status"] == "resolved"),
            "rate": sum(1 for t in items if t["status"] == "resolved") / len(items)
            if items
            else None,
        }
        for name, items in sorted(groups.items())
    }


def _token_totals(tasks: list[dict]) -> dict:
    def total(field: str) -> int:
        return sum(t[field] or 0 for t in tasks)

    values = [t["total_tokens"] for t in tasks if t["total_tokens"] is not None]
    return {
        "input": total("input_tokens"),
        "output": total("output_tokens"),
        "total": total("total_tokens"),
        "p50_per_task": _p50_p95(values)["p50"],
        "p95_per_task": _p50_p95(values)["p95"],
    }


def _failure_counts(tasks: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for task in tasks:
        if task["status"] in ("failed", "exception"):
            kind = task["failure_kind"] or "unknown"
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tb2-jobs", type=Path, default=RUNS_DIR / "jobs")
    parser.add_argument("--swebench-results", type=Path, default=RESULTS_DIR)
    parser.add_argument("--swebench-runs", type=Path, default=RUNS_DIR / "swebench-lite")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    combined = {"tb2": None, "swebench_lite": None}

    if args.tb2_jobs.exists():
        combined["tb2"] = collect_tb2(args.tb2_jobs)
    if args.swebench_runs.exists():
        combined["swebench_lite"] = collect_swebench(args.swebench_results, args.swebench_runs)

    output = args.output or (RESULTS_DIR / "metrics.json")
    output.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {output}")
    for name, report in combined.items():
        if not report:
            continue
        if name == "tb2":
            print(
                f"[TB2] n={report['n_tasks']} resolution={report['resolution_rate']:.3f}"
                if report["resolution_rate"] is not None
                else f"[TB2] n={report['n_tasks']}"
            )
        else:
            pct = report["percent_resolved"]
            print(
                f"[SWE-bench Lite] n={report['n_instances']} resolved={report['n_resolved']} ({pct:.1f}%)"
                if pct is not None
                else f"[SWE-bench Lite] n={report['n_instances']} (no official report yet)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
