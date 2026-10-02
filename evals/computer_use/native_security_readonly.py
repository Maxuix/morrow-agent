"""Opt-in exact-token security experiment using a hash-bound SDK prototype.

No input, dependency installation or production gate change. Unknown remains
unknown. This is not native action admission or full security acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import runpy
import sys
from pathlib import Path

from morrow.core.computer_use import ComputerUseContractError

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"
REASONS = {
    None,
    "invalid_arguments",
    "token_unconfirmed",
    "target_unconfirmed",
    "query_failed",
    "ancestry_unconfirmed",
    "subrole_unreadable",
    "unsupported_role",
    "unsupported_subrole",
}


def security_response(content: str | None) -> dict:
    try:
        if not isinstance(content, str) or len(content) > 4096:
            raise ValueError
        value = json.loads(content)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "classification", "binding_verified", "reason"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["classification"] not in {"non_sensitive", "sensitive", "unknown"}
            or type(value["binding_verified"]) is not bool
            or value["reason"] not in REASONS
            or (
                value["classification"] != "unknown"
                and (value["binding_verified"] is not True or value["reason"] is not None)
            )
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise ComputerUseContractError("prototype_query_invalid") from None
    return value


def prototype_module(root: Path, expected_sha256: str):
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ComputerUseContractError("prototype_hash_invalid")
    library = root / "cua_driver/libcua_driver_sdk.dylib"
    try:
        actual = hashlib.sha256(library.read_bytes()).hexdigest()
    except OSError:
        raise ComputerUseContractError("prototype_library_missing") from None
    if actual != expected_sha256:
        raise ComputerUseContractError("prototype_hash_mismatch")
    sys.path.insert(0, str(root.resolve()))
    sdk = importlib.import_module("cua_driver")
    if Path(sdk.__file__).resolve() != (root / "cua_driver/__init__.py").resolve():
        raise ComputerUseContractError("prototype_module_mismatch")
    return sdk


async def inspect_security(path: Path, *, sdk, prototype_sha256: str) -> dict:
    readonly = runpy.run_path(str(Path(__file__).with_name("native_readonly.py")))
    counter = runpy.run_path(str(Path(__file__).with_name("native_counter.py")))
    before = counter["counter_oracle"](path)
    queries = []
    diagnostic = {"query_entries": 0}

    class Query(readonly["_DiagnosedSession"]):
        async def get_window_state(self, request):
            state = await super().get_window_state(request)
            for field in state.elements or ():
                if field.role.lower().replace("_", "") not in {"axtextfield", "axtextarea"}:
                    continue
                diagnostic["query_entries"] += 1
                result = await self._native.call_tool(
                    "get_element_security",
                    json.dumps(
                        {
                            "pid": request.pid,
                            "window_id": request.window_id,
                            "element_token": field.element_token,
                            "session": request.session,
                        }
                    ),
                )
                if result.is_error:
                    raise ComputerUseContractError("prototype_query_refused")
                queries.append(security_response(result.structured_json))
            return state

    inspect = readonly["inspect_fixture"]
    inspect.__globals__["_DiagnosedSession"] = Query
    inspect.__globals__["load_sdk"] = lambda: sdk
    result = await inspect(fixture_window=(before["pid"], before["window_id"]))
    after = counter["counter_oracle"](path)
    counter["validate_counter_identity"](before, after, unchanged=True)
    result.update(
        prototype=True,
        prototype_dylib_sha256=prototype_sha256,
        security_queries=queries,
        query_diagnostic=diagnostic,
        original_probe_reason=result.get("reason"),
        fixture_state_unchanged=before["sha256"] == after["sha256"],
    )
    passed = (
        result["status"] == "passed"
        and len(queries) == 2
        and all(item["binding_verified"] for item in queries)
        and sorted(item["classification"] for item in queries) == ["non_sensitive", "sensitive"]
        and result["fixture_state_unchanged"]
    )
    result["security_classification_passed"] = passed
    if not passed:
        result.update(status="failed", reason="prototype_security_unconfirmed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--fixture-bundle-id", choices=(FIXTURE_BUNDLE_ID,), required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--prototype-package-directory", type=Path, required=True)
    parser.add_argument("--prototype-dylib-sha256", required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        sdk = prototype_module(args.prototype_package_directory, args.prototype_dylib_sha256)
        result = asyncio.run(
            inspect_security(
                args.fixture_state_file, sdk=sdk, prototype_sha256=args.prototype_dylib_sha256
            )
        )
    except ComputerUseContractError as exc:
        result = {"status": "failed", "prototype": True, "reason": exc.code}
    except Exception as exc:
        result = {
            "status": "failed",
            "prototype": True,
            "reason": "prototype_query_failed",
            "exception_type": type(exc).__name__,
        }
    try:
        args.evidence_file.write_text(json.dumps(result, sort_keys=True) + "\n")
    except OSError:
        result = {"status": "failed", "prototype": True, "reason": "evidence_write_failed"}
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
