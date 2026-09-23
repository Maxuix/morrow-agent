"""Terminal-Bench 2.0 driver: pilot (25) then full (89), with token-budget control.

Usage:
    python run_tb2.py --pilot            # 25-task pilot
    python run_tb2.py --full             # all 89 tasks
    python run_tb2.py --tasks a,b,c      # explicit subset
    python run_tb2.py --pilot --dry-run  # print plan without running

The driver shells out to the official Harbor CLI (``harbor run``) with the
custom ``harness.morrow_harbor_agent:MorrowAgent`` installed agent. Task
success is decided exclusively by each task's official verifier (Harbor
``verify.sh`` / tests); this driver never interprets results itself.

Budget governance: before each ``harbor run`` batch the driver admits the
batch against the shared ``TokenBudget`` ledger (default 100M total). After
the batch, exact usage from Morrow ``run.completed`` records is written back.
When the remaining budget cannot cover the reservation, the batch is refused
and the driver exits with a summary.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
VENDOR_HARBOR = BENCH_DIR / "vendor" / "harbor"
VENDOR_TB2 = BENCH_DIR / "vendor" / "terminal-bench-2"
HARBOR_BIN = BENCH_DIR / ".venv" / "bin" / "harbor"
PILOT_TASKS = BENCH_DIR / "config" / "pilot-tasks.txt"
RUNS_DIR = BENCH_DIR / "runs"
RESULTS_DIR = BENCH_DIR / "results"
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

sys.path.insert(0, str(BENCH_DIR))
from harness.budget import TokenBudget  # noqa: E402


def _task_metadata() -> dict[str, dict]:
    import tomllib

    meta = {}
    for task_dir in sorted(VENDOR_TB2.iterdir()):
        toml = task_dir / "task.toml"
        if not toml.exists():
            continue
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        meta[task_dir.name] = {
            "difficulty": data["metadata"].get("difficulty"),
            "category": data["metadata"].get("category"),
        }
    return meta


def _all_tasks() -> list[str]:
    return sorted(_task_metadata())


def _pilot_tasks() -> list[str]:
    return [
        line.strip()
        for line in PILOT_TASKS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _load_dotenv() -> dict[str, str]:
    env_file = BENCH_DIR / ".env"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--pilot", action="store_true", help="25-task pilot")
    scope.add_argument("--full", action="store_true", help="all 89 tasks")
    scope.add_argument("--tasks", help="comma-separated task names")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=None,
        help="Morrow reasoning effort forwarded to the model",
    )
    parser.add_argument(
        "--concurrency", type=int, default=int(os.environ.get("MORROW_BENCH_CONCURRENCY", "4"))
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
        "--agent-timeout-multiplier",
        type=float,
        default=1.0,
        help="Harbor agent-execution timeout multiplier (default: 1.0)",
    )
    parser.add_argument("--job-name", default=None)
    args = parser.parse_args()

    if args.agent_timeout_multiplier <= 0:
        parser.error("--agent-timeout-multiplier must be greater than zero")

    dotenv = _load_dotenv()
    env = {**dotenv, **os.environ}
    reasoning_effort = args.reasoning_effort or env.get("MORROW_BENCH_REASONING_EFFORT", "high")
    if reasoning_effort not in REASONING_EFFORTS:
        print("invalid reasoning effort", file=sys.stderr)
        return 2

    tasks = (
        _pilot_tasks()
        if args.pilot
        else _all_tasks()
        if args.full
        else (args.tasks.split(",") if args.tasks else _pilot_tasks())
    )
    job_name = args.job_name or (
        "tb2-pilot" if args.pilot else "tb2-full" if args.full else "tb2-subset"
    )
    run_key_prefix = f"tb2:{reasoning_effort}:{job_name}"
    ledger_path = RUNS_DIR / "budget-ledger.json"
    budget = TokenBudget(ledger_path, budget_total=args.budget_total, reservation=args.reservation)

    plan = {
        "job_name": job_name,
        "reasoning_effort": reasoning_effort,
        "tasks": tasks,
        "n_tasks": len(tasks),
        "concurrency": args.concurrency,
        "budget": budget.summary(),
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    missing = [t for t in tasks if t not in _all_tasks()]
    if missing:
        print(f"unknown tasks: {missing}", file=sys.stderr)
        return 2
    if not env.get("MORROW_BENCH_API_KEY"):
        print("MORROW_BENCH_API_KEY is required", file=sys.stderr)
        return 2

    # One admission per task; harbor runs the admitted set in one job.
    admitted: list[str] = []
    for task in tasks:
        if budget.admit(f"{run_key_prefix}:{task}"):
            admitted.append(task)
    if not admitted:
        print("budget exhausted; no tasks admitted")
        return 1

    include_patterns = list(admitted)
    cmd = [
        str(HARBOR_BIN),
        "run",
        "-p",
        str(VENDOR_TB2),
        "-a",
        "harness.morrow_harbor_agent:MorrowAgent",
        "-o",
        str(RUNS_DIR / "jobs" / reasoning_effort),
        "--job-name",
        job_name,
        "--effort",
        reasoning_effort,
        "--agent-timeout-multiplier",
        str(args.agent_timeout_multiplier),
        "-n",
        str(args.concurrency),
        "-y",
        "--ak",
        f"provider_adapter={env.get('MORROW_BENCH_PROVIDER_ADAPTER', 'openai-compatible')}",
        "--ak",
        f"provider_base_url={env['MORROW_BENCH_PROVIDER_BASE_URL']}",
        "--ak",
        f"provider_id={env.get('MORROW_BENCH_PROVIDER_ID', 'bench')}",
        "--ak",
        f"model_id={env['MORROW_BENCH_MODEL_ID']}",
        "--ak",
        f"api_model_id={env['MORROW_BENCH_API_MODEL_ID']}",
        "--ak",
        f"reasoning_effort={reasoning_effort}",
    ]
    for pattern in include_patterns:
        cmd += ["-i", pattern]

    env_full = {**dotenv, **os.environ}
    env_full["PYTHONPATH"] = str(BENCH_DIR) + os.pathsep + env_full.get("PYTHONPATH", "")
    # Harbor's Docker environment passes per-exec env values to ``docker
    # compose exec``.  Mirror the provider secret into the child process
    # environment so the adapter can use the argv-safe ``-e KEY`` form; the
    # credential never becomes a command-line argument.
    provider_id = env.get("MORROW_BENCH_PROVIDER_ID", "bench")
    provider_key = f"MORROW_{provider_id.upper().replace('-', '_')}_API_KEY"
    env_full[provider_key] = env["MORROW_BENCH_API_KEY"]
    print("running:", " ".join(cmd))
    proc = subprocess.run(cmd, env=env_full)
    rc = proc.returncode

    # Write back exact usage from the morrow JSONL logs of this job.
    finalized = _finalize_from_job_logs(
        budget,
        run_key_prefix,
        RUNS_DIR / "jobs" / reasoning_effort / job_name,
    )
    summary = budget.summary()
    job_dir = RUNS_DIR / "jobs" / reasoning_effort / job_name
    has_exceptions = _job_has_exceptions(job_dir)
    print(
        json.dumps(
            {
                "harbor_exit": rc,
                "job_exceptions": has_exceptions,
                "finalized_from_logs": finalized,
                "budget": summary,
            },
            indent=2,
        )
    )
    return 0 if admitted and rc == 0 and not has_exceptions else 1


def _job_has_exceptions(job_dir: Path) -> bool:
    """Detect Harbor trial exceptions so environment failures are not masked."""
    if not job_dir.exists():
        return False
    for result_path in job_dir.rglob("result.json"):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if result.get("exception_info"):
            return True
    return False


def _finalize_from_job_logs(budget: TokenBudget, run_key_prefix: str, job_dir: Path) -> int:
    """Match ledger admissions to per-trial morrow JSONL logs and correct usage."""
    count = 0
    if not job_dir.exists():
        return 0
    for log in job_dir.rglob("morrow-run.jsonl"):
        trial_name = log.parent.parent.name
        record = None
        try:
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and item.get("kind") == "run.completed":
                    record = item
        except OSError:
            continue
        metrics = (record or {}).get("metrics") or {}
        usage = metrics.get("usage")
        if not usage:
            continue
        # Trial dirs are named <task>__<id>; find the admitted run_key.
        task = trial_name.split("__")[0]
        run_key = f"{run_key_prefix}:{task}"
        entry = next(
            (
                item
                for item in budget.state["entries"]
                if item["run_key"] == run_key and item["status"] == "admitted"
            ),
            None,
        )
        if entry is not None:
            budget.finalize(run_key, usage=usage)
            count += 1
    return count


if __name__ == "__main__":
    raise SystemExit(main())
