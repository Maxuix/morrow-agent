"""Bounded explicit workspace selection using descriptor-relative, no-follow reads."""

import mimetypes
import os
import stat
from contextlib import contextmanager
from pathlib import PurePosixPath

from morrow.core.attachments import IMAGE_TYPES, MAX_FILE_BYTES


@contextmanager
def open_selected(root, relative, *, directory=False):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or len(relative) > 1024:
        raise ValueError("Select a relative workspace path")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.parts
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def read_selected(root, relative):
    with open_selected(root, relative) as descriptor:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
            raise ValueError("Selected file is empty, special, or too large")
        chunks = []
        remaining = MAX_FILE_BYTES + 1
        while remaining and (chunk := os.read(descriptor, min(remaining, 65536))):
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            raise ValueError("Selected file exceeds the limit")
        return b"".join(chunks)


def search_selected(root, query="", *, directory="."):
    if len(query) > 256:
        raise ValueError("Search text is too long")
    pending = [(directory, 0)]
    result, visited = [], 0
    while pending and visited < 2000 and len(result) < 50:
        relative, depth = pending.pop()
        with open_selected(root, relative, directory=True) as descriptor:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 2000 or len(result) >= 50:
                        break
                    if entry.name.startswith(".") or entry.is_symlink():
                        continue
                    path = str(PurePosixPath(relative) / entry.name)
                    if entry.is_dir(follow_symlinks=False) and depth < 4:
                        if entry.name not in {"node_modules", "__pycache__", "dist"}:
                            pending.append((path, depth + 1))
                    elif (
                        entry.is_file(follow_symlinks=False) and query.casefold() in path.casefold()
                    ):
                        result.append(
                            {
                                "path": path,
                                "name": entry.name,
                                "byte_size": entry.stat(follow_symlinks=False).st_size,
                            }
                        )
    return {
        "files": sorted(result, key=lambda f: f["path"]),
        "truncated": bool(pending or visited >= 2000 or len(result) >= 50),
        "scanned": min(visited, 2000),
        "limit": 50,
    }


def selected_media_type(path):
    media = mimetypes.guess_type(path)[0]
    if media in IMAGE_TYPES or media == "application/pdf":
        return media
    return "text/plain"
