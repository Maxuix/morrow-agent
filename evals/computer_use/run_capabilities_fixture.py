"""Repeat the five functional gates with scripted decisions and real native effects.

This uses ordinary AgentLoop and approval. It is a fixture regression, never a
Luna/API-model acceptance claim. Every case owns a fresh synthetic application.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from live_bridge import atomic_json
from live_provider import run_fixture, start_fixture

from morrow.adapters.computer_use.action_inputs import FUNCTIONAL_SDK_VERSION
from morrow.adapters.computer_use.sdk_loader import load_sdk
from morrow.core.computer_use import ComputerUseContractError
from morrow.core.models import AssistantMessage, FunctionToolCall, ModelEvent, ModelFinishReason

CASES = (
    ("enabled-foreground", "postcondition_attribute", "foreground", "hybrid"),
    ("enabled-background", "postcondition_attribute", "background", "hybrid"),
    ("wheel-foreground", "coordinate_scroll", "foreground", "hybrid"),
    ("double-background", "double_click", "background", "hybrid"),
    ("scroll-semantic", "scroll", "background", "semantic"),
)


class FixtureController:
    scripted_provider = True
    turn_budget = 6

    def __init__(self, case: str, mode: str):
        self.case, self.mode = case, mode
        self.turn = 0

    async def stream(self, model, messages, tools=(), *, generation=None):
        self.turn += 1
        tool_messages = [m for m in messages if m.role == "tool"]
        result = {}
        if tool_messages:
            envelope = json.JSONDecoder().raw_decode(tool_messages[-1].content)[0]
            if envelope.get("ok") is not True:
                raise ComputerUseContractError("fixture_public_tool_failed")
            result = envelope["result"]
        name = "computer_observe"
        if self.turn == 1:
            arguments = {"operation": "discover"}
        elif self.turn == 2:
            targets = result.get("targets", [])
            if len(targets) != 1:
                raise ComputerUseContractError("fixture_public_target_missing")
            arguments = {
                "operation": "window",
                "target_ref": targets[0]["target_ref"],
                "include_image": self.mode == "hybrid",
            }
        elif self.turn == 3:
            name = "computer_action"
            if self.case == "coordinate_scroll":
                # Fixture rows occupy the lower middle of its controlled image.
                # The independent oracle must still prove the wheel hit that region.
                image = result["image"]
                action = {
                    "type": "scroll",
                    "x": image["width"] // 2,
                    "y": image["height"] * 3 // 4,
                    "direction": "down",
                    "amount": 3,
                }
            else:
                role, label = (
                    ("axscrollarea", "fixture-scroll")
                    if self.case == "scroll"
                    else ("axbutton", "Increment")
                )
                nodes = [e for e in result["elements"] if e["role"] == role and e["label"] == label]
                if len(nodes) != 1:
                    raise ComputerUseContractError("fixture_public_element_missing")
                ref = nodes[0]["element_ref"]
                action = {
                    "type": "scroll" if self.case == "scroll" else "click",
                    "element_ref": ref,
                }
                if self.case == "scroll":
                    action.update(direction="down", amount=3)
                elif self.case == "double_click":
                    action.update(button="left", count=2)
                else:
                    action["postcondition"] = {
                        "type": "attribute_equals",
                        "element_ref": ref,
                        "attribute": "enabled",
                        "value": "true",
                    }
            arguments = {"observation_id": result["observation_id"], "action": action}
        else:
            yield ModelEvent(
                kind="completed",
                message=AssistantMessage(content="Fixture action ended. No native action retry."),
                finish_reason=ModelFinishReason.STOP,
            )
            return
        yield ModelEvent(
            kind="completed",
            message=AssistantMessage(
                tool_calls=(
                    FunctionToolCall(
                        id=f"fixture-{self.turn}", name=name, arguments=json.dumps(arguments)
                    ),
                )
            ),
            finish_reason=ModelFinishReason.TOOL_CALLS,
        )


async def run(args):
    sdk = load_sdk()
    if sdk.__version__ != FUNCTIONAL_SDK_VERSION:
        raise ComputerUseContractError("fixture_functional_sdk_required")
    package = Path(sdk.__file__).parent
    campaign = {
        "schema_version": 1,
        "scripted_provider": True,
        "sdk_version": sdk.__version__,
        "sdk_dylib_sha256": hashlib.sha256(
            (package / "libcua_driver_sdk.dylib").read_bytes()
        ).hexdigest(),
        "cases": [],
    }
    for name, case, delivery, mode in CASES:
        if args.gate is None:
            # A closed SDK runtime cannot be re-created reliably in the same
            # interpreter. Each case owns both a Python host and a fixture PID.
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--allow-desktop",
                "--fixture-app",
                str(args.fixture_app),
                "--fixture-state-file",
                str(args.fixture_state_file),
                "--output-directory",
                str(args.output_directory),
                "--gate",
                name,
            ]
            child = await asyncio.create_subprocess_exec(*command)
            await child.wait()
            result = json.loads((args.output_directory / f"{name}.json").read_text())
            campaign["cases"].append(result)
            continue
        if args.gate != name:
            continue
        process = await start_fixture(
            args.fixture_app, args.fixture_state_file, foreground=delivery == "foreground"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="morrow-five-regression-") as directory:
                result = await run_fixture(
                    args.fixture_state_file,
                    sdk,
                    Path(directory),
                    "",
                    case,
                    delivery,
                    mode,
                    controller_provider=FixtureController(case, mode),
                )
            result["gate"] = name
            campaign["cases"].append(result)
            atomic_json(args.output_directory / f"{name}.json", result)
            print(name, result["status"], flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
            await asyncio.to_thread(process.wait, timeout=10)
    campaign["status"] = (
        "passed" if all(c["status"] == "passed" for c in campaign["cases"]) else "failed"
    )
    atomic_json(args.output_directory / "campaign.json", campaign)
    return 0 if campaign["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--fixture-app", type=Path, required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--gate", choices=tuple(c[0] for c in CASES))
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except Exception as exc:
        atomic_json(
            args.output_directory / (f"{args.gate}.json" if args.gate else "campaign.json"),
            {
                "status": "failed",
                "scripted_provider": True,
                "reason": exc.code
                if isinstance(exc, ComputerUseContractError)
                else "fixture_campaign_failed",
                "exception_type": type(exc).__name__,
            },
        )
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
