#!/usr/bin/env python3
"""Prepare, verify and mechanically record the Morrow Code Agent Mini Eval."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DATASET_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = DATASET_ROOT.parents[1]
MANIFEST_PATH = DATASET_ROOT / "manifest.toml"
PROTOCOL_PATH = DATASET_ROOT / "protocol.toml"
PROFILE_TEMPLATE_PATH = DATASET_ROOT / "profile.template.json"
MARKER_NAME = ".morrow-eval.json"

RESULT_CLASSES = (
    "PASS",
    "FAIL_MODEL",
    "FAIL_TOOL_CONTRACT",
    "FAIL_RUNTIME",
    "DENIED_POLICY",
    "BLOCKED_ENV",
    "BUDGET_EXHAUSTED",
)
TOOL_TERMINAL_STATES = ("succeeded", "failed", "denied", "cancelled", "blocked")
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost",
    "duration_ms",
    "rounds",
    "user_interventions",
    "rework_count",
)
TOOL_DIAGNOSTIC_FIELDS = (
    "invalid_arguments",
    "unaccounted_tool_calls",
    "total_tool_calls",
    "basic_tool_blocked",
)
FIXED_PI_TASK_IDS = ("MORROW-003", "MORROW-005", "EXTERNAL-003", "EXTERNAL-004")
FROZEN_THRESHOLDS = {
    "simple_medium_required_pass": 5,
    "difficult_required_pass": 2,
    "minimum_total_pass": 7,
    "max_fail_runtime": 0,
    "max_basic_tool_blocked": 0,
    "max_success_without_expected_diff": 0,
    "max_unexpected_paths": 0,
    "max_invalid_arguments_fraction": 0.01,
    "max_unaccounted_tool_calls": 0,
    "pi_required_task_count": 4,
    "pi_max_quality_deficit": 1,
    "require_complete_usage": True,
}
STOP_CODES = (
    "completed",
    "verifier_failed",
    "tool_contract_failed",
    "runtime_failed",
    "policy_denied",
    "environment_blocked",
    "budget_exhausted",
    "cancelled",
    "evidence_unavailable",
)
REQUIRED_EVIDENCE = (
    "run-manifest.json",
    "runtime-evidence.json",
    "verifier-output.txt",
    "workspace-status.json",
    "workspace-diff.patch",
    "run-result.json",
)
MAX_TEXT_BYTES = 256 * 1024
MAX_STATUS_ENTRIES = 512
MAX_SOURCE_DIRTY_ENTRIES = 64
MAX_REASON_LENGTH = 240
DEFAULT_VERIFIER_TIMEOUT_SECONDS = 120.0
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SECRET_TEXT_RE = re.compile(
    r"(?ix)(?:"
    r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----|"
    r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b|"
    r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b|"
    r"\bAIza[0-9A-Za-z_-]{20,}\b|"
    r"\bnpm_[A-Za-z0-9]{20,}\b|"
    r"\bBearer\s+[A-Za-z0-9._-]{16,}\b|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{16,}[\"']?"
    r")"
)
FORBIDDEN_KEY_PARTS = (
    "credential",
    "secret",
    "password",
    "api_key",
    "access_token",
    "refresh_token",
    "reasoning",
    "traceback",
    "raw_payload",
    "raw_result",
    "tool_arguments",
    "tool_args",
    "tool_results",
)


class EvalError(RuntimeError):
    """A deterministic, user-safe evaluation harness error."""


class ProfileError(EvalError):
    """The operator profile is missing, malformed or sensitive."""


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


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvalError("value is not canonical JSON") from exc


def canonical_json(value: object) -> str:
    return canonical_bytes(value).decode("utf-8") + "\n"


def content_hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def bytes_hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvalError(f"unable to hash evidence file: {path.name}") from exc
    return "sha256:" + digest.hexdigest()


def _without_integrity(value: Mapping[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "integrity"}


def _write_bytes_create(path: Path, value: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise EvalError(f"output already exists: {path}") from exc
    except OSError as exc:
        raise EvalError(f"unable to write evaluation artifact: {path.name}") from exc


def _write_json_create(path: Path, value: object) -> None:
    _write_bytes_create(path, canonical_json(value).encode("utf-8"))


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvalError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise EvalError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    if unknown:
        error_type = ProfileError if label.startswith("profile") else EvalError
        raise error_type(f"unknown {label} field: {unknown[0]}")


def _profile_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    _exact_keys(value, expected, label)
    missing = sorted(expected - set(value))
    if missing:
        raise ProfileError(f"missing {label} field: {missing[0]}")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        error_type = ProfileError if label.startswith("profile") else EvalError
        raise error_type(f"{label} must be an object")
    return value


def _text(value: object, label: str, *, allow_unavailable: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise EvalError(f"{label} must be a non-empty string")
    if value == "unavailable" and allow_unavailable:
        return value
    if "\x00" in value or any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise EvalError(f"{label} contains unsupported control characters")
    return value


def _safe_reason(value: object, label: str = "reason") -> str:
    reason = _text(value, label)
    if len(reason) > MAX_REASON_LENGTH:
        raise EvalError(f"{label} is too long")
    _reject_sensitive_text(reason, label)
    return reason


def _reject_sensitive_text(value: str, label: str) -> None:
    if "Traceback (most recent call last)" in value:
        raise EvalError(f"{label} contains forbidden traceback material")
    if SECRET_TEXT_RE.search(value):
        raise EvalError(f"{label} contains forbidden credential material")


def _reject_sensitive_content(value: object, label: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvalError(f"{label} contains a non-string field")
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                error_type = ProfileError if label.startswith("profile") else EvalError
                raise error_type(f"{label} contains forbidden sensitive field")
            _reject_sensitive_content(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_content(item, f"{label}[{index}]")
    elif isinstance(value, str):
        _reject_sensitive_text(value, label)


def _safe_path(value: object, label: str = "path") -> str:
    path = _text(value, label)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path in (".", ""):
        raise EvalError(f"{label} must be a relative path")
    if "\x00" in path or len(path) > 512:
        raise EvalError(f"{label} is not a safe bounded path")
    _reject_sensitive_text(path, label)
    return candidate.as_posix()


def _is_sha256(value: object, label: str) -> str:
    text = _text(value, label, allow_unavailable=True)
    if text != "unavailable" and not SHA256_RE.fullmatch(text):
        raise EvalError(f"{label} must be a sha256 digest or unavailable")
    return text


def _number_or_unavailable(
    value: object, label: str, *, integer: bool = False
) -> int | float | str:
    if value == "unavailable":
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvalError(f"{label} must be numeric or unavailable")
    if not math.isfinite(value) or value < 0:
        raise EvalError(f"{label} must be a finite non-negative number")
    if integer and not isinstance(value, int):
        raise EvalError(f"{label} must be an integer or unavailable")
    return value


def validate_profile(profile: Mapping[str, object]) -> dict[str, object]:
    """Validate and return a JSON-safe, non-secret operator profile."""

    _reject_sensitive_content(profile, "profile")
    root = _mapping(profile, "profile")
    expected_root = {
        "schema_version",
        "profile_id",
        "agent",
        "provider",
        "model",
        "sampling",
        "tools",
        "permissions",
        "budgets",
        "system_prompt",
        "project_instructions",
        "execution",
    }
    _profile_keys(root, expected_root, "profile")
    if root.get("schema_version") != 1:
        raise ProfileError("unsupported profile schema version")
    profile_id = _text(root.get("profile_id"), "profile.profile_id")
    if not IDENTIFIER_RE.fullmatch(profile_id):
        raise ProfileError("profile.profile_id has unsupported characters")

    normalized: dict[str, object] = {"schema_version": 1, "profile_id": profile_id}
    for section, fields in (
        ("agent", {"id", "version", "entrypoint"}),
        ("provider", {"id", "revision"}),
        ("model", {"id", "revision"}),
    ):
        item = _mapping(root.get(section), f"profile.{section}")
        _profile_keys(item, fields, f"profile.{section}")
        normalized[section] = {
            key: _text(item.get(key), f"profile.{section}.{key}") for key in sorted(fields)
        }

    sampling = _mapping(root.get("sampling"), "profile.sampling")
    sampling_fields = {"temperature", "top_p", "seed", "max_output_tokens"}
    _profile_keys(sampling, sampling_fields, "profile.sampling")
    normalized["sampling"] = {
        "temperature": _number_or_unavailable(sampling.get("temperature"), "sampling.temperature"),
        "top_p": _number_or_unavailable(sampling.get("top_p"), "sampling.top_p"),
        "seed": _number_or_unavailable(sampling.get("seed"), "sampling.seed", integer=True),
        "max_output_tokens": _number_or_unavailable(
            sampling.get("max_output_tokens"), "sampling.max_output_tokens", integer=True
        ),
    }

    tools = root.get("tools")
    if not isinstance(tools, list):
        raise ProfileError("profile.tools must be a list")
    normalized_tools: list[dict[str, object]] = []
    tool_names: set[str] = set()
    for index, raw_tool in enumerate(tools):
        tool = _mapping(raw_tool, f"profile.tools[{index}]")
        _profile_keys(tool, {"name", "schema_hash", "schema"}, f"profile.tools[{index}]")
        name = _text(tool.get("name"), f"profile.tools[{index}].name")
        if name in tool_names:
            raise ProfileError("profile.tools contains duplicate names")
        tool_names.add(name)
        schema_hash = _is_sha256(tool.get("schema_hash"), f"profile.tools[{index}].schema_hash")
        schema = _mapping(tool.get("schema"), f"profile.tools[{index}].schema")
        if schema_hash != "unavailable" and schema_hash != content_hash(dict(schema)):
            raise ProfileError(f"profile.tools[{index}].schema_hash does not match schema")
        normalized_tools.append({"name": name, "schema_hash": schema_hash, "schema": dict(schema)})
    normalized["tools"] = normalized_tools

    permissions = _mapping(root.get("permissions"), "profile.permissions")
    permission_fields = {"mode", "sandbox", "network", "filesystem"}
    _profile_keys(permissions, permission_fields, "profile.permissions")
    normalized["permissions"] = {
        key: _text(permissions.get(key), f"profile.permissions.{key}")
        for key in sorted(permission_fields)
    }

    budgets = _mapping(root.get("budgets"), "profile.budgets")
    budget_fields = {
        "input_tokens",
        "output_tokens",
        "context_tokens",
        "rounds",
        "deadline_seconds",
        "tool_calls",
        "model_attempts",
    }
    _profile_keys(budgets, budget_fields, "profile.budgets")
    integer_budgets = {
        "input_tokens",
        "output_tokens",
        "context_tokens",
        "rounds",
        "tool_calls",
        "model_attempts",
    }
    normalized["budgets"] = {
        key: _number_or_unavailable(
            budgets.get(key),
            f"profile.budgets.{key}",
            integer=key in integer_budgets,
        )
        for key in sorted(budget_fields)
    }

    system_prompt = _mapping(root.get("system_prompt"), "profile.system_prompt")
    _profile_keys(system_prompt, {"version", "sha256"}, "profile.system_prompt")
    normalized["system_prompt"] = {
        "version": _text(system_prompt.get("version"), "profile.system_prompt.version"),
        "sha256": _is_sha256(system_prompt.get("sha256"), "profile.system_prompt.sha256"),
    }

    instructions = root.get("project_instructions")
    if not isinstance(instructions, list):
        raise ProfileError("profile.project_instructions must be a list")
    normalized_instructions: list[dict[str, str]] = []
    for index, raw_instruction in enumerate(instructions):
        instruction = _mapping(raw_instruction, f"profile.project_instructions[{index}]")
        _profile_keys(instruction, {"source", "sha256"}, f"profile.project_instructions[{index}]")
        normalized_instructions.append(
            {
                "source": _safe_path(
                    instruction.get("source"), f"project_instructions[{index}].source"
                ),
                "sha256": _is_sha256(
                    instruction.get("sha256"), f"project_instructions[{index}].sha256"
                ),
            }
        )
    normalized["project_instructions"] = normalized_instructions

    execution = _mapping(root.get("execution"), "profile.execution")
    execution_fields = {"python", "morrow", "agent"}
    _profile_keys(execution, execution_fields, "profile.execution")
    normalized["execution"] = {
        key: _text(execution.get(key), f"profile.execution.{key}")
        for key in sorted(execution_fields)
    }
    return normalized


def load_profile(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProfileError("profile file is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ProfileError("profile file must contain a JSON object")
    try:
        return validate_profile(value)
    except EvalError as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError(str(exc)) from exc


def load_protocol(path: Path = PROTOCOL_PATH) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            protocol = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EvalError("protocol file is unreadable") from exc
    protocol_section = _mapping(protocol.get("protocol"), "protocol.protocol")
    _exact_keys(
        protocol_section,
        {
            "id",
            "version",
            "dataset_id",
            "minimum_repetitions",
            "unavailable_usage",
            "incomplete_status",
        },
        "protocol.protocol",
    )
    if protocol_section.get("id") != "s7p-00" or protocol_section.get("version") != 1:
        raise EvalError("unsupported evaluation protocol version")
    if protocol_section.get("dataset_id") != "morrow-code-agent-mini":
        raise EvalError("protocol dataset ID is not frozen")
    if protocol_section.get("minimum_repetitions") != 2:
        raise EvalError("protocol minimum repetitions are not frozen at two")
    if protocol_section.get("unavailable_usage") != "unavailable":
        raise EvalError("protocol unavailable usage marker is not frozen")
    if protocol_section.get("incomplete_status") != "INCOMPLETE":
        raise EvalError("protocol incomplete status is not frozen")

    result_classes = _mapping(protocol.get("result_classes"), "protocol.result_classes")
    _exact_keys(result_classes, {"values"}, "protocol.result_classes")
    if result_classes.get("values") != list(RESULT_CLASSES):
        raise EvalError("protocol result taxonomy does not match the frozen contract")
    terminal_states = _mapping(
        protocol.get("tool_terminal_states"), "protocol.tool_terminal_states"
    )
    _exact_keys(terminal_states, {"values"}, "protocol.tool_terminal_states")
    if terminal_states.get("values") != list(TOOL_TERMINAL_STATES):
        raise EvalError("protocol tool terminal states do not match the frozen contract")
    tool_diagnostics = _mapping(protocol.get("tool_diagnostics"), "protocol.tool_diagnostics")
    _exact_keys(tool_diagnostics, {"fields"}, "protocol.tool_diagnostics")
    if tool_diagnostics.get("fields") != list(TOOL_DIAGNOSTIC_FIELDS):
        raise EvalError("protocol tool diagnostics do not match the frozen contract")

    evidence = _mapping(protocol.get("evidence"), "protocol.evidence")
    _exact_keys(evidence, {"required"}, "protocol.evidence")
    if evidence.get("required") != list(REQUIRED_EVIDENCE):
        raise EvalError("protocol required evidence does not match the frozen contract")
    comparison = _mapping(protocol.get("comparison"), "protocol.comparison")
    _exact_keys(comparison, {"pi_task_ids"}, "protocol.comparison")
    if comparison.get("pi_task_ids") != list(FIXED_PI_TASK_IDS):
        raise EvalError("protocol Pi comparison task IDs do not match the frozen contract")
    thresholds = _mapping(protocol.get("thresholds"), "protocol.thresholds")
    _exact_keys(thresholds, set(FROZEN_THRESHOLDS), "protocol.thresholds")
    for field, expected in FROZEN_THRESHOLDS.items():
        actual = thresholds.get(field)
        if type(actual) is not type(expected) or actual != expected:
            raise EvalError(f"protocol threshold is not frozen: {field}")
    return protocol


def _digest_tree(root: Path, inputs: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for relative in inputs:
        candidate = root / relative
        if candidate.is_dir():
            files.extend(path for path in candidate.rglob("*") if path.is_file())
        elif candidate.is_file():
            files.append(candidate)
        else:
            raise EvalError(f"dataset material is missing: {relative}")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash(path).encode("ascii"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def dataset_hash(root: Path = DATASET_ROOT) -> str:
    return _digest_tree(root, ("manifest.toml", "README.md", "THIRD_PARTY_NOTICES.md", "tasks"))


def _file_snapshot(root: Path) -> list[dict[str, str]]:
    snapshots: list[dict[str, str]] = []
    for relative in ("pyproject.toml", "uv.lock", "src/morrow/resources/runtime-policy.toml"):
        path = root / relative
        if path.is_file():
            snapshots.append({"path": relative, "sha256": file_hash(path)})
    return snapshots


def load_tasks() -> tuple[Task, ...]:
    try:
        with MANIFEST_PATH.open("rb") as handle:
            manifest = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EvalError("evaluation manifest is unreadable") from exc
    if manifest.get("version") != 1:
        raise EvalError("unsupported evaluation manifest version")
    try:
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
    except (KeyError, TypeError) as exc:
        raise EvalError("evaluation manifest contains an invalid task") from exc
    identifiers = [task.id for task in tasks]
    if len(tasks) != 10 or len(set(identifiers)) != len(identifiers):
        raise EvalError("evaluation manifest must contain ten uniquely identified tasks")
    for task in tasks:
        if not task.prompt_path.is_file():
            raise EvalError(f"task prompt is missing: {task.id}")
        for field in ("expected_paths", "required_paths"):
            paths = task.values.get(field, [])
            if not isinstance(paths, list):
                raise EvalError(f"{task.id}.{field} must be a list")
            for path in paths:
                _safe_path(path, f"{task.id}.{field}")
    return tasks


def task_by_id(task_id: str) -> Task:
    normalized = task_id.upper()
    for task in load_tasks():
        if task.id == normalized:
            return task
    raise EvalError(f"unknown task: {task_id}")


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise EvalError("evaluation command timed out") from exc


def git_archive(
    commit: str,
    destination: Path,
    paths: list[str] | None = None,
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> None:
    command = ["git", "archive", "--format=tar", commit]
    if paths:
        command.extend(["--", *paths])
    result = subprocess.run(command, cwd=repository_root, check=False, capture_output=True)
    if result.returncode:
        raise EvalError("evaluation baseline commit is unavailable")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                member_path = Path(member.name)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise EvalError("unsafe path in git archive")
            archive.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise EvalError("evaluation baseline archive is invalid") from exc


def copy_material(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise EvalError(f"evaluation material is missing: {source.name}")
    for source_path in sorted(source.rglob("*")):
        if source_path.is_dir():
            continue
        relative = source_path.relative_to(source)
        if relative.suffix == ".txt":
            relative = relative.with_suffix("")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source_path, target)
        except OSError as exc:
            raise EvalError("unable to copy evaluation material") from exc


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
            raise EvalError("failed to initialize evaluation workspace")


def _marker() -> dict[str, object]:
    return {
        "dataset": "morrow-code-agent-mini",
        "manifest_version": 1,
        "task_id": None,
        "gold_workspace": False,
        "protocol_id": "s7p-00",
        "protocol_version": 1,
    }


def prepare_task(
    task: Task,
    output: Path,
    *,
    gold: bool = False,
    repository_root: Path = REPOSITORY_ROOT,
) -> None:
    output = output.resolve()
    if output.exists():
        raise EvalError(f"output already exists: {output}")
    output.mkdir(parents=True)
    try:
        if task.is_history:
            commit_key = "gold_commit" if gold else "base_commit"
            git_archive(str(task.values[commit_key]), output, repository_root=repository_root)
        else:
            starter = DATASET_ROOT / str(task.values["starter_dir"])
            copy_material(starter, output)
            if gold:
                solution = DATASET_ROOT / str(task.values["solution_dir"])
                copy_material(solution, output)
        shutil.copyfile(task.prompt_path, output / "TASK.md")
        marker = _marker()
        marker["task_id"] = task.id
        marker["gold_workspace"] = gold
        (output / MARKER_NAME).write_text(canonical_json(marker), encoding="utf-8")
        initialize_repository(output)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def verify_marker(task: Task, workspace: Path) -> None:
    marker_path = workspace / MARKER_NAME
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvalError("workspace does not contain a valid evaluation marker") from exc
    if not isinstance(marker, Mapping) or marker.get("task_id") != task.id:
        raise EvalError("workspace marker does not match the requested task")


def verification_command(task: Task, workspace: Path, verifier_root: Path) -> list[str]:
    test_files = [str(verifier_root / str(path)) for path in task.values["test_files"]]
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *test_files]
    test_filter = task.values.get("test_filter")
    if test_filter:
        command.extend(["-k", str(test_filter)])
    return command


def run_verification(
    task: Task,
    workspace: Path,
    *,
    capture: bool = False,
    timeout_seconds: float | None = None,
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
            timeout=DEFAULT_VERIFIER_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds,
        )


def git_tree_hash(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        raise EvalError("evaluation workspace has no baseline tree")
    return "sha256:" + hashlib.sha256(result.stdout.strip().encode("ascii")).hexdigest()


def _git_commit(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}\n?", result.stdout):
        raise EvalError("evaluation workspace has no baseline commit")
    return result.stdout.strip()


def _git_status_lines(workspace: Path, *, include_ignored: bool = False) -> list[str]:
    command = ["git", "status", "--porcelain=v1", "--untracked-files=all"]
    if include_ignored:
        command.append("--ignored")
    result = subprocess.run(
        command,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise EvalError("unable to collect workspace Git status")
    return result.stdout.splitlines()


def _status_entries(lines: list[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in lines:
        if len(line) < 4:
            raise EvalError("workspace Git status is malformed")
        status = line[:2]
        raw_path = line[3:]
        paths = [part.strip() for part in raw_path.split(" -> ") if part.strip()]
        if not paths:
            raise EvalError("workspace Git status contains an empty path")
        for path in paths:
            entries.append({"status": status, "path": _safe_path(path, "workspace path")})
    return entries


def _workspace_unsafe_paths(workspace: Path) -> list[str]:
    unsafe: list[str] = []
    pending = [workspace.resolve()]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise EvalError("unable to inspect workspace files") from exc
        for entry in children:
            relative = Path(entry.path).relative_to(workspace.resolve())
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise EvalError("unable to inspect workspace files") from exc
            mode = entry_stat.st_mode
            if relative == Path(".git"):
                if stat.S_ISDIR(mode):
                    continue
                unsafe.append(relative.as_posix())
                continue
            if relative.parts and relative.parts[0] == ".git":
                continue
            if stat.S_ISLNK(mode):
                unsafe.append(relative.as_posix())
            elif stat.S_ISDIR(mode):
                pending.append(Path(entry.path))
            elif not stat.S_ISREG(mode) or entry_stat.st_nlink > 1:
                unsafe.append(relative.as_posix())
    return sorted(set(unsafe))


def _validate_workspace_root(bundle: Path, workspace: Path) -> None:
    try:
        mode = workspace.lstat().st_mode
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise EvalError("run bundle workspace is missing or unreadable") from exc
    expected = bundle.resolve() / "workspace"
    if not stat.S_ISDIR(mode) or resolved != expected:
        raise EvalError("run bundle workspace must be a real directory inside the bundle")


def _git_diff(workspace: Path, entries: list[dict[str, str]]) -> bytes:
    result = subprocess.run(
        [
            "git",
            "diff",
            "HEAD",
            "--no-ext-diff",
            "--binary",
            "--no-renames",
            "--no-color",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise EvalError("unable to collect workspace Git diff")
    chunks = [result.stdout]
    unsafe_paths = set(_workspace_unsafe_paths(workspace))
    for entry in entries:
        if entry["status"] != "??" or entry["path"] in unsafe_paths:
            continue
        untracked = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--binary",
                "--no-color",
                "--",
                "/dev/null",
                entry["path"],
            ],
            cwd=workspace,
            check=False,
            capture_output=True,
        )
        if untracked.returncode not in {0, 1}:
            raise EvalError("unable to collect untracked workspace diff")
        chunks.append(untracked.stdout)
    value = b"\n".join(chunk for chunk in chunks if chunk)
    _reject_sensitive_text(value.decode("utf-8", errors="replace"), "workspace diff")
    return value


def _source_status(root: Path, *, diagnostic_dirty: bool) -> dict[str, object]:
    lines = _git_status_lines(root)
    entries = _status_entries(lines)
    if entries and not diagnostic_dirty:
        raise EvalError("clean source checkout is required for a comparable run")
    bounded = entries[:MAX_SOURCE_DIRTY_ENTRIES]
    return {
        "state": "clean" if not entries else "dirty-diagnostic",
        "comparison_eligible": not entries,
        "path_count": len(entries),
        "truncated": len(entries) > len(bounded),
        "paths": [entry["path"] for entry in bounded],
        "summary_sha256": bytes_hash("\n".join(lines).encode("utf-8")),
    }


def _baseline_source(task: Task) -> dict[str, str]:
    if task.is_history:
        return {"kind": "git-archive", "ref": str(task.values["base_commit"])}
    return {"kind": "starter-material", "ref": str(task.values["starter_dir"])}


def _change_policy(task: Task) -> dict[str, object]:
    allowed = [
        _safe_path(path, f"{task.id}.expected_paths")
        for path in task.values.get("expected_paths", [])
    ]
    required = [
        _safe_path(path, f"{task.id}.required_paths")
        for path in task.values.get("required_paths", [])
    ]
    return {
        "mode": "allowlist",
        "allowed_paths": sorted(set(allowed)),
        "required_paths": sorted(set(required)),
        "requires_change": bool(task.values.get("requires_change", True)),
        "unexpected_change": "fail",
    }


def _task_spec(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "origin": task.origin,
        "difficulty": task.difficulty,
        "category": task.category,
        "sha256": content_hash(task.values),
        "change_policy": _change_policy(task),
    }


def _path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def start_run(
    task: Task | str,
    output: Path,
    *,
    repetition: int,
    profile: Mapping[str, object] | Path,
    source_root: Path = REPOSITORY_ROOT,
    diagnostic_dirty: bool = False,
) -> Path:
    """Create a frozen manifest and fresh baseline workspace before Agent execution."""

    if isinstance(task, str):
        task = task_by_id(task)
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 1:
        raise EvalError("repetition must be a positive integer")
    normalized_profile = (
        load_profile(profile) if isinstance(profile, Path) else validate_profile(profile)
    )
    source_root = source_root.resolve()
    output = output.resolve()
    if output.exists():
        raise EvalError(f"output already exists: {output}")
    if _path_is_inside(output, DATASET_ROOT.resolve()) or _path_is_inside(output, source_root):
        raise EvalError("run bundle must be outside the evaluator and source checkout")

    protocol = load_protocol()
    source_dirty = _source_status(source_root, diagnostic_dirty=diagnostic_dirty)
    protocol_snapshot = {
        "id": protocol["protocol"]["id"],
        "version": protocol["protocol"]["version"],
        "sha256": file_hash(PROTOCOL_PATH),
    }
    dataset_snapshot = {
        "id": "morrow-code-agent-mini",
        "revision": "manifest-v1",
        "manifest_sha256": file_hash(MANIFEST_PATH),
        "sha256": dataset_hash(),
    }
    config_snapshot = _file_snapshot(source_root)
    try:
        output.mkdir(parents=True)
        workspace = output / "workspace"
        prepare_task(task, workspace)
        unsafe_paths = _workspace_unsafe_paths(workspace)
        if unsafe_paths:
            raise EvalError("prepared workspace contains unsafe file types")
        manifest: dict[str, object] = {
            "schema": "morrow.s7p-00.run-manifest.v1",
            "run": {
                "id": uuid.uuid4().hex,
                "task_id": task.id,
                "repetition": repetition,
                "created_at": datetime.now(UTC).isoformat(),
                "mode": "diagnostic_dirty"
                if not source_dirty["comparison_eligible"]
                else "comparable",
            },
            "source": {
                "repository": "morrow",
                "evaluator_commit": _git_commit(source_root),
                "dirty": source_dirty,
            },
            "dataset": dataset_snapshot,
            "protocol": protocol_snapshot,
            "task": _task_spec(task),
            "workspace": {
                "directory": "workspace/",
                "baseline_source": _baseline_source(task),
                "baseline_commit": _git_commit(workspace),
                "baseline_tree_sha256": git_tree_hash(workspace),
                "marker_sha256": file_hash(workspace / MARKER_NAME),
            },
            "configuration": config_snapshot,
            "profile": normalized_profile,
            "profile_sha256": content_hash(normalized_profile),
            "evidence": {"required": list(REQUIRED_EVIDENCE)},
        }
        manifest["integrity"] = {"algorithm": "sha256", "sha256": content_hash(manifest)}
        _write_json_create(output / "run-manifest.json", manifest)
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    return output


def _manifest_path(value: Path) -> Path:
    path = value.absolute()
    if path.is_dir():
        path = path / "run-manifest.json"
    if path.name != "run-manifest.json":
        raise EvalError("manifest path must name run-manifest.json")
    return path


def _regular_artifact(path: Path, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise EvalError(f"{label} is missing or unreadable") from exc
    if not stat.S_ISREG(mode) or resolved != path:
        raise EvalError(f"{label} must be a regular file inside the run bundle")
    return path


def _validate_integrity(value: Mapping[str, object], label: str) -> str:
    integrity = _mapping(value.get("integrity"), f"{label}.integrity")
    _exact_keys(integrity, {"algorithm", "sha256"}, f"{label}.integrity")
    if integrity.get("algorithm") != "sha256":
        raise EvalError(f"{label} uses an unsupported integrity algorithm")
    expected = content_hash(_without_integrity(value))
    if integrity.get("sha256") != expected:
        raise EvalError(f"{label} integrity mismatch")
    return expected


def _validate_manifest(manifest: Mapping[str, object]) -> str:
    _reject_sensitive_content(manifest, "run manifest")
    _exact_keys(
        manifest,
        {
            "schema",
            "run",
            "source",
            "dataset",
            "protocol",
            "task",
            "workspace",
            "configuration",
            "profile",
            "profile_sha256",
            "evidence",
            "integrity",
        },
        "run manifest",
    )
    if manifest.get("schema") != "morrow.s7p-00.run-manifest.v1":
        raise EvalError("unsupported run manifest schema")
    run = _mapping(manifest.get("run"), "run manifest.run")
    _exact_keys(run, {"id", "task_id", "repetition", "created_at", "mode"}, "run manifest.run")
    run_id = _text(run.get("id"), "run manifest.run.id")
    if not IDENTIFIER_RE.fullmatch(run_id):
        raise EvalError("run manifest.run.id is invalid")
    task_id = _text(run.get("task_id"), "run manifest.run.task_id")
    if task_id != task_id.upper():
        raise EvalError("run manifest task ID must be canonical")
    repetition = run.get("repetition")
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 1:
        raise EvalError("run manifest repetition is invalid")
    _text(run.get("created_at"), "run manifest.run.created_at")
    if run.get("mode") not in {"comparable", "diagnostic_dirty"}:
        raise EvalError("run manifest mode is invalid")

    source = _mapping(manifest.get("source"), "run manifest.source")
    _exact_keys(source, {"repository", "evaluator_commit", "dirty"}, "run manifest.source")
    _text(source.get("repository"), "run manifest.source.repository")
    commit = _text(source.get("evaluator_commit"), "run manifest.source.evaluator_commit")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise EvalError("run manifest evaluator commit is invalid")
    dirty = _mapping(source.get("dirty"), "run manifest.source.dirty")
    _exact_keys(
        dirty,
        {"state", "comparison_eligible", "path_count", "truncated", "paths", "summary_sha256"},
        "run manifest.source.dirty",
    )
    if dirty.get("state") not in {"clean", "dirty-diagnostic"}:
        raise EvalError("run manifest dirty state is invalid")
    if not isinstance(dirty.get("comparison_eligible"), bool):
        raise EvalError("run manifest comparability is invalid")
    if type(dirty.get("path_count")) is not int or dirty["path_count"] < 0:
        raise EvalError("run manifest dirty path count is invalid")
    if not isinstance(dirty.get("truncated"), bool):
        raise EvalError("run manifest dirty truncation is invalid")
    if not isinstance(dirty.get("paths"), list):
        raise EvalError("run manifest dirty paths are invalid")
    if len(dirty["paths"]) > MAX_SOURCE_DIRTY_ENTRIES or len(dirty["paths"]) > dirty["path_count"]:
        raise EvalError("run manifest dirty paths exceed their bounded count")
    if dirty["truncated"] != (dirty["path_count"] > MAX_SOURCE_DIRTY_ENTRIES):
        raise EvalError("run manifest dirty truncation is inconsistent")
    if dirty["state"] == "clean" and (
        dirty["path_count"]
        or dirty["paths"]
        or dirty["truncated"]
        or not dirty["comparison_eligible"]
    ):
        raise EvalError("clean run manifest dirty evidence is inconsistent")
    if dirty["state"] == "dirty-diagnostic" and dirty["comparison_eligible"]:
        raise EvalError("dirty diagnostic run cannot be comparison eligible")
    if (run["mode"] == "comparable") != dirty["comparison_eligible"]:
        raise EvalError("run manifest mode and source comparability disagree")
    for path in dirty["paths"]:
        _safe_path(path, "run manifest dirty path")
    _is_sha256(dirty.get("summary_sha256"), "run manifest dirty summary")

    dataset = _mapping(manifest.get("dataset"), "run manifest.dataset")
    _exact_keys(dataset, {"id", "revision", "manifest_sha256", "sha256"}, "run manifest.dataset")
    if dataset.get("id") != "morrow-code-agent-mini":
        raise EvalError("run manifest dataset ID is invalid")
    if dataset.get("revision") != "manifest-v1":
        raise EvalError("run manifest dataset revision is invalid")
    _is_sha256(dataset.get("manifest_sha256"), "run manifest.dataset.manifest_sha256")
    _is_sha256(dataset.get("sha256"), "run manifest.dataset.sha256")

    protocol = _mapping(manifest.get("protocol"), "run manifest.protocol")
    _exact_keys(protocol, {"id", "version", "sha256"}, "run manifest.protocol")
    if protocol.get("id") != "s7p-00" or protocol.get("version") != 1:
        raise EvalError("run manifest protocol revision is invalid")
    _is_sha256(protocol.get("sha256"), "run manifest.protocol.sha256")

    task = _mapping(manifest.get("task"), "run manifest.task")
    _exact_keys(
        task,
        {"id", "title", "origin", "difficulty", "category", "sha256", "change_policy"},
        "run manifest.task",
    )
    if task.get("id") != task_id:
        raise EvalError("run manifest task does not match run identity")
    for key in ("title", "origin", "difficulty", "category"):
        _text(task.get(key), f"run manifest.task.{key}")
    _is_sha256(task.get("sha256"), "run manifest.task.sha256")
    policy = _mapping(task.get("change_policy"), "run manifest.task.change_policy")
    _exact_keys(
        policy,
        {"mode", "allowed_paths", "required_paths", "requires_change", "unexpected_change"},
        "run manifest.task.change_policy",
    )
    if policy.get("mode") != "allowlist" or policy.get("unexpected_change") != "fail":
        raise EvalError("run manifest change policy is invalid")
    for field in ("allowed_paths", "required_paths"):
        paths = policy.get(field)
        if not isinstance(paths, list):
            raise EvalError("run manifest change policy paths are invalid")
        for path in paths:
            _safe_path(path, f"run manifest change policy.{field}")
    if not isinstance(policy.get("requires_change"), bool):
        raise EvalError("run manifest change policy requirement is invalid")
    task_record = task_by_id(task_id)
    if dict(task) != _task_spec(task_record):
        raise EvalError("run manifest task snapshot does not match the dataset")

    workspace = _mapping(manifest.get("workspace"), "run manifest.workspace")
    _exact_keys(
        workspace,
        {
            "directory",
            "baseline_source",
            "baseline_commit",
            "baseline_tree_sha256",
            "marker_sha256",
        },
        "run manifest.workspace",
    )
    if workspace.get("directory") != "workspace/":
        raise EvalError("run manifest workspace directory is invalid")
    baseline_source = _mapping(
        workspace.get("baseline_source"), "run manifest.workspace.baseline_source"
    )
    _exact_keys(baseline_source, {"kind", "ref"}, "run manifest.workspace.baseline_source")
    _text(baseline_source.get("kind"), "run manifest baseline source kind")
    _text(baseline_source.get("ref"), "run manifest baseline source ref")
    if dict(baseline_source) != _baseline_source(task_record):
        raise EvalError("run manifest baseline source does not match the dataset")
    baseline_commit = _text(
        workspace.get("baseline_commit"), "run manifest.workspace.baseline_commit"
    )
    if not re.fullmatch(r"[0-9a-f]{40}", baseline_commit):
        raise EvalError("run manifest baseline commit is invalid")
    _is_sha256(workspace.get("baseline_tree_sha256"), "run manifest baseline tree")
    _is_sha256(workspace.get("marker_sha256"), "run manifest marker hash")

    configuration = manifest.get("configuration")
    if not isinstance(configuration, list):
        raise EvalError("run manifest configuration snapshot is invalid")
    for item in configuration:
        entry = _mapping(item, "run manifest.configuration")
        _exact_keys(entry, {"path", "sha256"}, "run manifest.configuration")
        _safe_path(entry.get("path"), "run manifest.configuration.path")
        _is_sha256(entry.get("sha256"), "run manifest.configuration.sha256")

    profile = validate_profile(_mapping(manifest.get("profile"), "run manifest.profile"))
    if manifest.get("profile_sha256") != content_hash(profile):
        raise EvalError("run manifest profile hash mismatch")
    evidence = _mapping(manifest.get("evidence"), "run manifest.evidence")
    _exact_keys(evidence, {"required"}, "run manifest.evidence")
    if evidence.get("required") != list(REQUIRED_EVIDENCE):
        raise EvalError("run manifest required evidence is incomplete")
    return _validate_integrity(manifest, "run manifest")


def read_manifest(path: Path) -> dict[str, object]:
    manifest_path = _manifest_path(path)
    _regular_artifact(manifest_path, "run manifest")
    return _read_json(manifest_path, "run manifest")


def _current_dataset_snapshot() -> dict[str, str]:
    return {
        "manifest_sha256": file_hash(MANIFEST_PATH),
        "sha256": dataset_hash(),
    }


def _validate_current_revisions(
    manifest: Mapping[str, object], *, source_root: Path | None = None
) -> None:
    protocol = _mapping(manifest["protocol"], "run manifest.protocol")
    current_protocol = load_protocol()
    if protocol["id"] != current_protocol["protocol"]["id"]:
        raise EvalError("protocol ID mismatch")
    if protocol["version"] != current_protocol["protocol"]["version"]:
        raise EvalError("protocol version mismatch")
    if protocol["sha256"] != file_hash(PROTOCOL_PATH):
        raise EvalError("protocol hash mismatch")

    dataset = _mapping(manifest["dataset"], "run manifest.dataset")
    current_dataset = _current_dataset_snapshot()
    if (
        dataset["manifest_sha256"] != current_dataset["manifest_sha256"]
        or dataset["sha256"] != current_dataset["sha256"]
    ):
        raise EvalError("dataset hash mismatch")
    if source_root is not None:
        source_root = source_root.resolve()
        _source_status(source_root, diagnostic_dirty=False)
        source = _mapping(manifest["source"], "run manifest.source")
        if source["evaluator_commit"] != _git_commit(source_root):
            raise EvalError("evaluator commit mismatch")
        if manifest["configuration"] != _file_snapshot(source_root):
            raise EvalError("configuration snapshot mismatch")


def rebuild_workspace(
    manifest_path: Path,
    output: Path,
    *,
    source_root: Path = REPOSITORY_ROOT,
) -> Path:
    """Recreate a baseline workspace and verify its recorded tree identity."""

    manifest_path = _manifest_path(manifest_path)
    manifest = read_manifest(manifest_path)
    _validate_manifest(manifest)
    source_root = source_root.resolve()
    _validate_current_revisions(manifest, source_root=source_root)
    task = task_by_id(str(manifest["run"]["task_id"]))
    if manifest["source"]["dirty"]["comparison_eligible"] is False:
        raise EvalError("dirty diagnostic manifest cannot be used for a comparable rebuild")
    output = output.resolve()
    if output.exists():
        raise EvalError(f"output already exists: {output}")
    if _path_is_inside(output, DATASET_ROOT.resolve()) or _path_is_inside(output, source_root):
        raise EvalError("rebuilt workspace must be outside the evaluator and source checkout")
    try:
        prepare_task(task, output)
        unsafe_paths = _workspace_unsafe_paths(output)
        if unsafe_paths:
            raise EvalError("rebuilt workspace contains unsafe file types")
        expected_workspace = _mapping(manifest["workspace"], "run manifest.workspace")
        if git_tree_hash(output) != expected_workspace["baseline_tree_sha256"]:
            raise EvalError("rebuilt workspace baseline tree mismatch")
        if file_hash(output / MARKER_NAME) != expected_workspace["marker_sha256"]:
            raise EvalError("rebuilt workspace marker mismatch")
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    return output


def _unavailable_runtime() -> dict[str, object]:
    return {
        "schema_version": 1,
        "availability": "unavailable",
        "tool_states": {state: "unavailable" for state in TOOL_TERMINAL_STATES},
        "tool_diagnostics": {field: "unavailable" for field in TOOL_DIAGNOSTIC_FIELDS},
        "usage": {field: "unavailable" for field in USAGE_FIELDS},
        "stop": {"code": "evidence_unavailable", "reason": "evidence_unavailable"},
    }


def normalize_runtime_evidence(
    value: Mapping[str, object] | Path | None,
) -> dict[str, object]:
    if value is None:
        return _unavailable_runtime()
    if isinstance(value, Path):
        raw = _read_json(value, "runtime evidence")
    else:
        raw = dict(value)
    _reject_sensitive_content(raw, "runtime evidence")
    _exact_keys(
        raw,
        {"schema_version", "availability", "tool_states", "tool_diagnostics", "usage", "stop"},
        "runtime evidence",
    )
    if raw.get("schema_version") != 1:
        raise EvalError("unsupported runtime evidence schema")
    availability = raw.get("availability", "unavailable")
    if availability not in {"available", "unavailable"}:
        raise EvalError("runtime evidence availability is invalid")

    normalized: dict[str, object] = {"schema_version": 1, "availability": availability}
    raw_states = raw.get("tool_states")
    if raw_states is None and availability == "unavailable":
        states: dict[str, int | str] = {state: "unavailable" for state in TOOL_TERMINAL_STATES}
    else:
        state_mapping = _mapping(raw_states, "runtime evidence.tool_states")
        _exact_keys(state_mapping, set(TOOL_TERMINAL_STATES), "runtime evidence.tool_states")
        states = {
            state: _number_or_unavailable(
                state_mapping.get(state), f"runtime evidence.tool_states.{state}", integer=True
            )
            for state in TOOL_TERMINAL_STATES
        }
        if availability == "available" and any(value == "unavailable" for value in states.values()):
            raise EvalError("available runtime evidence must account for every tool terminal state")
    normalized["tool_states"] = states

    raw_diagnostics = raw.get("tool_diagnostics")
    if raw_diagnostics is None:
        diagnostics: dict[str, int | str] = {
            field: "unavailable" for field in TOOL_DIAGNOSTIC_FIELDS
        }
    else:
        diagnostic_mapping = _mapping(raw_diagnostics, "runtime evidence.tool_diagnostics")
        _exact_keys(
            diagnostic_mapping, set(TOOL_DIAGNOSTIC_FIELDS), "runtime evidence.tool_diagnostics"
        )
        diagnostics = {
            field: _number_or_unavailable(
                diagnostic_mapping.get(field),
                f"runtime evidence.tool_diagnostics.{field}",
                integer=True,
            )
            for field in TOOL_DIAGNOSTIC_FIELDS
        }
    normalized["tool_diagnostics"] = diagnostics

    raw_usage = raw.get("usage")
    usage_mapping = _mapping(raw_usage, "runtime evidence.usage") if raw_usage is not None else {}
    _exact_keys(usage_mapping, set(USAGE_FIELDS), "runtime evidence.usage")
    usage: dict[str, int | float | str] = {}
    for field in USAGE_FIELDS:
        usage[field] = _number_or_unavailable(
            usage_mapping.get(field, "unavailable"),
            f"runtime evidence.usage.{field}",
            integer=field != "cost",
        )
    normalized["usage"] = usage

    if availability == "unavailable" and any(
        value != "unavailable"
        for section in (states, diagnostics, usage)
        for value in section.values()
    ):
        raise EvalError("unavailable runtime evidence must not contain numeric metrics")

    if availability == "available" and all(
        value != "unavailable" for section in (states, diagnostics) for value in section.values()
    ):
        total_tool_calls = diagnostics["total_tool_calls"]
        unaccounted_tool_calls = diagnostics["unaccounted_tool_calls"]
        terminal_tool_calls = sum(states.values())
        if total_tool_calls != terminal_tool_calls + unaccounted_tool_calls:
            raise EvalError("runtime evidence tool terminal accounting is inconsistent")
        if diagnostics["invalid_arguments"] > total_tool_calls:
            raise EvalError("runtime evidence invalid argument count is inconsistent")
        if diagnostics["basic_tool_blocked"] > total_tool_calls:
            raise EvalError("runtime evidence basic-tool blocker count is inconsistent")

    stop_mapping = _mapping(raw.get("stop"), "runtime evidence.stop")
    _exact_keys(stop_mapping, {"code", "reason"}, "runtime evidence.stop")
    stop_code = _text(stop_mapping.get("code"), "runtime evidence.stop.code")
    if stop_code not in STOP_CODES:
        raise EvalError("runtime evidence stop code is invalid")
    stop_reason = _text(stop_mapping.get("reason"), "runtime evidence.stop.reason")
    if stop_reason != stop_code:
        raise EvalError("runtime evidence stop reason must equal its stop code")
    if availability == "unavailable" and stop_code != "evidence_unavailable":
        raise EvalError("unavailable runtime evidence must use evidence_unavailable stop code")
    normalized["stop"] = {"code": stop_code, "reason": stop_code}
    return normalized


def _bounded_patch(patch: bytes) -> tuple[bytes, bool]:
    if len(patch) <= MAX_TEXT_BYTES:
        return patch, False
    return patch[:MAX_TEXT_BYTES], True


def _collect_workspace_evidence(workspace: Path) -> tuple[dict[str, object], bytes, bool]:
    try:
        lines = _git_status_lines(workspace, include_ignored=True)
        entries = _status_entries(lines)
        unsafe_paths = _workspace_unsafe_paths(workspace)
        patch = _git_diff(workspace, entries)
    except EvalError:
        status = {
            "schema_version": 1,
            "availability": "unavailable",
            "error_code": "workspace_git_unavailable",
            "changed_paths": [],
            "ignored_paths": [],
            "unsafe_paths": [],
            "entries": [],
            "path_count": 0,
            "truncated": False,
            "patch_truncated": False,
        }
        return status, b"", False
    all_changed_paths = sorted({entry["path"] for entry in entries} | set(unsafe_paths))
    changed_paths = all_changed_paths[:MAX_STATUS_ENTRIES]
    bounded_unsafe_paths = [path for path in unsafe_paths if path in set(changed_paths)]
    patch_bytes, patch_truncated = _bounded_patch(patch)
    status = {
        "schema_version": 1,
        "availability": "available",
        "entries": entries[:MAX_STATUS_ENTRIES],
        "changed_paths": changed_paths,
        "ignored_paths": sorted(
            path
            for path in {entry["path"] for entry in entries if entry["status"] == "!!"}
            if path in set(changed_paths)
        ),
        "unsafe_paths": bounded_unsafe_paths,
        "path_count": max(len(all_changed_paths), len(entries)),
        "truncated": max(len(all_changed_paths), len(entries)) > MAX_STATUS_ENTRIES,
        "patch_truncated": patch_truncated,
        "status_sha256": bytes_hash("\n".join(lines).encode("utf-8")),
    }
    return status, patch_bytes, not status["truncated"] and not patch_truncated


def _classify(
    *,
    verifier_status: str,
    verifier_passed: bool,
    unexpected: list[str],
    expected_change_present: bool,
    required_paths_present: bool,
    runtime: Mapping[str, object],
) -> tuple[str, str]:
    if runtime["availability"] == "unavailable":
        return "BLOCKED_ENV", "runtime evidence unavailable"
    if verifier_status == "error":
        return "FAIL_RUNTIME", "external verifier could not be invoked"
    stop_code = _mapping(runtime["stop"], "runtime evidence.stop")["code"]
    mapped = {
        "tool_contract_failed": "FAIL_TOOL_CONTRACT",
        "runtime_failed": "FAIL_RUNTIME",
        "policy_denied": "DENIED_POLICY",
        "environment_blocked": "BLOCKED_ENV",
        "budget_exhausted": "BUDGET_EXHAUSTED",
        "cancelled": "FAIL_RUNTIME",
    }
    if stop_code in mapped:
        return mapped[stop_code], f"run stopped with {stop_code}"
    if verifier_passed and unexpected:
        return "FAIL_MODEL", "verifier passed but unexpected workspace changes were recorded"
    if verifier_passed and not required_paths_present:
        return "FAIL_MODEL", "verifier passed but required workspace changes were missing"
    if verifier_passed and not expected_change_present:
        return "FAIL_MODEL", "verifier passed but no expected change was recorded"
    if verifier_passed:
        return "PASS", "verifier passed and workspace changes matched the policy"
    if stop_code == "verifier_failed":
        return "FAIL_MODEL", "external verifier reported failure"
    return "FAIL_MODEL", "external verifier did not pass"


def _read_verifier_output(result: subprocess.CompletedProcess[str]) -> bytes:
    output = result.stdout or ""
    if not isinstance(output, str):
        raise EvalError("external verifier output is not text")
    _reject_sensitive_text(output, "verifier output")
    raw = output.encode("utf-8")
    if len(raw) > MAX_TEXT_BYTES:
        raise EvalError("external verifier output exceeds the evidence bound")
    return raw


def _verifier_timeout(manifest: Mapping[str, object]) -> float:
    profile = _mapping(manifest["profile"], "run manifest.profile")
    budgets = _mapping(profile["budgets"], "run manifest.profile.budgets")
    deadline = budgets["deadline_seconds"]
    if deadline == "unavailable":
        return DEFAULT_VERIFIER_TIMEOUT_SECONDS
    return min(float(deadline), DEFAULT_VERIFIER_TIMEOUT_SECONDS)


def _cleanup_partial_finalization(bundle: Path) -> None:
    run_result = bundle / "run-result.json"
    has_run_result = run_result.exists() or run_result.is_symlink()
    complete_artifacts = all(
        (bundle / filename).is_file() and not (bundle / filename).is_symlink()
        for filename in REQUIRED_EVIDENCE[1:]
    )
    if has_run_result and complete_artifacts:
        raise EvalError("run bundle is already finalized: run-result.json")
    existing = [
        bundle / filename
        for filename in REQUIRED_EVIDENCE[1:]
        if (bundle / filename).exists() or (bundle / filename).is_symlink()
    ]
    if has_run_result and run_result not in existing:
        existing.append(run_result)
    for path in existing:
        try:
            path.unlink()
        except OSError as exc:
            raise EvalError("unable to clear incomplete finalization") from exc
    try:
        stale_staging = [
            path
            for path in bundle.iterdir()
            if path.name.startswith(".finalize-") and path.is_dir()
        ]
        for path in stale_staging:
            shutil.rmtree(path)
    except OSError as exc:
        raise EvalError("unable to clear incomplete finalization") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise EvalError("unable to commit evaluation artifacts") from exc


def _write_finalization_artifacts(bundle: Path, artifacts: Mapping[str, bytes]) -> None:
    staging: Path | None = None
    promoted: list[Path] = []
    try:
        staging = Path(tempfile.mkdtemp(prefix=".finalize-", dir=bundle))
        for filename in REQUIRED_EVIDENCE[1:]:
            if filename not in artifacts:
                raise EvalError(f"finalization artifact is missing: {filename}")
            _write_bytes_create(staging / filename, artifacts[filename])
        _fsync_directory(staging)
        for filename in REQUIRED_EVIDENCE[1:]:
            destination = bundle / filename
            if destination.exists() or destination.is_symlink():
                raise EvalError(f"run bundle artifact already exists: {filename}")
            os.replace(staging / filename, destination)
            promoted.append(destination)
        _fsync_directory(bundle)
    except (EvalError, OSError) as exc:
        for path in reversed(promoted):
            try:
                path.unlink()
            except OSError:
                pass
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, EvalError):
            raise
        raise EvalError("unable to commit evaluation artifacts") from exc
    else:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def finalize_run(
    manifest_path: Path,
    runtime_evidence: Mapping[str, object] | Path | None = None,
) -> dict[str, object]:
    """Run the external verifier and create the immutable evidence/result artifacts."""

    manifest_path = _manifest_path(manifest_path)
    bundle = manifest_path.parent
    manifest = read_manifest(manifest_path)
    _validate_manifest(manifest)
    task = task_by_id(str(manifest["run"]["task_id"]))
    workspace = bundle / "workspace"
    _validate_workspace_root(bundle, workspace)
    _cleanup_partial_finalization(bundle)

    runtime = normalize_runtime_evidence(runtime_evidence)
    verifier_status = "not_run"
    verifier_returncode: int | str = "unavailable"
    verifier_passed = False
    unsafe_workspace_paths = _workspace_unsafe_paths(workspace)
    if unsafe_workspace_paths:
        verifier_status = "error"
        verifier_bytes = b"external verifier skipped: unsafe workspace file types\n"
    else:
        try:
            verification = run_verification(
                task,
                workspace,
                capture=True,
                timeout_seconds=_verifier_timeout(manifest),
            )
        except EvalError:
            verifier_status = "error"
            verifier_bytes = b"external verifier could not be invoked\n"
        else:
            verifier_status = "completed"
            verifier_returncode = verification.returncode
            verifier_passed = verification.returncode == 0
            verifier_bytes = _read_verifier_output(verification)
    status, patch_bytes, git_evidence_complete = _collect_workspace_evidence(workspace)
    changed_paths = list(status["changed_paths"]) if status["availability"] == "available" else []
    policy = _mapping(
        _mapping(manifest["task"], "run manifest.task")["change_policy"], "change policy"
    )
    allowed_paths = set(policy["allowed_paths"])
    required_paths = set(policy["required_paths"])
    expected_changed = sorted(set(changed_paths) & allowed_paths)
    unsafe_paths = set(status.get("unsafe_paths", []))
    unexpected = sorted((set(changed_paths) - allowed_paths) | unsafe_paths)
    missing_required = sorted(required_paths - set(expected_changed))
    expected_change_present = bool(expected_changed) or not policy["requires_change"]
    required_paths_present = not missing_required
    evidence_complete = (
        _runtime_evidence_complete(runtime)
        and status["availability"] == "available"
        and git_evidence_complete
        and verifier_status != "error"
    )
    result_class, reason = _classify(
        verifier_status=verifier_status,
        verifier_passed=verifier_passed,
        unexpected=unexpected,
        expected_change_present=expected_change_present,
        required_paths_present=required_paths_present,
        runtime=runtime,
    )
    pass_eligible = (
        result_class == "PASS"
        and verifier_passed
        and not unexpected
        and expected_change_present
        and required_paths_present
        and evidence_complete
        and manifest["source"]["dirty"]["comparison_eligible"]
        and _mapping(runtime["stop"], "runtime evidence.stop")["code"] == "completed"
    )
    if result_class == "PASS" and not pass_eligible:
        result_class, reason = "FAIL_RUNTIME", "required finalization evidence was incomplete"

    runtime_bytes = canonical_json(runtime).encode("utf-8")
    status_bytes = canonical_json(status).encode("utf-8")
    evidence = {
        "run-manifest.json": {
            "sha256": file_hash(manifest_path),
            "bytes": manifest_path.stat().st_size,
        },
        "runtime-evidence.json": {"sha256": bytes_hash(runtime_bytes), "bytes": len(runtime_bytes)},
        "verifier-output.txt": {"sha256": bytes_hash(verifier_bytes), "bytes": len(verifier_bytes)},
        "workspace-status.json": {"sha256": bytes_hash(status_bytes), "bytes": len(status_bytes)},
        "workspace-diff.patch": {"sha256": bytes_hash(patch_bytes), "bytes": len(patch_bytes)},
    }
    result: dict[str, object] = {
        "schema": "morrow.s7p-00.run-result.v1",
        "run": {
            "id": manifest["run"]["id"],
            "task_id": manifest["run"]["task_id"],
            "repetition": manifest["run"]["repetition"],
        },
        "protocol": manifest["protocol"],
        "profile_sha256": manifest["profile_sha256"],
        "result": {
            "class": result_class,
            "reason": reason,
            "reason_code": _mapping(runtime["stop"], "runtime evidence.stop")["code"],
        },
        "verifier": {
            "status": verifier_status,
            "exit_code": verifier_returncode,
            "passed": verifier_passed,
        },
        "paths": {
            "changed": changed_paths,
            "expected": expected_changed,
            "unexpected": unexpected,
            "missing_required": missing_required,
        },
        "quality": {
            "pass_eligible": pass_eligible,
            "evidence_complete": evidence_complete,
            "expected_change_present": expected_change_present,
            "required_paths_present": required_paths_present,
            "source_comparison_eligible": manifest["source"]["dirty"]["comparison_eligible"],
        },
        "tool_states": runtime["tool_states"],
        "tool_diagnostics": runtime["tool_diagnostics"],
        "usage": runtime["usage"],
        "stop": runtime["stop"],
        "evidence": evidence,
    }
    result["integrity"] = {"algorithm": "sha256", "sha256": content_hash(result)}
    _write_finalization_artifacts(
        bundle,
        {
            "runtime-evidence.json": runtime_bytes,
            "verifier-output.txt": verifier_bytes,
            "workspace-status.json": status_bytes,
            "workspace-diff.patch": patch_bytes,
            "run-result.json": canonical_json(result).encode("utf-8"),
        },
    )
    return result


def _validate_runtime_artifact(value: Mapping[str, object]) -> dict[str, object]:
    return normalize_runtime_evidence(value)


def _read_bounded_text_file(path: Path, label: str) -> bytes:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise EvalError(f"{label} is unreadable") from exc
    if len(value) > MAX_TEXT_BYTES:
        raise EvalError(f"{label} exceeds the evidence bound")
    try:
        text = value.decode("utf-8")
    except UnicodeError as exc:
        raise EvalError(f"{label} is not UTF-8 text") from exc
    _reject_sensitive_text(text, label)
    return value


def _validate_workspace_status(value: Mapping[str, object]) -> dict[str, object]:
    status = dict(value)
    availability = status.get("availability")
    if status.get("schema_version") != 1 or availability not in {"available", "unavailable"}:
        raise EvalError("workspace status schema is invalid")
    if availability == "available":
        _exact_keys(
            status,
            {
                "schema_version",
                "availability",
                "entries",
                "changed_paths",
                "ignored_paths",
                "unsafe_paths",
                "path_count",
                "truncated",
                "patch_truncated",
                "status_sha256",
            },
            "workspace status",
        )
    else:
        _exact_keys(
            status,
            {
                "schema_version",
                "availability",
                "error_code",
                "changed_paths",
                "ignored_paths",
                "unsafe_paths",
                "entries",
                "path_count",
                "truncated",
                "patch_truncated",
            },
            "workspace status",
        )
        _text(status.get("error_code"), "workspace status.error_code")

    entries = status.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_STATUS_ENTRIES:
        raise EvalError("workspace status entries are invalid")
    entry_paths: list[str] = []
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, f"workspace status.entries[{index}]")
        _exact_keys(entry, {"status", "path"}, f"workspace status.entries[{index}]")
        status_code = _text(entry.get("status"), f"workspace status.entries[{index}].status")
        if len(status_code) != 2:
            raise EvalError("workspace status entry code is invalid")
        entry_paths.append(_safe_path(entry.get("path"), "workspace status entry path"))
    normalized_lists: dict[str, list[str]] = {}
    for field in ("changed_paths", "ignored_paths", "unsafe_paths"):
        paths = status.get(field)
        if (
            not isinstance(paths, list)
            or len(paths) > MAX_STATUS_ENTRIES
            or any(not isinstance(path, str) for path in paths)
        ):
            raise EvalError(f"workspace status {field} are invalid")
        normalized_lists[field] = [
            _safe_path(path, f"workspace status {field} path") for path in paths
        ]
        if normalized_lists[field] != sorted(set(normalized_lists[field])):
            raise EvalError(f"workspace status {field} are not canonical")
    normalized_changed = normalized_lists["changed_paths"]
    if not set(normalized_lists["ignored_paths"]).issubset(normalized_changed):
        raise EvalError("workspace status ignored paths are inconsistent")
    if not set(normalized_lists["unsafe_paths"]).issubset(normalized_changed):
        raise EvalError("workspace status unsafe paths are inconsistent")
    path_count = status.get("path_count")
    if (
        type(path_count) is not int
        or path_count < len(entries)
        or path_count < len(normalized_changed)
    ):
        raise EvalError("workspace status path count is invalid")
    truncated = status.get("truncated")
    patch_truncated = status.get("patch_truncated")
    if not isinstance(truncated, bool) or not isinstance(patch_truncated, bool):
        raise EvalError("workspace status truncation flags are invalid")
    if truncated != (path_count > MAX_STATUS_ENTRIES):
        raise EvalError("workspace status truncation is inconsistent")
    if not truncated and normalized_changed != sorted(
        set(entry_paths) | set(normalized_lists["unsafe_paths"])
    ):
        raise EvalError("workspace status changed paths do not match entries")
    if availability == "available":
        _is_sha256(status.get("status_sha256"), "workspace status hash")
    elif (
        entries
        or normalized_changed
        or normalized_lists["ignored_paths"]
        or normalized_lists["unsafe_paths"]
        or path_count
        or truncated
        or patch_truncated
    ):
        raise EvalError("unavailable workspace status contains change evidence")
    return status


def _runtime_evidence_complete(runtime: Mapping[str, object]) -> bool:
    return runtime["availability"] == "available" and all(
        value != "unavailable"
        for section in (
            runtime["tool_states"],
            runtime["tool_diagnostics"],
            runtime["usage"],
        )
        for value in section.values()
    )


def validate_run_bundle(bundle: Path) -> dict[str, object]:
    """Revalidate a finalized bundle without changing it."""

    bundle = bundle.resolve()
    manifest_path = _manifest_path(bundle)
    _regular_artifact(manifest_path, "run manifest")
    manifest = read_manifest(manifest_path)
    manifest_hash = _validate_manifest(manifest)
    _validate_current_revisions(manifest)
    result_path = _regular_artifact(bundle / "run-result.json", "run result")
    result = _read_json(result_path, "run result")
    _reject_sensitive_content(result, "run result")
    _exact_keys(
        result,
        {
            "schema",
            "run",
            "protocol",
            "profile_sha256",
            "result",
            "verifier",
            "paths",
            "quality",
            "tool_states",
            "tool_diagnostics",
            "usage",
            "stop",
            "evidence",
            "integrity",
        },
        "run result",
    )
    if result.get("schema") != "morrow.s7p-00.run-result.v1":
        raise EvalError("unsupported run result schema")
    result_hash = _validate_integrity(result, "run result")
    run = _mapping(result["run"], "run result.run")
    manifest_run = _mapping(manifest["run"], "run manifest.run")
    if dict(run) != {
        "id": manifest_run["id"],
        "task_id": manifest_run["task_id"],
        "repetition": manifest_run["repetition"],
    }:
        raise EvalError("run result identity does not match its manifest")
    if (
        result["protocol"] != manifest["protocol"]
        or result["profile_sha256"] != manifest["profile_sha256"]
    ):
        raise EvalError("run result protocol/profile snapshot mismatch")

    evidence = _mapping(result["evidence"], "run result.evidence")
    expected_evidence = set(REQUIRED_EVIDENCE) - {"run-result.json"}
    if set(evidence) != expected_evidence:
        raise EvalError("run result evidence set is incomplete")
    for filename, raw_metadata in evidence.items():
        metadata = _mapping(raw_metadata, f"run result.evidence.{filename}")
        _exact_keys(metadata, {"sha256", "bytes"}, f"run result.evidence.{filename}")
        _is_sha256(metadata.get("sha256"), f"run result.evidence.{filename}.sha256")
        if type(metadata.get("bytes")) is not int or metadata["bytes"] < 0:
            raise EvalError("run result evidence byte count is invalid")
        path = _regular_artifact(bundle / filename, f"evidence artifact {filename}")
        if file_hash(path) != metadata["sha256"] or path.stat().st_size != metadata["bytes"]:
            raise EvalError(f"evidence hash mismatch: {filename}")
    if evidence["run-manifest.json"]["sha256"] != file_hash(manifest_path):
        raise EvalError("run result manifest hash mismatch")

    runtime = _validate_runtime_artifact(
        _read_json(
            _regular_artifact(bundle / "runtime-evidence.json", "runtime evidence"),
            "runtime evidence",
        )
    )
    if (
        runtime["tool_states"] != result["tool_states"]
        or runtime["tool_diagnostics"] != result["tool_diagnostics"]
    ):
        raise EvalError("run result tool evidence mismatch")
    if runtime["usage"] != result["usage"] or runtime["stop"] != result["stop"]:
        raise EvalError("run result runtime evidence mismatch")
    status = _validate_workspace_status(
        _read_json(
            _regular_artifact(bundle / "workspace-status.json", "workspace status"),
            "workspace status",
        )
    )
    _read_bounded_text_file(
        _regular_artifact(bundle / "verifier-output.txt", "verifier output"),
        "verifier output",
    )
    _read_bounded_text_file(
        _regular_artifact(bundle / "workspace-diff.patch", "workspace diff"),
        "workspace diff",
    )

    result_section = _mapping(result["result"], "run result.result")
    _exact_keys(result_section, {"class", "reason", "reason_code"}, "run result.result")
    if result_section.get("class") not in RESULT_CLASSES:
        raise EvalError("run result class is not in the frozen taxonomy")
    _safe_reason(result_section.get("reason"))
    if result_section.get("reason_code") not in STOP_CODES:
        raise EvalError("run result reason code is invalid")
    verifier = _mapping(result["verifier"], "run result.verifier")
    _exact_keys(verifier, {"status", "exit_code", "passed"}, "run result.verifier")
    verifier_status = verifier.get("status")
    if verifier_status not in {"completed", "error"}:
        raise EvalError("finalized run result must contain verifier evidence")
    exit_code = verifier.get("exit_code")
    if verifier_status == "completed":
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise EvalError("completed verifier must have an integer exit code")
    elif exit_code != "unavailable":
        raise EvalError("non-completed verifier must have an unavailable exit code")
    if not isinstance(verifier.get("passed"), bool):
        raise EvalError("run result verifier status is invalid")
    if verifier["passed"] != (verifier_status == "completed" and exit_code == 0):
        raise EvalError("run result verifier pass flag is inconsistent")
    paths = _mapping(result["paths"], "run result.paths")
    _exact_keys(
        paths, {"changed", "expected", "unexpected", "missing_required"}, "run result.paths"
    )
    normalized_paths: dict[str, list[str]] = {}
    for field in paths:
        if not isinstance(paths[field], list) or any(
            not isinstance(path, str) for path in paths[field]
        ):
            raise EvalError("run result path evidence is invalid")
        normalized_paths[field] = [
            _safe_path(path, f"run result.paths.{field}") for path in paths[field]
        ]
        if normalized_paths[field] != sorted(set(normalized_paths[field])):
            raise EvalError(f"run result.paths.{field} is not canonical")
    if normalized_paths["changed"] != status["changed_paths"]:
        raise EvalError("run result changed paths do not match workspace status")
    policy = _mapping(
        _mapping(manifest["task"], "run manifest.task")["change_policy"],
        "run manifest change policy",
    )
    allowed_paths = set(policy["allowed_paths"])
    required_paths = set(policy["required_paths"])
    expected_paths = sorted(set(normalized_paths["changed"]) & allowed_paths)
    unsafe_paths = set(status.get("unsafe_paths", []))
    unexpected_paths = sorted((set(normalized_paths["changed"]) - allowed_paths) | unsafe_paths)
    missing_required = sorted(required_paths - set(expected_paths))
    if (
        normalized_paths["expected"] != expected_paths
        or normalized_paths["unexpected"] != unexpected_paths
        or normalized_paths["missing_required"] != missing_required
    ):
        raise EvalError("run result change policy evidence is inconsistent")
    quality = _mapping(result["quality"], "run result.quality")
    _exact_keys(
        quality,
        {
            "pass_eligible",
            "evidence_complete",
            "expected_change_present",
            "required_paths_present",
            "source_comparison_eligible",
        },
        "run result.quality",
    )
    if any(not isinstance(quality[field], bool) for field in quality):
        raise EvalError("run result quality evidence is invalid")
    expected_change_present = bool(expected_paths) or not policy["requires_change"]
    required_paths_present = not missing_required
    evidence_complete = (
        _runtime_evidence_complete(runtime)
        and status["availability"] == "available"
        and not status["truncated"]
        and not status["patch_truncated"]
        and verifier_status != "error"
    )
    derived_class, derived_reason = _classify(
        verifier_status=verifier_status,
        verifier_passed=verifier["passed"],
        unexpected=unexpected_paths,
        expected_change_present=expected_change_present,
        required_paths_present=required_paths_present,
        runtime=runtime,
    )
    source_comparison_eligible = manifest["source"]["dirty"]["comparison_eligible"]
    pass_eligible = (
        derived_class == "PASS"
        and verifier["passed"]
        and not unexpected_paths
        and expected_change_present
        and required_paths_present
        and evidence_complete
        and source_comparison_eligible
        and runtime["stop"]["code"] == "completed"
    )
    if derived_class == "PASS" and not pass_eligible:
        derived_class, derived_reason = (
            "FAIL_RUNTIME",
            "required finalization evidence was incomplete",
        )
    if result_section["class"] != derived_class or result_section["reason"] != derived_reason:
        raise EvalError("run result classification is not mechanically derived")
    expected_quality = {
        "pass_eligible": pass_eligible,
        "evidence_complete": evidence_complete,
        "expected_change_present": expected_change_present,
        "required_paths_present": required_paths_present,
        "source_comparison_eligible": source_comparison_eligible,
    }
    if dict(quality) != expected_quality:
        raise EvalError("run result quality evidence is inconsistent")
    if result_section["reason_code"] != runtime["stop"]["code"]:
        raise EvalError("run result reason code does not match the stop evidence")
    return {
        "manifest": manifest,
        "manifest_sha256": manifest_hash,
        "result": result,
        "result_sha256": result_hash,
        "runtime": runtime,
        "status": status,
    }


def _bundle_paths(root: Path) -> list[Path]:
    if root.is_file() and root.name == "run-result.json":
        return [root.parent]
    if root.is_file() and root.name == "run-manifest.json":
        return [root.parent]
    if not root.is_dir():
        return []
    bundle_paths: set[Path] = set()
    for filename in ("run-manifest.json", "run-result.json"):
        for artifact_path in sorted(root.rglob(filename)):
            if not artifact_path.parent.is_dir():
                continue
            bundle_paths.add(artifact_path.parent)
    return sorted(bundle_paths)


def _sum_numeric(entries: list[dict[str, object]], field: str) -> int | float | str:
    if not entries:
        return "unavailable"
    values = [entry["runtime"]["usage"][field] for entry in entries]
    if any(value == "unavailable" for value in values):
        return "unavailable"
    return sum(values)


def _gate_for_entries(
    entries: list[dict[str, object]], protocol: Mapping[str, object]
) -> dict[str, object]:
    thresholds = _mapping(protocol["thresholds"], "protocol.thresholds")
    tasks = {task.id: task for task in load_tasks()}
    by_repetition: dict[int, list[dict[str, object]]] = {}
    for entry in entries:
        repetition = int(entry["manifest"]["run"]["repetition"])
        by_repetition.setdefault(repetition, []).append(entry)
    diagnostics: list[str] = []
    repetitions: dict[str, object] = {}
    for repetition in sorted(by_repetition):
        current = by_repetition[repetition]
        simple_medium = sum(
            1
            for entry in current
            if tasks[entry["manifest"]["run"]["task_id"]].difficulty in {"简单", "中等"}
            and entry["result"]["result"]["class"] == "PASS"
        )
        difficult = sum(
            1
            for entry in current
            if tasks[entry["manifest"]["run"]["task_id"]].difficulty == "困难"
            and entry["result"]["result"]["class"] == "PASS"
        )
        passed = sum(1 for entry in current if entry["result"]["result"]["class"] == "PASS")
        fail_runtime = sum(
            1 for entry in current if entry["result"]["result"]["class"] == "FAIL_RUNTIME"
        )
        repetitions[str(repetition)] = {
            "simple_medium_pass": simple_medium,
            "difficult_pass": difficult,
            "total_pass": passed,
            "fail_runtime": fail_runtime,
        }
        if simple_medium != thresholds["simple_medium_required_pass"]:
            diagnostics.append(f"repetition {repetition}: simple/medium pass threshold failed")
        if difficult < thresholds["difficult_required_pass"]:
            diagnostics.append(f"repetition {repetition}: difficult pass threshold failed")
        if passed < thresholds["minimum_total_pass"]:
            diagnostics.append(f"repetition {repetition}: total pass threshold failed")
        if fail_runtime > thresholds["max_fail_runtime"]:
            diagnostics.append(f"repetition {repetition}: FAIL_RUNTIME present")

    unexpected_count = sum(len(entry["result"]["paths"]["unexpected"]) for entry in entries)
    no_diff_success = sum(
        1
        for entry in entries
        if entry["result"]["result"]["class"] == "PASS"
        and not entry["result"]["quality"]["expected_change_present"]
    )
    diagnostic_values = {
        field: [entry["runtime"]["tool_diagnostics"][field] for entry in entries]
        for field in TOOL_DIAGNOSTIC_FIELDS
    }
    state_values = [
        entry["runtime"]["tool_states"][state]
        for entry in entries
        for state in TOOL_TERMINAL_STATES
    ]
    metrics_available = not any(
        value == "unavailable"
        for values in [*diagnostic_values.values(), state_values]
        for value in values
    )
    if metrics_available:
        invalid_arguments = sum(diagnostic_values["invalid_arguments"])
        total_tool_calls = sum(diagnostic_values["total_tool_calls"])
        unaccounted = sum(diagnostic_values["unaccounted_tool_calls"])
        basic_blocked = sum(diagnostic_values["basic_tool_blocked"])
        invalid_fraction = (
            invalid_arguments / total_tool_calls if total_tool_calls else "unavailable"
        )
        for entry in entries:
            states = entry["runtime"]["tool_states"]
            diagnostics_for_entry = entry["runtime"]["tool_diagnostics"]
            terminal_count = sum(states.values())
            if diagnostics_for_entry["total_tool_calls"] != (
                terminal_count + diagnostics_for_entry["unaccounted_tool_calls"]
            ):
                diagnostics.append("tool terminal accounting invariant failed")
            if (
                diagnostics_for_entry["invalid_arguments"]
                > diagnostics_for_entry["total_tool_calls"]
            ):
                diagnostics.append("invalid argument count exceeded total tool calls")
            if (
                diagnostics_for_entry["basic_tool_blocked"]
                > diagnostics_for_entry["total_tool_calls"]
            ):
                diagnostics.append("basic-tool blocker count exceeded total tool calls")
        if total_tool_calls == 0:
            diagnostics.append("invalid argument fraction is undefined with zero tool calls")
    else:
        invalid_arguments = "unavailable"
        total_tool_calls = "unavailable"
        unaccounted = "unavailable"
        basic_blocked = "unavailable"
        invalid_fraction = "unavailable"
        diagnostics.append("tool diagnostics are unavailable")
    if basic_blocked != "unavailable" and basic_blocked > thresholds["max_basic_tool_blocked"]:
        diagnostics.append("basic-tool environment blocker threshold failed")
    if no_diff_success > thresholds["max_success_without_expected_diff"]:
        diagnostics.append("success without an expected diff was recorded")
    if unexpected_count > thresholds["max_unexpected_paths"]:
        diagnostics.append("unexpected workspace paths were recorded")
    if (
        invalid_fraction != "unavailable"
        and invalid_fraction > thresholds["max_invalid_arguments_fraction"]
    ):
        diagnostics.append("invalid argument fraction threshold failed")
    if unaccounted != "unavailable" and unaccounted > thresholds["max_unaccounted_tool_calls"]:
        diagnostics.append("unaccounted tool calls were recorded")
    source_ineligible = sum(
        1 for entry in entries if not entry["manifest"]["source"]["dirty"]["comparison_eligible"]
    )
    if source_ineligible:
        diagnostics.append("dirty diagnostic runs cannot satisfy the comparison gate")
    return {
        "status": "PASS" if not diagnostics else "FAIL",
        "diagnostics": diagnostics,
        "repetitions": repetitions,
        "invalid_arguments": invalid_arguments,
        "total_tool_calls": total_tool_calls,
        "invalid_arguments_fraction": invalid_fraction,
        "unaccounted_tool_calls": unaccounted,
        "unexpected_paths": unexpected_count,
        "basic_tool_blocked": basic_blocked,
        "success_without_expected_diff": no_diff_success,
        "pi_comparison": {
            "status": "NOT_EVALUATED",
            "required_task_ids": protocol["comparison"]["pi_task_ids"],
            "reason": "Pi A/B evidence is collected by the later S7P-09 comparison lane",
        },
    }


def summarize_runs(root: Path, *, output: Path | None = None) -> dict[str, object]:
    """Validate finalized bundles and mechanically aggregate their facts."""

    root = root.resolve()
    protocol = load_protocol()
    bundles = _bundle_paths(root)
    valid: list[dict[str, object]] = []
    invalid: list[dict[str, str]] = []
    for bundle in bundles:
        try:
            valid.append(validate_run_bundle(bundle))
        except EvalError as exc:
            invalid.append({"bundle": str(bundle.relative_to(root)), "reason": str(exc)})

    by_key: dict[tuple[str, int], list[dict[str, object]]] = {}
    for entry in valid:
        run = entry["manifest"]["run"]
        by_key.setdefault((run["task_id"], run["repetition"]), []).append(entry)
    duplicate_keys = sorted(
        f"{task_id}#{repetition}"
        for (task_id, repetition), values in by_key.items()
        if len(values) > 1
    )
    accepted = [values[0] for values in by_key.values() if len(values) == 1]
    run_id_counts: dict[str, int] = {}
    for entry in accepted:
        run_id = entry["manifest"]["run"]["id"]
        run_id_counts[run_id] = run_id_counts.get(run_id, 0) + 1
    duplicate_run_ids = sorted(run_id for run_id, count in run_id_counts.items() if count > 1)

    protocol_revisions = sorted(
        {
            (
                entry["manifest"]["protocol"]["id"],
                entry["manifest"]["protocol"]["version"],
                entry["manifest"]["protocol"]["sha256"],
            )
            for entry in accepted
        }
    )
    profile_revisions = sorted({entry["manifest"]["profile_sha256"] for entry in accepted})
    mixed_protocol = len(protocol_revisions) > 1
    mixed_profile = len(profile_revisions) > 1
    minimum_repetitions = protocol["protocol"]["minimum_repetitions"]
    expected_keys = {
        (
            task.id,
            repetition,
        )
        for task in load_tasks()
        for repetition in range(1, minimum_repetitions + 1)
    }
    actual_keys = set(by_key)
    missing = sorted(
        f"{task_id}#{repetition}" for task_id, repetition in expected_keys - actual_keys
    )
    unexpected = sorted(
        f"{task_id}#{repetition}" for task_id, repetition in actual_keys - expected_keys
    )
    unavailable_metrics = sorted(
        {
            field
            for entry in accepted
            for field, value in entry["runtime"]["usage"].items()
            if value == "unavailable"
        }
    )
    unavailable_metrics.extend(
        f"tool_states.{field}"
        for entry in accepted
        for field, value in entry["runtime"]["tool_states"].items()
        if value == "unavailable"
    )
    unavailable_metrics.extend(
        f"tool_diagnostics.{field}"
        for entry in accepted
        for field, value in entry["runtime"]["tool_diagnostics"].items()
        if value == "unavailable"
    )
    unavailable_metrics.extend(
        "runtime.availability"
        for entry in accepted
        if entry["runtime"]["availability"] == "unavailable"
    )
    unavailable_metrics = sorted(set(unavailable_metrics))
    source_ineligible = sorted(
        f"{entry['manifest']['run']['task_id']}#{entry['manifest']['run']['repetition']}"
        for entry in accepted
        if not entry["manifest"]["source"]["dirty"]["comparison_eligible"]
    )
    diagnostics: dict[str, object] = {
        "missing_runs": missing,
        "unexpected_runs": unexpected,
        "duplicates": duplicate_keys,
        "duplicate_run_ids": duplicate_run_ids,
        "invalid_bundles": invalid,
        "mixed_protocol": mixed_protocol,
        "mixed_profile": mixed_profile,
        "unavailable_metrics": unavailable_metrics,
        "source_ineligible_runs": source_ineligible,
    }
    complete = not (
        missing
        or unexpected
        or duplicate_keys
        or duplicate_run_ids
        or invalid
        or mixed_protocol
        or mixed_profile
        or unavailable_metrics
        or source_ineligible
        or not accepted
    )
    result_counts = {result_class: 0 for result_class in RESULT_CLASSES}
    tool_counts: dict[str, int | str] = {}
    for entry in accepted:
        result_counts[entry["result"]["result"]["class"]] += 1
    for state in TOOL_TERMINAL_STATES:
        values = [entry["runtime"]["tool_states"][state] for entry in accepted]
        tool_counts[state] = (
            "unavailable"
            if not values or any(value == "unavailable" for value in values)
            else sum(values)
        )
    outcome_counts = {
        "failed": sum(
            result_counts[result_class]
            for result_class in (
                "FAIL_MODEL",
                "FAIL_TOOL_CONTRACT",
                "FAIL_RUNTIME",
                "BUDGET_EXHAUSTED",
            )
        ),
        "denied": result_counts["DENIED_POLICY"],
        "blocked": result_counts["BLOCKED_ENV"],
    }
    protocol_snapshot = protocol_revisions[0] if len(protocol_revisions) == 1 else None
    profile_snapshot = profile_revisions[0] if len(profile_revisions) == 1 else None
    summary: dict[str, object] = {
        "schema": "morrow.s7p-00.summary.v1",
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "protocol": (
            {
                "id": protocol_snapshot[0],
                "version": protocol_snapshot[1],
                "sha256": protocol_snapshot[2],
            }
            if protocol_snapshot
            else "unavailable"
        ),
        "profile_sha256": profile_snapshot or "unavailable",
        "runs": {
            "discovered": len(bundles),
            "valid": len(valid),
            "accepted": len(accepted),
            "expected": len(expected_keys),
        },
        "result_counts": result_counts,
        "results": result_counts,
        "outcome_counts": outcome_counts,
        "tool_states": tool_counts,
        "usage": {field: _sum_numeric(accepted, field) for field in USAGE_FIELDS},
        "diagnostics": diagnostics,
    }
    if complete:
        protocol = load_protocol()
        summary["gate"] = _gate_for_entries(accepted, protocol)
    else:
        summary["gate"] = {"status": "NOT_EVALUATED", "diagnostics": ["summary is incomplete"]}
    if output is not None:
        _write_json_create(output.resolve(), summary)
    return summary


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
            try:
                _read_verifier_output(baseline_result)
                _read_verifier_output(gold_result)
            except EvalError:
                baseline_failed = False
                gold_passed = False
                state = "FAIL"
            else:
                baseline_failed = baseline_result.returncode != 0
                gold_passed = gold_result.returncode == 0
                state = "PASS" if baseline_failed and gold_passed else "FAIL"
            print(
                f"{state} {task.id}: baseline={baseline_result.returncode} "
                f"gold={gold_result.returncode}"
            )
            if state == "FAIL":
                failures.append(task.id)
                print("verifier output withheld")
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
    start = subparsers.add_parser("start", help="freeze a profile and create a run bundle")
    start.add_argument("task_id")
    start.add_argument("output", type=Path)
    start.add_argument("--repetition", type=int, required=True)
    start.add_argument("--profile", type=Path, required=True)
    start.add_argument(
        "--diagnostic-dirty",
        action="store_true",
        help="allow a dirty evaluator checkout, marking the run ineligible for comparison",
    )
    rebuild = subparsers.add_parser("rebuild", help="rebuild a baseline workspace from a manifest")
    rebuild.add_argument("manifest", type=Path)
    rebuild.add_argument("output", type=Path)
    finalize = subparsers.add_parser("finalize", help="verify and finalize a run bundle")
    finalize.add_argument("manifest", type=Path)
    finalize.add_argument("--runtime-evidence", type=Path)
    summarize = subparsers.add_parser("summarize", help="aggregate finalized run bundles")
    summarize.add_argument("root", type=Path)
    summarize.add_argument("--output", type=Path)
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
            verification = run_verification(task, arguments.workspace, capture=True)
            _read_verifier_output(verification)
            return verification.returncode
        if arguments.command == "start":
            bundle = start_run(
                arguments.task_id,
                arguments.output,
                repetition=arguments.repetition,
                profile=arguments.profile,
                diagnostic_dirty=arguments.diagnostic_dirty,
            )
            print(bundle)
            return 0
        if arguments.command == "rebuild":
            workspace = rebuild_workspace(arguments.manifest, arguments.output)
            print(workspace)
            return 0
        if arguments.command == "finalize":
            result = finalize_run(arguments.manifest, arguments.runtime_evidence)
            print(canonical_json({"run": result["run"], "result": result["result"]}).strip())
            return 0 if result["result"]["class"] == "PASS" else 1
        if arguments.command == "summarize":
            summary = summarize_runs(arguments.root, output=arguments.output)
            print(canonical_json(summary).strip())
            return (
                0 if summary["status"] == "COMPLETE" and summary["gate"]["status"] == "PASS" else 1
            )
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
