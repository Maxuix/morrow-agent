"""Local scope summaries expose frozen choices without publishing internal references."""

import json

import pytest

from morrow.application.computer_permissions import computer_scope_lines, computer_scope_summary
from morrow.core.computer_use import ComputerUseWindowIdentity, decode_computer_use_scope
from test_computer_use_driver import _scope


@pytest.mark.parametrize("version", [1, 2])
def test_scope_summary_preserves_scope_and_excludes_internal_binding(version):
    legacy = _scope()
    scope = legacy
    if version == 2:
        scope = decode_computer_use_scope(
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


@pytest.mark.parametrize(
    "action",
    [
        {"type": "click", "element_ref": "celem_private", "button": "right", "count": 2},
        {"type": "type_text", "text": "Never display this input", "element_ref": "celem_private"},
        {"type": "press_key", "key": "enter"},
        {"type": "hotkey", "keys": ["meta", "q"]},
        {"type": "scroll", "direction": "down", "amount": 2},
    ],
)
def test_action_preview_excludes_input_refs_and_uses_frozen_delivery(action):
    from pydantic import TypeAdapter

    from morrow.application.computer_permissions import computer_action_preview_lines
    from morrow.core.computer_use import ComputerUseAction, TargetRef

    scope = _scope()
    target = TargetRef(
        target_ref="ctarget_private",
        agent_run_id=scope.agent_run_id,
        generation=scope.generation,
        app=scope.apps[0],
        process_identity="cproc_private",
        window_identity="cwin_private",
        display_label="受控窗口",
    )
    typed = TypeAdapter(ComputerUseAction).validate_python(action)
    preview = "\n".join(computer_action_preview_lines(scope, target, typed))
    assert "目标窗口：受控窗口" in preview and "投递：前台" in preview
    assert "cproc_" not in preview and "ctarget_" not in preview and "celem_" not in preview
    assert "Never display this input" not in preview
    if action["type"] == "click":
        assert "右键双击" in preview
    if action["type"] == "type_text":
        assert "输入文本" in preview
    unknown = "\n".join(computer_action_preview_lines(scope, None, typed))
    assert "观察已失效，请重新观察" in unknown
