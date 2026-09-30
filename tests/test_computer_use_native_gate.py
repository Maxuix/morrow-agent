"""Native gate diagnostics use fake typed results and never initialize a Driver."""

from __future__ import annotations

import runpy
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
    }
