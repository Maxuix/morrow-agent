"""Fixture cleanup binds the application PID, never the open launcher or a reused PID."""

import runpy
from pathlib import Path
from types import SimpleNamespace

from morrow.core.computer_use import ComputerUseContractError


def process_type(monkeypatch):
    directory = Path(__file__).parents[1] / "evals/computer_use"
    monkeypatch.syspath_prepend(str(directory))
    return runpy.run_path(str(directory / "live_provider.py"))["FixtureProcess"]


def test_fixture_cleanup_targets_app_even_after_launcher_exit(monkeypatch):
    fixture = process_type(monkeypatch)
    namespace = fixture.__init__.__globals__
    monkeypatch.setitem(namespace, "read_process_birth", lambda pid: (pid, 123))
    killed = []
    monkeypatch.setattr(namespace["os"], "kill", lambda pid, sig: killed.append(pid))
    launcher = SimpleNamespace(pid=901, poll=lambda: 0)
    process = fixture(launcher, 902)
    assert process.poll() is None
    process.terminate()
    assert killed == [902]


def test_fixture_cleanup_never_signals_reused_pid(monkeypatch):
    fixture = process_type(monkeypatch)
    namespace = fixture.__init__.__globals__
    monkeypatch.setitem(namespace, "read_process_birth", lambda pid: (pid, 123))
    process = fixture(SimpleNamespace(poll=lambda: None), 902)
    monkeypatch.setitem(namespace, "read_process_birth", lambda pid: (pid, 124))
    killed = []
    monkeypatch.setattr(namespace["os"], "kill", lambda pid, sig: killed.append(pid))
    assert process.poll() == 0
    process.terminate()
    assert killed == []


def test_fixture_cleanup_never_signals_missing_pid(monkeypatch):
    fixture = process_type(monkeypatch)
    namespace = fixture.__init__.__globals__
    monkeypatch.setitem(namespace, "read_process_birth", lambda pid: (pid, 123))
    process = fixture(SimpleNamespace(poll=lambda: None), 902)

    def gone(pid):
        raise ComputerUseContractError("target_identity_unavailable")

    monkeypatch.setitem(namespace, "read_process_birth", gone)
    killed = []
    monkeypatch.setattr(namespace["os"], "kill", lambda pid, sig: killed.append(pid))
    process.terminate()
    assert killed == []
