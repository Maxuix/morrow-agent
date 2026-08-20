"""Exact-target Artifact orphan cleanup; business rows are never rewritten."""

from __future__ import annotations

from morrow.application.artifacts import ArtifactService
from morrow.application.cleanup_fs import (
    CleanupCandidate,
    QuarantineAttempt,
    QuarantinedTarget,
    TrustedArtifactLayout,
    UnsafeArtifactLayout,
)
from morrow.core.artifacts import ArtifactMetadata
from morrow.core.cleanup import OrphanCleanupReport
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode


class ArtifactCleanupService:
    def __init__(self, artifacts: ArtifactService) -> None:
        self.artifacts = artifacts

    def run(self, *, dry_run: bool = True) -> OrphanCleanupReport:
        journal = self.artifacts.journal
        if not dry_run and journal._session.mode not in {
            StoreOpenMode.READ_WRITE,
            StoreOpenMode.CREATE,
        }:
            raise StorageError(StorageErrorCode.UNAVAILABLE, "operational store is not writable")
        if not dry_run and getattr(journal, "_executor", None) is not None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "Artifact cleanup cannot run inside another transaction",
            )

        metadata, referenced = self._global_artifact_authority()
        known = {item.artifact_id for item in metadata}
        try:
            with TrustedArtifactLayout.open(self.artifacts.filesystem) as layout:
                candidates = layout.scan(metadata, referenced)
                eligible, refused, reasons = self._classify(candidates, known, referenced)
                if dry_run:
                    return self._report(
                        dry_run=True,
                        inspected=len(candidates),
                        eligible=len(eligible),
                        removed=0,
                        quarantined=0,
                        refused=refused,
                        reasons=reasons,
                    )

                removed = 0
                quarantined = 0
                for target in eligible:
                    attempt = self._quarantine_one(layout, target)
                    if attempt.status == "authority_preserved":
                        refused += 1
                        reasons.append("global_authority_preserved")
                    elif attempt.status == "target_changed":
                        refused += 1
                        reasons.append("target_changed")
                    elif attempt.status == "target_changed_quarantined":
                        refused += 1
                        if attempt.quarantine is None:
                            reasons.append("target_changed_quarantined")
                            continue
                        restoration = self._restore_one(
                            layout,
                            attempt.quarantine,
                            require_original_inode=False,
                        )
                        if restoration.status == "authority_preserved":
                            reasons.append("global_authority_preserved")
                            reasons.append("target_changed_quarantined")
                        elif restoration.status == "restored_retained":
                            reasons.append("target_changed")
                            reasons.append("quarantine_retained")
                        else:
                            reasons.append("target_changed_quarantined")
                    elif attempt.status != "quarantined" or attempt.quarantine is None:
                        refused += 1
                        reasons.append("unsafe_target_refused")
                    else:
                        quarantined += 1
                        reasons.append("quarantine_retained")
                return self._report(
                    dry_run=False,
                    inspected=len(candidates),
                    eligible=len(eligible),
                    removed=removed,
                    quarantined=quarantined,
                    refused=refused,
                    reasons=reasons,
                )
        except UnsafeArtifactLayout as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "Artifact cleanup path chain is unsafe",
            ) from exc

    def _quarantine_one(
        self, layout: TrustedArtifactLayout, target: CleanupCandidate
    ) -> QuarantineAttempt:
        journal = self.artifacts.journal
        transact_once = getattr(journal, "transact_once", None)
        if not callable(transact_once) or not callable(
            getattr(journal, "has_global_artifact_authority", None)
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store cannot protect Artifact cleanup",
            )
        if target.artifact_id is None:
            return QuarantineAttempt("unsafe_target")
        staged: list[QuarantinedTarget] = []

        def work(transactional_journal) -> QuarantineAttempt:
            if transactional_journal.has_global_artifact_authority(target.artifact_id):
                return QuarantineAttempt("authority_preserved")
            return layout.quarantine(target, on_moved=staged.append)

        try:
            return transact_once(work)
        except BaseException:
            for quarantine in staged:
                try:
                    self._restore_one(
                        layout,
                        quarantine,
                        require_original_inode=True,
                    )
                except BaseException:
                    # The original failure remains authoritative. Quarantine is
                    # deliberately retained when recovery is itself uncertain.
                    pass
            raise

    def _restore_one(
        self,
        layout: TrustedArtifactLayout,
        quarantine: QuarantinedTarget,
        *,
        require_original_inode: bool,
    ) -> QuarantineAttempt:
        journal = self.artifacts.journal
        transact_once = getattr(journal, "transact_once", None)
        if not callable(transact_once) or not callable(
            getattr(journal, "has_global_artifact_authority", None)
        ):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store cannot protect Artifact cleanup recovery",
            )
        artifact_id = quarantine.target.artifact_id
        if artifact_id is None:
            return QuarantineAttempt("quarantine_preserved", quarantine)

        def work(transactional_journal) -> QuarantineAttempt:
            if transactional_journal.has_global_artifact_authority(artifact_id):
                return QuarantineAttempt("authority_preserved", quarantine)
            if layout.restore_quarantine(
                quarantine,
                require_original_inode=require_original_inode,
            ):
                return QuarantineAttempt("restored_retained", quarantine)
            return QuarantineAttempt("quarantine_preserved", quarantine)

        return transact_once(work)

    def _global_artifact_authority(
        self,
    ) -> tuple[tuple[ArtifactMetadata, ...], frozenset[str]]:
        journal = self.artifacts.journal
        list_workspace_ids = getattr(journal, "list_workspace_ids", None)
        if not callable(list_workspace_ids):
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "operational store cannot establish global Artifact authority",
            )
        metadata: list[ArtifactMetadata] = []
        referenced: set[str] = set()
        for workspace_id in list_workspace_ids():
            metadata.extend(journal.list_artifacts(workspace_id))
            referenced.update(item[0] for item in journal.list_artifact_references(workspace_id))
        return tuple(metadata), frozenset(referenced)

    @staticmethod
    def _classify(
        candidates: tuple[CleanupCandidate, ...],
        known: set[str],
        referenced: frozenset[str],
    ) -> tuple[list[CleanupCandidate], int, list[str]]:
        eligible: list[CleanupCandidate] = []
        refused = 0
        reasons: list[str] = []
        for candidate in candidates:
            if candidate.artifact_id is None:
                refused += 1
                reasons.append("unsafe_target_refused")
            elif candidate.artifact_id in known:
                refused += 1
                reasons.append("managed_metadata_preserved")
            elif candidate.artifact_id in referenced:
                refused += 1
                reasons.append("managed_reference_preserved")
            elif not candidate.is_private_regular_file:
                refused += 1
                reasons.append("unsafe_target_refused")
            else:
                eligible.append(candidate)
        return eligible, refused, reasons

    @staticmethod
    def _report(
        *,
        dry_run: bool,
        inspected: int,
        eligible: int,
        removed: int,
        quarantined: int,
        refused: int,
        reasons: list[str],
    ) -> OrphanCleanupReport:
        return OrphanCleanupReport(
            dry_run=dry_run,
            inspected=inspected,
            eligible=eligible,
            removed=removed,
            quarantined=quarantined,
            refused=refused,
            reasons=tuple(sorted(set(reasons))),
        )
