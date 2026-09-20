"""Build distributable GUI assets, then the sdist and wheel from a clean checkout."""

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use only cached build dependencies")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    offline = ["--offline"] if args.offline else []
    subprocess.run(
        ["pnpm", "--dir", "gui", "install", "--frozen-lockfile", *offline], cwd=root, check=True
    )
    subprocess.run(["pnpm", "--dir", "gui", "build"], cwd=root, check=True)
    subprocess.run(["uv", "build", *offline], cwd=root, check=True)


if __name__ == "__main__":
    main()
