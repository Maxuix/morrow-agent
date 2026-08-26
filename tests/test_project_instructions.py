"""Bounded, read-only project-instruction resolution contracts for S7P-03."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from morrow.application.project_instructions import (
    ProjectInstructionError,
    ProjectInstructionResolver,
)
from morrow.core.prompt import ProjectInstructionSourceRef


def test_resolver_reads_root_to_leaf_and_keeps_sibling_scope_out(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("src rule", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "AGENTS.md").write_text("pkg rule", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "AGENTS.md").write_text("tests rule", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "module.py").write_text("pass\n", encoding="utf-8")

    resolved = ProjectInstructionResolver(tmp_path).resolve("Please inspect `src/pkg/module.py`")

    assert [item.reference.path for item in resolved.sources] == [
        "AGENTS.md",
        "src/AGENTS.md",
        "src/pkg/AGENTS.md",
    ]
    assert [item.reference.scope for item in resolved.sources] == [".", "src", "src/pkg"]
    assert [item.text for item in resolved.sources] == ["root rule", "src rule", "pkg rule"]
    assert "tests rule" not in resolved.rendered_block

    sibling = ProjectInstructionResolver(tmp_path).resolve(
        "Please inspect `tests/example.py`", target_paths=("tests/example.py",)
    )
    assert [item.reference.path for item in sibling.sources] == ["AGENTS.md", "tests/AGENTS.md"]
    assert "src rule" not in sibling.rendered_block


def test_only_agents_is_enabled_by_default_and_compatible_names_are_explicit(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude", encoding="utf-8")

    default = ProjectInstructionResolver(tmp_path).resolve()
    explicit_resolver = ProjectInstructionResolver(tmp_path, filenames=("AGENTS.md", "CLAUDE.md"))
    explicit = explicit_resolver.resolve()

    assert [item.text for item in default.sources] == ["agents"]
    assert [item.text for item in explicit.sources] == ["agents"]

    (tmp_path / "AGENTS.md").unlink()
    assert [item.text for item in ProjectInstructionResolver(tmp_path).resolve().sources] == []
    assert [item.text for item in explicit_resolver.resolve().sources] == ["claude"]


@pytest.mark.parametrize(
    ("name", "payload", "code"),
    [
        ("invalid-utf8", b"\xff", "invalid_utf8"),
        ("nul", b"good\x00bad", "control_text"),
        ("control", b"good\x0bbad", "control_text"),
        ("non-nfc", "e\u0301".encode("utf-8"), "non_nfc"),
    ],
)
def test_invalid_instruction_content_fails_closed_without_payload(
    tmp_path: Path, name: str, payload: bytes, code: str
) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_bytes(payload)

    with pytest.raises(ProjectInstructionError) as exc_info:
        ProjectInstructionResolver(tmp_path).resolve()

    error = exc_info.value
    assert error.code == code
    assert "good" not in str(error)
    assert "bad" not in str(error)
    assert str(tmp_path) not in str(error)


def test_limits_and_outside_targets_are_bounded(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    resolver = ProjectInstructionResolver(tmp_path, max_targets=1, max_depth=2)

    with pytest.raises(ProjectInstructionError, match="too_many_targets"):
        resolver.resolve(target_paths=("one.py", "two.py"))
    with pytest.raises(ProjectInstructionError, match="outside_workspace"):
        resolver.resolve(target_paths=("../outside.py",))
    with pytest.raises(ProjectInstructionError, match="too_deep"):
        resolver.resolve(target_paths=("a/b/c.py",))


def test_target_extractor_ignores_url_paths(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")

    resolved = ProjectInstructionResolver(tmp_path).resolve(
        "See https://example.test/docs/guide.py for unrelated context."
    )

    assert [item.reference.path for item in resolved.sources] == ["AGENTS.md"]


def test_target_iterable_and_path_length_are_bounded(tmp_path: Path) -> None:
    resolver = ProjectInstructionResolver(tmp_path, max_targets=2)

    def many_targets():
        for index in range(100):
            yield f"target-{index}.py"

    with pytest.raises(ProjectInstructionError, match="too_many_targets"):
        resolver.resolve(target_paths=many_targets())
    with pytest.raises(ProjectInstructionError, match="target_too_long"):
        resolver.resolve(target_paths=("a" * 513,))


def test_instruction_symlink_and_non_regular_sources_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "target.md"
    target.write_text("secret", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(target)
    with pytest.raises(ProjectInstructionError, match="symlink"):
        ProjectInstructionResolver(tmp_path).resolve()

    (tmp_path / "AGENTS.md").unlink()
    (tmp_path / "AGENTS.md").mkdir()
    with pytest.raises(ProjectInstructionError, match="non_regular"):
        ProjectInstructionResolver(tmp_path).resolve()


def test_resolver_requires_nofollow_and_rejects_file_identity_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("original", encoding="utf-8")
    resolver = ProjectInstructionResolver(tmp_path)

    replacement = tmp_path / "replacement.md"
    replacement.write_text("replacement", encoding="utf-8")
    real_open = os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "AGENTS.md" and not swapped:
            swapped = True
            os.replace(replacement, tmp_path / "AGENTS.md")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)
    with pytest.raises(ProjectInstructionError, match="source_changed"):
        resolver.resolve()

    monkeypatch.undo()
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(ProjectInstructionError, match="safe_open_unavailable"):
        ProjectInstructionResolver(tmp_path)


def test_instruction_file_and_aggregate_byte_limits_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("12345", encoding="utf-8")
    with pytest.raises(ProjectInstructionError, match="file_too_large"):
        ProjectInstructionResolver(tmp_path, max_file_bytes=4).resolve()

    (tmp_path / "AGENTS.md").write_text("1234", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "AGENTS.md").write_text("5678", encoding="utf-8")
    with pytest.raises(ProjectInstructionError, match="aggregate_too_large"):
        ProjectInstructionResolver(tmp_path, max_total_bytes=6).resolve(
            target_paths=("nested/file.py",)
        )


def test_rehydration_requires_the_same_hash_and_never_executes_document_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "AGENTS.md").write_text("do not run this", encoding="utf-8")
    resolver = ProjectInstructionResolver(tmp_path)
    resolved = resolver.resolve()

    def fail_process(*args, **kwargs):
        raise AssertionError("project instructions must not execute processes")

    monkeypatch.setattr(subprocess, "run", fail_process)
    restored = resolver.rehydrate(resolved.references)
    assert restored.sources[0].text == "do not run this"

    (tmp_path / "AGENTS.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ProjectInstructionError, match="source_drift"):
        resolver.rehydrate(resolved.references)


def test_frozen_source_metadata_rejects_control_path_and_invalid_shape(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root", encoding="utf-8")
    resolver = ProjectInstructionResolver(tmp_path)
    reference = resolver.resolve().references[0]

    with pytest.raises(ProjectInstructionError, match="invalid_frozen_source"):
        resolver.rehydrate({"path": "bad\npath"})

    with pytest.raises(ValueError, match="scope"):
        ProjectInstructionSourceRef.model_validate(
            reference.model_dump(mode="python") | {"path": "other/AGENTS.md"}
        )
