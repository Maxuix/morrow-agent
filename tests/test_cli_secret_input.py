from __future__ import annotations

import getpass
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from morrow.adapters.credentials.keyring import CredentialAccessError
from morrow.interfaces import cli


def _unexpected_prompt(*_args, **_kwargs):
    pytest.fail("credential prompt must not be reached")


@pytest.mark.parametrize("stdin", [None, SimpleNamespace(isatty=lambda: False)])
def test_secret_rejects_noninteractive_input(monkeypatch, stdin):
    monkeypatch.setattr(cli, "environment_credential", lambda _provider: None)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(getpass, "getpass", _unexpected_prompt)

    with pytest.raises(CredentialAccessError, match="交互终端") as error:
        cli._secret()

    assert error.value.code == "secure_input_unavailable"


@pytest.mark.parametrize("provider_id", ["opencode-go", "custom-provider"])
def test_secret_environment_credential_does_not_require_terminal(monkeypatch, provider_id):
    key = f"MORROW_{provider_id.upper().replace('-', '_')}_API_KEY"
    monkeypatch.setenv(key, "synthetic-test-credential")
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(getpass, "getpass", _unexpected_prompt)

    assert cli._secret(provider_id) == "synthetic-test-credential"


def test_secret_accepts_secure_interactive_input(monkeypatch):
    monkeypatch.setattr(cli, "environment_credential", lambda _provider: None)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "synthetic-test-credential")

    assert cli._secret() == "synthetic-test-credential"


def test_secret_blocks_getpass_echo_fallback_before_reading(monkeypatch, capsys):
    monkeypatch.setattr(cli, "environment_credential", lambda _provider: None)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(getpass, "getpass", getpass.fallback_getpass)
    monkeypatch.setattr(getpass, "_raw_input", _unexpected_prompt)

    with pytest.raises(CredentialAccessError, match="安全") as error:
        cli._secret()

    assert error.value.code == "secure_input_unavailable"
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("failure", [EOFError, OSError])
def test_secret_bounds_terminal_failures(monkeypatch, failure):
    monkeypatch.setattr(cli, "environment_credential", lambda _provider: None)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def failed_prompt(_prompt):
        raise failure("sensitive backend details")

    monkeypatch.setattr(getpass, "getpass", failed_prompt)
    with pytest.raises(CredentialAccessError) as error:
        cli._secret()

    assert "sensitive backend details" not in str(error.value)


def test_provider_add_noninteractive_exits_without_prompt_or_write(monkeypatch):
    monkeypatch.setattr(cli, "environment_credential", lambda _provider: None)
    monkeypatch.setattr(getpass, "getpass", _unexpected_prompt)
    monkeypatch.setattr(
        cli,
        "build_application",
        lambda **_kwargs: SimpleNamespace(provider_service=SimpleNamespace(add=_unexpected_prompt)),
    )

    result = CliRunner().invoke(cli.app, ["provider", "add", "--preset", "opencode-go"])

    assert result.exit_code == 2, result.output
    assert "交互终端" in result.output
    assert "API Key（输入不回显）" not in result.output
    assert "GetPassWarning" not in result.output
