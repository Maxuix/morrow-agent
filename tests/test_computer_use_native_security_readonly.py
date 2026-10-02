"""Closed prototype evidence parsers; no native SDK imports or input."""

import json
import runpy

import pytest

from morrow.core.computer_use import ComputerUseContractError


@pytest.fixture
def module():
    return runpy.run_path("evals/computer_use/native_security_readonly.py")


def test_unknown_exact_binding_is_preserved(module):
    value = {
        "schema_version": 1,
        "classification": "unknown",
        "binding_verified": True,
        "reason": "unsupported_subrole",
    }
    assert module["security_response"](json.dumps(value)) == value


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"binding_verified": 1},
        {"classification": "safe"},
        {"classification": "non_sensitive", "binding_verified": False},
        {"classification": "sensitive", "reason": "unsupported_subrole"},
        {"reason": "private native error"},
        {"native_token": "private"},
        {"reason": ["unknown"]},
    ],
)
def test_rejects_open_or_inconsistent_security_response(module, changes):
    value = {
        "schema_version": 1,
        "classification": "unknown",
        "binding_verified": False,
        "reason": "target_unconfirmed",
    }
    value.update(changes)
    with pytest.raises(ComputerUseContractError, match="prototype_query_invalid"):
        module["security_response"](json.dumps(value))


def test_hash_mismatch_rejects_before_import(module, tmp_path):
    package = tmp_path / "cua_driver"
    package.mkdir()
    (package / "libcua_driver_sdk.dylib").write_bytes(b"invalid prototype")
    with pytest.raises(ComputerUseContractError, match="prototype_hash_mismatch"):
        module["prototype_module"](tmp_path, "0" * 64)
    with pytest.raises(ComputerUseContractError, match="prototype_hash_invalid"):
        module["prototype_module"](tmp_path, "invalid")
