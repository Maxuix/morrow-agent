#!/usr/bin/env python3
"""Prepare and verify the lightweight Morrow code-agent evaluation tasks."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

DATASET_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = DATASET_ROOT.parents[1]
MANIFEST_PATH = DATASET_ROOT / "manifest.toml"
MARKER_NAME = ".morrow-eval.json"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    origin: str
    difficulty: str
    category: str
    values: dict[str, object]

    @property
    def task_dir(self) -> Path:
        return DATASET_ROOT / "tasks" / self.id

    @property
    def prompt_path(self) -> Path:
        return self.task_dir / "TASK.md"

    @property
    def is_history(self) -> bool:
        return self.origin == "morrow-history"


def load_tasks() -> tuple[Task, ...]:
    with MANIFEST_PATH.open("rb") as handle:
        manifest = tomllib.load(handle)
    if manifest.get("version") != 1:
        raise RuntimeError("unsupported evaluation manifest version")
    tasks = tuple(
        Task(
            id=item["id"],
            title=item["title"],
            origin=item["origin"],
            difficulty=item["difficulty"],
            category=item["category"],
            values=item,
        )
        for item in manifest["tasks"]
    )
    identifiers = [task.id for task in tasks]
    if len(tasks) != 10 or len(set(identifiers)) != len(identifiers):
        raise RuntimeError("evaluation manifest must contain ten uniquely identified tasks")
    return tasks


def task_by_id(task_id: str) -> Task:
    normalized = task_id.upper()
    for task in load_tasks():
        if task.id == normalized:
            return task
    raise KeyError(f"unknown task: {task_id}")


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def git_archive(commit: str, destination: Path, paths: list[str] | None = None) -> None:
    command = ["git", "archive", "--format=tar", commit]
    if paths:
        command.extend(["--", *paths])
    result = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git archive failed for {commit}: {detail}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError("unsafe path in git archive")
        archive.extractall(destination, filter="data")


def copy_material(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"evaluation material is missing: {source}")
    for source_path in sorted(source.rglob("*")):
        if source_path.is_dir():
            continue
        relative = source_path.relative_to(source)
        if relative.suffix == ".txt":
            relative = relative.with_suffix("")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)


def initialize_repository(workspace: Path) -> None:
    commands = (
        ["git", "init", "-q"],
        ["git", "add", "--all"],
        [
            "git",
            "-c",
            "user.name=Morrow Eval",
            "-c",
            "user.email=morrow-eval@example.invalid",
            "commit",
            "-q",
            "-m",
            "evaluation baseline",
        ],
    )
    for command in commands:
        result = run_command(command, cwd=workspace, capture=True)
        if result.returncode:
            raise RuntimeError(f"failed to initialize evaluation repository: {result.stdout}")


def prepare_task(task: Task, output: Path, *, gold: bool = False) -> None:
    output = output.resolve()
    if output.exists():
        raise RuntimeError(f"output already exists: {output}")
    output.mkdir(parents=True)
    try:
        if task.is_history:
            commit_key = "gold_commit" if gold else "base_commit"
            git_archive(str(task.values[commit_key]), output)
        else:
            starter = DATASET_ROOT / str(task.values["starter_dir"])
            copy_material(starter, output)
            if gold:
                solution = DATASET_ROOT / str(task.values["solution_dir"])
                copy_material(solution, output)
        shutil.copyfile(task.prompt_path, output / "TASK.md")
        marker = {
            "dataset": "morrow-code-agent-mini",
            "manifest_version": 1,
            "task_id": task.id,
            "gold_workspace": gold,
        }
        (output / MARKER_NAME).write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        initialize_repository(output)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def verify_marker(task: Task, workspace: Path) -> None:
    marker_path = workspace / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("workspace does not contain a valid evaluation marker") from exc
    if marker.get("task_id") != task.id:
        raise RuntimeError("workspace marker does not match the requested task")


def verification_command(task: Task, workspace: Path, verifier_root: Path) -> list[str]:
    test_files = [str(verifier_root / str(path)) for path in task.values["test_files"]]
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_files]
    test_filter = task.values.get("test_filter")
    if test_filter:
        command.extend(["-k", str(test_filter)])
    return command


def run_verification(
    task: Task, workspace: Path, *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    workspace = workspace.resolve()
    verify_marker(task, workspace)
    with tempfile.TemporaryDirectory(prefix=f"morrow-eval-{task.id.lower()}-") as temp:
        verifier_root = Path(temp)
        if task.is_history:
            git_archive(str(task.values["gold_commit"]), verifier_root, ["tests"])
        else:
            verification = DATASET_ROOT / str(task.values["verification_dir"])
            copy_material(verification, verifier_root)
        environment = os.environ.copy()
        import_roots = [str(workspace / "src"), str(workspace), str(verifier_root)]
        if environment.get("PYTHONPATH"):
            import_roots.append(environment["PYTHONPATH"])
        environment["PYTHONPATH"] = os.pathsep.join(import_roots)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return run_command(
            verification_command(task, workspace, verifier_root),
            cwd=workspace,
            env=environment,
            capture=capture,
        )


def self_check(selected: tuple[Task, ...]) -> int:
    failures: list[str] = []
    for task in selected:
        with tempfile.TemporaryDirectory(prefix=f"morrow-eval-check-{task.id.lower()}-") as temp:
            root = Path(temp)
            baseline = root / "baseline"
            gold = root / "gold"
            prepare_task(task, baseline)
            baseline_result = run_verification(task, baseline, capture=True)
            prepare_task(task, gold, gold=True)
            gold_result = run_verification(task, gold, capture=True)
            baseline_failed = baseline_result.returncode != 0
            gold_passed = gold_result.returncode == 0
            state = "PASS" if baseline_failed and gold_passed else "FAIL"
            print(
                f"{state} {task.id}: baseline={baseline_result.returncode} "
                f"gold={gold_result.returncode}"
            )
            if state == "FAIL":
                failures.append(task.id)
                print("baseline output:")
                print((baseline_result.stdout or "").strip())
                print("gold output:")
                print((gold_result.stdout or "").strip())
    if failures:
        print(f"self-check failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"self-check passed: {len(selected)} tasks")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list the ten evaluation tasks")
    show = subparsers.add_parser("show", help="show metadata and the task prompt")
    show.add_argument("task_id")
    prepare = subparsers.add_parser("prepare", help="create an isolated task workspace")
    prepare.add_argument("task_id")
    prepare.add_argument("output", type=Path)
    verify = subparsers.add_parser("verify", help="run the task verifier")
    verify.add_argument("task_id")
    verify.add_argument("workspace", type=Path)
    check = subparsers.add_parser("self-check", help="prove baselines fail and gold states pass")
    check.add_argument("task_ids", nargs="*")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.command == "list":
            print("ID           难度  来源             类型            标题")
            for task in load_tasks():
                print(
                    f"{task.id:<12} {task.difficulty:<4} {task.origin:<16} "
                    f"{task.category:<15} {task.title}"
                )
            return 0
        if arguments.command == "show":
            task = task_by_id(arguments.task_id)
            print(f"{task.id} | {task.difficulty} | {task.origin} | {task.category}")
            print(task.prompt_path.read_text(encoding="utf-8"))
            return 0
        if arguments.command == "prepare":
            task = task_by_id(arguments.task_id)
            prepare_task(task, arguments.output)
            print(arguments.output.resolve())
            return 0
        if arguments.command == "verify":
            task = task_by_id(arguments.task_id)
            return run_verification(task, arguments.workspace).returncode
        selected = (
            tuple(task_by_id(task_id) for task_id in arguments.task_ids)
            if arguments.task_ids
            else load_tasks()
        )
        return self_check(selected)
    except (KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
