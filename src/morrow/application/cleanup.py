"""Exact-target Artifact orphan cleanup; business rows are never rewritten."""

from __future__ import annotations

import stat

from morrow.application.artifacts import ArtifactService
from morrow.core.cleanup import OrphanCleanupReport
from morrow.core.store import FILE_MODE, StorageError, StorageErrorCode, StoreOpenMode


class ArtifactCleanupService:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    def run(self, *, dry_run: bool = True) -> OrphanCleanupReport:
        if not dry_run and self.artifacts.journal._session.mode not in {
            StoreOpenMode.READ_WRITE,
            StoreOpenMode.CREATE,
        }:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational store is not writable")
        metadata = self.artifacts.journal.list_artifacts(self.artifacts.workspace_id)
        known = {item.artifact_id for item in metadata}
        report = self.artifacts.orphan_report()
        eligible = []
        refused = 0
        reasons: list[str] = []
        root = self.artifacts.filesystem.root.resolve()
        allowed_parents = {
            self.artifacts.filesystem.artifacts_dir.absolute(),
            self.artifacts.filesystem.artifacts_tmp.absolute(),
        }
        for candidate in report.candidates:
            path = candidate.path
            try:
                path.relative_to(root)
                if path.parent.absolute() not in allowed_parents or path.parent.is_symlink():
                    raise ValueError
                if path.is_symlink() or not path.is_file():
                    raise ValueError
                if candidate.artifact_id is None:
                    raise ValueError
                info = path.stat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != FILE_MODE
                ):
                    raise ValueError
                if not (path.name.endswith(".artifact") or path.name.endswith(".artifact.tmp")):
                    raise ValueError
                # A managed metadata row is business state, even when its bytes are
                # unreferenced. Only files with no metadata row can be removed.
                if candidate.artifact_id is not None and candidate.artifact_id in known:
                    refused += 1
                    reasons.append("managed_metadata_preserved")
                    continue
                eligible.append(path)
            except (OSError, ValueError):
                refused += 1
                reasons.append("unsafe_target_refused")
        removed = 0
        if not dry_run:
            for path in eligible:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    refused += 1
                    reasons.append("target_changed")
        return OrphanCleanupReport(
            dry_run=dry_run,
            inspected=len(report.candidates),
            eligible=len(eligible),
            removed=removed,
            refused=refused,
            reasons=tuple(sorted(set(reasons))),
        )
