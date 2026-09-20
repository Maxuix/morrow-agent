"""有界的本地 HTML 运行预览集合。

预览运行在一个独立的 loopback 监听端口上，只服务一次性构建好的固定字节集合：
入口 HTML 与其静态引用（script/link/img、CSS url()/@import、ES 静态 import/
export-from 与字面量动态 import）在工作区内解析并读成内存文件，之后无论工作区
如何变化，预览看到的都是这份集合。构建阶段就做全部路径与上限校验，运行时不
再任意读取工作区。
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from morrow.core.application import ApplicationError, ApplicationErrorCode
from morrow.services.files import LocalFileError, WorkspaceFileService

MAX_PREVIEW_FILES = 128
#: 集合总量上限；单个文件先按同一上限有界读取。
MAX_PREVIEW_BYTES = 20 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 20 * 1024 * 1024
MAX_PREVIEW_DEPTH = 8
MAX_ACTIVE_PREVIEWS = 4
PREVIEW_TTL_SECONDS = 20 * 60

_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "blob:", "mailto:", "javascript:")

_MEDIA_TYPES: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "map": "application/json; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "ico": "image/x-icon",
    "bmp": "image/bmp",
    "wasm": "application/wasm",
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "bin": "application/octet-stream",
    "woff2": "font/woff2",
    "woff": "font/woff",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}

_TEXT_MEDIA_PREFIXES = ("text/", "application/json", "application/javascript")

_HTML_REF = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']{1,512})["']""", re.IGNORECASE)
_IMPORT_MAP = re.compile(
    r"""<script\b[^>]*type\s*=\s*["']importmap["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
_MODULE_SCRIPT = re.compile(
    r"""<script\b[^>]*type\s*=\s*["']module["'][^>]*>(.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
_JS_FROM = re.compile(r"""\bfrom\s*["']([^"']{1,512})["']""")
_JS_SIDE_EFFECT = re.compile(r"""\bimport\s*["']([^"']{1,512})["']""")
_JS_DYNAMIC = re.compile(r"""\bimport\s*\(\s*["']([^"']{1,512})["']""")
#: 显式写成相对路径的字面量本地资源（纹理/JSON/字体等）。只认 ./ ../ / 开头，
#: 不猜测裸名字；是否真的存在由构建阶段读取结果决定，缺失会如实上报。
_JS_LOCAL_ASSET = re.compile(r"""["']((?:\.\.?/|/)[^"'\n]{1,512})["']""")
_CSS_URL = re.compile(r"""url\(\s*["']?([^"')]{1,512})["']?\s*\)""", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"""@import\s+(?:url\()?\s*["']([^"']{1,512})["']""", re.IGNORECASE)


def media_type_for(relative_path: str) -> str:
    name = relative_path.rsplit("/", 1)[-1]
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _MEDIA_TYPES.get(suffix, "application/octet-stream")


def _is_text(media_type: str) -> bool:
    return media_type.startswith(_TEXT_MEDIA_PREFIXES) or media_type == "image/svg+xml"


@dataclass(frozen=True)
class PreviewFile:
    """集合内的一个固定字节副本。"""

    path: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class PreviewBundle:
    """一次预览的不可变内容集合。"""

    workspace_id: str
    entry_path: str
    revision: str
    files: tuple[PreviewFile, ...]
    missing: tuple[str, ...]
    total_bytes: int

    def file(self, relative: str) -> PreviewFile | None:
        for item in self.files:
            if item.path == relative:
                return item
        return None

    def wire(self) -> dict[str, Any]:
        return {
            "entry_path": self.entry_path,
            "revision": self.revision,
            "missing": list(self.missing),
            "total_bytes": self.total_bytes,
            "files": [
                {"path": item.path, "media_type": item.media_type, "byte_size": len(item.content)}
                for item in self.files
            ],
        }


def registered_preview_bundle(
    *,
    workspace_id: str,
    entry_path: str,
    revision: str,
    files: tuple[tuple[str, bytes], ...],
    missing: tuple[str, ...] = (),
) -> PreviewBundle:
    """One preview collection built only from already registered snapshot bytes.

    This is the historical branch: the caller has already authorized every
    byte through its own owner, and the same 128-file / 20 MiB / entry rules
    apply. It never reads the workspace, so a preview of a past delivery cannot
    silently pick up a newer file.
    """

    if not files:
        raise ApplicationError(ApplicationErrorCode.INVALID, "预览集合没有任何文件")
    if len(files) > MAX_PREVIEW_FILES:
        raise ApplicationError(
            ApplicationErrorCode.INVALID, f"预览集合超过 {MAX_PREVIEW_FILES} 个文件上限"
        )
    built: list[PreviewFile] = []
    total = 0
    for path, content in files:
        if not isinstance(content, bytes):
            raise ApplicationError(ApplicationErrorCode.INVALID, "预览文件必须是原始字节")
        if len(content) > MAX_SINGLE_FILE_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "单个预览文件超过 20 MiB 上限")
        total += len(content)
        if total > MAX_PREVIEW_BYTES:
            raise ApplicationError(ApplicationErrorCode.INVALID, "预览集合超过 20 MiB 上限")
        built.append(PreviewFile(path=path, media_type=media_type_for(path), content=content))
    if not any(item.path == entry_path for item in built):
        raise ApplicationError(ApplicationErrorCode.INVALID, "预览集合缺少入口文件")
    return PreviewBundle(
        workspace_id=workspace_id,
        entry_path=entry_path,
        revision=revision,
        files=tuple(built),
        missing=missing,
        total_bytes=total,
    )


def parse_import_map(text: str) -> dict[str, str]:
    """解析 HTML 中的 import map；损坏时忽略，不猜测映射。"""

    match = _IMPORT_MAP.search(text)
    if match is None:
        return {}
    try:
        payload = json.loads(match.group(1))
    except (ValueError, TypeError):
        return {}
    imports = payload.get("imports") if isinstance(payload, dict) else None
    if not isinstance(imports, dict):
        return {}
    return {
        key: value
        for key, value in imports.items()
        if isinstance(key, str) and isinstance(value, str) and value
    }


def html_references(text: str) -> tuple[list[str], str]:
    """返回入口 HTML 的静态引用与合并后的内联模块脚本正文。"""

    references = [match.group(1) for match in _HTML_REF.finditer(text)]
    inline = "\n".join(match.group(1) for match in _MODULE_SCRIPT.finditer(text))
    return references, inline


def script_references(text: str) -> list[str]:
    found = [match.group(1) for match in _JS_FROM.finditer(text)]
    found.extend(match.group(1) for match in _JS_SIDE_EFFECT.finditer(text))
    found.extend(match.group(1) for match in _JS_DYNAMIC.finditer(text))
    found.extend(match.group(1) for match in _JS_LOCAL_ASSET.finditer(text))
    return found[: MAX_PREVIEW_FILES * 8]


def style_references(text: str) -> list[str]:
    found = [match.group(1) for match in _CSS_IMPORT.finditer(text)]
    found.extend(match.group(1) for match in _CSS_URL.finditer(text))
    return found


def resolve_reference(
    base_dir: str,
    specifier: str,
    import_map: dict[str, str],
    *,
    bare_is_external: bool,
) -> tuple[str | None, str]:
    """把一条引用解析成工作区相对路径。

    返回 (路径, 状态)，状态是 ok/external/escape/unmapped。只有 ok 会进入集合；
    escape 与 unmapped 作为缺失依赖上报，绝不放宽到工作区之外，也不猜测裸说明符
    的落点。
    """

    clean = specifier.split("?", 1)[0].split("#", 1)[0].strip()
    if not clean or clean.startswith(_EXTERNAL_PREFIXES):
        return None, "external"
    relative = clean.startswith("./") or clean.startswith("../") or clean.startswith("/")
    if relative:
        mapped: str | None = clean
    else:
        mapped = None
        for key in sorted(import_map, key=len, reverse=True):
            value = import_map[key]
            if clean == key:
                mapped = value
                break
            if key.endswith("/") and clean.startswith(key):
                mapped = value + clean[len(key) :]
                break
        if mapped is None:
            if bare_is_external:
                return None, "unmapped"
            mapped = clean
    joined = (
        mapped.lstrip("/")
        if mapped.startswith("/")
        else (posixpath.join(base_dir, mapped) if base_dir else mapped)
    )
    normalized = posixpath.normpath(joined)
    if normalized.startswith("..") or normalized in (".", ""):
        return None, "escape"
    return normalized, "ok"


class _PreviewReadError(Exception):
    """集合构建期间的单个依赖读取失败；作为缺失依赖上报。"""


def _preview_error(error: LocalFileError) -> ApplicationError:
    code = {
        "not_found": ApplicationErrorCode.NOT_FOUND,
        "outside_workspace": ApplicationErrorCode.CROSS_WORKSPACE,
        "conflict": ApplicationErrorCode.STALE,
        "read_only": ApplicationErrorCode.READ_ONLY,
    }.get(error.code, ApplicationErrorCode.INVALID)
    return ApplicationError(code, error.message)


class HtmlPreviewBuilder:
    """把一个工作区入口文件展开成有界的静态依赖集合。"""

    def __init__(self, files: WorkspaceFileService, *, workspace_id: str) -> None:
        self.files = files
        self.workspace_id = workspace_id

    def build(self, entry_path: str) -> PreviewBundle:
        entry = self._entry(entry_path)
        files: dict[str, PreviewFile] = {}
        missing: list[str] = []
        total = 0
        entry_revision = ""
        import_map: dict[str, str] = {}
        pending: list[tuple[str, int]] = [(entry, 0)]
        while pending:
            relative, depth = pending.pop(0)
            if relative in files:
                continue
            if len(files) >= MAX_PREVIEW_FILES:
                missing.append(f"{relative}（超过 {MAX_PREVIEW_FILES} 个文件上限）")
                continue
            try:
                raw, revision = self._read(relative)
            except _PreviewReadError as exc:
                if relative == entry:
                    # 入口本身不可读不是“缺失依赖”，必须硬失败。
                    raise ApplicationError(
                        ApplicationErrorCode.NOT_FOUND, "预览入口不可读"
                    ) from None
                missing.append(f"{relative}（{exc}）")
                continue

            if total + len(raw) > MAX_PREVIEW_BYTES:
                raise ApplicationError(
                    ApplicationErrorCode.INVALID,
                    "预览集合超过 20 MiB 上限",
                )
            media = media_type_for(relative)
            files[relative] = PreviewFile(path=relative, media_type=media, content=raw)
            total += len(raw)
            if relative == entry:
                entry_revision = revision
            if depth >= MAX_PREVIEW_DEPTH or not _is_text(media):
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            base_dir = posixpath.dirname(relative)
            if media.startswith("text/html"):
                references, inline = html_references(text)
                import_map.update(parse_import_map(text))
                for specifier in references:
                    self._enqueue(pending, missing, base_dir, specifier, import_map, depth, False)
                for specifier in script_references(inline):
                    self._enqueue(pending, missing, base_dir, specifier, import_map, depth, True)
            elif "javascript" in media:
                for specifier in script_references(text):
                    self._enqueue(pending, missing, base_dir, specifier, import_map, depth, True)
            elif media.startswith("text/css"):
                for specifier in style_references(text):
                    self._enqueue(pending, missing, base_dir, specifier, import_map, depth, False)
        return PreviewBundle(
            workspace_id=self.workspace_id,
            entry_path=entry,
            revision=entry_revision,
            files=tuple(files.values()),
            missing=tuple(missing),
            total_bytes=total,
        )

    @staticmethod
    def _enqueue(
        pending: list[tuple[str, int]],
        missing: list[str],
        base_dir: str,
        specifier: str,
        import_map: dict[str, str],
        depth: int,
        bare_is_external: bool,
    ) -> None:
        target, status = resolve_reference(
            base_dir, specifier, import_map, bare_is_external=bare_is_external
        )
        if status == "ok" and target is not None:
            pending.append((target, depth + 1))
        elif status == "escape":
            missing.append(f"{specifier}（工作区外引用）")
        elif status == "unmapped":
            missing.append(f"{specifier}（导入映射未命中）")

    def _entry(self, entry_path: str) -> str:
        try:
            resolved = self.files.resolver.resolve_file(entry_path)
        except LocalFileError as exc:
            raise _preview_error(exc) from None
        if resolved.kind != "file":
            raise ApplicationError(ApplicationErrorCode.INVALID, "预览入口不是普通文件")
        return resolved.relative_path

    def _read(self, relative: str) -> tuple[bytes, str]:
        try:
            resolved = self.files.resolver.resolve_file(relative)
            raw = self.files.filesystem.read_bytes(resolved.target, max_bytes=MAX_SINGLE_FILE_BYTES)
        except LocalFileError as exc:
            raise _PreviewReadError("读取失败") from exc
        return raw, hashlib.sha256(raw).hexdigest()


@dataclass
class _RegistryEntry:
    bundle: PreviewBundle
    created_at: float
    last_access: float


class PreviewRegistry:
    """按 preview 身份隔离的有界内存集合，短期过期。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = PREVIEW_TTL_SECONDS,
        max_previews: int = MAX_ACTIVE_PREVIEWS,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_previews = max_previews
        self._entries: dict[str, _RegistryEntry] = {}

    def register(self, bundle: PreviewBundle) -> str:
        self.purge_expired()
        if len(self._entries) >= self.max_previews:
            oldest = min(self._entries.items(), key=lambda item: item[1].last_access)
            self._entries.pop(oldest[0], None)
        preview_id = secrets.token_urlsafe(16)
        now = time.monotonic()
        self._entries[preview_id] = _RegistryEntry(bundle=bundle, created_at=now, last_access=now)
        return preview_id

    def get(self, preview_id: str) -> PreviewBundle | None:
        self.purge_expired()
        entry = self._entries.get(preview_id)
        if entry is None:
            return None
        entry.last_access = time.monotonic()
        return entry.bundle

    def release(self, preview_id: str) -> bool:
        return self._entries.pop(preview_id, None) is not None

    def release_workspace(self, workspace_id: str) -> int:
        dropped = [
            key for key, entry in self._entries.items() if entry.bundle.workspace_id == workspace_id
        ]
        for key in dropped:
            self._entries.pop(key, None)
        return len(dropped)

    def purge_expired(self) -> int:
        now = time.monotonic()
        stale = [
            key
            for key, entry in self._entries.items()
            if now - entry.last_access > self.ttl_seconds
        ]
        for key in stale:
            self._entries.pop(key, None)
        return len(stale)

    def active(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
