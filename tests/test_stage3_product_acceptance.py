from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from morrow.adapters.credentials.keyring import MemoryCredentialStore
from morrow.bootstrap import build_application, build_session_application
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelRef, ToolApprovalDecision
from morrow.testing import ScriptedModelProvider

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")


class _Approval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, request):
        self.requests.append(request)
        return ToolApprovalDecision(approved=True)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        },
    )


def _repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Morrow Test")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _call(call_id: str, name: str, arguments: dict) -> FunctionToolCall:
    return FunctionToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


@pytest.mark.asyncio
async def test_fake_provider_python_locate_patch_fail_correct_validate_and_report(tmp_path):
    project = tmp_path / "python-project"
    _repo(project)
    source = project / "main.py"
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")
    (project / "test_answer.py").write_text(
        "from main import answer\n\ndef test_answer():\n    assert answer() == 2\n",
        encoding="utf-8",
    )
    _git(project, "add", "main.py", "test_answer.py")
    _git(project, "commit", "-qm", "initial")
    original_sha = _sha256(source)
    source.write_text("def answer():\n    return 3\n", encoding="utf-8")
    bad_sha = _sha256(source)
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")

    provider = ScriptedModelProvider(
        [
            AssistantMessage(
                tool_calls=(
                    _call(
                        "search",
                        "search_text",
                        {"path": ".", "pattern": "answer", "literal": True},
                    ),
                )
            ),
            AssistantMessage(tool_calls=(_call("read", "read_file", {"path": "main.py"}),)),
            AssistantMessage(
                tool_calls=(
                    _call(
                        "bad-patch",
                        "apply_patch",
                        {
                            "path": "main.py",
                            "expected_sha256": original_sha,
                            "edits": [{"old_text": "return 1", "new_text": "return 3"}],
                        },
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call(
                        "bad-test",
                        "run_command",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; raise SystemExit(0 if 'return 2' in Path('main.py').read_text() else 1)",
                            ]
                        },
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call(
                        "good-patch",
                        "apply_patch",
                        {
                            "path": "main.py",
                            "expected_sha256": bad_sha,
                            "edits": [{"old_text": "return 3", "new_text": "return 2"}],
                        },
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call(
                        "good-test",
                        "run_command",
                        {
                            "argv": [
                                sys.executable,
                                "-c",
                                "from pathlib import Path; raise SystemExit(0 if 'return 2' in Path('main.py').read_text() else 1)",
                            ]
                        },
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call("status", "git_status", {}),
                    _call("diff", "git_diff", {"paths": ["main.py"]}),
                    _call("changes", "show_changes", {}),
                )
            ),
            AssistantMessage(content="已定位、修复并验证 main.py；第一次校验失败，修正后通过。"),
        ]
    )
    approval = _Approval()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    [item async for item in session_app.orchestrator.stream("修复 answer 并运行校验")]

    assert source.read_text(encoding="utf-8") == "def answer():\n    return 2\n"
    tool_payloads = [
        json.loads(message.content)
        for message in session_app.session.messages
        if message.role == "tool"
    ]
    command_results = [
        payload["result"]
        for payload in tool_payloads
        if payload.get("ok") and "command_class" in payload.get("result", {})
    ]
    assert [result["exit_code"] for result in command_results] == [1, 0]
    diff_payload = next(
        payload["result"]
        for payload in tool_payloads
        if payload.get("ok")
        and payload.get("result", {}).get("repository") is True
        and "diff" in payload.get("result", {})
    )
    assert "+    return 2" in diff_payload["diff"]
    assert len(approval.requests) == 4
    assert session_app.session.latest_metrics is not None
    assert session_app.session.latest_metrics.tool_calls == 9
    assert session_app.session.latest_metrics.validation_outcome == "failed"
    assert session_app.session.latest_metrics.changed_file_count == 1
    assert "已定位、修复并验证" in session_app.session.messages[-1].content


@pytest.mark.asyncio
async def test_fake_provider_nested_text_fixture_preserves_user_change_and_reports_unrun_validation(
    tmp_path,
):
    project = tmp_path / "text-project"
    _repo(project)
    target = project / "docs" / "guide" / "readme.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Guide\n\nold text\n", encoding="utf-8")
    user_file = project / "notes.txt"
    user_file.write_text("user change\n", encoding="utf-8")
    _git(project, "add", "docs/guide/readme.md", "notes.txt")
    _git(project, "commit", "-qm", "initial")
    user_file.write_text("pre-existing user change\n", encoding="utf-8")
    target_sha = _sha256(target)

    provider = ScriptedModelProvider(
        [
            AssistantMessage(tool_calls=(_call("list", "list_directory", {"path": "docs/guide"}),)),
            AssistantMessage(
                tool_calls=(_call("read", "read_file", {"path": "docs/guide/readme.md"}),)
            ),
            AssistantMessage(
                tool_calls=(
                    _call(
                        "patch",
                        "apply_patch",
                        {
                            "path": "docs/guide/readme.md",
                            "expected_sha256": target_sha,
                            "edits": [{"old_text": "old text", "new_text": "new text"}],
                        },
                    ),
                )
            ),
            AssistantMessage(
                tool_calls=(
                    _call("changes", "show_changes", {}),
                    _call("status", "git_status", {}),
                    _call("diff", "git_diff", {"paths": ["docs/guide/readme.md"]}),
                )
            ),
            AssistantMessage(content="文档已修改并展示 Diff；本次未运行项目校验。"),
        ]
    )
    approval = _Approval()
    app = build_application(state_root=tmp_path / "state", credentials=MemoryCredentialStore())
    identity = app.workspace_service.confirm(app.workspace_service.resolve(project))
    session_app = build_session_application(
        app,
        identity,
        provider=provider,
        model=ModelRef(provider_id="p", model_id="m"),
        approval_port=approval,
    )

    [item async for item in session_app.orchestrator.stream("更新文档并报告验证边界")]

    assert target.read_text(encoding="utf-8") == "# Guide\n\nnew text\n"
    assert user_file.read_text(encoding="utf-8") == "pre-existing user change\n"
    assert len(approval.requests) == 1
    assert "未运行项目校验" in session_app.session.messages[-1].content
