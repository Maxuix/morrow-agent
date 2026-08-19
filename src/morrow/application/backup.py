"""Online Operational Store backup with managed Artifact bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore, restrict_path
from morrow.core.artifacts import ArtifactIntegrityError, ArtifactState
from morrow.core.backup import (
    ArtifactBackupEntry,
    ArtifactBackupStatus,
    BackupBundleReport,
    BackupManifest,
    BackupVerificationReport,
)
from morrow.core.models import utc_now
from morrow.core.store import DIRECTORY_MODE, FILE_MODE, StorageError, StoreOpenMode

_BUNDLE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BackupBundleError(RuntimeError):
    """A backup target or bundle is invalid without exposing host details."""


class OperationalBackupService:
    def __init__(
        self, store: OperationalStore, *, journal: SqliteOperationalJournal | None = None
    ) -> None:
        self.store = store
        self.journal = journal

    def create(self, bundle_name: str | None = None) -> BackupBundleReport:
        name = bundle_name or f"operational-{int(self.store.clock.now().timestamp())}"
        self._validate_name(name)
        bundle = self.store.layout.backups_dir / f"{name}.bundle"
        if bundle.exists():
            raise BackupBundleError("backup bundle already exists")
        try:
            bundle.mkdir(parents=True, mode=DIRECTORY_MODE)
            restrict_path(bundle, DIRECTORY_MODE)
            artifact_dir = bundle / "artifacts"
            artifact_dir.mkdir(mode=DIRECTORY_MODE)
            restrict_path(artifact_dir, DIRECTORY_MODE)
        except OSError as exc:
            raise BackupBundleError("backup bundle could not be created") from exc

        temporary_database = None
        try:
            backup = self.store.backup(f"{name}.sqlite")
            temporary_database = self.store.layout.backups_dir / backup.destination_name
            database = bundle / "database.sqlite"
            shutil.move(temporary_database, database)
            restrict_path(database, FILE_MODE)
            entries, workspace_ids, schema_version = self._copy_artifacts(artifact_dir)
            manifest = BackupManifest(
                schema_version=schema_version,
                workspace_ids=workspace_ids,
                artifacts=entries,
                created_at=utc_now(),
            )
            manifest_payload = manifest.model_dump(mode="json")
            manifest_bytes = json.dumps(
                manifest_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            manifest_path = bundle / "manifest.json"
            temporary_manifest = bundle / "manifest.json.tmp"
            temporary_manifest.write_bytes(manifest_bytes)
            restrict_path(temporary_manifest, FILE_MODE)
            os.replace(temporary_manifest, manifest_path)
            manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
            manifest_digest = bundle / "manifest.sha256"
            temporary_digest = bundle / "manifest.sha256.tmp"
            temporary_digest.write_text(f"{manifest_hash}\n", encoding="ascii")
            restrict_path(temporary_digest, FILE_MODE)
            os.replace(temporary_digest, manifest_digest)
            verified = self.verify(bundle)
            if not verified.ok:
                raise BackupBundleError(
                    "backup bundle failed verification: " + ", ".join(verified.issues)
                )
            return BackupBundleReport(
                bundle_name=bundle.name,
                database_name="database.sqlite",
                manifest_name="manifest.json",
                schema_version=schema_version,
                integrity_ok=verified.ok,
                manifest_sha256=manifest_hash,
                artifacts=entries,
                credentials_excluded=verified.credentials_excluded,
            )
        except (BackupBundleError, StorageError):
            self._remove_bundle(bundle)
            if temporary_database is not None and temporary_database.exists():
                temporary_database.unlink(missing_ok=True)
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._remove_bundle(bundle)
            if temporary_database is not None and temporary_database.exists():
                temporary_database.unlink(missing_ok=True)
            raise BackupBundleError("backup bundle could not be completed") from exc

    def verify(self, bundle: Path) -> BackupVerificationReport:
        root = self._validate_bundle_path(bundle)
        database = root / "database.sqlite"
        manifest_path = root / "manifest.json"
        manifest_digest_path = root / "manifest.sha256"
        artifact_dir = root / "artifacts"
        issues: list[str] = []
        database_ok = False
        foreign_keys_ok = False
        manifest_ok = False
        artifacts_ok = False
        manifest = None
        if not database.is_file() or database.is_symlink():
            issues.append("database_missing")
        else:
            connection = None
            try:
                connection = sqlite3.connect(database, uri=False, isolation_level=None)
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
                database_ok = bool(integrity and integrity[0] == "ok")
                foreign_keys_ok = not foreign
                if not database_ok:
                    issues.append("database_integrity")
                if not foreign_keys_ok:
                    issues.append("foreign_keys")
            except sqlite3.Error:
                issues.append("database_unreadable")
            finally:
                if connection is not None:
                    connection.close()
        if not manifest_path.is_file() or manifest_path.is_symlink():
            issues.append("manifest_missing")
        else:
            try:
                if manifest_path.stat().st_size > 32 * 1024:
                    raise ValueError("manifest exceeds its budget")
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = BackupManifest.model_validate(payload)
                manifest_ok = True
            except (OSError, ValueError, json.JSONDecodeError):
                issues.append("manifest_invalid")
        if not manifest_digest_path.is_file() or manifest_digest_path.is_symlink():
            issues.append("manifest_digest_missing")
            manifest_ok = False
        else:
            try:
                expected_digest = manifest_digest_path.read_text(encoding="ascii").strip()
                actual_digest, _size = _hash_file(manifest_path)
                if expected_digest != actual_digest:
                    issues.append("manifest_changed")
                    manifest_ok = False
            except (OSError, UnicodeError):
                issues.append("manifest_digest_invalid")
                manifest_ok = False
        if manifest is not None:
            artifacts_ok = True
            if not artifact_dir.is_dir() or artifact_dir.is_symlink():
                issues.append("artifact_directory_missing")
                artifacts_ok = False
            for entry in manifest.artifacts:
                target = artifact_dir / f"{entry.artifact_id}.artifact"
                if entry.status is not ArtifactBackupStatus.COPIED:
                    issues.append(f"artifact_{entry.status.value}")
                    artifacts_ok = False
                    continue
                if not target.is_file() or target.is_symlink():
                    issues.append("artifact_missing")
                    artifacts_ok = False
                    continue
                digest, size = _hash_file(target)
                if digest != entry.sha256 or size != entry.byte_size:
                    issues.append("artifact_changed")
                    artifacts_ok = False
            if database.is_file() and not database.is_symlink():
                connection = None
                try:
                    connection = sqlite3.connect(database, isolation_level=None)
                    rows = connection.execute(
                        "SELECT artifact_id, workspace_id, state, sha256, byte_size FROM artifacts"
                    ).fetchall()
                    database_entries = {
                        (str(row[0]), str(row[1])): (str(row[2]), str(row[3]), int(row[4]))
                        for row in rows
                    }
                    manifest_entries = {
                        (entry.artifact_id, entry.workspace_id): (
                            entry.state,
                            entry.sha256,
                            entry.byte_size,
                        )
                        for entry in manifest.artifacts
                    }
                    if database_entries != manifest_entries:
                        issues.append("manifest_database_mismatch")
                        artifacts_ok = False
                except sqlite3.Error:
                    issues.append("manifest_database_unreadable")
                    artifacts_ok = False
                finally:
                    if connection is not None:
                        connection.close()
            if artifact_dir.is_dir() and not artifact_dir.is_symlink():
                expected_names = {f"{entry.artifact_id}.artifact" for entry in manifest.artifacts}
                try:
                    unexpected = [
                        child
                        for child in artifact_dir.iterdir()
                        if child.name not in expected_names
                    ]
                except OSError:
                    unexpected = [artifact_dir]
                if unexpected:
                    issues.append("artifact_unexpected")
                    artifacts_ok = False
            if not artifacts_ok:
                issues.append("artifact_restore")
        return BackupVerificationReport(
            bundle_name=root.name,
            database_integrity_ok=database_ok,
            foreign_keys_ok=foreign_keys_ok,
            manifest_ok=manifest_ok,
            artifacts_ok=artifacts_ok,
            credentials_excluded=self._credentials_excluded(root),
            issues=tuple(dict.fromkeys(issues)),
        )

    def _copy_artifacts(self, target: Path):
        handle = None
        try:
            if self.journal is not None:
                journal = self.journal
                schema_version = journal._session.schema_version
            else:
                handle = self.store.open(StoreOpenMode.DIAGNOSE)
                journal = SqliteOperationalJournal(handle)
                schema_version = handle.schema_version
            workspace_ids = journal.list_workspace_ids()
            filesystem = FilesystemArtifactStore(self.store.layout)
            entries: list[ArtifactBackupEntry] = []
            for workspace_id in workspace_ids:
                for metadata in journal.list_artifacts(workspace_id):
                    status = ArtifactBackupStatus.NOT_AVAILABLE
                    copied = False
                    verified = False
                    if metadata.state is ArtifactState.AVAILABLE:
                        try:
                            filesystem.verify(metadata)
                            source = filesystem.existing_final_path(metadata.artifact_id)
                            destination = target / metadata.filename
                            shutil.copyfile(source, destination)
                            restrict_path(destination, FILE_MODE)
                            digest, size = _hash_file(destination)
                            if digest == metadata.sha256 and size == metadata.byte_size:
                                status = ArtifactBackupStatus.COPIED
                                copied = True
                                verified = True
                            else:
                                status = ArtifactBackupStatus.CHANGED
                        except ArtifactIntegrityError as exc:
                            status = (
                                ArtifactBackupStatus.MISSING
                                if exc.code.value.endswith("missing")
                                else ArtifactBackupStatus.CORRUPT
                            )
                        except OSError:
                            status = ArtifactBackupStatus.CHANGED
                    entries.append(
                        ArtifactBackupEntry(
                            artifact_id=metadata.artifact_id,
                            workspace_id=metadata.workspace_id,
                            state=metadata.state.value,
                            sha256=metadata.sha256,
                            byte_size=metadata.byte_size,
                            status=status,
                            copied=copied,
                            verified=verified,
                        )
                    )
            return tuple(entries), workspace_ids, schema_version
        finally:
            if handle is not None:
                handle.close()

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _BUNDLE_NAME.fullmatch(name):
            raise BackupBundleError("backup bundle name is invalid")

    def _validate_bundle_path(self, bundle: Path) -> Path:
        root = bundle.expanduser().absolute()
        base = self.store.layout.backups_dir.absolute()
        try:
            root.relative_to(base)
        except ValueError as exc:
            raise BackupBundleError("backup bundle target is outside the managed store") from exc
        if root.name != bundle.name or not root.name.endswith(".bundle"):
            raise BackupBundleError("backup bundle target is invalid")
        if not root.is_dir() or root.is_symlink():
            raise BackupBundleError("backup bundle is missing")
        return root

    @staticmethod
    def _credentials_excluded(root: Path) -> bool:
        forbidden = {"config.yaml", "workspace-index.yaml", "credentials", "keyring"}
        try:
            return not any(path.name in forbidden for path in root.rglob("*"))
        except OSError:
            return False

    @staticmethod
    def _remove_bundle(bundle: Path) -> None:
        if bundle.exists() and bundle.is_dir() and not bundle.is_symlink():
            shutil.rmtree(bundle)


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
