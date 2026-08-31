from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from morrow.bootstrap import build_application, build_skill_services
from morrow.interfaces.cli import app

SOURCE = Path(__file__).parent / "fixtures" / "stage6" / "handwritten-skill"


@pytest.mark.parametrize("workspace", [None, "ws_install_test"])
def test_reinstall_receipt_matches_preserved_binding(tmp_path, workspace):
    runner = CliRunner()
    state_root = tmp_path / "state"
    common = ["--state-root", str(state_root)]
    if workspace:
        common += ["--workspace", workspace]

    def invoke(*args, input=None):
        result = runner.invoke(app, ["skill", *args, *common], input=input)
        assert result.exit_code == 0, result.output
        return result

    services = build_skill_services(
        build_application(state_root=state_root), workspace_id=workspace
    )
    first = invoke("install", str(SOURCE), "--yes")
    assert "Binding: disabled" in first.output
    status = services.queries.show("acceptance-skill", scope_id=workspace)
    assert status.binding is None
    first_version = status.versions[0].version_id
    invoke("enable", "acceptance-skill")
    invoke("pin", "acceptance-skill", first_version)

    second = invoke("install", str(SOURCE), input="y\n")
    assert "不改变现有 Binding" in second.output
    assert "Binding: enabled" in second.output
    assert f"pinned_version: {first_version}" in second.output
    status = services.queries.show("acceptance-skill", scope_id=workspace)
    assert status.enabled is True
    assert status.pinned_version_id == first_version
    assert len(status.versions) == 2

    invoke("disable", "acceptance-skill")
    third = invoke("install", str(SOURCE), "--yes")
    assert "Binding: disabled" in third.output
    assert f"pinned_version: {first_version}" in third.output
    status = services.queries.show("acceptance-skill", scope_id=workspace)
    assert status.enabled is False
    assert status.pinned_version_id == first_version
    assert len(status.versions) == 3


def test_install_declined_does_not_publish_package(tmp_path):
    state_root = tmp_path / "state"
    result = CliRunner().invoke(
        app, ["skill", "install", str(SOURCE), "--state-root", str(state_root)], input="n\n"
    )

    assert result.exit_code == 2
    assert "不改变现有 Binding" in result.output
    services = build_skill_services(build_application(state_root=state_root))
    assert not services.queries.list()
