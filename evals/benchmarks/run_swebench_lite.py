"""SWE-bench Lite driver: generate patches with Morrow, score with the official harness.

Usage:
    python run_swebench_lite.py --limit 50            # first 50 instances
    python run_swebench_lite.py --shards 4 --shard 0  # sharded run
    python run_swebench_lite.py --evaluate-only       # re-run official scoring

Phase 1 (patch generation) runs Morrow inside each instance's official Docker
image and writes predictions in the official CSV format. Phase 2 invokes the
official ``swebench.harness.run_evaluation`` — no custom result interpretation.
The label on every report is "SWE-bench Lite" (300 tasks), never "Verified".
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
VENDOR = BENCH_DIR / "vendor"
DATASET = VENDOR / "swebench-lite" / "test.jsonl"
RUNS_DIR = BENCH_DIR / "runs" / "swebench-lite"
RESULTS_DIR = BENCH_DIR / "results" / "swebench-lite"
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

sys.path.insert(0, str(BENCH_DIR))
from harness.budget import TokenBudget  # noqa: E402
from harness.fingerprint import run_fingerprint, sha256_file, write_json  # noqa: E402
from harness.swe_lite_runner import SweLiteRunner  # noqa: E402


def _load_dotenv() -> dict[str, str]:
    env_file = BENCH_DIR / ".env"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def _load_instances() -> list[dict]:
    return [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=None,
        help="Morrow reasoning effort forwarded to the model",
    )
    parser.add_argument(
        "--budget-total",
        type=int,
        default=int(os.environ.get("MORROW_BENCH_TOKEN_BUDGET", "100_000_000")),
    )
    parser.add_argument(
        "--reservation",
        type=int,
        default=int(os.environ.get("MORROW_BENCH_RESERVATION", "1_000_000")),
    )
    parser.add_argument(
        "--timeout", type=int, default=1800, help="per-instance agent timeout (sec)"
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="re-run instances already recorded in instances-*.jsonl "
        "(default: skip them, so interrupted campaigns resume without rework)",
    )
    args = parser.parse_args()

    dotenv = _load_dotenv()
    env = {**dotenv, **os.environ}
    reasoning_effort = args.reasoning_effort or env.get("MORROW_BENCH_REASONING_EFFORT", "low")
    if reasoning_effort not in REASONING_EFFORTS:
        print("invalid reasoning effort", file=sys.stderr)
        return 2

    effort_runs_dir = RUNS_DIR / reasoning_effort
    effort_results_dir = RESULTS_DIR / reasoning_effort
    effort_runs_dir.mkdir(parents=True, exist_ok=True)
    effort_results_dir.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    budget = TokenBudget(
        BENCH_DIR / "runs" / "budget-ledger.json",
        budget_total=args.budget_total,
        reservation=args.reservation,
    )
    run_id = uuid.uuid4().hex

    if not args.evaluate_only:
        instances = _load_instances()[args.offset :]
        if args.shards > 1:
            instances = instances[args.shard :: args.shards]
        if args.limit:
            instances = instances[: args.limit]

        write_json(
            effort_runs_dir / f"run-fingerprint-{run_id}.json",
            run_fingerprint(
                BENCH_DIR,
                settings={
                    "run_id": run_id,
                    "dataset_sha256": sha256_file(DATASET),
                    "instance_ids": [item["instance_id"] for item in instances],
                    "provider_adapter": env.get(
                        "MORROW_BENCH_PROVIDER_ADAPTER", "openai-compatible"
                    ),
                    "provider_id": env.get("MORROW_BENCH_PROVIDER_ID", "bench"),
                    "provider_base_url_sha256": hashlib.sha256(
                        env["MORROW_BENCH_PROVIDER_BASE_URL"].encode("utf-8")
                    ).hexdigest(),
                    "model_id": env["MORROW_BENCH_MODEL_ID"],
                    "api_model_id": env["MORROW_BENCH_API_MODEL_ID"],
                    "reasoning_effort": reasoning_effort,
                    "context_window_tokens": env.get("MORROW_BENCH_CONTEXT_WINDOW_TOKENS"),
                    "max_output_tokens": env.get("MORROW_BENCH_MAX_OUTPUT_TOKENS"),
                    "image_template_sha256": hashlib.sha256(
                        env.get("MORROW_BENCH_SWE_IMAGE_TEMPLATE", "").encode("utf-8")
                    ).hexdigest(),
                    "timeout_seconds": args.timeout,
                    "concurrency": 1,
                    "permission_mode": "manual",
                    "reservation": args.reservation,
                    "budget_total": args.budget_total,
                },
            ),
        )

        runner = SweLiteRunner(
            workspace=effort_runs_dir / "workspaces",
            provider_env={"api_key": env["MORROW_BENCH_API_KEY"]},
            image_template=env.get("MORROW_BENCH_SWE_IMAGE_TEMPLATE"),
            morrow_env={
                "adapter": env.get("MORROW_BENCH_PROVIDER_ADAPTER", "openai-compatible"),
                "base_url": env["MORROW_BENCH_PROVIDER_BASE_URL"],
                "provider_id": env.get("MORROW_BENCH_PROVIDER_ID", "bench"),
                "model_id": env["MORROW_BENCH_MODEL_ID"],
                "api_model_id": env["MORROW_BENCH_API_MODEL_ID"],
                "reasoning_effort": reasoning_effort,
                "context_window_tokens": env.get("MORROW_BENCH_CONTEXT_WINDOW_TOKENS", ""),
                "max_output_tokens": env.get("MORROW_BENCH_MAX_OUTPUT_TOKENS", ""),
            },
            timeout_sec=args.timeout,
        )

        results_path = effort_runs_dir / f"instances-shard{args.shard}.jsonl"
        # Resume support: instances already recorded (any status) are skipped
        # unless --rerun. Their patches/logs stay on disk and are re-merged into
        # predictions by _write_predictions below.
        finished: set[str] = set()
        if results_path.exists() and not args.rerun:
            for line in results_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    finished.add(json.loads(line)["instance_id"])
                except json.JSONDecodeError:
                    continue  # torn last line from a killed write; entry simply re-runs
            if finished:
                print(f"resuming: {len(finished)} finished instances will be skipped")

        with results_path.open("a", encoding="utf-8") as out:
            for instance in instances:
                if instance["instance_id"] in finished:
                    continue
                run_key = f"swebench-lite:{run_id}:{instance['instance_id']}"
                if not budget.admit(run_key):
                    print(f"budget exhausted; stopping before {instance['instance_id']}")
                    break
                print(f"=== {instance['instance_id']} (remaining budget {budget.remaining:,}) ===")
                result = runner.run_instance(instance)
                budget.finalize(run_key, usage=result.usage or None)
                out.write(
                    json.dumps(
                        result.__dict__
                        | {"run_id": run_id}
                        | {"patch_file": str(result.patch_file) if result.patch_file else None},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                out.flush()
                print(
                    f"  -> {result.status} in {result.duration_sec:.0f}s "
                    f"tokens={budget._known_tokens(result.usage):,}"
                )

    predictions = effort_runs_dir / "predictions.csv"
    _write_predictions(
        effort_runs_dir,
        predictions,
        model_name=env.get("MORROW_BENCH_API_MODEL_ID", "morrow"),
    )

    if not predictions.exists():
        print("no predictions yet", file=sys.stderr)
        return 1

    eval_cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        "princeton-nlp/SWE-bench_Lite",
        "--predictions_path",
        str(predictions),
        "--run_id",
        f"morrow-swebench-lite-{reasoning_effort}",
        "--report_dir",
        str(effort_results_dir),
    ]
    print("running official evaluation:", " ".join(eval_cmd))
    env_full = os.environ.copy()
    env_full["PYTHONPATH"] = str(VENDOR / "swebench") + os.pathsep + env_full.get("PYTHONPATH", "")
    proc = subprocess.run(eval_cmd, env=env_full)
    print(json.dumps({"official_eval_exit": proc.returncode, "budget": budget.summary()}, indent=2))
    return proc.returncode


def _write_predictions(runs_dir: Path, predictions: Path, *, model_name: str) -> None:
    """Merge per-instance patches into the official predictions CSV."""
    rows: dict[str, str] = {}
    for patch_file in sorted(runs_dir.rglob("*__patch.diff")):
        instance_id = patch_file.name.split("__patch.diff")[0]
        rows[instance_id] = patch_file.read_text(encoding="utf-8", errors="replace")
    if not rows:
        return
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["instance_id", "model_patch", "model_name_or_path"]
        )
        writer.writeheader()
        for instance_id, patch in sorted(rows.items()):
            writer.writerow(
                {
                    "instance_id": instance_id,
                    "model_patch": patch if patch.strip() else "",
                    "model_name_or_path": model_name,
                }
            )
    print(f"wrote {len(rows)} predictions to {predictions}")


if __name__ == "__main__":
    raise SystemExit(main())
