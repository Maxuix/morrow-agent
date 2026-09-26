"""Benchmark bootstrap accepts only explicit model limits as exact limits."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from morrow.core.models import ModelCapabilityOverrides

SETUP_PATH = Path(__file__).resolve().parents[1] / "evals/benchmarks/harness/bench_setup.py"


def test_bench_setup_configures_supplied_limits_without_guessing(monkeypatch, tmp_path) -> None:
    spec = importlib.util.spec_from_file_location("h6_bench_setup", SETUP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    configured: list[ModelCapabilityOverrides] = []
    existing = SimpleNamespace(capabilities=ModelCapabilityOverrides(reasoning_efforts=("low",)))
    providers = SimpleNamespace(models={"model": existing})
    service = SimpleNamespace(
        list=lambda: SimpleNamespace(providers={"bench": providers}),
        configure_model=lambda _provider, _model, *, capabilities: configured.append(capabilities),
        use_model=lambda *_args: None,
    )
    app = SimpleNamespace(
        workspace_service=SimpleNamespace(
            resolve=lambda _path: SimpleNamespace(
                status="existing", identity=SimpleNamespace(workspace_id="ws")
            )
        ),
        provider_service=service,
    )
    monkeypatch.setattr(module, "build_application", lambda **_kwargs: app)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bench_setup.py",
            "--state-root",
            str(tmp_path),
            "--workspace",
            str(tmp_path),
            "--provider-id",
            "bench",
            "--adapter",
            "openai-compatible",
            "--base-url",
            "https://example.test",
            "--model-id",
            "model",
            "--api-model-id",
            "model",
            "--context-window-tokens",
            "131072",
            "--max-output-tokens",
            "8192",
        ],
    )

    module.main()

    assert len(configured) == 1
    assert configured[0].context_window_tokens == 131_072
    assert configured[0].max_output_tokens == 8192
    assert configured[0].reasoning_efforts == ("low",)
