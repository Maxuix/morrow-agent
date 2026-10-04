"""Native gate diagnostics use fake typed results and never initialize a Driver."""

from __future__ import annotations

import json
import runpy
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("reason", ["ax_window_unresolved: secret AX text", "secret SDK error"])
async def test_native_gate_records_only_bounded_metadata(reason):
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    request = object()
    evidence = {}
    state = SimpleNamespace(
        elements=[SimpleNamespace(value="secret field")],
        images=[SimpleNamespace(data_base64="secret image")],
        elements_complete=False,
        truncated=False,
        degraded=True,
        degraded_reason=reason,
        screenshot_frame_valid=False,
    )

    async def read(actual):
        assert actual is request
        return state

    marker = object()
    native = SimpleNamespace(get_window_state=read, close=marker)
    session = module["_DiagnosedSession"](native, evidence)
    assert session.close is marker
    assert await session.get_window_state(request) is state
    assert "secret" not in str(evidence)
    assert evidence["window_read"] == {
        "ax_element_count": 1,
        "ax_complete": False,
        "truncated": False,
        "degraded": True,
        "reason": "ax_window_unresolved"
        if reason.startswith("ax_window_unresolved")
        else "degraded",
        "image_count": 1,
        "frame_valid": False,
        "max_returned_depth": None,
        "truncation_reason": None,
    }


@pytest.mark.parametrize(
    "state",
    [
        None,
        [],
        {},
        {"schemaVersion": 1, "pid": True, "window": {"number": 3}},
        {"schemaVersion": True, "pid": 2, "window": {"number": 3}},
        {"schemaVersion": 1, "pid": 2, "window": {"number": -1}},
        {"schemaVersion": 1, "pid": 2**31, "window": {"number": 3}},
        {"schemaVersion": 3, "pid": 2, "window": {"number": 3}},
    ],
)
def test_fixture_identity_rejects_invalid_state(tmp_path, state):
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    with pytest.raises(module["ComputerUseContractError"], match="fixture_state_invalid"):
        module["read_fixture_window"](path)


def test_fixture_identity_bounded_and_exact(tmp_path):
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    read = module["read_fixture_window"]
    assert read(None) is None
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schemaVersion": 1, "pid": 2, "window": {"number": 3}}))
    assert read(path) == (2, 3)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "pid": 2,
                "window": {"number": 3},
                "buttonActionCallbacks": 0,
            }
        )
    )
    assert read(path) == (2, 3)
    path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(module["ComputerUseContractError"], match="fixture_state_invalid"):
        read(path)
    path.unlink()
    with pytest.raises(module["ComputerUseContractError"], match="fixture_state_invalid"):
        read(path)


def test_fixture_selection_requires_both_native_identities():
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    records = {
        "other_pid": SimpleNamespace(pid=9, window_id=3),
        "other_window": SimpleNamespace(pid=2, window_id=7),
        "exact": SimpleNamespace(pid=2, window_id=3),
    }
    targets = tuple(SimpleNamespace(window_identity=key) for key in records)
    session = SimpleNamespace(_registry=SimpleNamespace(window=records.__getitem__))
    select = module["select_fixture_targets"]
    assert select(session, targets, None) is targets
    assert select(session, targets, (2, 3)) == (targets[2],)
    assert select(session, targets, (2, 99)) == ()
    assert len(select(session, (targets[2], targets[2]), (2, 3))) == 2


@pytest.mark.parametrize("reason", ["node_budget", "timeout", "secret native diagnostic"])
async def test_truncation_diagnostic_uses_fixed_codes(reason):
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    state = SimpleNamespace(
        elements=[SimpleNamespace(depth=5), SimpleNamespace(depth="secret")],
        images=[],
        elements_complete=False,
        truncated=True,
        degraded=False,
        degraded_reason=None,
        screenshot_frame_valid=True,
        truncation_reason=reason,
    )

    async def read(request):
        return state

    evidence = {}
    session = module["_DiagnosedSession"](SimpleNamespace(get_window_state=read), evidence)
    await session.get_window_state(None)
    assert evidence["window_read"]["max_returned_depth"] == 5
    assert evidence["window_read"]["truncation_reason"] == (
        reason if reason in ("node_budget", "timeout") else "unknown"
    )
    assert "secret" not in str(evidence)


async def test_discovery_diagnostics_keep_selection_and_exclude_content():
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    evidence = {"discovery": {"selected_count": 1, "target_count": 2}}

    async def apps(request):
        return SimpleNamespace(
            apps=[
                SimpleNamespace(bundle_id=module["FIXTURE_BUNDLE_ID"], pid=2, title="secret"),
                SimpleNamespace(bundle_id="secret", pid=7),
            ]
        )

    async def windows(request):
        return SimpleNamespace(windows=[SimpleNamespace(window_id=3, title="secret")])

    session = module["_DiagnosedSession"](
        SimpleNamespace(list_apps=apps, list_windows=windows), evidence
    )
    await session.list_apps(None)
    await session.list_windows(None)
    await session.list_apps(None)
    assert evidence["discovery"] == {
        "selected_count": 1,
        "target_count": 2,
        "fixture_app_count": 1,
        "fixture_pids": [2],
        "window_count": 1,
        "windows": [{"window_id": 3, "fixture_title": False}],
    }
    assert "secret" not in str(evidence)


@pytest.mark.parametrize("fails", [False, True])
async def test_core_probe_constructs_reads_and_finishes_on_owner_loop(monkeypatch, fails):
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    inspect = module["inspect_on_core_host"]
    owner_threads = []

    async def fake_inspect(*, fixture_window, native_walk_limit):
        assert fixture_window == (2, 3)
        assert native_walk_limit is None
        owner_threads.append(threading.current_thread())
        if fails:
            raise RuntimeError("probe_failed")
        return {"owner_main_thread": threading.current_thread() is threading.main_thread()}

    monkeypatch.setitem(inspect.__globals__, "inspect_fixture", fake_inspect)
    if fails:
        with pytest.raises(RuntimeError, match="^probe_failed$"):
            await inspect(fixture_window=(2, 3))
    else:
        assert await inspect(fixture_window=(2, 3)) == {
            "owner_main_thread": False,
            "host_mode": "core_owner_probe",
        }
    assert len(owner_threads) == 1 and not owner_threads[0].is_alive()


async def test_native_walk_experiment_is_explicit_and_bounded():
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    request = SimpleNamespace(max_elements=200)
    state = SimpleNamespace(
        elements=[],
        images=[],
        elements_complete=False,
        truncated=False,
        degraded=False,
        degraded_reason=None,
        screenshot_frame_valid=True,
    )

    async def read(actual):
        assert actual is request
        assert actual.max_elements == 400
        return state

    session = module["_DiagnosedSession"](
        SimpleNamespace(get_window_state=read), {}, native_walk_limit=400
    )
    assert await session.get_window_state(request) is state


@pytest.mark.parametrize("limit", [True, 400.0, 401, -1])
def test_native_walk_experiment_rejects_other_limits(limit):
    module = runpy.run_path("evals/computer_use/native_readonly.py")
    with pytest.raises(module["ComputerUseContractError"], match="fixture_walk_limit_invalid"):
        module["_DiagnosedSession"](None, {}, native_walk_limit=limit)
