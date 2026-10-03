"""Real GUI composition, with a fake read probe and no native Driver."""

import json
import runpy
import threading

import pytest
import uvicorn


@pytest.mark.parametrize("case", ["passed", "read_failed", "image_blocked", "missing_capture"])
def test_gui_gate_uses_full_context_and_preserves_production_default_off(
    tmp_path, monkeypatch, case
):
    module = runpy.run_path("evals/computer_use/gui_readonly.py")
    main = module["main"]
    evidence = tmp_path / "evidence.json"
    seen = []

    async def inspect(*, fixture_window):
        assert fixture_window == (2, 3)
        assert threading.current_thread() is not threading.main_thread()
        seen.append("probe")
        return {
            "status": "failed" if case == "read_failed" else "passed",
            "reason": "fixture_ax_missing" if case == "read_failed" else None,
            "image_share_error": "image_decode" if case == "image_blocked" else None,
            "prepared_capture": {
                "byte_size": None if case == "missing_capture" else 100,
                "sha256": "a" * 64,
            },
        }

    monkeypatch.setattr(
        main.__globals__["runpy"],
        "run_path",
        lambda _: {"read_fixture_window": lambda _: (2, 3), "inspect_fixture": inspect},
    )

    class Server:
        def __init__(self, config):
            self.config = config

        def run(self, *, sockets):
            assert sockets[0].getsockname()[0] == "127.0.0.1"
            assert sockets[0].getsockname()[1] > 0
            assert self.config.app is not None
            seen.append("transport")

    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gui_readonly.py",
            "--allow-desktop",
            "--fixture-bundle-id",
            module["FIXTURE_BUNDLE_ID"],
            "--fixture-state-file",
            str(tmp_path / "unused-state.json"),
            "--evidence-file",
            str(evidence),
        ],
    )
    if case == "passed":
        main()
        assert seen == ["probe", "transport"]
    else:
        with pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == 1
        assert seen == ["probe"]
    result = json.loads(evidence.read_text())
    assert result["full_service_context"] is True
    assert result["production_desktop_active"] is False
    assert result["host_mode"] == "gui_composition_probe"
    assert result["gui_url"].startswith("http://127.0.0.1:")
    assert result["status"] == ("passed" if case == "passed" else "failed")
    assert "synthetic-fixture-only" not in evidence.read_text()
