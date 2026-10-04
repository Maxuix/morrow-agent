"""Compile only the fixture's Codable values; no application or desktop is launched."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("swiftc") is None, reason="Swift compiler unavailable")
def test_unknown_pointer_coordinates_do_not_block_independent_snapshot_export(tmp_path):
    fixture = Path("evals/computer_use/Fixture.swift").read_text()
    types = fixture[
        fixture.index("struct WindowFacts:") : fixture.index("// This file is independent")
    ]
    source = tmp_path / "export.swift"
    source.write_text(
        "import Foundation\n"
        + types
        + """
let unknown = PointerEvent(kind: "left", clickCount: 1, windowX: .nan, windowY: .infinity, deltaY: 0)
let known = PointerEvent(kind: "wheel", clickCount: 0, windowX: 20, windowY: 30, deltaY: -3)
let snapshot = FixtureSnapshot(instanceId: "fixture", pid: 123, revision: 2,
    count: 1, text: "", secureFieldPopulated: false, scrollOffset: 0,
    window: nil, liveText: "", liveSecureText: "", textChangeEvents: 0,
    secureChangeEvents: 0, lastEditedField: nil, mouseClickCounts: [1],
    buttonActionCallbacks: 1, rightMouseEvents: 0, menuActions: 0,
    keyDownCharacters: [], keyDownFields: [], keyUpEvents: 0,
    pointerEvents: [unknown, known], scrollRegion: nil, windowIsKey: true, appActive: true)
let content = try JSONEncoder().encode(snapshot)
print(String(data: content, encoding: .utf8)!)
"""
    )
    binary = tmp_path / "export"
    subprocess.run(["swiftc", str(source), "-o", str(binary)], check=True, capture_output=True)
    output = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout
    state = json.loads(output)
    assert state["schemaVersion"] == 4 and state["exportHealthy"] is True
    assert state["revision"] == 2 and state["count"] == state["buttonActionCallbacks"] == 1
    unknown, known = state["pointerEvents"]
    assert unknown["windowX"] is None and unknown["windowY"] is None
    assert unknown["positionKnown"] is False
    assert known["windowX"] == 20 and known["windowY"] == 30 and known["positionKnown"] is True
