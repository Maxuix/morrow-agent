"""Build the local fixture. This never launches an application or calls a SDK."""

from __future__ import annotations

import argparse
import plistlib
import subprocess
from pathlib import Path

FIXTURE_BUNDLE_ID = "com.morrow.ComputerUseFixture"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    app = args.output_directory.resolve() / "MorrowComputerUseFixture.app"
    contents = app / "Contents"
    binary = contents / "MacOS" / "MorrowComputerUseFixture"
    binary.parent.mkdir(parents=True, exist_ok=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": FIXTURE_BUNDLE_ID,
                "CFBundleExecutable": binary.name,
                "CFBundleName": "MorrowComputerUseFixture",
                "CFBundlePackageType": "APPL",
                "LSMinimumSystemVersion": "14.0",
                "NSHighResolutionCapable": True,
            }
        )
    )
    subprocess.run(
        [
            "swiftc",
            "-parse-as-library",
            "-framework",
            "SwiftUI",
            str(Path(__file__).with_name("Fixture.swift")),
            "-o",
            str(binary),
        ],
        check=True,
    )
    print(app)


if __name__ == "__main__":
    main()
