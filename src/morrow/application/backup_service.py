"""Stage 6 Backup bundle creation, verification and isolated restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock, Timeout

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.adapters.state.extension_yaml import ExtensionYamlLoadStatus, ExtensionYamlStore
from morrow.adapters.state.journal import SqliteOperationalJournal
from morrow.adapters.state.operational import OperationalStore
from morrow.application.agent_definitions.integrity import verify_definition_rows
from morrow.application.learning.learning_backup import verify_learning_references
from morrow.application.learning.memory_backup import verify_memory_references
from morrow.application.mcp.backup import verify_mcp_backup_references
from morrow.application.preferences.backup import verify_preference_references
from morrow.application.skills.backup import (
    SkillBackupCapture,
    SkillBackupError,
    capture_referenced_skills,
    referenced_version_ids,
    verify_skill_capture,
)
from morrow.core.artifacts import ArtifactIntegrityError, ArtifactState
from morrow.core.backup import (
    BackupArtifactEntry,
    BackupFileEntry,
    BackupFileKind,
    BackupManifest,
    BackupReference,
    BackupRestoreReport,
    BackupSkillEntry,
    BackupVerificationReport,
    BackupYamlEntry,
)
from morrow.core.domain import CREDENTIAL_LITERAL_PATTERN as _SECRET_VALUE_PATTERN
from morrow.core.preference_documents import GLOBAL_CONFIG_SCHEMA_VERSION
from morrow.core.state_schema import (
    WORKSPACE_INDEX_SCHEMA_VERSION,
    WORKSPACE_PREFERENCE_SCHEMA_VERSION,
    WORKSPACE_PROFILE_SCHEMA_VERSION,
)
from morrow.core.store import (
    DIRECTORY_MODE,
    FILE_MODE,
    SUPPORTED_SCHEMA_VERSION,
    StorageError,
    StoreOpenMode,
)


class BackupError(RuntimeError):
    """Sanitized backup/restore failure."""


class DefinitionSourceBackupError(BackupError):
    """A raw desired-source draft needs local secret removal before copying."""


class BackupService:
    """Own the Stage 6-specific half of the operational backup composition."""

    def __init__(self, store: OperationalStore) -> None:
        self.store = store

    def create(self, bundle_name: str) -> tuple[Path, BackupManifest, str]:
        self._validate_name(bundle_name)
        final = self.store.layout.backups_dir / f"{bundle_name}.bundle"
        if os.path.lexists(final):
            raise BackupError("backup bundle already exists")
        self.store.ensure_layout()
        staging = Path(
            tempfile.mkdtemp(prefix=f".{bundle_name}.bundle-", dir=self.store.layout.backups_dir)
        )
        temporary_database: Path | None = None
        source_handle = None
        try:
            with self._extension_maintenance_lock():
                yaml_documents = self._capture_yaml_documents(staging)
                yaml_signatures = self._signatures(yaml_documents)
                definition_files = self._capture_definition_sources(staging)
                yaml_signatures.update(
                    {item.path: (item.sha256, item.byte_size) for item in definition_files}
                )
                source_handle = self.store.open(StoreOpenMode.DIAGNOSE)
                source_journal = SqliteOperationalJournal(source_handle)
                database, temporary_database = self._backup_database(bundle_name, staging)
                connection = sqlite3.connect(database, isolation_level=None)
                try:
                    workspace_ids = source_journal.list_workspace_ids()
                    artifacts, artifact_files = self._capture_artifacts(
                        source_journal, staging / "artifacts"
                    )
                    versions = source_journal.list_skill_versions()
                    pinned = self._pinned_versions(yaml_documents)
                    references = referenced_version_ids(
                        _ConnectionExecutor(connection), pinned_version_ids=tuple(pinned)
                    )
                    bound = self._enabled_binding_keys(yaml_documents)
                    skill_capture = capture_referenced_skills(
                        self.store.layout.data_root,
                        versions,
                        referenced_ids=references,
                        bound_skill_keys=bound,
                        target_root=staging,
                    )
                    schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    manifest = self._manifest(
                        connection,
                        database,
                        workspace_ids,
                        yaml_documents,
                        skill_capture,
                        artifacts,
                        artifact_files,
                        schema_version,
                        definition_files,
                    )
                finally:
                    connection.close()
                source_handle.close()
                source_handle = None
                self._recheck_source(yaml_signatures, skill_capture.skills)
                self._write_manifest(staging, manifest)
                verification = self.verify(staging, allow_staging=True)
                if not verification.ok:
                    raise BackupError("backup failed verification")
                if os.path.lexists(final):
                    raise BackupError("backup bundle already exists")
                os.replace(staging, final)
                _fsync_directory(final.parent)
                staging = Path()
            return final, manifest, _manifest_digest(final / "manifest.json")
        except DefinitionSourceBackupError:
            raise
        except (BackupError, SkillBackupError, StorageError, Timeout) as exc:
            raise BackupError("backup could not be completed") from exc
        except (OSError, sqlite3.Error, TypeError, ValueError, yaml.YAMLError) as exc:
            raise BackupError("backup could not be completed") from exc
        finally:
            if source_handle is not None:
                source_handle.close()
            if temporary_database is not None:
                temporary_database.unlink(missing_ok=True)
            if staging != Path() and staging.exists():
                _remove_owned_directory(staging)

    def verify(self, bundle: Path, *, allow_staging: bool = False) -> BackupVerificationReport:
        root = self._validate_bundle(bundle, allow_staging=allow_staging)
        issues: list[str] = []
        manifest, manifest_ok = self._read_manifest(root, issues)
        files_ok = yaml_ok = skills_ok = artifacts_ok = references_ok = False
        database_ok = foreign_keys_ok = False
        if manifest is not None:
            files_ok = self._verify_files(root, manifest, issues)
            yaml_ok = self._verify_yaml(root, manifest, issues)
            for item in manifest.files:
                if item.kind is BackupFileKind.DEFINITION_SOURCE:
                    try:
                        _reject_secret_text(_read_regular_file(root / item.path))
                    except (ValueError, OSError):
                        yaml_ok = False
                        issues.append("definition_source_unsafe")
            skills_ok, skill_issues = verify_skill_capture(root, manifest.skill_versions)
            issues.extend(skill_issues)
            artifacts_ok = self._verify_artifacts(root, manifest, issues)
            database_ok, foreign_keys_ok, references_ok = self._verify_database(
                root, manifest, issues
            )
        credentials_excluded = _credentials_excluded(root)
        if not credentials_excluded:
            issues.append("credentials_present")
        return BackupVerificationReport(
            bundle_name=root.name,
            manifest_ok=manifest_ok,
            database_integrity_ok=database_ok,
            foreign_keys_ok=foreign_keys_ok,
            files_ok=files_ok,
            yaml_ok=yaml_ok,
            skills_ok=skills_ok,
            artifacts_ok=artifacts_ok,
            references_ok=references_ok,
            credentials_excluded=credentials_excluded,
            issues=tuple(dict.fromkeys(issues)),
        )

    def restore(self, bundle: Path, target_root: Path) -> BackupRestoreReport:
        verification = self.verify(bundle)
        if not verification.ok:
            return BackupRestoreReport(
                bundle_name=bundle.name,
                target_root=str(target_root),
                restored=False,
                issues=verification.issues,
            )
        root = self._validate_bundle(bundle)
        target = target_root.expanduser().absolute()
        if os.path.lexists(target):
            return BackupRestoreReport(
                bundle_name=root.name,
                target_root=str(target),
                restored=False,
                issues=("restore_target_exists",),
            )
        try:
            target.relative_to(root)
            return BackupRestoreReport(
                bundle_name=root.name,
                target_root=str(target),
                restored=False,
                issues=("restore_target_inside_bundle",),
            )
        except ValueError:
            pass
        try:
            _assert_directory_chain(target.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_directory_chain(target.parent)
            temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.restore-", dir=target.parent))
        except OSError:
            return BackupRestoreReport(
                bundle_name=root.name,
                target_root=str(target),
                restored=False,
                issues=("restore_target_unavailable",),
            )
        try:
            manifest, _ = self._read_manifest(root, [])
            if manifest is None:
                raise BackupError("backup manifest is unavailable")
            for item in manifest.files:
                source = root / item.path
                relative = Path(item.path)
                destination = temporary / relative
                if item.kind is BackupFileKind.DATABASE:
                    destination = temporary / "store" / "operational.sqlite"
                _copy_verified_file(
                    source,
                    destination,
                    expected_digest=item.sha256,
                    preserve_exec=item.kind is BackupFileKind.SKILL_PACKAGE,
                )
            _fsync_directory(temporary)
            if os.path.lexists(target):
                raise BackupError("restore target appeared during publication")
            os.replace(temporary, target)
            _fsync_directory(target.parent)
            temporary = Path()
            return BackupRestoreReport(
                bundle_name=root.name,
                target_root=str(target),
                restored=True,
            )
        except (OSError, ValueError, BackupError):
            return BackupRestoreReport(
                bundle_name=root.name,
                target_root=str(target),
                restored=False,
                issues=("restore_failed",),
            )
        finally:
            if temporary != Path() and temporary.exists():
                _remove_owned_directory(temporary)

    def _backup_database(self, bundle_name: str, staging: Path) -> tuple[Path, Path]:
        report = self.store.backup(f"{bundle_name}-stage6.sqlite")
        temporary = self.store.layout.backups_dir / report.destination_name
        database = staging / "database.sqlite"
        shutil.move(temporary, database)
        os.chmod(database, FILE_MODE)
        return database, temporary

    def _capture_artifacts(
        self, journal: SqliteOperationalJournal, target: Path
    ) -> tuple[tuple[BackupArtifactEntry, ...], tuple[BackupFileEntry, ...]]:
        target.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
        filesystem = FilesystemArtifactStore(self.store.layout)
        entries: list[BackupArtifactEntry] = []
        files: list[BackupFileEntry] = []
        for workspace_id in journal.list_workspace_ids():
            for metadata in journal.list_artifacts(workspace_id):
                if metadata.state is not ArtifactState.AVAILABLE:
                    raise BackupError("current backup requires every Artifact to be available")
                try:
                    filesystem.verify(metadata)
                    source = filesystem.existing_final_path(metadata.artifact_id)
                    destination = target / metadata.filename
                    _copy_verified_file(source, destination, expected_digest=metadata.sha256)
                except (ArtifactIntegrityError, OSError) as exc:
                    raise BackupError("referenced Artifact is missing or changed") from exc
                digest, size = _hash_file(destination)
                relative = _relative(self._staging_root(target), destination)
                entry = BackupArtifactEntry(
                    artifact_id=metadata.artifact_id,
                    workspace_id=metadata.workspace_id,
                    state=metadata.state.value,
                    sha256=metadata.sha256,
                    byte_size=metadata.byte_size,
                    status="copied",
                    copied=True,
                    verified=digest == metadata.sha256 and size == metadata.byte_size,
                    path=relative,
                )
                entries.append(entry)
                files.append(
                    BackupFileEntry(
                        path=relative,
                        kind=BackupFileKind.ARTIFACT,
                        sha256=digest,
                        byte_size=size,
                    )
                )
        return tuple(entries), tuple(files)

    def _manifest(
        self,
        connection: sqlite3.Connection,
        database: Path,
        workspace_ids: tuple[str, ...],
        yaml_documents: tuple[tuple[BackupYamlEntry, Path], ...],
        skills: SkillBackupCapture,
        artifacts: tuple[BackupArtifactEntry, ...],
        artifact_files: tuple[BackupFileEntry, ...],
        schema_version: int,
        definition_files: tuple[BackupFileEntry, ...] = (),
    ) -> BackupManifest:
        yaml_entries = tuple(item for item, _path in yaml_documents)
        yaml_files = tuple(
            BackupFileEntry(
                path=item.path,
                kind=(
                    BackupFileKind.EXTENSION_YAML
                    if item.path.endswith("extensions.yaml")
                    else BackupFileKind.CONFIGURATION
                ),
                sha256=item.sha256,
                byte_size=(root / item.path).stat().st_size,
            )
            for item, root in yaml_documents
        )
        database_path = "database.sqlite"
        database_digest, database_size = _hash_file(database)
        database_file = BackupFileEntry(
            path=database_path,
            kind=BackupFileKind.DATABASE,
            sha256=database_digest,
            byte_size=database_size,
        )
        references = [
            BackupReference(
                kind="skill_version", identifier=item.version_id, target=item.package_path
            )
            for item in skills.skills
        ]
        references.extend(
            BackupReference(kind="artifact", identifier=item.artifact_id, target=item.path)
            for item in artifacts
        )
        references.extend(
            BackupReference(kind="yaml", identifier=item.path, target=item.path)
            for item in yaml_entries
        )
        references.extend(
            BackupReference(kind="definition_source", identifier=item.path, target=item.path)
            for item in definition_files
        )
        schema_versions = {"operational": schema_version, "extension_yaml": 1}
        return BackupManifest(
            schema_version=schema_version,
            schema_versions=schema_versions,
            workspace_ids=tuple(sorted(set(workspace_ids))),
            files=tuple(
                sorted(
                    (database_file, *yaml_files, *artifact_files, *skills.files, *definition_files),
                    key=lambda item: item.path,
                )
            ),
            artifacts=tuple(sorted(artifacts, key=lambda item: item.artifact_id)),
            yaml_documents=tuple(sorted(yaml_entries, key=lambda item: item.path)),
            skill_versions=skills.skills,
            references=tuple(references),
        )

    @staticmethod
    def _write_manifest(root: Path, manifest: BackupManifest) -> None:
        raw = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        temporary = root / "manifest.json.tmp"
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, root / "manifest.json")
        digest = root / "manifest.sha256.tmp"
        with digest.open("w", encoding="ascii") as handle:
            handle.write(hashlib.sha256(raw).hexdigest() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(digest, FILE_MODE)
        os.replace(digest, root / "manifest.sha256")
        _fsync_directory(root)

    def _capture_definition_sources(self, staging):
        captured = []
        root = self.store.layout.data_root
        for workspace_id in self._workspace_ids_from_paths():
            relative = f"workspaces/{workspace_id}/agent-definitions.yaml"
            source = root / relative
            if not os.path.lexists(source):
                continue
            _assert_safe_chain(root, source)
            raw = _read_regular_file(source)
            try:
                _reject_secret_text(raw)
            except ValueError:
                raise DefinitionSourceBackupError(
                    "Agent definition source cannot be copied safely"
                ) from None
            entry = BackupFileEntry(
                path=relative,
                kind=BackupFileKind.DEFINITION_SOURCE,
                sha256=hashlib.sha256(raw).hexdigest(),
                byte_size=len(raw),
            )
            _copy_bytes(source, staging / relative)
            captured.append(entry)
        return tuple(captured)

    def _capture_yaml_documents(self, staging: Path) -> tuple[tuple[BackupYamlEntry, Path], ...]:
        source_root = self.store.layout.data_root
        candidates: list[tuple[Path, str, str | None]] = []
        for relative, scope, scope_id in (
            ("config.yaml", "global", None),
            ("workspace-index.yaml", "global", None),
            ("extensions.yaml", "global", None),
        ):
            candidates.append((source_root / relative, scope, scope_id))
        for workspace_id in self._workspace_ids_from_paths():
            base = source_root / "workspaces" / workspace_id
            candidates.extend(
                (
                    (base / "extensions.yaml", "workspace", workspace_id),
                    (base / "preferences.yaml", "workspace", workspace_id),
                    (base / "profile.yaml", "workspace", workspace_id),
                )
            )
        captured: list[tuple[BackupYamlEntry, Path]] = []
        for source, scope, scope_id in candidates:
            if not os.path.lexists(source):
                continue
            relative = source.relative_to(source_root).as_posix()
            _assert_safe_chain(source_root, source)
            raw = _read_regular_file(source)
            payload = _parse_safe_yaml(raw)
            schema = _schema_int(payload)
            revision = _revision_int(payload)
            if schema > _supported_configuration_schema(relative):
                raise BackupError("configuration schema is newer than this client")
            if relative.endswith("extensions.yaml"):
                extension_store = ExtensionYamlStore(staging, create=False)
                destination = staging / relative
                _copy_bytes(source, destination)
                load = (
                    extension_store.load_global()
                    if scope == "global"
                    else extension_store.load_workspace(scope_id or "")
                )
                if load.status is not ExtensionYamlLoadStatus.OK or load.value is None:
                    raise BackupError("Extension YAML is unavailable")
                schema = load.source_schema_version or schema
                revision = load.revision
            else:
                destination = staging / relative
                _copy_bytes(source, destination)
            captured.append(
                (
                    BackupYamlEntry(
                        path=relative,
                        scope=scope,
                        scope_id=scope_id,
                        revision=revision,
                        schema_version=schema,
                        sha256=hashlib.sha256(raw).hexdigest(),
                    ),
                    staging,
                )
            )
        return tuple(captured)

    def _recheck_source(
        self,
        signatures: dict[str, tuple[str, int]],
        skills: tuple[BackupSkillEntry, ...],
    ) -> None:
        current = {}
        for path in signatures:
            current[path] = _file_signature(self.store.layout.data_root / path)
        if current != signatures:
            raise BackupError("configuration changed while backup was being created")
        ok, _issues = verify_skill_capture(self.store.layout.data_root, skills)
        if not ok:
            raise BackupError("Skill package changed while backup was being created")

    def _signatures(self, documents: tuple[tuple[BackupYamlEntry, Path], ...]):
        return {
            item.path: _file_signature(self.store.layout.data_root / item.path)
            for item, _root in documents
        }

    def _workspace_ids_from_paths(self) -> tuple[str, ...]:
        ids: set[str] = set()
        root = self.store.layout.data_root / "workspaces"
        if root.is_dir() and not root.is_symlink():
            for path in root.iterdir():
                if path.is_dir() and not path.is_symlink() and path.name.startswith("ws_"):
                    ids.add(path.name)
        return tuple(sorted(ids))

    @staticmethod
    def _pinned_versions(documents) -> frozenset[str]:
        ids = {
            binding.pinned_version_id
            for item, _root in documents
            if item.path.endswith("extensions.yaml")
            for binding in _extension_bindings(item, _root)
            if binding.pinned_version_id
        }
        return frozenset(ids)

    @staticmethod
    def _enabled_binding_keys(documents) -> frozenset[tuple[str | None, str]]:
        # The typed extension is loaded again in _extension_bindings; this
        # helper remains conservative if a configuration document is present.
        result: set[tuple[str | None, str]] = set()
        for item, root in documents:
            if not item.path.endswith("extensions.yaml"):
                continue
            for binding in _extension_bindings(item, root):
                if binding.enabled:
                    result.add((binding.scope_id, binding.skill_id))
        return frozenset(result)

    @staticmethod
    def _validate_name(name: str) -> None:
        if (
            not name
            or len(name) > 64
            or any(
                ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                for ch in name
            )
        ):
            raise BackupError("backup bundle name is invalid")

    def _validate_bundle(self, bundle: Path, *, allow_staging: bool = False) -> Path:
        root = bundle.expanduser().absolute()
        base = self.store.layout.backups_dir.absolute()
        try:
            root.relative_to(base)
        except ValueError as exc:
            raise BackupError("backup bundle is outside the managed store") from exc
        if not root.is_dir() or root.is_symlink():
            raise BackupError("backup bundle is missing")
        if not allow_staging and not root.name.endswith(".bundle"):
            raise BackupError("backup bundle target is invalid")
        return root

    def _read_manifest(self, root: Path, issues: list[str]) -> tuple[BackupManifest | None, bool]:
        path = root / "manifest.json"
        digest_path = root / "manifest.sha256"
        try:
            raw = _read_regular_file(path)
            if len(raw) > 512 * 1024:
                raise ValueError
            if (
                _read_regular_file(digest_path).decode("ascii").strip()
                != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("manifest_version") != 2:
                raise ValueError
            return BackupManifest.model_validate(payload), True
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            issues.append("manifest_invalid")
            return None, False

    def _verify_files(self, root: Path, manifest: BackupManifest, issues: list[str]) -> bool:
        expected = {item.path for item in manifest.files}
        actual: set[str] = set()
        safe = True
        for current, directories, files in os.walk(root, followlinks=False):
            for name in (*directories, *files):
                path = Path(current) / name
                if path.is_symlink():
                    issues.append("symlink_present")
                    safe = False
            for name in files:
                path = Path(current) / name
                relative = _relative(root, path)
                if relative in {"manifest.json", "manifest.sha256"}:
                    continue
                actual.add(relative)
        if actual != expected:
            issues.append("file_set_mismatch")
            safe = False
        for item in manifest.files:
            path = root / item.path
            try:
                digest, size = _hash_file(path)
            except (OSError, ValueError):
                issues.append("file_missing")
                safe = False
                continue
            if digest != item.sha256 or size != item.byte_size:
                issues.append("file_changed")
                safe = False
        return safe

    def _verify_yaml(self, root: Path, manifest: BackupManifest, issues: list[str]) -> bool:
        valid = True
        by_path = {item.path: item for item in manifest.files}
        for item in manifest.yaml_documents:
            path = root / item.path
            try:
                expected_path = _expected_yaml_path(item.scope, item.scope_id)
                if item.path.endswith("extensions.yaml"):
                    if item.path != expected_path:
                        raise ValueError
                elif item.path in {"config.yaml", "workspace-index.yaml"}:
                    if item.scope != "global" or item.scope_id is not None:
                        raise ValueError
                elif item.scope == "workspace" and item.scope_id is not None:
                    expected = {
                        f"workspaces/{item.scope_id}/preferences.yaml",
                        f"workspaces/{item.scope_id}/profile.yaml",
                    }
                    if item.path not in expected:
                        raise ValueError
                else:
                    raise ValueError
                raw = _read_regular_file(path)
                payload = _parse_safe_yaml(raw)
                if (
                    _schema_int(payload) != item.schema_version
                    or _revision_int(payload) != item.revision
                ):
                    raise ValueError
                if item.schema_version > _supported_configuration_schema(item.path):
                    raise ValueError
                if item.path.endswith("extensions.yaml"):
                    store = ExtensionYamlStore(root, create=False)
                    loaded = (
                        store.load_global()
                        if item.scope == "global"
                        else store.load_workspace(item.scope_id or "")
                    )
                    if loaded.status is not ExtensionYamlLoadStatus.OK or loaded.value is None:
                        raise ValueError
                file_entry = by_path.get(item.path)
                if file_entry is None or file_entry.kind not in {
                    BackupFileKind.EXTENSION_YAML,
                    BackupFileKind.CONFIGURATION,
                }:
                    raise ValueError
            except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError):
                issues.append("yaml_invalid")
                valid = False
        return valid

    def _verify_artifacts(self, root: Path, manifest: BackupManifest, issues: list[str]) -> bool:
        valid = True
        for item in manifest.artifacts:
            path = root / item.path
            try:
                digest, size = _hash_file(path)
            except (OSError, ValueError):
                issues.append("artifact_missing")
                valid = False
                continue
            if digest != item.sha256 or size != item.byte_size or not item.verified:
                issues.append("artifact_changed")
                valid = False
        return valid

    def _verify_database(
        self, root: Path, manifest: BackupManifest, issues: list[str]
    ) -> tuple[bool, bool, bool]:
        path = root / manifest.database_path
        integrity = foreign_keys = references = False
        try:
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                foreign_keys = not connection.execute("PRAGMA foreign_key_check").fetchall()
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if (
                    user_version != manifest.schema_version
                    or user_version > SUPPORTED_SCHEMA_VERSION
                ):
                    issues.append("database_schema")
                if not integrity:
                    issues.append("database_integrity")
                if not foreign_keys:
                    issues.append("foreign_keys")
                memory_ok, memory_issues = verify_memory_references(connection)
                learning_ok, learning_issues = verify_learning_references(connection)
                preference_ok, preference_issues = verify_preference_references(connection)
                issues.extend(memory_issues + learning_issues + preference_issues)
                mcp_ok, mcp_issues = verify_mcp_backup_references(connection)
                issues.extend(mcp_issues)
                definitions_ok, definition_issues = verify_definition_rows(connection)
                issues.extend(definition_issues)
                references = all(
                    (
                        memory_ok,
                        definitions_ok,
                        learning_ok,
                        preference_ok,
                        mcp_ok,
                        self._verify_cross_store_rows(connection, manifest, issues),
                    )
                )
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            issues.append("database_unreadable")
        return integrity, foreign_keys, references

    @staticmethod
    def _verify_cross_store_rows(
        connection: sqlite3.Connection, manifest: BackupManifest, issues: list[str]
    ) -> bool:
        valid = True
        if _has_table(connection, "skill_versions"):
            rows = {
                str(row[0]): row
                for row in connection.execute(
                    "SELECT version_id, skill_id, tree_digest, source_kind, scope, scope_id, "
                    "effective_trust "
                    "FROM skill_versions"
                ).fetchall()
            }
            manifest_by_id = {item.version_id: item for item in manifest.skill_versions}
            for item in manifest.skill_versions:
                row = rows.get(item.version_id)
                if (
                    row is None
                    or row[1] != item.skill_id
                    or row[2] != item.tree_digest
                    or row[3] != item.source_kind
                    or row[5] != (item.scope_id or "")
                    or (item.effective_trust != "unknown" and row[6] != item.effective_trust)
                ):
                    issues.append("skill_reference_missing")
                    valid = False
            references = referenced_version_ids(_ConnectionExecutor(connection))
            for version_id in references:
                row = rows.get(version_id)
                if row is None:
                    issues.append("skill_reference_missing")
                    valid = False
                elif row[3] in {"imported", "generated"} and version_id not in manifest_by_id:
                    issues.append("skill_reference_missing")
                    valid = False
        if _has_table(connection, "artifacts"):
            rows = {
                (str(row[0]), str(row[1])): (str(row[2]), str(row[3]), int(row[4]))
                for row in connection.execute(
                    "SELECT artifact_id, workspace_id, state, sha256, byte_size FROM artifacts"
                ).fetchall()
            }
            listed = {
                (item.artifact_id, item.workspace_id): (item.state, item.sha256, item.byte_size)
                for item in manifest.artifacts
            }
            if rows != listed:
                issues.append("artifact_reference_mismatch")
                valid = False
        return valid

    def _extension_maintenance_lock(self) -> FileLock:
        lock = self.store.layout.data_root / "locks" / "extensions-maintenance.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(lock), timeout=5)

    @staticmethod
    def _staging_root(path: Path) -> Path:
        # Artifact callers pass <staging>/artifacts; the bundle root is one level up.
        return path.parent


class _ConnectionExecutor:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, parameters=()):
        return self.connection.execute(sql, parameters).fetchall()


def _extension_bindings(item: BackupYamlEntry, root: Path):
    raw = _parse_safe_yaml(_read_regular_file(root / item.path))
    from morrow.core.skills.bindings import GlobalExtensionDocument, WorkspaceExtensionDocument

    model = (
        GlobalExtensionDocument.model_validate(raw)
        if item.scope == "global"
        else WorkspaceExtensionDocument.model_validate(raw)
    )
    return model.bindings


def _schema_int(payload: dict[str, Any]) -> int:
    value = payload.get("schema_version", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("configuration schema is invalid")
    return value


def _revision_int(payload: dict[str, Any]) -> int:
    value = payload.get("revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("configuration revision is invalid")
    return value


def _supported_configuration_schema(path: str) -> int:
    if path == "config.yaml":
        return max(GLOBAL_CONFIG_SCHEMA_VERSION, 2)
    if path == "workspace-index.yaml":
        return WORKSPACE_INDEX_SCHEMA_VERSION
    if path.endswith("/preferences.yaml"):
        return WORKSPACE_PREFERENCE_SCHEMA_VERSION
    if path.endswith("/profile.yaml"):
        return WORKSPACE_PROFILE_SCHEMA_VERSION
    return 1


def _expected_yaml_path(scope: str, scope_id: str | None) -> str:
    if scope == "global":
        return "extensions.yaml"
    if scope_id is None:
        raise ValueError("workspace YAML requires a scope id")
    return f"workspaces/{scope_id}/extensions.yaml"


def _parse_safe_yaml(raw: bytes) -> dict[str, Any]:
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("configuration document exceeds its byte budget")
    payload = yaml.safe_load(raw.decode("utf-8", errors="strict"))
    if not isinstance(payload, dict):
        raise ValueError("configuration document must be a mapping")
    _reject_secret_text(raw)
    _reject_secret_values(payload)
    return payload


def _reject_secret_values(value: Any, *, key: str = "") -> None:
    key_lower = key.casefold()
    if any(
        needle in key_lower
        for needle in ("api_key", "authorization", "password", "secret", "token")
    ) or ("credential" in key_lower and key_lower not in {"credential_ref", "credential_refs"}):
        raise ValueError("configuration contains secret material")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _reject_secret_values(child_value, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_values(child, key=key)
    elif isinstance(value, str) and _SECRET_VALUE_PATTERN.search(value):
        raise ValueError("configuration contains secret material")


_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*"
    r"(?:[\"']?)(?!credential_ref\b|credential_refs\b)[^\s,#\"']+"
)


def _reject_secret_text(raw: bytes) -> None:
    text = raw.decode("utf-8", errors="strict")
    if _SECRET_VALUE_PATTERN.search(text) or _SECRET_ASSIGNMENT_PATTERN.search(text):
        raise ValueError("configuration contains secret material")


def _read_regular_file(path: Path) -> bytes:
    raw, _mode = _read_regular_file_stat(path)
    return raw


def _read_regular_file_stat(path: Path) -> tuple[bytes, int]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError("backup source could not be opened safely") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("backup source is not a private regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks), info.st_mode
    except OSError as exc:
        raise ValueError("backup source could not be read safely") from exc
    finally:
        os.close(descriptor)


def _assert_safe_chain(root: Path, path: Path) -> None:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise BackupError("backup source path escapes the data root") from exc
    current = root.absolute()
    for part in relative.parts:
        current /= part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            if current != path.absolute():
                raise BackupError("backup source path contains an unsafe directory")


def _assert_directory_chain(path: Path) -> None:
    """Reject existing symlink/non-directory ancestors before mkdir/replace."""

    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("restore parent contains an unsafe path component")


def _copy_bytes(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    if destination.exists() and destination.is_symlink():
        raise BackupError("backup destination is a symlink")
    destination.write_bytes(_read_regular_file(source))
    os.chmod(destination, FILE_MODE)


def _copy_verified_file(
    source: Path,
    destination: Path,
    *,
    expected_digest: str | None = None,
    preserve_exec: bool = False,
) -> None:
    raw, mode = _read_regular_file_stat(source)
    digest = hashlib.sha256(raw).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise BackupError("source file changed during backup")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    if destination.exists() and destination.is_symlink():
        raise BackupError("restore destination is a symlink")
    destination.write_bytes(raw)
    os.chmod(destination, 0o700 if preserve_exec and mode & 0o111 else FILE_MODE)


def _file_signature(path: Path) -> tuple[str, int]:
    raw = _read_regular_file(path)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _hash_file(path: Path) -> tuple[str, int]:
    raw = _read_regular_file(path)
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _manifest_digest(path: Path) -> str:
    return hashlib.sha256(_read_regular_file(path)).hexdigest()


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _credentials_excluded(root: Path) -> bool:
    forbidden = {"credentials", "keyring", "credential-store", "secrets"}
    for _current, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            if name.casefold() in forbidden or name.casefold().endswith(".key"):
                return False
    return True


def _remove_owned_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        return
    shutil.rmtree(path)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = ["BackupError", "BackupService"]
