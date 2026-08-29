"""Availability-first project-instruction loading contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from morrow.application.project_instructions import ProjectInstructionResolver


def test_resolver_loads_one_root_file_by_precedence(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.override.md").write_text("override", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("nested", encoding="utf-8")

    resolved = ProjectInstructionResolver(tmp_path).resolve()

    assert [item.reference.path for item in resolved.sources] == ["AGENTS.override.md"]
    assert [item.reference.scope for item in resolved.sources] == ["."]
    assert resolved.sources[0].text == "override"
    assert "nested" not in resolved.rendered_block


def test_resolver_falls_back_to_agents_then_claude(tmp_path: Path) -> None:
    agents = tmp_path / "AGENTS.md"
    claude = tmp_path / "CLAUDE.md"
    agents.write_text("agents", encoding="utf-8")
    claude.write_text("claude", encoding="utf-8")

    resolver = ProjectInstructionResolver(tmp_path)
    assert resolver.resolve().sources[0].text == "agents"
    agents.unlink()
    assert resolver.resolve().sources[0].text == "claude"


@pytest.mark.parametrize(
    "payload",
    [b"\xff", b"good\x00bad", b"good\x0bbad", b"x" * 32],
)
def test_malformed_or_oversized_root_instruction_warns_and_is_skipped(
    tmp_path: Path, payload: bytes
) -> None:
    (tmp_path / "AGENTS.md").write_bytes(payload)
    resolver = ProjectInstructionResolver(tmp_path, max_file_bytes=16)

    with pytest.warns(RuntimeWarning, match="Skipping project instruction"):
        resolved = resolver.resolve()

    assert resolved.sources == ()


def test_non_regular_candidate_warns_and_falls_back(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.override.md").mkdir()
    (tmp_path / "AGENTS.md").write_text("fallback", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="not a regular file"):
        resolved = ProjectInstructionResolver(tmp_path).resolve()

    assert resolved.sources[0].text == "fallback"


def test_instruction_symlink_uses_normal_file_semantics(tmp_path: Path) -> None:
    target = tmp_path / "guidance.md"
    target.write_text("linked guidance", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(target)

    resolved = ProjectInstructionResolver(tmp_path).resolve()

    assert resolved.sources[0].text == "linked guidance"


def test_non_nfc_text_is_normalized_instead_of_rejected(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("e\u0301", encoding="utf-8")

    resolved = ProjectInstructionResolver(tmp_path).resolve()

    assert resolved.sources[0].text == "é"


def test_rehydration_reloads_current_context_and_never_executes_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instruction = tmp_path / "AGENTS.md"
    instruction.write_text("first", encoding="utf-8")
    resolver = ProjectInstructionResolver(tmp_path)
    first = resolver.resolve()

    def fail_process(*args, **kwargs):
        raise AssertionError("project instructions must not execute processes")

    monkeypatch.setattr(subprocess, "run", fail_process)
    instruction.write_text("current", encoding="utf-8")
    restored = resolver.rehydrate(first.references)

    assert restored.sources[0].text == "current"


def test_custom_names_and_filename_validation_remain_explicit(tmp_path: Path) -> None:
    (tmp_path / "PROJECT.md").write_text("custom", encoding="utf-8")
    resolved = ProjectInstructionResolver(tmp_path, filenames=("PROJECT.md",)).resolve()
    assert resolved.sources[0].text == "custom"

    with pytest.raises(ValueError, match="filename"):
        ProjectInstructionResolver(tmp_path, filenames=("nested/AGENTS.md",))
