"""Check public local links and archived evidence hashes in an exported repository.

Run against a git-archive export to avoid relying on ignored local evidence.
This reads files only; it neither runs historical scripts nor repeats native/API inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def verify(root: Path) -> tuple[int, int]:
    root = root.resolve()
    portable = root / "docs/acceptance/portable"
    manifest = json.loads((portable / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest["entries"]
    for entry in entries:
        path = (root / entry["archive_path"]).resolve()
        if not path.is_relative_to(portable):
            raise ValueError("archive path is outside portable evidence")
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"evidence digest mismatch: {entry['archive_path']}")

    # The retained sidecar's original source names must resolve to exact raw bytes.
    sidecar = root / "docs/acceptance/evidence/computer-use-recovery-verdict-2026-10-05"
    recomputed = json.loads((sidecar / "recomputed.json").read_text(encoding="utf-8"))
    by_source = {entry["source"]: entry for entry in entries}
    for case in recomputed["cases"]:
        archived = by_source.get(case["source"])
        if archived is None or archived["sha256"] != case["source_sha256"]:
            raise ValueError(f"unresolved sidecar source: {case['source']}")
    if recomputed["original_summary_sha256"] not in {entry["sha256"] for entry in entries}:
        raise ValueError("original recovery summary is absent from archive")

    validation = root / "docs/acceptance/evidence/version-closeout-2026-10-06"
    checks = json.loads((validation / "manifest.json").read_text(encoding="utf-8"))
    for entry in checks["entries"]:
        path = (validation / entry["path"]).resolve()
        if not path.is_relative_to(validation):
            raise ValueError("validation log path is outside its archive")
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"validation log digest mismatch: {entry['path']}")

    public = [root / "README.md", root / "README.zh-CN.md", root / "docs/README.md"]
    public.append(root / "docs/ARCHITECTURE.md")
    public.extend((root / "docs/architecture").glob("*.md"))
    public.extend((root / "docs/acceptance").glob("*.md"))
    public.extend((root / "docs/research").glob("*.md"))
    public.extend((portable / "views").rglob("*.md"))
    public.append(portable / "README.md")
    public.extend((root / "evals").glob("*/README.md"))
    links = 0
    for file in public:
        for target in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
            target = target.strip().removeprefix("<").removesuffix(">")
            url = urlsplit(target)
            if url.scheme or url.netloc or target.startswith("#"):
                continue
            if target.startswith("/"):
                raise ValueError(f"absolute navigation: {file.relative_to(root)}")
            path = (file.parent / unquote(url.path)).resolve()
            if not path.is_relative_to(root) or not path.exists():
                raise ValueError(f"missing local link: {file.relative_to(root)} -> {target}")
            links += 1
    return len(entries), links


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Exported repository root")
    args = parser.parse_args()
    raw, links = verify(args.root)
    print(f"{raw} raw hashes and {links} public local links verified")


if __name__ == "__main__":
    main()
