"""Local scope summaries expose frozen choices without publishing internal references."""

import json

import pytest

from morrow.application.computer_permissions import computer_scope_lines, computer_scope_summary
from morrow.core.computer_use import ComputerUseScope, ComputerUseWindowIdentity
from test_computer_use_driver import _scope


@pytest.mark.parametrize("version", [1, 2])
def test_scope_summary_preserves_scope_and_excludes_internal_binding(version):
    legacy = _scope()
    scope = legacy
    if version == 2:
        scope = ComputerUseScope.model_validate(
            legacy.model_dump()
            | {
                "schema_version": 2,
                "windows": (
                    ComputerUseWindowIdentity(
                        app=legacy.apps[0], window_identity="cwin_private_one"
                    ),
                    ComputerUseWindowIdentity(
                        app=legacy.apps[0], window_identity="cwin_private_two"
                    ),
                ),
            }
        )
    original = scope.model_dump_json()
    summary = computer_scope_summary(scope)
    lines = computer_scope_lines(scope)
    assert summary["apps"] == ["com.example.Notes"]
    assert summary["operations"] == ["observe", "action"]
    assert summary["delivery"] == "foreground"
    assert summary["image_share"] == "controlled_window"
    if version == 2:
        assert summary["window_count"] == 2
        assert "2 个明确选中窗口" in "\n".join(lines)
    else:
        assert summary["window_count"] is None
        assert "未记录固定窗口" in "\n".join(lines)
    assert "cwin_" not in json.dumps(summary) + str(lines)
    assert "arun_" not in json.dumps(summary) + str(lines)
    assert "task_" not in json.dumps(summary) + str(lines)
    assert scope.model_dump_json() == original
