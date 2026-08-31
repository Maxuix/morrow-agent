"""Current Operational Store backup facade."""

from __future__ import annotations

from pathlib import Path

from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.backup_service import (
    BackupError,
    BackupService,
    DefinitionSourceBackupError,
)
from morrow.core.backup import BackupBundleReport, BackupRestoreReport, BackupVerificationReport


class BackupBundleError(RuntimeError):
    """A backup target or bundle is invalid without exposing host details."""


class OperationalBackupService:
    """Create, verify, and restore only the current complete bundle format."""

    def __init__(
        self, store: OperationalStore, *, journal: SqliteOperationalJournal | None = None
    ) -> None:
        del journal
        self.store = store
        self.backend = BackupService(store)

    def create(self, bundle_name: str | None = None) -> BackupBundleReport:
        name = bundle_name or f"operational-{int(self.store.clock.now().timestamp())}"
        try:
            bundle, manifest, manifest_digest = self.backend.create(name)
        except DefinitionSourceBackupError as exc:
            raise BackupBundleError(
                f"{exc.filename} contains detected secret material; remove the value before backup"
            ) from None
        except BackupError as exc:
            raise BackupBundleError("backup bundle could not be completed") from exc
        return BackupBundleReport(
            bundle_name=bundle.name,
            schema_version=manifest.schema_version,
            integrity_ok=True,
            manifest_sha256=manifest_digest,
            artifacts=manifest.artifacts,
            skill_versions=manifest.skill_versions,
        )

    def verify(self, bundle: Path) -> BackupVerificationReport:
        return self.backend.verify(bundle)

    def restore(self, bundle: Path, target_root: Path) -> BackupRestoreReport:
        return self.backend.restore(bundle, target_root)


__all__ = ["BackupBundleError", "OperationalBackupService"]
