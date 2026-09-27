"""The build manifest binds the exact packaged source to the current tree."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from harness.assets import build_manifest, verify_bundle, wheel_source_mismatches
from harness.fingerprint import write_json


class AssetTests(unittest.TestCase):
    def test_stale_wheel_is_rejected_even_with_same_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            source = repo / "src" / "morrow"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("version = 1\n")
            (repo / "uv.lock").write_text("lock")
            assets = repo / "assets"
            assets.mkdir()
            (assets / "wheelhouse").mkdir()
            (assets / "cpython-3.12-x86_64-unknown-linux-gnu-install_only.tar.gz").touch()
            wheel = assets / "morrow_agent-0.1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("morrow/__init__.py", "version = 1\n")
            write_json(assets / "asset-manifest.json", build_manifest(repo, assets, wheel))
            verify_bundle(repo, assets)
            (source / "__init__.py").write_text("version = 2\n")
            self.assertEqual(wheel_source_mismatches(wheel, repo / "src"), ["morrow/__init__.py"])
            with self.assertRaisesRegex(ValueError, "wheel differs from source"):
                verify_bundle(repo, assets)


if __name__ == "__main__":
    unittest.main()
