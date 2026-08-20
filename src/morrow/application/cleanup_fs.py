"""Descriptor-bound filesystem protocol for Artifact orphan cleanup."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.state.artifacts import FilesystemArtifactStore
from morrow.core.artifacts import (
    ARTIFACT_FILE_SUFFIX,
    ARTIFACT_TEMP_SUFFIX,
    ArtifactMetadata,
    ArtifactRetention,
    ArtifactState,
)
from morrow.core.domain import ARTIFACT_ID_PREFIX, validate_prefixed_id
from morrow.core.store import DIRECTORY_MODE, FILE_MODE

FINAL_PARENT = "final"
TEMP_PARENT = "temp"
_QUARANTINE_PREFIX = ".morrow-cleanup-"
_QUARANTINE_SUFFIX = ".quarantine"
_QUARANTINE_PAYLOAD = "payload"
_QUARANTINE_NAME_ATTEMPTS = 8


class UnsafeArtifactLayout(RuntimeError):
    """The managed Artifact directory chain cannot be trusted."""


@dataclass(frozen=True)
class _DirectoryHandle:
    descriptor: int
    device: int
    inode: int

    def matches(self, info: os.stat_result) -> bool:
        return stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (
            self.device,
            self.inode,
        )


@dataclass(frozen=True)
class CleanupCandidate:
    parent: str
    name: str
    artifact_id: str | None
    device: int | None
    inode: int | None
    mode: int | None
    links: int | None

    def matches(self, info: os.stat_result) -> bool:
        return (
            self.device is not None
            and self.inode is not None
            and (info.st_dev, info.st_ino) == (self.device, self.inode)
            and stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == FILE_MODE
        )

    @property
    def is_private_regular_file(self) -> bool:
        return (
            self.mode is not None
            and stat.S_ISREG(self.mode)
            and self.links == 1
            and stat.S_IMODE(self.mode) == FILE_MODE
        )


@dataclass(frozen=True)
class QuarantinedTarget:
    target: CleanupCandidate
    directory_name: str
    directory_device: int
    directory_inode: int


@dataclass(frozen=True)
class QuarantineAttempt:
    status: str
    quarantine: QuarantinedTarget | None = None


class TrustedArtifactLayout:
    """Open and retain a trusted ``data_root/artifacts[/tmp]`` directory chain."""

    def __init__(
        self,
        *,
        root_path: Path,
        root: _DirectoryHandle,
        artifacts_name: str,
        artifacts: _DirectoryHandle,
        temp_name: str,
        temp: _DirectoryHandle,
    ) -> None:
        self.root_path = root_path
        self.root = root
        self.artifacts_name = artifacts_name
        self.artifacts = artifacts
        self.temp_name = temp_name
        self.temp = temp

    @classmethod
    def open(cls, filesystem: FilesystemArtifactStore) -> TrustedArtifactLayout:
        root_path = filesystem.root.absolute()
        artifacts_path = filesystem.artifacts_dir.absolute()
        temp_path = filesystem.artifacts_tmp.absolute()
        if artifacts_path.parent != root_path or temp_path.parent != artifacts_path:
            raise UnsafeArtifactLayout

        descriptors: list[int] = []
        try:
            root_fd, root_info = cls._open_directory(root_path)
            descriptors.append(root_fd)
            cls._validate_root(root_info)
            artifacts_fd, artifacts_info = cls._open_directory(artifacts_path.name, dir_fd=root_fd)
            descriptors.append(artifacts_fd)
            cls._validate_private_directory(artifacts_info)
            temp_fd, temp_info = cls._open_directory(temp_path.name, dir_fd=artifacts_fd)
            descriptors.append(temp_fd)
            cls._validate_private_directory(temp_info)
        except (OSError, ValueError) as exc:
            for descriptor in reversed(descriptors):
                cls._close_quietly(descriptor)
            raise UnsafeArtifactLayout from exc

        return cls(
            root_path=root_path,
            root=cls._handle(root_fd, root_info),
            artifacts_name=artifacts_path.name,
            artifacts=cls._handle(artifacts_fd, artifacts_info),
            temp_name=temp_path.name,
            temp=cls._handle(temp_fd, temp_info),
        )

    def __enter__(self) -> TrustedArtifactLayout:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._close_quietly(self.temp.descriptor)
        self._close_quietly(self.artifacts.descriptor)
        self._close_quietly(self.root.descriptor)

    def assert_stable(self) -> None:
        try:
            root_info = os.stat(self.root_path, follow_symlinks=False)
            self._validate_root(root_info)
            self._require_match(self.root, root_info)
            self._require_match(self.root, os.fstat(self.root.descriptor))

            artifacts_info = os.stat(
                self.artifacts_name,
                dir_fd=self.root.descriptor,
                follow_symlinks=False,
            )
            self._validate_private_directory(artifacts_info)
            self._require_match(self.artifacts, artifacts_info)
            self._require_match(self.artifacts, os.fstat(self.artifacts.descriptor))

            temp_info = os.stat(
                self.temp_name,
                dir_fd=self.artifacts.descriptor,
                follow_symlinks=False,
            )
            self._validate_private_directory(temp_info)
            self._require_match(self.temp, temp_info)
            self._require_match(self.temp, os.fstat(self.temp.descriptor))
        except (OSError, ValueError) as exc:
            raise UnsafeArtifactLayout from exc

    def scan(
        self,
        metadata: tuple[ArtifactMetadata, ...],
        referenced: frozenset[str],
    ) -> tuple[CleanupCandidate, ...]:
        self.assert_stable()
        by_id = {item.artifact_id: item for item in metadata}
        candidates = [
            *self._scan_directory(
                self.artifacts,
                parent=FINAL_PARENT,
                suffix=ARTIFACT_FILE_SUFFIX,
                by_id=by_id,
                referenced=referenced,
                skip_managed_temp=True,
            ),
            *self._scan_directory(
                self.temp,
                parent=TEMP_PARENT,
                suffix=ARTIFACT_TEMP_SUFFIX,
                by_id=by_id,
                referenced=referenced,
                skip_managed_temp=False,
            ),
        ]
        for item in metadata:
            if (
                item.artifact_id in referenced
                or item.retention is ArtifactRetention.PINNED
                or item.state is ArtifactState.AVAILABLE
            ):
                continue
            candidates.append(
                CleanupCandidate(
                    parent=FINAL_PARENT,
                    name=item.filename,
                    artifact_id=item.artifact_id,
                    device=None,
                    inode=None,
                    mode=None,
                    links=None,
                )
            )
        return tuple(candidates)

    def quarantine(
        self,
        target: CleanupCandidate,
        *,
        on_moved: Callable[[QuarantinedTarget], None],
    ) -> QuarantineAttempt:
        self.assert_stable()
        parent = self._parent(target.parent)
        try:
            current = os.stat(target.name, dir_fd=parent.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return QuarantineAttempt("target_changed")
        except OSError as exc:
            raise UnsafeArtifactLayout from exc
        if not target.matches(current):
            return QuarantineAttempt("target_changed")

        name, descriptor, info = self._create_quarantine(parent)
        quarantine = QuarantinedTarget(target, name, info.st_dev, info.st_ino)
        moved = False
        try:
            try:
                os.rename(
                    target.name,
                    _QUARANTINE_PAYLOAD,
                    src_dir_fd=parent.descriptor,
                    dst_dir_fd=descriptor,
                )
            except FileNotFoundError:
                self._remove_empty_quarantine(parent, quarantine)
                return QuarantineAttempt("target_changed")
            moved = True
            on_moved(quarantine)
            os.fsync(descriptor)
            os.fsync(parent.descriptor)
            try:
                isolated = os.stat(
                    _QUARANTINE_PAYLOAD,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                return QuarantineAttempt("target_changed_quarantined", quarantine)
            if not target.matches(isolated):
                return QuarantineAttempt("target_changed_quarantined", quarantine)
            return QuarantineAttempt("quarantined", quarantine)
        except OSError as exc:
            if not moved:
                self._remove_empty_quarantine(parent, quarantine)
            raise UnsafeArtifactLayout from exc
        finally:
            self._close_quietly(descriptor)

    def restore_quarantine(
        self,
        quarantine: QuarantinedTarget,
        *,
        require_original_inode: bool = True,
    ) -> bool:
        """Restore by exclusive hard link; never overwrite a replacement entry."""

        parent = self._parent(quarantine.target.parent)
        directory_descriptor: int | None = None
        source_descriptor: int | None = None
        destination_descriptor: int | None = None
        try:
            self.assert_stable()
            directory_descriptor = self._open_quarantine(parent, quarantine)
            source_descriptor = self._open_payload(
                directory_descriptor,
                flags=os.O_RDONLY,
            )
            info = os.fstat(source_descriptor)
            if require_original_inode:
                if not quarantine.target.matches(info):
                    return False
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return False
            os.link(
                _QUARANTINE_PAYLOAD,
                quarantine.target.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=parent.descriptor,
                follow_symlinks=False,
            )
            destination_descriptor = self._open_payload(
                parent.descriptor,
                name=quarantine.target.name,
                flags=os.O_RDONLY,
            )
            destination = os.fstat(destination_descriptor)
            if (destination.st_dev, destination.st_ino) != (info.st_dev, info.st_ino):
                return False
            os.fsync(parent.descriptor)
            return True
        except (OSError, UnsafeArtifactLayout):
            return False
        finally:
            if destination_descriptor is not None:
                self._close_quietly(destination_descriptor)
            if source_descriptor is not None:
                self._close_quietly(source_descriptor)
            if directory_descriptor is not None:
                self._close_quietly(directory_descriptor)

    def _scan_directory(
        self,
        directory: _DirectoryHandle,
        *,
        parent: str,
        suffix: str,
        by_id: dict[str, ArtifactMetadata],
        referenced: frozenset[str],
        skip_managed_temp: bool,
    ) -> tuple[CleanupCandidate, ...]:
        try:
            names = sorted(os.listdir(directory.descriptor))
        except OSError as exc:
            raise UnsafeArtifactLayout from exc
        candidates: list[CleanupCandidate] = []
        for name in names:
            try:
                info = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise UnsafeArtifactLayout from exc
            if skip_managed_temp and name == self.temp_name and self.temp.matches(info):
                continue
            artifact_id = self._artifact_id_from_name(name, suffix)
            item = by_id.get(artifact_id) if artifact_id is not None else None
            if item is not None and (
                artifact_id in referenced or item.retention is ArtifactRetention.PINNED
            ):
                continue
            candidates.append(
                CleanupCandidate(
                    parent=parent,
                    name=name,
                    artifact_id=artifact_id,
                    device=info.st_dev,
                    inode=info.st_ino,
                    mode=info.st_mode,
                    links=info.st_nlink,
                )
            )
        return tuple(candidates)

    def _create_quarantine(self, parent: _DirectoryHandle) -> tuple[str, int, os.stat_result]:
        for _attempt in range(_QUARANTINE_NAME_ATTEMPTS):
            name = f"{_QUARANTINE_PREFIX}{secrets.token_hex(16)}{_QUARANTINE_SUFFIX}"
            try:
                os.mkdir(name, DIRECTORY_MODE, dir_fd=parent.descriptor)
            except FileExistsError:
                continue
            except OSError as exc:
                raise UnsafeArtifactLayout from exc
            descriptor: int | None = None
            try:
                descriptor, info = self._open_directory(name, dir_fd=parent.descriptor)
                os.fchmod(descriptor, DIRECTORY_MODE)
                info = os.fstat(descriptor)
                self._validate_private_directory(info)
                return name, descriptor, info
            except (OSError, ValueError) as exc:
                if descriptor is not None:
                    self._close_quietly(descriptor)
                try:
                    os.rmdir(name, dir_fd=parent.descriptor)
                except OSError:
                    pass
                raise UnsafeArtifactLayout from exc
        raise UnsafeArtifactLayout

    def _open_quarantine(self, parent: _DirectoryHandle, quarantine: QuarantinedTarget) -> int:
        descriptor: int | None = None
        try:
            descriptor, info = self._open_directory(
                quarantine.directory_name, dir_fd=parent.descriptor
            )
            self._validate_private_directory(info)
            if (info.st_dev, info.st_ino) != (
                quarantine.directory_device,
                quarantine.directory_inode,
            ):
                raise ValueError
            return descriptor
        except (OSError, ValueError) as exc:
            if descriptor is not None:
                self._close_quietly(descriptor)
            raise UnsafeArtifactLayout from exc

    @staticmethod
    def _open_payload(
        directory_descriptor: int,
        *,
        flags: int,
        name: str = _QUARANTINE_PAYLOAD,
    ) -> int:
        selected = flags
        for flag_name in ("O_CLOEXEC", "O_NOFOLLOW"):
            selected |= getattr(os, flag_name, 0)
        try:
            return os.open(name, selected, dir_fd=directory_descriptor)
        except OSError as exc:
            raise UnsafeArtifactLayout from exc

    def _remove_empty_quarantine(
        self, parent: _DirectoryHandle, quarantine: QuarantinedTarget
    ) -> bool:
        try:
            info = os.stat(
                quarantine.directory_name,
                dir_fd=parent.descriptor,
                follow_symlinks=False,
            )
            self._validate_private_directory(info)
            if (info.st_dev, info.st_ino) != (
                quarantine.directory_device,
                quarantine.directory_inode,
            ):
                return False
            os.rmdir(quarantine.directory_name, dir_fd=parent.descriptor)
            os.fsync(parent.descriptor)
            return True
        except (OSError, ValueError):
            return False

    def _parent(self, value: str) -> _DirectoryHandle:
        if value == FINAL_PARENT:
            return self.artifacts
        if value == TEMP_PARENT:
            return self.temp
        raise UnsafeArtifactLayout

    @staticmethod
    def _artifact_id_from_name(name: str, suffix: str) -> str | None:
        if not name.endswith(suffix):
            return None
        try:
            return validate_prefixed_id(name[: -len(suffix)], ARTIFACT_ID_PREFIX)
        except ValueError:
            return None

    @staticmethod
    def _open_directory(path: str | Path, *, dir_fd: int | None = None):
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        descriptor = os.open(path, flags, dir_fd=dir_fd)
        try:
            return descriptor, os.fstat(descriptor)
        except OSError:
            TrustedArtifactLayout._close_quietly(descriptor)
            raise

    @staticmethod
    def _validate_root(info: os.stat_result) -> None:
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & (
            stat.S_IWGRP | stat.S_IWOTH
        ):
            raise ValueError

    @staticmethod
    def _validate_private_directory(info: os.stat_result) -> None:
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != DIRECTORY_MODE:
            raise ValueError

    @staticmethod
    def _require_match(handle: _DirectoryHandle, info: os.stat_result) -> None:
        if not handle.matches(info):
            raise ValueError

    @staticmethod
    def _handle(descriptor: int, info: os.stat_result) -> _DirectoryHandle:
        return _DirectoryHandle(descriptor, info.st_dev, info.st_ino)

    @staticmethod
    def _close_quietly(descriptor: int) -> None:
        try:
            os.close(descriptor)
        except OSError:
            pass
