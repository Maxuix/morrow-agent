"""Opt-in fixture runner for human/live-model receipts, without HTTP credentials.

Uses the same ordinary AgentLoop/ToolExecutor and strict verdict as live_provider.
The external controller must read pending.json and atomically write its own receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from live_bridge import LiveControllerProvider, atomic_json
from live_provider import run_fixture, start_fixture

from morrow.adapters.computer_use.sdk_loader import load_sdk
from morrow.core.computer_use import ComputerUseContractError


async def run(args):
    if args.controller_directory.exists() and any(args.controller_directory.iterdir()):
        raise ValueError("controller_directory_not_empty")
    controller = LiveControllerProvider(
        args.controller_directory,
        case=args.case,
        goal="Follow the current case goal in the actual Morrow messages and inspect the real images.",
        delivery=args.delivery,
    )
    process = None
    try:
        process = await start_fixture(
            args.fixture_app,
            args.fixture_state_file,
            edit_seed="CommitSeed2026" if args.case == "commit" else "x" * args.seed_length,
            foreground=args.delivery == "foreground",
        )
        with tempfile.TemporaryDirectory(prefix="morrow-live-controller-") as temporary:
            result = await run_fixture(
                args.fixture_state_file,
                load_sdk(),
                Path(temporary),
                "",
                args.case,
                args.delivery,
                args.mode,
                controller_provider=controller,
            )
        atomic_json(args.evidence_file, result)
        return 0 if result.get("status") == "passed" else 1
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
            await asyncio.to_thread(process.wait, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-desktop", action="store_true", required=True)
    parser.add_argument("--fixture-app", type=Path, required=True)
    parser.add_argument("--fixture-state-file", type=Path, required=True)
    parser.add_argument("--controller-directory", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--seed-length", type=int, choices=(0, 4097), default=0)
    parser.add_argument("--delivery", choices=("foreground", "background"), default="foreground")
    parser.add_argument("--mode", choices=("semantic", "hybrid"), default="hybrid")
    parser.add_argument(
        "--case",
        choices=(
            "observe",
            "click",
            "type_text",
            "press_key",
            "hotkey",
            "commit",
            "secure",
            "coordinate_click",
            "coordinate_scroll",
            "scroll",
            "double_click",
            "right_click",
            "postcondition_text",
            "postcondition_exists",
            "postcondition_attribute",
            "denied",
        ),
        required=True,
    )
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except KeyboardInterrupt:
        atomic_json(args.evidence_file, {"status": "blocked", "reason": "controller_cancelled"})
        code = 1
    except Exception as exc:
        atomic_json(
            args.evidence_file,
            {
                "status": "failed",
                "reason": exc.code
                if isinstance(exc, ComputerUseContractError)
                else "controller_campaign_failed",
                "exception_type": type(exc).__name__,
            },
        )
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
