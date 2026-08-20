"""Filesystem half of the Stage 4 Artifact Store.

The adapter accepts only bytes already bounded and redacted by the caller.  It
never accepts a user path; every filename is derived from a validated opaque
Artifact ID and the managed directory layout.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path

from morrow.adapters.state.operational import restrict_path
from morrow.core.artifacts import (
    ARTIFACT_FILE_SUFFIX,
    ARTIFACT_MAX_BYTES,
    ARTIFACT_TEMP_SUFFIX,
    ArtifactBudgetError,
    ArtifactErrorCode,
    ArtifactIntegrityError,
    ArtifactMetadata,
    ArtifactOrphanCandidate,
    ArtifactOrphanReport,
    ArtifactPathError,
)
from morrow.core.domain import ARTIFACT_ID_PREFIX, validate_prefixed_id
from morrow.core.store import (
    DIRECTORY_MODE,
    FILE_MODE,
    OperationalStoreLayout,
    StorageError,
    StorageErrorCode,
)


class FilesystemArtifactStore:
    """Publish and inspect ID-addressed Artifact files below the data root."""

    def __init__(self, layout: OperationalStoreLayout) -> None:
        self.layout = layout
        self.root = layout.data_root.expanduser().absolute()
        self.artifacts_dir = layout.artifacts_dir
        self.artifacts_tmp = layout.artifacts_tmp

    def ensure_layout(self) -> None:
        self._ensure_directory(self.artifacts_dir)
        self._ensure_directory(self.artifacts_tmp)

    def final_path(self, artifact_id: str) -> Path:
        validate_prefixed_id(artifact_id, ARTIFACT_ID_PREFIX)
        return self._managed_path(self.artifacts_dir, f"{artifact_id}{ARTIFACT_FILE_SUFFIX}")

    def existing_final_path(self, artifact_id: str) -> Path:
        """Return a managed path without creating or chmod-ing directories."""

        validate_prefixed_id(artifact_id, ARTIFACT_ID_PREFIX)
        return self._managed_path_without_prepare(
            self.artifacts_dir, f"{artifact_id}{ARTIFACT_FILE_SUFFIX}"
        )

    def temp_path(self, artifact_id: str) -> Path:
        validate_prefixed_id(artifact_id, ARTIFACT_ID_PREFIX)
        return self._managed_path(self.artifacts_tmp, f"{artifact_id}{ARTIFACT_TEMP_SUFFIX}")

    def publish(
        self,
        metadata: ArtifactMetadata,
        content: bytes,
        *,
        faults=None,
    ) -> Path:
        """Write, fsync, verify, and atomically publish one final file.

        Failures intentionally leave the reserved staging metadata and any
        managed temp/final file in place for the doctor report.
        """

        if len(content) > ARTIFACT_MAX_BYTES or len(content) != metadata.byte_size:
            raise ArtifactIntegrityError(message="artifact byte size does not match metadata")
        digest = hashlib.sha256(content).hexdigest()
        if digest != metadata.sha256:
            raise ArtifactIntegrityError(message="artifact hash does not match metadata")
        self.ensure_layout()
        temp = self.temp_path(metadata.artifact_id)
        final = self.final_path(metadata.artifact_id)
        self._reject_existing_managed_file(temp)
        self._reject_existing_managed_file(final)

        descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(temp, flags, FILE_MODE)
            self._check_fault(faults, "artifact.after_temp_create")
            try:
                offset = 0
                while offset < len(content):
                    written = os.write(descriptor, content[offset : offset + 1024 * 1024])
                    if written <= 0:
                        raise OSError("artifact write made no progress")
                    offset += written
                os.fchmod(descriptor, FILE_MODE)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
                descriptor = None
            self._check_fault(faults, "artifact.file_fsync")
            self._verify_path(temp, metadata)
            self._check_fault(faults, "artifact.before_rename")
            os.replace(temp, final)
            self._check_fault(faults, "artifact.after_rename")
            self._fsync_directory(self.artifacts_dir)
            self._check_fault(faults, "artifact.after_parent_fsync")
            self._verify_path(final, metadata)
            return final
        except ArtifactIntegrityError:
            raise
        except FileExistsError as exc:
            raise ArtifactIntegrityError(
                code=ArtifactErrorCode.CONFLICT,
                message="artifact managed path already exists",
            ) from exc
        except OSError as exc:
            raise StorageError(
                # StorageError keeps the public message free of host paths.
                code=StorageErrorCode.UNAVAILABLE,
                message="operational store could not publish artifact bytes",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def verify(self, metadata: ArtifactMetadata) -> None:
        self._verify_path(self.existing_final_path(metadata.artifact_id), metadata)

    def read(self, metadata: ArtifactMetadata, *, max_bytes: int) -> bytes:
        if max_bytes > ARTIFACT_MAX_BYTES:
            raise ArtifactBudgetError("artifact read budget exceeded")
        if max_bytes < 0:
            raise ArtifactIntegrityError(message="artifact read limit is invalid")
        return self._read_verified(
            self.existing_final_path(metadata.artifact_id), metadata, max_bytes
        )

    def orphan_report(
        self, metadata: Iterable[ArtifactMetadata], *, referenced_ids: frozenset[str] = frozenset()
    ) -> ArtifactOrphanReport:
        by_id = {item.artifact_id: item for item in metadata}
        candidates: list[ArtifactOrphanCandidate] = []
        for directory, suffix, label in (
            (self.artifacts_tmp, ARTIFACT_TEMP_SUFFIX, "unmanaged_temp"),
            (self.artifacts_dir, ARTIFACT_FILE_SUFFIX, "unmanaged_final"),
        ):
            for path in self._iter_directory(directory):
                # ``artifacts/tmp`` is part of the managed layout, not an orphan
                # entry in the final Artifact directory. Keep an unexpected
                # symlink or non-directory collision visible to Doctor/Cleanup.
                if (
                    directory == self.artifacts_dir
                    and path == self.artifacts_tmp
                    and not path.is_symlink()
                    and path.is_dir()
                ):
                    continue
                artifact_id = self._artifact_id_from_path(path, suffix)
                if artifact_id is None:
                    candidates.append(ArtifactOrphanCandidate(None, path, label))
                    continue
                item = by_id.get(artifact_id)
                if item is None:
                    candidates.append(ArtifactOrphanCandidate(artifact_id, path, label))
                elif artifact_id not in referenced_ids and item.retention.value != "pinned":
                    candidates.append(
                        ArtifactOrphanCandidate(artifact_id, path, "unreferenced_managed_file")
                    )
        for item in metadata:
            if item.artifact_id in referenced_ids or item.retention.value == "pinned":
                continue
            if item.state.value != "available":
                candidates.append(
                    ArtifactOrphanCandidate(
                        item.artifact_id, self.existing_final_path(item.artifact_id), item.state
                    )
                )
        return ArtifactOrphanReport(tuple(candidates))

    def _verify_path(self, path: Path, metadata: ArtifactMetadata) -> None:
        self._read_verified(path, metadata, None)

    def _read_verified(
        self, path: Path, metadata: ArtifactMetadata, max_bytes: int | None
    ) -> bytes:
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError(
                code=ArtifactErrorCode.MISSING,
                message="artifact bytes are missing",
            ) from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ArtifactIntegrityError(message="artifact managed path is a symlink") from exc
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not stat artifact bytes"
            ) from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ArtifactIntegrityError(
                    message="artifact managed path is not a private regular file"
                )
            if info.st_size != metadata.byte_size:
                raise ArtifactIntegrityError(message="artifact byte size does not match metadata")
            if stat.S_IMODE(info.st_mode) != FILE_MODE:
                raise ArtifactIntegrityError(message="artifact file permissions are too broad")
            digest = hashlib.sha256()
            total = 0
            prefix = bytearray()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > ARTIFACT_MAX_BYTES:
                    raise ArtifactIntegrityError(message="artifact file exceeds the byte budget")
                digest.update(chunk)
                if max_bytes is not None and len(prefix) < max_bytes:
                    prefix.extend(chunk[: max_bytes - len(prefix)])
        except ArtifactIntegrityError:
            raise
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not read artifact bytes"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if total != metadata.byte_size or digest.hexdigest() != metadata.sha256:
            raise ArtifactIntegrityError(message="artifact bytes failed hash verification")
        return bytes(prefix)

    def _ensure_directory(self, path: Path) -> None:
        if path.is_symlink():
            raise ArtifactPathError()
        try:
            path.absolute().relative_to(self.root)
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise ArtifactPathError()
            restrict_path(path, DIRECTORY_MODE)
            path.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise ArtifactPathError() from exc
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not prepare artifact paths"
            ) from exc

    def _managed_path(self, directory: Path, filename: str) -> Path:
        self._ensure_directory(directory)
        path = directory / filename
        try:
            path.parent.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise ArtifactPathError() from exc
        return path

    def _managed_path_without_prepare(self, directory: Path, filename: str) -> Path:
        if directory.is_symlink():
            raise ArtifactPathError()
        path = directory / filename
        try:
            path.parent.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise ArtifactPathError() from exc
        return path

    @staticmethod
    def _reject_existing_managed_file(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink():
            raise ArtifactPathError()
        try:
            info = path.stat()
        except OSError as exc:
            raise ArtifactPathError() from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArtifactIntegrityError(message="artifact managed path collision is unsafe")
        raise ArtifactIntegrityError(
            code=ArtifactErrorCode.CONFLICT,
            message="artifact managed path already exists",
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _check_fault(faults, point: str) -> None:
        if faults is not None:
            faults.check(point)

    @staticmethod
    def _iter_directory(directory: Path) -> tuple[Path, ...]:
        try:
            return tuple(sorted(directory.iterdir(), key=lambda item: item.name))
        except OSError as exc:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational store could not inspect artifact paths"
            ) from exc

    @staticmethod
    def _artifact_id_from_path(path: Path, suffix: str) -> str | None:
        if not path.name.endswith(suffix):
            return None
        value = path.name[: -len(suffix)]
        try:
            return validate_prefixed_id(value, ARTIFACT_ID_PREFIX)
        except ValueError:
            return None
