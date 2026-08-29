"""Focused, offline contracts for the reproducible Code Agent Mini Eval lane."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application
from morrow.core.models import CredentialRef, ProviderConfig, ProviderModelConfig

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
        "provider": {"id": "fake", "revision": "https://example.test/v1"},
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


def _comparison_plan() -> dict[str, object]:
    morrow = _profile()
    pi = _profile()
    sampling = {"temperature": 0.0, "top_p": 1.0, "seed": 7, "max_output_tokens": 4_096}
    morrow["sampling"] = dict(sampling)
    pi["sampling"] = dict(sampling)
    pi["profile_id"] = "pi-coding-v1"
    pi["agent"] = {"id": "pi", "version": "0.84.2", "entrypoint": "pi"}
    source_hash = "sha256:" + "1" * 64
    plan: dict[str, object] = {
        "schema": eval_module.COMPARISON_PLAN_SCHEMA,
        "campaign_id": "s7p-09-fixture",
        "protocol": {"id": "s7p-00", "version": 1, "sha256": source_hash},
        "dataset": {"id": "morrow-code-agent-mini", "sha256": source_hash},
        "source": {
            "morrow_commit": "a" * 40,
            "morrow_source_sha256": source_hash,
            "pi_version": "0.84.2",
            "pi_package_sha256": source_hash,
            "pi_executable_sha256": source_hash,
            "clean": True,
        },
        "profiles": {
            "morrow": {"profile": morrow, "sha256": eval_module.content_hash(morrow)},
            "pi": {"profile": pi, "sha256": eval_module.content_hash(pi)},
        },
        "common_model": {
            "provider_family": "fake",
            "service": "https://example.test/v1",
            "canonical_model_id": "fake/model",
            "model_revision": "model-rev",
            "context_window": 16_384,
            "max_output_tokens": 4_096,
            "sampling": sampling,
        },
        "permissions": {
            "capabilities": list(eval_module.CAPABILITY_FAMILIES),
            "denials": [
                "credential_access",
                "external_filesystem",
                "git_mutation",
                "privilege_escalation",
                "task_network",
            ],
            "morrow_policy_sha256": source_hash,
            "pi_extension_sha256": source_hash,
        },
        "deadlines": {"run_seconds": 1_800, "tool_seconds": 120},
        "ceilings": {"total_tokens": 1_000_000, "currency": "USD", "total_cost": 100.0},
        "schedule": eval_module.frozen_campaign_schedule(),
        "evidence_root": {
            "id": "fixture-root",
            "path_sha256": source_hash,
            "minimum_free_bytes": 1_000_000,
        },
        "start_not_before": "2020-01-01T00:00:00+00:00",
        "hold_point": {
            "status": "approved",
            "scope": "common_model_and_campaign_ceilings",
            "approved_at": "2020-01-01T00:00:00+00:00",
            "morrow_readiness_sha256": source_hash,
            "pi_readiness_sha256": source_hash,
            "model_probe_sha256": source_hash,
        },
    }
    plan["integrity"] = eval_module.content_hash(plan)
    return plan


def _campaign_admission_dependencies(
    tmp_path: Path, *, with_credential: bool = True
) -> dict[str, object]:
    source_state = tmp_path / "configured-morrow-state"
    credentials = MemoryCredentialStore()
    credential_ref = CredentialRef(ref="provider:fake:evaluation")
    if with_credential:
        credentials.set(credential_ref.ref, "fixture-provider-value")
    app = build_application(state_root=source_state, credentials=credentials)
    written = app.global_store.update(
        lambda current: current.model_copy(
            update={
                "providers": {
                    "fake": ProviderConfig(
                        adapter="openai-compatible",
                        base_url="https://example.test/v1",
                        credential_ref=credential_ref,
                        models={"fake/model": ProviderModelConfig(api_model_id="fake/model")},
                    )
                },
                "active_model": None,
            }
        )
    )
    assert written.status.value == "ok"
    return {"source_state_root": source_state, "credentials": credentials}


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


def test_comparison_plan_freezes_exact_counterbalanced_schedule_and_profiles() -> None:
    plan = eval_module.validate_comparison_plan(_comparison_plan())

    schedule = plan["schedule"]
    assert len(schedule) == 28
    assert sum(entry["agent"] == "morrow" for entry in schedule) == 20
    assert sum(entry["agent"] == "pi" for entry in schedule) == 8
    for task_id in eval_module.FIXED_PI_TASK_IDS:
        rep1 = [
            entry["agent"]
            for entry in schedule
            if entry["task_id"] == task_id and entry["repetition"] == 1
        ]
        rep2 = [
            entry["agent"]
            for entry in schedule
            if entry["task_id"] == task_id and entry["repetition"] == 2
        ]
        assert rep1 == ["morrow", "pi"]
        assert rep2 == ["pi", "morrow"]

    drifted = _comparison_plan()
    drifted["schedule"][0], drifted["schedule"][1] = (
        drifted["schedule"][1],
        drifted["schedule"][0],
    )
    drifted["integrity"] = eval_module.content_hash(
        {key: value for key, value in drifted.items() if key != "integrity"}
    )
    with pytest.raises(eval_module.EvalError, match="frozen 28-run order"):
        eval_module.validate_comparison_plan(drifted)


def test_comparison_plan_accepts_explicit_reduced_single_repetition_variant() -> None:
    plan = _comparison_plan()
    plan["campaign_variant"] = eval_module.REDUCED_CAMPAIGN_VARIANT
    plan["schedule"] = eval_module.frozen_campaign_schedule(repetitions=(1,))
    plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in plan.items() if key != "integrity"}
    )

    normalized = eval_module.validate_comparison_plan(plan)

    assert normalized["campaign_variant"] == eval_module.REDUCED_CAMPAIGN_VARIANT
    assert len(normalized["schedule"]) == 14
    assert sum(entry["agent"] == "morrow" for entry in normalized["schedule"]) == 10
    assert sum(entry["agent"] == "pi" for entry in normalized["schedule"]) == 4
    assert {entry["repetition"] for entry in normalized["schedule"]} == {1}


def test_comparison_plan_rejects_unknown_sensitive_mixed_and_unapproved_values() -> None:
    unknown = _comparison_plan()
    unknown["extra"] = True
    with pytest.raises(eval_module.EvalError, match="unknown comparison plan field"):
        eval_module.validate_comparison_plan(unknown)

    sensitive = _comparison_plan()
    sensitive["provider_secret"] = "sk-" + "x" * 32
    with pytest.raises(eval_module.EvalError, match="forbidden sensitive field"):
        eval_module.validate_comparison_plan(sensitive)

    mixed = _comparison_plan()
    mixed["profiles"]["pi"]["profile"]["model"]["id"] = "other/model"
    mixed["profiles"]["pi"]["sha256"] = eval_module.content_hash(mixed["profiles"]["pi"]["profile"])
    mixed["integrity"] = eval_module.content_hash(
        {key: value for key, value in mixed.items() if key != "integrity"}
    )
    with pytest.raises(eval_module.EvalError, match="canonical model"):
        eval_module.validate_comparison_plan(mixed)

    no_budget = _comparison_plan()
    no_budget["ceilings"]["total_cost"] = 0
    no_budget["integrity"] = eval_module.content_hash(
        {key: value for key, value in no_budget.items() if key != "integrity"}
    )
    with pytest.raises(eval_module.EvalError, match="positive number"):
        eval_module.validate_comparison_plan(no_budget)

    unlimited_cost = _comparison_plan()
    unlimited_cost["ceilings"]["total_cost"] = None
    unlimited_cost["integrity"] = eval_module.content_hash(
        {key: value for key, value in unlimited_cost.items() if key != "integrity"}
    )
    assert eval_module.validate_comparison_plan(unlimited_cost)["ceilings"] == {
        "currency": "USD",
        "total_cost": None,
        "total_tokens": 1_000_000,
    }

    unapproved = _comparison_plan()
    unapproved["hold_point"]["status"] = "pending"
    unapproved["integrity"] = eval_module.content_hash(
        {key: value for key, value in unapproved.items() if key != "integrity"}
    )
    with pytest.raises(eval_module.EvalError, match="has not been approved"):
        eval_module.validate_comparison_plan(unapproved)

    unpinned = _comparison_plan()
    unpinned["source"]["pi_package_sha256"] = "unavailable"
    unpinned["integrity"] = eval_module.content_hash(
        {key: value for key, value in unpinned.items() if key != "integrity"}
    )
    with pytest.raises(eval_module.EvalError, match="cannot be unavailable"):
        eval_module.validate_comparison_plan(unpinned)


def _pi_usage(*, stop_reason: str = "stop", timestamp: int = 10) -> dict[str, object]:
    return {
        "type": "message_end",
        "message": {
            "role": "assistant",
            "provider": "fake",
            "model": "fake/model",
            "timestamp": timestamp,
            "stopReason": stop_reason,
            "content": [{"type": "text", "text": "raw response must not survive"}],
            "usage": {
                "input": 10,
                "output": 5,
                "cacheRead": 0,
                "cacheWrite": 0,
                "reasoning": 2,
                "totalTokens": 15,
                "cost": {
                    "input": 0.1,
                    "output": 0.2,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "total": 0.3,
                },
            },
        },
    }


def test_pi_trace_normalizer_pairs_tools_deduplicates_usage_and_excludes_raw_data(
    tmp_path: Path,
) -> None:
    events = [
        {"type": "session", "id": "ephemeral-session-metadata"},
        {"type": "turn_start", "turnIndex": 0, "timestamp": 1},
        {
            "type": "tool_execution_start",
            "toolCallId": "call-1",
            "toolName": "read",
            "args": {"path": str(tmp_path / "src" / "demo.py"), "api_key": "hidden-value"},
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "toolName": "read",
            "isError": False,
            "result": {"content": [{"type": "text", "text": "ghp_" + "x" * 36}]},
        },
        {
            "type": "tool_execution_start",
            "toolCallId": "call-2",
            "toolName": "bash",
            "args": {"command": "uv run pytest -q", "password": "do-not-retain"},
        },
        {
            "type": "tool_execution_end",
            "toolCallId": "call-2",
            "toolName": "bash",
            "isError": True,
            "result": {
                "details": {"evaluation": {"validation_status": "failed"}},
                "content": [{"type": "text", "text": "Traceback (most recent call last)"}],
            },
        },
        _pi_usage(stop_reason="stop"),
        {"type": "turn_end", "turnIndex": 0, "message": {}, "toolResults": []},
        {"type": "compaction_start", "reason": "overflow"},
        {"type": "compaction_end", "reason": "overflow", "aborted": False, "willRetry": True},
        {
            "type": "auto_retry_start",
            "attempt": 1,
            "maxAttempts": 2,
            "delayMs": 1,
            "errorMessage": "secret raw",
        },
        {"type": "auto_retry_end", "success": True, "attempt": 1},
    ]

    normalized = eval_module.normalize_pi_trace(events, duration_ms=50, workspace=tmp_path)
    serialized = json.dumps(normalized)
    assert normalized["rounds"] == 1
    assert normalized["model_attempts"] == 1
    assert normalized["usage"]["total_tokens"] == 15
    assert normalized["usage"]["cost"] == pytest.approx(0.3)
    assert normalized["tool_states"]["succeeded"] == 1
    assert normalized["tool_states"]["failed"] == 1
    assert normalized["context"]["first_relevant_read_round"] == 1
    assert normalized["context"]["first_validation_round"] == 1
    assert normalized["context"]["overflow_recoveries"] == 1
    assert "hidden-value" not in serialized
    assert "ghp_" not in serialized
    assert "Traceback" not in serialized
    assert "raw response" not in serialized


def test_pi_trace_normalizer_fails_closed_on_missing_duplicate_and_unknown_events() -> None:
    missing_end = [
        {"type": "tool_execution_start", "toolCallId": "call", "toolName": "read", "args": {}},
        _pi_usage(),
    ]
    with pytest.raises(eval_module.EvalError, match="without terminal events"):
        eval_module.normalize_pi_trace(missing_end, duration_ms=1)

    duplicated = [_pi_usage(), _pi_usage()]
    with pytest.raises(eval_module.EvalError, match="repeats an authoritative"):
        eval_module.normalize_pi_trace(duplicated, duration_ms=1)

    with pytest.raises(eval_module.EvalError, match="unsupported event type"):
        eval_module.normalize_pi_trace([{"type": "new_event_shape"}], duration_ms=1)


def test_pi_trace_normalizer_accepts_indexless_turn_events() -> None:
    normalized = eval_module.normalize_pi_trace(
        [
            {"type": "session", "version": 3},
            {"type": "turn_start"},
            _pi_usage(),
            {"type": "turn_end", "message": {}, "toolResults": []},
        ],
        duration_ms=1,
    )

    assert normalized["rounds"] == 1
    assert normalized["model_attempts"] == 1


def test_pi_trace_normalizer_classifies_agent_end_without_semantic_stop_as_runtime_failure() -> (
    None
):
    usage = _pi_usage(stop_reason="toolUse")
    normalized = eval_module.normalize_pi_trace(
        [usage, {"type": "agent_end", "messages": [], "willRetry": False}],
        duration_ms=1,
    )

    assert normalized["usage"]["total_tokens"] == 15
    assert normalized["stop"]["code"] == "runtime_failed"


@pytest.mark.parametrize(
    ("tool_name", "family"),
    [
        ("find_files", "search"),
        ("glob", "search"),
        ("search_text", "search"),
        ("git_status", "read"),
        ("git_diff", "read"),
        ("show_changes", "read"),
        ("delete_file", "edit"),
        ("move_file", "edit"),
        ("rename_file", "edit"),
        ("update_configuration", "edit"),
        ("run_skill_script", "command"),
    ],
)
def test_trace_tool_family_covers_the_complete_morrow_inventory(tool_name, family) -> None:
    assert eval_module._tool_family(tool_name) == family


def test_trace_paths_covers_move_and_rename_endpoints(tmp_path: Path) -> None:
    assert eval_module._trace_paths(
        {"source_path": "old.py", "destination_path": "src/new.py"}, tmp_path
    ) == ["old.py", "src/new.py"]


@pytest.mark.parametrize(
    ("stop_reason", "stop_code"),
    [
        ("stop", "completed"),
        ("length", "budget_exhausted"),
        ("error", "runtime_failed"),
        ("aborted", "cancelled"),
    ],
)
def test_pi_trace_stop_mapping_is_frozen(stop_reason: str, stop_code: str) -> None:
    trace = eval_module.normalize_pi_trace([_pi_usage(stop_reason=stop_reason)], duration_ms=1)
    assert trace["stop"] == {"code": stop_code, "reason": stop_code}


def test_morrow_trace_normalizer_computes_rework_and_rejects_sensitive_projection() -> None:
    trace = {
        "schema_version": 1,
        "rounds": 3,
        "model_attempts": 3,
        "tool_calls": [
            {
                "ordinal": 1,
                "round": 1,
                "capability": "edit",
                "state": "succeeded",
                "paths": ["demo.py"],
                "validator_kind": None,
                "effective_write": True,
                "validation_status": "not_run",
                "invalid_arguments": False,
                "basic_tool_blocked": False,
            },
            {
                "ordinal": 2,
                "round": 2,
                "capability": "command",
                "state": "failed",
                "paths": [],
                "validator_kind": "pytest",
                "effective_write": False,
                "validation_status": "failed",
                "invalid_arguments": False,
                "basic_tool_blocked": False,
            },
            {
                "ordinal": 3,
                "round": 3,
                "capability": "edit",
                "state": "succeeded",
                "paths": ["demo.py"],
                "validator_kind": None,
                "effective_write": True,
                "validation_status": "not_run",
                "invalid_arguments": False,
                "basic_tool_blocked": False,
            },
        ],
        "compactions": 0,
        "overflow_recoveries": 0,
        "retries": 0,
        "usage": {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40, "cost": 1.0},
        "duration_ms": 20,
        "stop_code": "completed",
    }
    normalized = eval_module.normalize_morrow_trace(trace)
    assert normalized["usage"]["rework_count"] == 1
    assert normalized["context"]["first_effective_write_round"] == 1
    assert normalized["context"]["latest_validation_outcome"] == "failed"

    trace["reasoning"] = "must never enter safe evidence"
    with pytest.raises(eval_module.EvalError, match="forbidden sensitive field"):
        eval_module.normalize_morrow_trace(trace)


def test_morrow_runner_projection_uses_safe_facts_and_discards_arguments() -> None:
    calls = [
        [
            {
                "id": "call-read",
                "name": "read_file",
                "arguments": json.dumps({"path": "src/demo.py", "secret": "sk-" + "x" * 24}),
            },
            {
                "id": "call-write",
                "name": "write_file",
                "arguments": json.dumps({"path": "src/demo.py", "content": "private payload"}),
            },
        ],
        [
            {
                "id": "call-test",
                "name": "run_command",
                "arguments": json.dumps({"command": "uv run pytest -q"}),
            }
        ],
    ]
    events = [
        {
            "type": "tool.status",
            "payload": {"call_id": call_id, "status": "succeeded"},
        }
        for call_id in ("call-read", "call-write", "call-test")
    ]
    facts = (
        {
            "kind": "change",
            "call_id": "call-write",
            "relative_paths": ("src/demo.py",),
            "before_revision": "sha256:" + "1" * 64,
            "after_revision": "sha256:" + "2" * 64,
            "changed_bytes": 12,
        },
        {
            "kind": "validation",
            "call_id": "call-test",
            "relative_paths": (".",),
            "validator_kind": "pytest",
            "status": "passed",
        },
    )
    metrics = {
        "finish_reason": "stop",
        "stop_code": None,
        "model_attempts": 3,
        "retry_count": 0,
        "compaction_count": 0,
        "overflow_recovery_count": 0,
        "usage": {
            "availability": "available",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
        "cost": {
            "availability": "available",
            "amount_minor": 2,
            "currency": "USD",
            "source": "fixture",
        },
    }

    projected = eval_module.project_morrow_safe_trace(
        events=events,
        tool_cycles=calls,
        facts=facts,
        metrics=metrics,
        duration_ms=50,
    )
    normalized = eval_module.normalize_morrow_trace(projected)

    assert normalized["usage"]["total_tokens"] == 120
    assert normalized["usage"]["cost"] == 0.02
    assert normalized["context"]["first_relevant_read_round"] == 1
    assert normalized["context"]["first_effective_write_round"] == 1
    assert normalized["context"]["first_validation_round"] == 2
    assert normalized["context"]["latest_validation_outcome"] == "passed"
    serialized = json.dumps(normalized)
    assert "private payload" not in serialized
    assert "sk-" not in serialized


def test_morrow_runner_keeps_provider_usage_when_unbounded_cost_is_unavailable() -> None:
    metrics = {
        "finish_reason": "stop",
        "stop_code": None,
        "model_attempts": 1,
        "retry_count": 0,
        "compaction_count": 0,
        "overflow_recovery_count": 0,
        "usage": {
            "availability": "available",
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
        "cost": {
            "availability": "unavailable",
            "amount_minor": None,
            "currency": None,
            "source": None,
        },
    }

    projected = eval_module.project_morrow_safe_trace(
        events=[], tool_cycles=[], facts=(), metrics=metrics, duration_ms=50
    )
    normalized = eval_module.normalize_morrow_trace(projected)

    assert normalized["usage"]["total_tokens"] == 120
    assert normalized["usage"]["cost"] == "unavailable"

    evidence = eval_module.runtime_evidence_from_normalized_trace(normalized)
    assert evidence["schema_version"] == 1
    assert evidence["availability"] == "available"
    assert evidence["usage"]["total_tokens"] == 120
    assert evidence["usage"]["cost"] == "unavailable"


def test_morrow_runner_preserves_unavailable_usage_as_partial_evidence() -> None:
    metrics = {
        "finish_reason": "error",
        "stop_code": "internal",
        "model_attempts": 3,
        "retry_count": 1,
        "compaction_count": 0,
        "overflow_recovery_count": 0,
        "usage": {
            "availability": "unavailable",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        },
        "cost": {
            "availability": "unavailable",
            "amount_minor": None,
            "currency": None,
            "source": None,
        },
    }

    projected = eval_module.project_morrow_safe_trace(
        events=[], tool_cycles=[], facts=(), metrics=metrics, duration_ms=50
    )
    assert projected["usage"] == {
        "input_tokens": "unavailable",
        "output_tokens": "unavailable",
        "total_tokens": "unavailable",
        "cost": "unavailable",
    }

    normalized = eval_module.normalize_morrow_trace(projected)
    evidence = eval_module.runtime_evidence_from_normalized_trace(normalized)

    assert evidence["availability"] == "available"
    assert evidence["usage"]["total_tokens"] == "unavailable"
    assert evidence["usage"]["duration_ms"] == 50
    assert evidence["usage"]["rounds"] == 3
    assert evidence["stop"]["code"] == "runtime_failed"
    assert eval_module._runtime_evidence_complete(evidence) is True


def test_morrow_runner_accepts_individually_available_usage_fields() -> None:
    metrics = {
        "finish_reason": "error",
        "stop_code": "internal",
        "model_attempts": 1,
        "retry_count": 0,
        "compaction_count": 0,
        "overflow_recovery_count": 0,
        "usage": {
            "availability": "available",
            "input_tokens": 100,
            "output_tokens": None,
            "total_tokens": None,
        },
        "cost": {
            "availability": "unavailable",
            "amount_minor": None,
            "currency": None,
            "source": None,
        },
    }

    projected = eval_module.project_morrow_safe_trace(
        events=[], tool_cycles=[], facts=(), metrics=metrics, duration_ms=50
    )
    normalized = eval_module.normalize_morrow_trace(projected)
    evidence = eval_module.runtime_evidence_from_normalized_trace(normalized)

    assert evidence["usage"]["input_tokens"] == 100
    assert evidence["usage"]["output_tokens"] == "unavailable"
    assert evidence["usage"]["total_tokens"] == "unavailable"


def test_permission_equivalence_and_evaluation_approval_contract(tmp_path: Path) -> None:
    from morrow.core.models import ToolApprovalRequest, ToolEffect

    matrix = eval_module.permission_equivalence_matrix(tmp_path)
    assert matrix["status"] == "PASS"
    assert matrix["mismatches"] == []
    assert {row["case_id"] for row in matrix["rows"]} == {
        "workspace_read",
        "workspace_write",
        "project_command",
        "external_filesystem",
        "task_network",
        "credential_access",
        "git_mutation",
        "privilege_escalation",
    }
    assert {
        row["case_id"]: row["morrow"]
        for row in matrix["rows"]
        if row["case_id"] in {"task_network", "git_mutation", "privilege_escalation"}
    } == {
        "task_network": "deny",
        "git_mutation": "deny",
        "privilege_escalation": "deny",
    }

    port = eval_module.EvaluationApprovalPort()
    approved = asyncio.run(
        port.request(
            ToolApprovalRequest(
                call_id="safe-command",
                effect=ToolEffect.NONE,
                reason_codes=("host_process_approval_required",),
            )
        )
    )
    denied = asyncio.run(
        port.request(
            ToolApprovalRequest(
                call_id="unknown",
                effect=ToolEffect.NONE,
                reason_codes=("legacy_static_approval",),
            )
        )
    )
    assert approved.approved is True
    assert denied.approved is False
    assert (port.approvals, port.rejections) == (1, 1)


def test_campaign_admission_loads_frozen_isolated_config_before_creation(
    tmp_path: Path,
) -> None:
    plan = _comparison_plan()
    evidence_root = tmp_path / "configured-evidence"
    plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(evidence_root.resolve()).encode("utf-8")
    )
    plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in plan.items() if key != "integrity"}
    )
    dependencies = _campaign_admission_dependencies(tmp_path)

    admission = eval_module.admit_campaign_run(
        plan,
        evidence_root,
        ordinal=1,
        reserve_tokens=10_000,
        reserve_cost=1.0,
        **dependencies,
    )

    entry = eval_module.validate_comparison_plan(plan)["schedule"][0]
    state_root = eval_module.campaign_morrow_state_root(evidence_root, entry)
    isolated = build_application(
        state_root=state_root,
        credentials=dependencies["credentials"],
    )
    isolated.provider_service.credential_resolver = (
        isolated.provider_service.resolve_frozen_credential
    )
    _provider, model = isolated.provider_service.build_active()
    config = isolated.provider_service.list()
    assert admission.is_dir()
    assert state_root.is_dir()
    assert state_root.stat().st_mode & 0o077 == 0
    assert str(model) == "fake/fake/model"
    assert set(config.providers) == {"fake"}
    assert config.providers["fake"].credential_ref == CredentialRef(ref="provider:fake:evaluation")
    assert "fixture-provider-value" not in (state_root / "config.yaml").read_text(encoding="utf-8")


def test_campaign_admission_does_not_consume_run_key_when_frozen_config_cannot_load(
    tmp_path: Path,
) -> None:
    plan = _comparison_plan()
    evidence_root = tmp_path / "unconfigured-evidence"
    plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(evidence_root.resolve()).encode("utf-8")
    )
    plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in plan.items() if key != "integrity"}
    )
    dependencies = _campaign_admission_dependencies(tmp_path, with_credential=False)
    entry = eval_module.validate_comparison_plan(plan)["schedule"][0]

    with pytest.raises(
        eval_module.EvalError,
        match="isolated Morrow campaign configuration could not be loaded",
    ):
        eval_module.admit_campaign_run(
            plan,
            evidence_root,
            ordinal=1,
            reserve_tokens=10_000,
            reserve_cost=1.0,
            **dependencies,
        )

    assert not (evidence_root / eval_module._campaign_run_key(entry)).exists()
    assert not eval_module.campaign_morrow_state_root(evidence_root, entry).exists()
    assert eval_module._campaign_records(evidence_root, plan) == []


def test_campaign_admission_is_create_only_ordered_confined_and_budgeted(tmp_path: Path) -> None:
    plan = _comparison_plan()
    admission_dependencies = _campaign_admission_dependencies(tmp_path)
    evidence_root = tmp_path / "protected-evidence"
    plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(evidence_root.resolve()).encode("utf-8")
    )
    plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in plan.items() if key != "integrity"}
    )
    first = eval_module.admit_campaign_run(
        plan,
        evidence_root,
        ordinal=1,
        reserve_tokens=10_000,
        reserve_cost=1.0,
        **admission_dependencies,
    )
    assert first.name.startswith("01-morrow-")
    assert (first / "admission.json").is_file()
    assert first.parent.stat().st_mode & 0o077 == 0

    with pytest.raises(eval_module.EvalError, match="out of frozen schedule order"):
        eval_module.admit_campaign_run(
            plan,
            evidence_root,
            ordinal=3,
            reserve_tokens=10_000,
            reserve_cost=1.0,
            **admission_dependencies,
        )

    too_expensive = _comparison_plan()
    too_expensive["ceilings"]["total_cost"] = 1.5
    too_expensive["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str((tmp_path / "other-evidence").resolve()).encode("utf-8")
    )
    too_expensive["integrity"] = eval_module.content_hash(
        {key: value for key, value in too_expensive.items() if key != "integrity"}
    )
    other_root = tmp_path / "other-evidence"
    eval_module.admit_campaign_run(
        too_expensive,
        other_root,
        ordinal=1,
        reserve_tokens=10_000,
        reserve_cost=1.0,
        **admission_dependencies,
    )
    with pytest.raises(eval_module.EvalError, match="currency ceiling"):
        eval_module.admit_campaign_run(
            too_expensive,
            other_root,
            ordinal=2,
            reserve_tokens=10_000,
            reserve_cost=1.0,
            **admission_dependencies,
        )

    unlimited_cost = _comparison_plan()
    unlimited_cost["ceilings"]["total_cost"] = None
    unlimited_root = tmp_path / "unlimited-cost-evidence"
    unlimited_cost["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(unlimited_root.resolve()).encode("utf-8")
    )
    unlimited_cost["integrity"] = eval_module.content_hash(
        {key: value for key, value in unlimited_cost.items() if key != "integrity"}
    )
    eval_module.admit_campaign_run(
        unlimited_cost,
        unlimited_root,
        ordinal=1,
        reserve_tokens=10_000,
        reserve_cost=1_000_000.0,
        **admission_dependencies,
    )


def test_campaign_capacity_prefers_runtime_usage_and_enforces_observed_overage(
    tmp_path: Path,
) -> None:
    plan = _comparison_plan()
    admission_dependencies = _campaign_admission_dependencies(tmp_path)
    plan["ceilings"]["total_tokens"] = 100
    evidence_root = tmp_path / "capacity-evidence"
    plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(evidence_root.resolve()).encode("utf-8")
    )
    plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in plan.items() if key != "integrity"}
    )
    first = eval_module.admit_campaign_run(
        plan,
        evidence_root,
        ordinal=1,
        reserve_tokens=10,
        reserve_cost=1.0,
        **admission_dependencies,
    )
    usage = _runtime_evidence()
    usage["usage"]["input_tokens"] = 90
    usage["usage"]["output_tokens"] = 5
    usage["usage"]["total_tokens"] = 95
    (first / "runtime-evidence.json").write_text(json.dumps(usage), encoding="utf-8")

    capacity = eval_module.campaign_capacity_usage(plan, evidence_root)

    assert capacity["known_tokens"] == 95
    assert capacity["accounted_tokens"] == 95
    assert capacity["runs"][0]["source"] == "runtime_evidence"
    with pytest.raises(eval_module.EvalError, match="token ceiling"):
        eval_module.admit_campaign_run(
            plan,
            evidence_root,
            ordinal=2,
            reserve_tokens=10,
            reserve_cost=1.0,
            **admission_dependencies,
        )


def test_campaign_capacity_falls_back_to_durable_morrow_request_usage(tmp_path: Path) -> None:
    plan = _comparison_plan()
    admission_dependencies = _campaign_admission_dependencies(tmp_path)
    evidence_root = tmp_path / "morrow-capacity-evidence"
    plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(evidence_root.resolve()).encode("utf-8")
    )
    plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in plan.items() if key != "integrity"}
    )
    first = eval_module.admit_campaign_run(
        plan,
        evidence_root,
        ordinal=1,
        reserve_tokens=10,
        reserve_cost=1.0,
        **admission_dependencies,
    )
    store = first / "morrow-state" / "store"
    store.mkdir(parents=True)
    connection = sqlite3.connect(store / "operational.sqlite")
    connection.execute(
        """
        CREATE TABLE agent_run_model_requests (
            agent_run_id TEXT NOT NULL,
            attempt_ordinal INTEGER NOT NULL,
            usage_availability TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER
        )
        """
    )
    connection.executemany(
        "INSERT INTO agent_run_model_requests VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("run", 1, "available", 40, 30, 70),
            ("run", 2, "unavailable", None, None, None),
        ],
    )
    connection.commit()
    connection.close()

    capacity = eval_module.campaign_capacity_usage(plan, evidence_root)

    assert capacity["known_tokens"] == 70
    assert capacity["accounted_tokens"] == 70
    assert capacity["unknown_requests"] == 1
    assert capacity["runs"][0]["source"] == "morrow_request_journal"


def test_campaign_capacity_includes_explicit_prior_campaigns(tmp_path: Path) -> None:
    prior_plan = _comparison_plan()
    admission_dependencies = _campaign_admission_dependencies(tmp_path)
    prior_root = tmp_path / "prior-evidence"
    prior_plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(prior_root.resolve()).encode("utf-8")
    )
    prior_plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in prior_plan.items() if key != "integrity"}
    )
    eval_module.admit_campaign_run(
        prior_plan,
        prior_root,
        ordinal=1,
        reserve_tokens=70,
        reserve_cost=1.0,
        **admission_dependencies,
    )
    (prior_root / "comparison-plan.json").write_text(json.dumps(prior_plan), encoding="utf-8")

    current_plan = _comparison_plan()
    current_plan["ceilings"]["total_tokens"] = 100
    current_root = tmp_path / "current-evidence"
    current_plan["evidence_root"]["path_sha256"] = eval_module.bytes_hash(
        str(current_root.resolve()).encode("utf-8")
    )
    current_plan["integrity"] = eval_module.content_hash(
        {key: value for key, value in current_plan.items() if key != "integrity"}
    )

    capacity = eval_module.campaign_capacity_usage(
        current_plan,
        current_root,
        prior_roots=(prior_root,),
    )

    assert capacity["accounted_tokens"] == 70
    assert capacity["remaining_tokens"] == 30
    assert capacity["prior_campaigns"][0]["run_count"] == 1
    planned = eval_module.campaign_capacity_usage(
        current_plan,
        current_root,
        prior_roots=(prior_root,),
        planned_reserve_tokens=31,
        planned_admissions=1,
    )
    assert planned["planned_reservation_tokens"] == 31
    assert planned["planned_over_ceiling"] is True
    with pytest.raises(eval_module.EvalError, match="token ceiling"):
        eval_module.admit_campaign_run(
            current_plan,
            current_root,
            ordinal=1,
            reserve_tokens=31,
            reserve_cost=1.0,
            prior_roots=(prior_root,),
            **admission_dependencies,
        )


def test_pi_capacity_fallback_deduplicates_repeated_assistant_usage(tmp_path: Path) -> None:
    run_dir = tmp_path / "pi-run"
    raw = run_dir / "pi-raw"
    raw.mkdir(parents=True)
    event = _pi_usage()
    (raw / "agent-stdout.raw").write_text(
        json.dumps(event) + "\n" + json.dumps(event) + "\n",
        encoding="utf-8",
    )

    usage = eval_module._pi_raw_usage(run_dir)

    assert usage == {
        "known_tokens": 15,
        "source": "pi_raw_assistant_usage",
        "unknown_requests": 0,
    }


def test_bounded_process_retains_only_hash_metadata_and_refuses_overwrite(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    result = eval_module.run_bounded_process(
        [sys.executable, "-c", "print('raw-secret-like-output')"],
        cwd=tmp_path,
        evidence_dir=evidence,
        timeout_seconds=5,
    )
    assert result["returncode"] == 0
    assert result["stdout"]["sha256"].startswith("sha256:")
    assert "raw-secret-like-output" not in json.dumps(result)
    with pytest.raises(eval_module.EvalError, match="raw output already exists"):
        eval_module.run_bounded_process(
            [sys.executable, "-c", "pass"],
            cwd=tmp_path,
            evidence_dir=evidence,
            timeout_seconds=5,
        )


def test_pi_agent_command_pins_policy_resources_and_selected_model() -> None:
    extension = EVAL_PATH.parent / "pi-evaluation-policy.ts"
    command = eval_module.pi_agent_command("fixture prompt", extension, model_id="fixture-model")

    assert command[:8] == [
        "pi",
        "--print",
        "--mode",
        "json",
        "--provider",
        "opencode-go",
        "--model",
        "fixture-model",
    ]
    assert command[command.index("--extension") + 1] == str(extension.resolve())
    assert command[command.index("--tools") + 1] == "read,bash,edit,write,grep,find,ls"
    assert command[command.index("--thinking") + 1] == "off"
    assert "--no-session" in command
    assert "--no-extensions" in command
    assert "--no-skills" in command
    assert "--no-prompt-templates" in command
    assert "--no-context-files" not in command
    assert "--api-key" not in command


def _paired_entry(task_id: str, repetition: int, result_class: str = "PASS") -> dict[str, object]:
    return {
        "manifest": {
            "run": {"task_id": task_id, "repetition": repetition},
            "source": {"dirty": {"comparison_eligible": True}},
        },
        "result": {"result": {"class": result_class}},
        "runtime": {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "cost": 0.1,
                "duration_ms": 1,
                "rounds": 1,
                "user_interventions": 0,
                "rework_count": 0,
            },
            "tool_diagnostics": {"basic_tool_blocked": 0},
        },
    }


def test_paired_comparison_is_mechanical_and_rejects_quality_or_tool_deficits() -> None:
    morrow = [
        _paired_entry(task_id, repetition)
        for task_id in eval_module.FIXED_PI_TASK_IDS
        for repetition in (1, 2)
    ]
    pi = [
        _paired_entry(task_id, repetition)
        for task_id in eval_module.FIXED_PI_TASK_IDS
        for repetition in (1, 2)
    ]
    passed = eval_module._paired_comparison_gate(morrow, pi)
    assert passed["status"] == "PASS"
    assert passed["quality_deficit"] == 0

    for entry in morrow:
        if entry["manifest"]["run"]["task_id"] in {"MORROW-003", "MORROW-005"}:
            entry["result"]["result"]["class"] = "FAIL_MODEL"
    failed = eval_module._paired_comparison_gate(morrow, pi)
    assert failed["status"] == "FAIL"
    assert failed["quality_deficit"] == 2

    morrow[0]["runtime"]["tool_diagnostics"]["basic_tool_blocked"] = 1
    failed = eval_module._paired_comparison_gate(morrow, pi)
    assert any("Morrow-only basic tool blocker" in item for item in failed["diagnostics"])


def test_paired_comparison_accepts_single_repetition_reduced_variant() -> None:
    morrow = [_paired_entry(task_id, 1) for task_id in eval_module.FIXED_PI_TASK_IDS]
    pi = [_paired_entry(task_id, 1) for task_id in eval_module.FIXED_PI_TASK_IDS]

    result = eval_module._paired_comparison_gate(
        morrow,
        pi,
        task_ids=eval_module.FIXED_PI_TASK_IDS,
        repetitions=(1,),
    )

    assert result["status"] == "PASS"
    assert result["quality_deficit"] == 0
    assert result["morrow_stable_task_passes"] == "not_applicable"
    assert result["pi_stable_task_passes"] == "not_applicable"
    assert result["observation_basis"] == "single_repetition"


def test_paired_comparison_allows_missing_cost_only_without_currency_ceiling() -> None:
    morrow = [
        _paired_entry(task_id, repetition)
        for task_id in eval_module.FIXED_PI_TASK_IDS
        for repetition in (1, 2)
    ]
    pi = [
        _paired_entry(task_id, repetition)
        for task_id in eval_module.FIXED_PI_TASK_IDS
        for repetition in (1, 2)
    ]
    for entry in morrow:
        entry["runtime"]["usage"]["cost"] = "unavailable"

    optional = eval_module._paired_comparison_gate(morrow, pi, require_cost=False)
    required = eval_module._paired_comparison_gate(morrow, pi, require_cost=True)

    assert optional["status"] == "PASS"
    assert required["status"] == "FAIL"
