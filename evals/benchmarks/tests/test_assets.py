"""The build manifest binds the exact packaged source to the current tree."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from harness.assets import build_manifest, verify_bundle, wheel_source_mismatches
from harness.fingerprint import write_json


class AssetTests(unittest.TestCase):
    def test_product_commit_ignores_later_documentation_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            source = repo / "src" / "morrow"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("version = 1\n")
            subprocess.run(["git", "-C", str(repo), "add", "src/morrow"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "source",
                ],
                check=True,
            )
            assets = repo / "assets"
            assets.mkdir()
            (assets / "wheelhouse").mkdir()
            (assets / "cpython-3.12-x86_64-unknown-linux-gnu-install_only.tar.gz").touch()
            wheel = assets / "morrow_agent-0.1.0-py3-none-any.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("morrow/__init__.py", "version = 1\n")
            first = build_manifest(repo, assets, wheel)["source_commit"]
            (repo / "README.md").write_text("documentation\n")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "docs",
                ],
                check=True,
            )
            self.assertEqual(build_manifest(repo, assets, wheel)["source_commit"], first)

    def test_wheel_compares_resources_and_rejects_missing_or_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "morrow" / "resources"
            source.mkdir(parents=True)
            resources = {
                "runtime-policy.toml": b"tool_timeout_seconds = 120\n",
                "stage5-preference-v2-evaluation.json": b'{"version": 2}\n',
            }
            for name, content in resources.items():
                (source / name).write_bytes(content)
            cache = source.parent / "__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"local bytecode")
            wheel = root / "candidate.whl"
            with ZipFile(wheel, "w") as archive:
                for name, content in resources.items():
                    archive.writestr(f"morrow/resources/{name}", content)
                archive.writestr("morrow/__pycache__/module.pyc", b"different bytecode")
            self.assertEqual(wheel_source_mismatches(wheel, root / "src"), [])
            (source / "additional.bin").write_bytes(b"new packaged resource")
            self.assertEqual(
                wheel_source_mismatches(wheel, root / "src"),
                ["morrow/resources/additional.bin"],
            )
            (source / "additional.bin").unlink()
            (source / "runtime-policy.toml").write_bytes(b"tool_timeout_seconds = 180\n")
            self.assertEqual(
                wheel_source_mismatches(wheel, root / "src"),
                ["morrow/resources/runtime-policy.toml"],
            )
            (source / "runtime-policy.toml").write_bytes(resources["runtime-policy.toml"])
            (source / "stage5-preference-v2-evaluation.json").unlink()
            self.assertEqual(
                wheel_source_mismatches(wheel, root / "src"),
                ["morrow/resources/stage5-preference-v2-evaluation.json"],
            )
            (source / "stage5-preference-v2-evaluation.json").write_bytes(b'{"version": 3}\n')
            self.assertEqual(
                wheel_source_mismatches(wheel, root / "src"),
                ["morrow/resources/stage5-preference-v2-evaluation.json"],
            )

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
