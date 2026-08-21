from typer.testing import CliRunner

from morrow.interfaces import cli as cli_module


def test_preference_inbox_typer_surface_is_separate_from_learning_inbox():
    result = CliRunner().invoke(cli_module.app, ["preferences", "inbox", "--help"])

    assert result.exit_code == 0
    assert "accept-many" in result.stdout
    assert "edit-and-accept" in result.stdout
    assert "learning" not in result.stdout
