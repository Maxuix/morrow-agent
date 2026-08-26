"""Focused, offline contracts for the reproducible Code Agent Mini Eval lane."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EVAL_PATH = Path(__file__).parents[1] / "evals" / "code-agent-mini" / "eval.py"
SPEC = importlib.util.spec_from_file_location("morrow_code_agent_mini_eval", EVAL_PATH)
assert SPEC and SPEC.loader
eval_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = eval_module
SPEC.loader.exec_module(eval_module)


def _profile() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile_id": "direct-coding-v1",
        "agent": {"id": "morrow-direct", "version": "test", "entrypoint": "morrow"},
        "provider": {"id": "fake", "revision": "fake-rev"},
        "model": {"id": "fake/model", "revision": "model-rev"},
        "sampling": {
            "temperature": "unavailable",
            "top_p": "unavailable",
            "seed": "unavailable",
            "max_output_tokens": "unavailable",
        },
        "tools": [
            {
                "name": "read_file",
                "schema": {"type": "object", "properties": {}},
                "schema_hash": eval_module.content_hash({"type": "object", "properties": {}}),
            }
        ],
        "permissions": {
            "mode": "auto-sandboxed",
            "sandbox": "workspace-only",
            "network": "denied",
            "filesystem": "workspace-read-write",
        },
        "budgets": {
            "input_tokens": "unavailable",
            "output_tokens": "unavailable",
            "context_tokens": "unavailable",
            "rounds": 60,
            "deadline_seconds": "unavailable",
            "tool_calls": "unavailable",
            "model_attempts": "unavailable",
        },
        "system_prompt": {"version": "direct-coding-v1", "sha256": "unavailable"},
        "project_instructions": [{"source": "AGENTS.md", "sha256": "unavailable"}],
        "execution": {"python": "3.12-test", "morrow": "test", "agent": "test"},
    }


def _source_repo(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Eval Test",
            "-c",
            "user.email=eval-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        cwd=source,
        check=True,
    )
    return source


def _runtime_evidence(
    *,
    tool_states: dict[str, int] | None = None,
    usage: dict[str, object] | None = None,
    stop_code: str = "completed",
) -> dict[str, object]:
    states = tool_states or {state: 0 for state in eval_module.TOOL_TERMINAL_STATES}
    return {
        "schema_version": 1,
        "availability": "available",
        "tool_states": states,
        "tool_diagnostics": {
            "invalid_arguments": 0,
            "unaccounted_tool_calls": 0,
            "total_tool_calls": sum(states.values()),
            "basic_tool_blocked": 0,
        },
        "usage": usage
        or {
            "input_tokens": 1,
            "output_tokens": 1,
            "total_tokens": 2,
            "cost": 0.0,
            "duration_ms": 1,
            "rounds": 1,
            "user_interventions": 0,
            "rework_count": 0,
        },
        "stop": {"code": stop_code, "reason": stop_code},
    }


def _passing_verifier(
    task: eval_module.Task,
    workspace: Path,
    *,
    capture: bool = False,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["synthetic-verifier"], returncode=0, stdout="synthetic verifier passed\n"
    )


def test_protocol_freezes_taxonomy_repetitions_and_gate() -> None:
    protocol = eval_module.load_protocol()

    assert protocol["protocol"]["id"] == "s7p-00"
    assert protocol["protocol"]["version"] == 1
    assert protocol["protocol"]["minimum_repetitions"] == 2
    assert protocol["result_classes"]["values"] == [
        "PASS",
        "FAIL_MODEL",
        "FAIL_TOOL_CONTRACT",
        "FAIL_RUNTIME",
        "DENIED_POLICY",
        "BLOCKED_ENV",
        "BUDGET_EXHAUSTED",
    ]
    assert protocol["tool_terminal_states"]["values"] == [
        "succeeded",
        "failed",
        "denied",
        "cancelled",
        "blocked",
    ]
    assert protocol["thresholds"]["minimum_total_pass"] == 7
    assert protocol["thresholds"]["max_invalid_arguments_fraction"] == 0.01


def test_profile_validation_is_strict_and_never_defaults_usage_to_zero() -> None:
    profile = eval_module.validate_profile(_profile())
    assert profile["budgets"]["input_tokens"] == "unavailable"

    unknown = _profile()
    unknown["unexpected"] = True
    with pytest.raises(eval_module.ProfileError, match="unknown profile field"):
        eval_module.validate_profile(unknown)

    sensitive = _profile()
    sensitive["provider"] = {"id": "fake", "revision": "fake", "credential": "secret"}
    with pytest.raises(eval_module.ProfileError, match="forbidden sensitive field"):
        eval_module.validate_profile(sensitive)

    missing = _profile()
    del missing["provider"]
    with pytest.raises(eval_module.ProfileError, match="missing profile field"):
        eval_module.validate_profile(missing)


def test_start_and_rebuild_make_equivalent_fresh_workspaces(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    first = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run-1",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    second = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run-2",
        repetition=2,
        profile=_profile(),
        source_root=source,
    )

    first_manifest = json.loads((first / "run-manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second / "run-manifest.json").read_text(encoding="utf-8"))
    assert (
        first_manifest["workspace"]["baseline_tree_sha256"]
        == second_manifest["workspace"]["baseline_tree_sha256"]
    )
    assert first_manifest["dataset"]["sha256"] == second_manifest["dataset"]["sha256"]
    assert first_manifest["protocol"]["sha256"] == second_manifest["protocol"]["sha256"]
    marker = json.loads((first / "workspace" / eval_module.MARKER_NAME).read_text())
    assert "expected_change_policy" not in marker

    rebuilt = eval_module.rebuild_workspace(
        first / "run-manifest.json", tmp_path / "rebuilt", source_root=source
    )
    rebuilt_tree = eval_module.git_tree_hash(rebuilt)
    assert rebuilt_tree == first_manifest["workspace"]["baseline_tree_sha256"]


def test_start_rejects_existing_output_and_dirty_comparable_source(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    task = eval_module.task_by_id("EXTERNAL-001")
    output = tmp_path / "run"
    eval_module.start_run(task, output, repetition=1, profile=_profile(), source_root=source)
    with pytest.raises(eval_module.EvalError, match="output already exists"):
        eval_module.start_run(task, output, repetition=2, profile=_profile(), source_root=source)

    (source / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(eval_module.EvalError, match="clean source checkout"):
        eval_module.start_run(
            task, tmp_path / "dirty-run", repetition=1, profile=_profile(), source_root=source
        )


def test_rebuild_rejects_dataset_and_protocol_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )

    with monkeypatch.context() as patcher:
        patcher.setattr(
            eval_module, "dataset_hash", lambda root=eval_module.DATASET_ROOT: "sha256:" + "0" * 64
        )
        with pytest.raises(eval_module.EvalError, match="dataset hash mismatch"):
            eval_module.rebuild_workspace(
                bundle / "run-manifest.json", tmp_path / "rebuilt-dataset", source_root=source
            )

    original_file_hash = eval_module.file_hash

    def mismatched_protocol_hash(path: Path) -> str:
        if path.resolve() == eval_module.PROTOCOL_PATH.resolve():
            return "sha256:" + "0" * 64
        return original_file_hash(path)

    with monkeypatch.context() as patcher:
        patcher.setattr(eval_module, "file_hash", mismatched_protocol_hash)
        with pytest.raises(eval_module.EvalError, match="protocol hash mismatch"):
            eval_module.rebuild_workspace(
                bundle / "run-manifest.json", tmp_path / "rebuilt-protocol", source_root=source
            )


def test_rebuild_rejects_source_revision_and_configuration_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    (source / "pyproject.toml").write_text(
        "[project]\nname = 'fixture'\nversion = 'drifted'\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "pyproject.toml"], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Eval Test",
            "-c",
            "user.email=eval-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "drift",
        ],
        cwd=source,
        check=True,
    )
    with pytest.raises(eval_module.EvalError, match="evaluator commit mismatch"):
        eval_module.rebuild_workspace(
            bundle / "run-manifest.json", tmp_path / "rebuilt-commit", source_root=source
        )

    recorded_commit = json.loads((bundle / "run-manifest.json").read_text(encoding="utf-8"))[
        "source"
    ]["evaluator_commit"]
    with monkeypatch.context() as patcher:
        patcher.setattr(eval_module, "_git_commit", lambda root: recorded_commit)
        with pytest.raises(eval_module.EvalError, match="configuration snapshot mismatch"):
            eval_module.rebuild_workspace(
                bundle / "run-manifest.json", tmp_path / "rebuilt-config", source_root=source
            )


def test_profile_schema_hash_and_runtime_accounting_are_verified() -> None:
    profile = _profile()
    profile["tools"][0]["schema_hash"] = "sha256:" + "0" * 64
    with pytest.raises(eval_module.ProfileError, match="schema_hash does not match schema"):
        eval_module.validate_profile(profile)

    evidence = _runtime_evidence(
        tool_states={"succeeded": 1, "failed": 0, "denied": 0, "cancelled": 0, "blocked": 0}
    )
    evidence["tool_diagnostics"]["total_tool_calls"] = 0
    with pytest.raises(eval_module.EvalError, match="terminal accounting"):
        eval_module.normalize_runtime_evidence(evidence)

    evidence = _runtime_evidence()
    evidence["stop"]["reason"] = "free-form explanation"
    with pytest.raises(eval_module.EvalError, match="stop reason must equal"):
        eval_module.normalize_runtime_evidence(evidence)


def test_verifier_output_rejects_common_credential_shapes() -> None:
    for output in ("AKIA1234567890ABCDEF\n", "ghp_" + "a" * 36 + "\n"):
        with pytest.raises(eval_module.EvalError, match="credential material"):
            eval_module._read_verifier_output(
                subprocess.CompletedProcess(
                    args=["synthetic-verifier"], returncode=0, stdout=output
                )
            )


def test_verifier_command_has_a_bounded_timeout(tmp_path: Path) -> None:
    with pytest.raises(eval_module.EvalError, match="timed out"):
        eval_module.run_command(
            [sys.executable, "-c", "import time; time.sleep(0.1)"],
            cwd=tmp_path,
            capture=True,
            timeout=0.001,
        )


def test_finalize_is_retryable_after_atomic_artifact_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    expected_path = bundle / "workspace" / "phone_number.py"
    with expected_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# atomic retry change\n")
    monkeypatch.setattr(eval_module, "run_verification", _passing_verifier)
    original_write = eval_module._write_bytes_create

    def fail_result(path: Path, value: bytes) -> None:
        if path.name == "run-result.json":
            raise eval_module.EvalError("injected artifact failure")
        original_write(path, value)

    monkeypatch.setattr(eval_module, "_write_bytes_create", fail_result)
    with pytest.raises(eval_module.EvalError, match="injected artifact failure"):
        eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())
    assert not any((bundle / filename).exists() for filename in eval_module.REQUIRED_EVIDENCE[1:])

    (bundle / "run-result.json").write_text("partial\n", encoding="utf-8")
    monkeypatch.setattr(eval_module, "_write_bytes_create", original_write)
    result = eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())
    assert result["result"]["class"] == "PASS"


def test_summary_discovers_manifest_only_partial_bundle(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        runs_root / "partial",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )

    summary = eval_module.summarize_runs(runs_root)
    assert summary["status"] == "INCOMPLETE"
    assert summary["runs"]["discovered"] == 1
    assert summary["diagnostics"]["invalid_bundles"]

    orphan = runs_root / "orphan"
    orphan.mkdir()
    (orphan / "run-result.json").write_text("{}\n", encoding="utf-8")
    orphan_summary = eval_module.summarize_runs(runs_root)
    assert orphan_summary["runs"]["discovered"] == 2
    assert len(orphan_summary["diagnostics"]["invalid_bundles"]) == 2


def test_finalize_requires_verifier_success_and_no_unexpected_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )

    def synthetic_verifier(
        task: eval_module.Task,
        workspace: Path,
        *,
        capture: bool = False,
        timeout_seconds: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["synthetic-verifier"], returncode=0, stdout="synthetic verifier passed\n"
        )

    monkeypatch.setattr(eval_module, "run_verification", synthetic_verifier)
    expected_path = bundle / "workspace" / "phone_number.py"
    with expected_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# synthetic evaluation change\n")
    result = eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())
    assert result["result"]["class"] == "PASS"
    assert result["paths"]["unexpected"] == []

    unexpected_bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run-unexpected",
        repetition=2,
        profile=_profile(),
        source_root=source,
    )
    expected_path = unexpected_bundle / "workspace" / "phone_number.py"
    with expected_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# synthetic evaluation change\n")
    (unexpected_bundle / "workspace" / "agent-notes.txt").write_text("note", encoding="utf-8")
    result = eval_module.finalize_run(unexpected_bundle / "run-manifest.json", _runtime_evidence())
    assert result["result"]["class"] != "PASS"
    assert result["paths"]["unexpected"] == ["agent-notes.txt"]


def test_finalize_requires_every_required_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )

    monkeypatch.setattr(eval_module, "run_verification", _passing_verifier)
    result = eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())

    assert result["result"]["class"] == "FAIL_MODEL"
    assert result["paths"]["missing_required"] == ["phone_number.py"]
    assert result["quality"]["required_paths_present"] is False
    eval_module.validate_run_bundle(bundle)


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink"])
def test_finalize_rejects_unsafe_workspace_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_kind: str
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / f"run-{unsafe_kind}",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    expected_path = bundle / "workspace" / "phone_number.py"
    outside = tmp_path / f"outside-{unsafe_kind}.py"
    outside.write_text(expected_path.read_text(encoding="utf-8"), encoding="utf-8")
    expected_path.unlink()
    if unsafe_kind == "symlink":
        expected_path.symlink_to(outside)
    else:
        expected_path.hardlink_to(outside)

    def verifier_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("unsafe workspace reached the external verifier")

    monkeypatch.setattr(eval_module, "run_verification", verifier_must_not_run)
    result = eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())

    assert result["result"]["class"] == "FAIL_RUNTIME"
    assert result["verifier"]["status"] == "error"
    assert result["paths"]["unexpected"] == ["phone_number.py"]
    eval_module.validate_run_bundle(bundle)


def test_finalize_rejects_workspace_root_symlink(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    real_workspace = tmp_path / "outside-workspace"
    real_workspace.mkdir()
    shutil.rmtree(bundle / "workspace")
    (bundle / "workspace").symlink_to(real_workspace, target_is_directory=True)

    with pytest.raises(eval_module.EvalError, match="real directory inside the bundle"):
        eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())


def test_finalize_records_staged_and_ignored_changes_as_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    workspace = bundle / "workspace"
    expected_path = workspace / "phone_number.py"
    with expected_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# staged evaluation change\n")
    subprocess.run(["git", "add", "phone_number.py"], cwd=workspace, check=True)
    (workspace / ".git" / "info" / "exclude").write_text("agent-notes.txt\n", encoding="utf-8")
    (workspace / "agent-notes.txt").write_text("ignored note\n", encoding="utf-8")

    monkeypatch.setattr(eval_module, "run_verification", _passing_verifier)
    result = eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())

    assert result["result"]["class"] == "FAIL_MODEL"
    assert result["paths"]["changed"] == ["agent-notes.txt", "phone_number.py"]
    assert result["paths"]["unexpected"] == ["agent-notes.txt"]
    status = json.loads((bundle / "workspace-status.json").read_text(encoding="utf-8"))
    assert status["ignored_paths"] == ["agent-notes.txt"]
    assert b"phone_number.py" in (bundle / "workspace-diff.patch").read_bytes()
    eval_module.validate_run_bundle(bundle)


def test_finalize_persists_raw_evidence_and_tamper_is_detected(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    result = eval_module.finalize_run(
        bundle / "run-manifest.json",
        _runtime_evidence(
            tool_states={"succeeded": 3, "failed": 3, "denied": 2, "cancelled": 0, "blocked": 0}
        ),
    )
    assert result["tool_states"]["failed"] == 3
    assert result["tool_states"]["denied"] == 2
    assert (bundle / "verifier-output.txt").read_text(encoding="utf-8")
    assert (bundle / "workspace-diff.patch").exists()
    assert (bundle / "workspace-status.json").exists()
    assert result["evidence"]["runtime-evidence.json"]["sha256"].startswith("sha256:")

    (bundle / "runtime-evidence.json").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(eval_module.EvalError, match="hash mismatch"):
        eval_module.validate_run_bundle(bundle)


def test_bundle_artifacts_must_remain_regular_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    expected_path = bundle / "workspace" / "phone_number.py"
    with expected_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# artifact check\n")
    monkeypatch.setattr(eval_module, "run_verification", _passing_verifier)
    eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())

    verifier_output = bundle / "verifier-output.txt"
    outside = tmp_path / "outside-verifier-output.txt"
    outside.write_bytes(verifier_output.read_bytes())
    verifier_output.unlink()
    verifier_output.symlink_to(outside)
    with pytest.raises(eval_module.EvalError, match="regular file inside"):
        eval_module.validate_run_bundle(bundle)


def test_summary_keeps_failed_denied_blocked_separate_and_rejects_duplicate(tmp_path: Path) -> None:
    source = _source_repo(tmp_path)
    bundles: list[Path] = []
    for repetition, stop_code in ((1, "policy_denied"), (2, "environment_blocked")):
        bundle = eval_module.start_run(
            eval_module.task_by_id("EXTERNAL-001"),
            tmp_path / f"run-{repetition}",
            repetition=repetition,
            profile=_profile(),
            source_root=source,
        )
        eval_module.finalize_run(
            bundle / "run-manifest.json",
            _runtime_evidence(
                tool_states={
                    "succeeded": 1,
                    "failed": 3,
                    "denied": 2,
                    "cancelled": 0,
                    "blocked": 4,
                },
                usage={
                    "input_tokens": "unavailable",
                    "output_tokens": "unavailable",
                    "total_tokens": "unavailable",
                    "cost": "unavailable",
                    "duration_ms": 1,
                    "rounds": 1,
                    "user_interventions": 0,
                    "rework_count": 0,
                },
                stop_code=stop_code,
            ),
        )
        bundles.append(bundle)

    summary = eval_module.summarize_runs(tmp_path)
    assert summary["status"] == "INCOMPLETE"
    assert summary["tool_states"]["failed"] == 6
    assert summary["tool_states"]["denied"] == 4
    assert summary["tool_states"]["blocked"] == 8
    assert summary["usage"]["input_tokens"] == "unavailable"

    duplicate = tmp_path / "duplicate"
    shutil.copytree(bundles[0], duplicate)
    duplicate_summary = eval_module.summarize_runs(tmp_path)
    assert duplicate_summary["status"] == "INCOMPLETE"
    assert duplicate_summary["diagnostics"]["duplicates"]


def test_two_complete_synthetic_repetitions_have_deterministic_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    def synthetic_verifier(
        task: eval_module.Task,
        workspace: Path,
        *,
        capture: bool = False,
        timeout_seconds: float | None = None,
    ):
        return subprocess.CompletedProcess(
            args=["synthetic-verifier"], returncode=0, stdout="synthetic verifier passed\n"
        )

    monkeypatch.setattr(eval_module, "run_verification", synthetic_verifier)
    for repetition in (1, 2):
        for task in eval_module.load_tasks():
            bundle = eval_module.start_run(
                task,
                runs_root / f"{task.id.lower()}-{repetition}",
                repetition=repetition,
                profile=_profile(),
                source_root=source,
            )
            for relative in task.values["required_paths"]:
                expected_path = bundle / "workspace" / relative
                expected_path.parent.mkdir(parents=True, exist_ok=True)
                with expected_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n# synthetic evaluation change\n")
            eval_module.finalize_run(
                bundle / "run-manifest.json",
                _runtime_evidence(
                    tool_states={
                        "succeeded": 1,
                        "failed": 0,
                        "denied": 0,
                        "cancelled": 0,
                        "blocked": 0,
                    }
                ),
            )

    summary = eval_module.summarize_runs(runs_root)
    assert summary["status"] == "COMPLETE"
    assert summary["runs"] == {"discovered": 20, "valid": 20, "accepted": 20, "expected": 20}
    assert summary["result_counts"]["PASS"] == 20
    assert summary["outcome_counts"] == {"failed": 0, "denied": 0, "blocked": 0}
    assert summary["tool_states"] == {
        "succeeded": 20,
        "failed": 0,
        "denied": 0,
        "cancelled": 0,
        "blocked": 0,
    }
    assert summary["gate"]["status"] == "PASS"


def test_gate_uses_explicit_basic_tool_blocker_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_repo(tmp_path)
    bundle = eval_module.start_run(
        eval_module.task_by_id("EXTERNAL-001"),
        tmp_path / "run",
        repetition=1,
        profile=_profile(),
        source_root=source,
    )
    expected_path = bundle / "workspace" / "phone_number.py"
    with expected_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# gate diagnostic change\n")
    monkeypatch.setattr(eval_module, "run_verification", _passing_verifier)
    eval_module.finalize_run(bundle / "run-manifest.json", _runtime_evidence())
    entry = eval_module.validate_run_bundle(bundle)
    entry["runtime"]["tool_diagnostics"]["basic_tool_blocked"] = 1
    gate = eval_module._gate_for_entries([entry], eval_module.load_protocol())
    assert gate["basic_tool_blocked"] == 1
    assert "basic-tool environment blocker threshold failed" in gate["diagnostics"]
