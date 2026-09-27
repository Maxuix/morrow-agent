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
import math
import tomllib
from collections import Counter
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
        # Harbor writes a second result.json at the job root. A trial has a
        # named directory and an official trial id; never count job summaries.
        if result_path.parent == jobs_dir or result_path.parent.name in {"trials", "jobs"}:
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            continue
        task_name = str(result.get("task_name", ""))
        short = task_name.split("/")[-1]
        if not short or result.get("trial_name") != result_path.parent.name:
            continue
        verifier = result.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        reward = rewards.get("reward", rewards.get("verify")) if rewards else None
        if (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or not math.isfinite(reward)
        ):
            reward = None
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

        status = "resolved" if reward == 1 else "unresolved" if reward == 0 else "unknown"
        failure_kind = None
        if exception:
            failure_kind = _classify_failure(exception)
        elif reward == 0:
            failure_kind = "unknown"

        duration = _duration_sec(timing)
        trial_duration = _duration_sec(result)
        usage = morrow_metrics.get("usage") or {}
        diagnostic_file = result_path.parent / "agent" / "logs" / "morrow-diagnostics.jsonl"
        tasks.append(
            {
                "task": short,
                "difficulty": (meta.get(short) or {}).get("difficulty", "unknown"),
                "category": (meta.get(short) or {}).get("category", "unknown"),
                "status": status,
                "agent_status": "exception"
                if exception
                else "completed"
                if timing.get("finished_at")
                else "unknown",
                "failure_kind": failure_kind,
                "reward": reward,
                "duration_sec": duration,
                "agent_started": bool(timing.get("started_at")),
                "trial_duration_sec": trial_duration,
                "stop_code": morrow_metrics.get("stop_code"),
                "finish_reason": morrow_metrics.get("finish_reason"),
                "validation_outcome": morrow_metrics.get("validation_outcome"),
                "goal_verification": morrow_metrics.get("goal_verification"),
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
                "usage_availability": usage.get("availability"),
                "exception": (exception.get("type") or "")[:120] or None,
                "trial_id": result["id"],
                "trial_name": result["trial_name"],
                "result_path": str(result_path),
                "terminal": bool(result.get("finished_at")),
                "agent_input_tokens": agent_ctx.get("n_input_tokens"),
                "agent_output_tokens": agent_ctx.get("n_output_tokens"),
                "agent_cost_usd": agent_ctx.get("cost_usd"),
                "partial_usage": partial_usage,
                "run_id": (((fingerprint or {}).get("campaign") or {}).get("settings") or {}).get(
                    "run_id"
                ),
                "fingerprint_file": str(fingerprint_file) if fingerprint is not None else None,
                "diagnostic_file": str(diagnostic_file) if diagnostic_file.is_file() else None,
                "terminal_metrics_file": str(metrics_file) if metrics_file.is_file() else None,
                "fingerprint": fingerprint,
            }
        )

    resolved = [t for t in tasks if t["reward"] == 1]
    n = len(tasks)
    counts = Counter(task["task"] for task in tasks)
    duplicates = sorted(task for task, count in counts.items() if count > 1)
    one_job = bool(tasks) and all(
        _trial_job(Path(task["result_path"])) == jobs_dir for task in tasks
    )
    campaign = (tasks[0]["fingerprint"] or {}).get("campaign") if one_job else None
    settings = (campaign or {}).get("settings") or {}
    planned = settings.get("tasks") or {}
    if not isinstance(planned, dict):
        planned = {}
    protocol = json.loads((BENCH_DIR / "config" / "v2" / "protocol.json").read_text())
    frozen = protocol["benchmark"]["task_checksums"]
    full_intent = one_job and (len(planned) == len(frozen) or "full" in jobs_dir.name)
    validity_errors = []
    if duplicates:
        validity_errors.append(f"duplicate tasks: {duplicates}")
    if full_intent:
        for field in ("source", "wheel", "assets", "harbor", "dataset"):
            if not isinstance((campaign or {}).get(field), dict):
                validity_errors.append(f"campaign {field} fingerprint missing")
        if not ((campaign or {}).get("assets") or {}).get("build_manifest_sha256"):
            validity_errors.append("asset build manifest fingerprint missing")
        if ((campaign or {}).get("harbor") or {}).get("commit") != protocol["benchmark"][
            "harbor_commit"
        ]:
            validity_errors.append("Harbor commit differs from frozen protocol")
        if ((campaign or {}).get("dataset") or {}).get("commit") != protocol["benchmark"][
            "dataset_commit"
        ]:
            validity_errors.append("dataset commit differs from frozen protocol")
        for field in (
            "run_id",
            "provider_adapter",
            "provider_base_url_sha256",
            "model_id",
            "api_model_id",
            "reasoning_effort",
            "context_window_tokens",
            "max_output_tokens",
            "agent_timeout_multiplier",
            "permission_mode",
        ):
            if settings.get(field) is None:
                validity_errors.append(f"campaign setting missing: {field}")
        if settings.get("attempts_per_task") != 1 or settings.get("harbor_max_retries") != 0:
            validity_errors.append("campaign attempts or retries differ from protocol")
        if planned != frozen:
            validity_errors.append("planned task checksums differ from frozen protocol")
        if set(counts) != set(frozen):
            validity_errors.append("observed task set differs from frozen protocol")
        if any(task["reward"] is None for task in tasks):
            validity_errors.append("missing verifier reward")
        if n != len(frozen):
            validity_errors.append("trial count differs from frozen protocol")
        for task in tasks:
            fp = task["fingerprint"]
            if not isinstance(fp, dict) or fp.get("campaign") != campaign:
                validity_errors.append(f"missing or mixed campaign fingerprint: {task['task']}")
                continue
            if fp.get("task_checksum_sha256") != frozen.get(task["task"]):
                validity_errors.append(f"task checksum mismatch: {task['task']}")
            if fp.get("installed_wheel_sha256") != (campaign.get("wheel") or {}).get("sha256"):
                validity_errors.append(f"wheel mismatch: {task['task']}")
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
            ):
                if fp.get(field) != settings.get(field):
                    validity_errors.append(f"{field} mismatch: {task['task']}")
        if len({task["trial_id"] for task in tasks}) != n:
            validity_errors.append("duplicate trial ids")
        if len({task["run_id"] for task in tasks}) != 1 or not tasks[0]["run_id"]:
            validity_errors.append("run id missing or mixed")
    report_kind = (
        "fixed_version_full"
        if full_intent and not validity_errors
        else "incomplete_full"
        if full_intent
        else "diagnostic_subset"
        if one_job
        else "mixed_campaign"
    )
    task_table = [
        {
            "task": name,
            "planned": True,
            "admitted": name in counts,
            "started": name in counts,
            "terminal": name in counts
            and any(row["terminal"] for row in tasks if row["task"] == name),
            "reward": next((row["reward"] for row in tasks if row["task"] == name), None),
            "trials": [row for row in tasks if row["task"] == name],
        }
        for name in (frozen if full_intent else planned or counts)
    ]
    report = {
        "benchmark": "Terminal-Bench 2.0",
        "report_kind": report_kind,
        "validity_errors": validity_errors,
        "duplicate_tasks": duplicates,
        "unknown_tasks": sorted(set(counts) - set(frozen if full_intent else planned))
        if planned or full_intent
        else [],
        "missing_tasks": sorted(set(planned) - set(counts)),
        "outcome_coverage": {
            "known": sum(t["reward"] is not None for t in tasks),
            "expected": len(frozen) if full_intent else len(planned) or n,
        },
        "operational_lower_bound": len(resolved) / len(frozen) if full_intent else None,
        "task_table": task_table,
        "fingerprinted_trials": sum(t["fingerprint_file"] is not None for t in tasks),
        "run_ids": sorted({t["run_id"] for t in tasks if t["run_id"]}),
        "n_tasks": n,
        "resolution_rate": (len(resolved) / len(frozen))
        if report_kind == "fixed_version_full"
        else (len(resolved) / n)
        if n and not full_intent and all(task["reward"] is not None for task in tasks)
        else None,
        "by_difficulty": _breakdown(tasks, "difficulty"),
        "by_category": _breakdown(tasks, "category"),
        "duration_sec": _p50_p95(
            [t["duration_sec"] for t in tasks if t["duration_sec"] is not None]
        ),
        "trial_duration_sec": _p50_p95(
            [t["trial_duration_sec"] for t in tasks if t["trial_duration_sec"] is not None]
        ),
        "diagnostics_coverage": {
            "entered_agent_run": sum(t["agent_started"] for t in tasks),
            "with_evidence": sum(
                t["agent_started"]
                and (
                    t["fingerprint_file"] is not None
                    or t["diagnostic_file"] is not None
                    or t["terminal_metrics_file"] is not None
                    or t["partial_usage"] is not None
                )
                for t in tasks
            ),
        },
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
        "cost_known_lower_bound_usd": sum(
            t["cost_usd"] for t in tasks if t["cost_usd"] is not None
        ),
        "cost_coverage": {
            "known": sum(t["cost_usd"] is not None for t in tasks),
            "expected": len(frozen) if full_intent else n,
        },
        "cost_usd_total": sum(t["cost_usd"] for t in tasks if t["cost_usd"] is not None)
        if n and all(t["cost_usd"] is not None for t in tasks) and not validity_errors
        else None,
        "cost_per_task": (sum(t["cost_usd"] for t in tasks) / n)
        if n and all(t["cost_usd"] is not None for t in tasks) and not validity_errors
        else None,
        "cost_per_resolved": (sum(t["cost_usd"] for t in tasks) / len(resolved))
        if resolved and all(t["cost_usd"] is not None for t in tasks) and not validity_errors
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


def _trial_job(result_path: Path) -> Path:
    parent = result_path.parent.parent
    return parent.parent if parent.name == "trials" else parent


def _cost_usd(cost: dict) -> float | None:
    if cost.get("availability") == "available" and cost.get("amount_minor") is not None:
        if cost.get("currency") == "USD":
            return cost["amount_minor"] / 100.0
    return None


def _classify_failure(exception: dict) -> str:
    text = f"{exception.get('type', '')} {exception.get('message', '')}".lower()
    if "timeout" in text or "timed out" in text:
        return "agent_deadline"
    if "rate" in text or "usage limit" in text or "quota" in text:
        return "provider_network"
    if "docker" in text or "image" in text or "environment" in text:
        return "environment_setup"
    if "tool" in text:
        return "harness"
    return "unknown"


def _breakdown(tasks: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = {}
    for task in tasks:
        groups.setdefault(task[key], []).append(task)
    return {
        name: {
            "n": len(items),
            "resolved": sum(1 for t in items if t["reward"] == 1),
            "rate": sum(1 for t in items if t["reward"] == 1) / len(items)
            if items and all(t["reward"] is not None for t in items)
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
        "complete_usage_tasks": sum(t["usage_availability"] == "available" for t in tasks),
        "partial_usage_tasks": sum(t["partial_usage"] is not None for t in tasks),
        "missing_usage_tasks": sum(
            t["usage_availability"] != "available" and t["partial_usage"] is None for t in tasks
        ),
        "known_lower_bound": sum(
            t["total_tokens"]
            if t["total_tokens"] is not None
            else ((t["partial_usage"] or {}).get("known_input_tokens") or 0)
            + ((t["partial_usage"] or {}).get("known_output_tokens") or 0)
            for t in tasks
        ),
        "p50_per_task": _p50_p95(values)["p50"],
        "p95_per_task": _p50_p95(values)["p95"],
    }


def _failure_counts(tasks: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for task in tasks:
        if task["exception"] or task["reward"] == 0:
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
