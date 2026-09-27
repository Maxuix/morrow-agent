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
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
VENDOR_HARBOR = BENCH_DIR / "vendor" / "harbor"
VENDOR_TB2 = BENCH_DIR / "vendor" / "terminal-bench-2"
HARBOR_BIN = BENCH_DIR / ".venv" / "bin" / "harbor"
PILOT_TASKS = BENCH_DIR / "config" / "pilot-tasks.txt"
RUNS_DIR = BENCH_DIR / "runs"
RESULTS_DIR = BENCH_DIR / "results"
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
PROTOCOL = BENCH_DIR / "config" / "v2" / "protocol.json"
# Keep aligned with morrow.core.runtime_policy.PI_DEFAULT_RESERVE_TOKENS.
MORROW_DEFAULT_RESERVE_TOKENS = 16_384
MORROW_MAX_CONTEXT_TOKENS = 10_000_000
MORROW_MAX_OUTPUT_TOKENS = 1_000_000

sys.path.insert(0, str(BENCH_DIR))
from harness.assets import verify_bundle  # noqa: E402
from harness.budget import TokenBudget  # noqa: E402
from harness.fingerprint import run_fingerprint, sha256_tree, write_json  # noqa: E402


def _preflight(tasks: list[str]) -> None:
    """Check local execution inputs before reserving budget; do not read verifier content."""
    assets = BENCH_DIR / "assets"
    required = [HARBOR_BIN, assets / "uv-x86_64-unknown-linux-gnu", assets / "wheelhouse"]
    if any(not path.exists() for path in required):
        raise FileNotFoundError("Harbor executable or offline benchmark assets are missing")
    if (
        len(list(assets.glob("morrow_agent-*.whl"))) != 1
        or len(list(assets.glob("cpython-3.12*-x86_64-unknown-linux-gnu-install_only.tar.gz"))) != 1
    ):
        raise FileNotFoundError("expected exactly one Morrow wheel and one CPython asset")
    for task in tasks:
        if not (VENDOR_TB2 / task / "task.toml").is_file():
            raise FileNotFoundError(f"task manifest missing: {task}")
    verify_bundle(BENCH_DIR.parent.parent, assets)


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
    parser.add_argument("--concurrency", type=int)
    parser.add_argument(
        "--budget-total",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--reservation",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--agent-timeout-multiplier",
        type=float,
        default=1.0,
        help="Harbor agent-execution timeout multiplier (default: 1.0)",
    )
    parser.add_argument("--job-name", default=None)
    parser.add_argument(
        "--resume-run-id", help="resume the exact previous run ID and frozen job configuration"
    )
    args = parser.parse_args()

    if args.resume_run_id and not re.fullmatch(r"[0-9a-f]{32}", args.resume_run_id):
        parser.error("--resume-run-id must be a 32-character lowercase hex run ID")

    if not math.isfinite(args.agent_timeout_multiplier) or args.agent_timeout_multiplier <= 0:
        parser.error("--agent-timeout-multiplier must be finite and greater than zero")

    dotenv = _load_dotenv()
    env = {**dotenv, **os.environ}
    try:
        args.concurrency = (
            args.concurrency
            if args.concurrency is not None
            else int(env.get("MORROW_BENCH_CONCURRENCY", "2"))
        )
        args.budget_total = (
            args.budget_total
            if args.budget_total is not None
            else int(env.get("MORROW_BENCH_TOKEN_BUDGET", "300000000"))
        )
        args.reservation = (
            args.reservation
            if args.reservation is not None
            else int(env.get("MORROW_BENCH_RESERVATION", "1000000"))
        )
    except ValueError:
        parser.error("concurrency, budget total and reservation must be integers")
    if min(args.concurrency, args.budget_total, args.reservation) <= 0:
        parser.error("concurrency, budget total and reservation must be positive")
    reasoning_effort = args.reasoning_effort or env.get("MORROW_BENCH_REASONING_EFFORT", "high")
    if reasoning_effort not in REASONING_EFFORTS:
        print("invalid reasoning effort", file=sys.stderr)
        return 2

    tasks = (
        _pilot_tasks()
        if args.pilot
        else _all_tasks()
        if args.full
        else ([task.strip() for task in args.tasks.split(",")] if args.tasks else _pilot_tasks())
    )
    if not tasks or len(tasks) != len(set(tasks)) or any(not task for task in tasks):
        parser.error("task list must be nonempty and contain unique task names")
    missing = [task for task in tasks if task not in _all_tasks()]
    if missing:
        parser.error(f"unknown tasks: {missing}")
    if args.full:
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        frozen = protocol["benchmark"]["task_checksums"]
        if set(tasks) != set(frozen) or any(
            sha256_tree(VENDOR_TB2 / task) != frozen[task] for task in tasks
        ):
            parser.error("full task set or task contents differ from frozen protocol")
    job_label = args.job_name or (
        "tb2-pilot" if args.pilot else "tb2-full" if args.full else "tb2-subset"
    )
    run_id = args.resume_run_id or uuid.uuid4().hex
    job_name = f"{job_label}-{run_id[:12]}"
    run_key_prefix = f"tb2:{run_id}"
    ledger_path = RUNS_DIR / "budget-ledger.json"
    try:
        budget_snapshot = TokenBudget.preview(
            ledger_path, budget_total=args.budget_total, reservation=args.reservation
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(f"budget preview failed: {exc}")

    plan = {
        "job_name": job_name,
        "run_id": run_id,
        "reasoning_effort": reasoning_effort,
        "tasks": tasks,
        "n_tasks": len(tasks),
        "concurrency": args.concurrency,
        "budget": budget_snapshot,
        "required_reservation": len(tasks) * args.reservation,
        "admission_possible_at_snapshot": budget_snapshot["remaining"]
        >= len(tasks) * args.reservation,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0 if plan["admission_possible_at_snapshot"] else 1

    required = (
        "MORROW_BENCH_API_KEY",
        "MORROW_BENCH_PROVIDER_BASE_URL",
        "MORROW_BENCH_MODEL_ID",
        "MORROW_BENCH_API_MODEL_ID",
        "MORROW_BENCH_CONTEXT_WINDOW_TOKENS",
        "MORROW_BENCH_MAX_OUTPUT_TOKENS",
    )
    if any(not env.get(key) for key in required):
        print("benchmark model configuration is incomplete", file=sys.stderr)
        return 2
    try:
        context_tokens = int(env["MORROW_BENCH_CONTEXT_WINDOW_TOKENS"])
        output_tokens = int(env["MORROW_BENCH_MAX_OUTPUT_TOKENS"])
    except ValueError:
        print("model capacities must be integers", file=sys.stderr)
        return 2
    if not (
        0 < output_tokens <= MORROW_MAX_OUTPUT_TOKENS
        and max(output_tokens, MORROW_DEFAULT_RESERVE_TOKENS)
        < context_tokens
        <= MORROW_MAX_CONTEXT_TOKENS
    ):
        print("model capacities are outside Morrow's supported range or reserve", file=sys.stderr)
        return 2
    try:
        _preflight(tasks)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"benchmark preflight failed: {exc}", file=sys.stderr)
        return 2

    manifest_path = RUNS_DIR / "manifests" / f"{run_id}.json"
    fingerprint = run_fingerprint(
        BENCH_DIR,
        settings={
            "run_id": run_id,
            "job_name": job_name,
            "tasks": {task: sha256_tree(VENDOR_TB2 / task) for task in tasks},
            "provider_adapter": env.get("MORROW_BENCH_PROVIDER_ADAPTER", "openai-compatible"),
            "provider_id": env.get("MORROW_BENCH_PROVIDER_ID", "bench"),
            "provider_base_url_sha256": hashlib.sha256(
                env["MORROW_BENCH_PROVIDER_BASE_URL"].encode("utf-8")
            ).hexdigest(),
            "model_id": env["MORROW_BENCH_MODEL_ID"],
            "api_model_id": env["MORROW_BENCH_API_MODEL_ID"],
            "reasoning_effort": reasoning_effort,
            "context_window_tokens": context_tokens,
            "max_output_tokens": output_tokens,
            "agent_timeout_multiplier": args.agent_timeout_multiplier,
            "attempts_per_task": 1,
            "harbor_max_retries": 0,
            "concurrency": args.concurrency,
            "permission_mode": "manual",
            "reservation": args.reservation,
            "budget_total": args.budget_total,
        },
    )
    if args.resume_run_id:
        if not manifest_path.is_file() or json.loads(manifest_path.read_text()) != fingerprint:
            print("resume fingerprint differs or run ID is unknown", file=sys.stderr)
            return 2
    else:
        write_json(manifest_path, fingerprint)

    budget = TokenBudget(ledger_path, budget_total=args.budget_total, reservation=args.reservation)
    if not budget.admit_many([f"{run_key_prefix}:{task}" for task in tasks]):
        print("budget cannot admit the complete task set; no tasks admitted")
        return 1

    admitted = tasks
    include_patterns = list(tasks)
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
        "--n-attempts",
        "1",
        "--max-retries",
        "0",
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
        "--ak",
        f"context_window_tokens={context_tokens}",
        "--ak",
        f"max_output_tokens={output_tokens}",
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
    env_full["MORROW_BENCH_RUN_MANIFEST"] = str(manifest_path)
    print(f"running Harbor job {job_name} with {len(tasks)} tasks")
    proc = subprocess.run(cmd, env=env_full)
    rc = proc.returncode

    # Write back exact usage from the morrow JSONL logs of this job.
    finalized = _finalize_from_job_logs(
        budget,
        run_key_prefix,
        RUNS_DIR / "jobs" / reasoning_effort / job_name,
    )
    for task in admitted:
        run_key = f"{run_key_prefix}:{task}"
        if any(
            item["run_key"] == run_key and item["status"] == "admitted"
            for item in budget.state["entries"]
        ):
            budget.finalize(run_key, usage=None)
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
    log_dirs = {path.parent for path in job_dir.rglob("morrow-run.jsonl")}
    log_dirs.update(path.parent for path in job_dir.rglob("morrow-partial-metrics.json"))
    for log_dir in sorted(log_dirs):
        agent_dir = next(
            (parent for parent in (log_dir, *log_dir.parents) if parent.name == "agent"), None
        )
        if agent_dir is None:
            continue
        trial_name = agent_dir.parent.name
        record = None
        log = log_dir / "morrow-run.jsonl"
        if log.is_file():
            try:
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("kind") == "run.completed":
                        record = item
            except OSError:
                pass
        metrics = (record or {}).get("metrics") or {}
        usage = metrics.get("usage")
        if not usage:
            partial_path = log_dir / "morrow-partial-metrics.json"
            if partial_path.is_file():
                try:
                    partial = json.loads(partial_path.read_text(encoding="utf-8"))
                    usage = {
                        "availability": "partial",
                        "input_tokens": partial.get("known_input_tokens"),
                        "output_tokens": partial.get("known_output_tokens"),
                        "unknown_request_count": partial.get("unknown_request_count"),
                    }
                except (OSError, json.JSONDecodeError):
                    pass
        if not usage:
            continue
        # Trial dirs are named <task>__<id>; find the admitted run_key.
        task = trial_name.split("__")[0]
        run_key = f"{run_key_prefix}:{task}"
        entry = next(
            (
                item
                for item in budget.state["entries"]
                if item["run_key"] == run_key
                and item["status"] in ("admitted", "finalized")
                and not budget._entry_complete(item)
            ),
            None,
        )
        if entry is not None:
            budget.finalize(run_key, usage=usage)
            count += 1
    return count


if __name__ == "__main__":
    raise SystemExit(main())
