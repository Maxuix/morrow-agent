"""Bounded byte-line diffs and conservative three-way inverse edits."""

from __future__ import annotations

import codecs
import os
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from morrow.core.domain import canonical_json_bytes, sha256_digest

MAX_HUNKS = 200
DIFF_VERSION = "byte-lines-v1"


class HunkConflict(ValueError):
    pass


def enabled(feature: str) -> bool:
    return os.environ.get(f"MORROW_RESTORE_{feature}", "1") != "0"


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    new_start: int
    new_end: int


def text_lines(raw: bytes) -> tuple[bool, list[bytes]]:
    bom = raw.startswith(codecs.BOM_UTF8)
    if bom:
        raw = raw[len(codecs.BOM_UTF8) :]
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HunkConflict("修改块要求 UTF-8 文本") from exc
    if b"\x00" in raw or b"\r" in raw.replace(b"\r\n", b""):
        raise HunkConflict("修改块要求受支持的文本和换行格式")
    if raw.count(b"\n") > 100_000:
        raise HunkConflict("文件行数超出修改块比较预算")
    parts = raw.split(b"\n")
    crlf = sum(part.endswith(b"\r") for part in parts[:-1])
    if crlf not in {0, len(parts) - 1}:
        raise HunkConflict("修改块不支持混合换行")
    return bom, [part + b"\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def edits(base: list[bytes], other: list[bytes]) -> list[Edit]:
    if base == other:
        return []
    occurrences = Counter(other)
    if sum(occurrences[line] for line in base) > 2_000_000:
        raise HunkConflict("文本重复度超出修改块比较预算")
    return [
        Edit(start, end, new_start, new_end)
        for tag, start, end, new_start, new_end in SequenceMatcher(
            None, base, other, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]


class HunkPlan:
    def __init__(self, row, before: bytes, after: bytes):
        self.bom, self.before = text_lines(before)
        after_bom, self.after = text_lines(after)
        if self.bom != after_bom or row.before.mode != row.after.mode:
            raise HunkConflict("修改块要求前后 BOM 与权限一致")
        changes = edits(self.before, self.after)
        if len(changes) > MAX_HUNKS:
            raise HunkConflict("修改块数量超出比较预算")
        self.changes = {
            sha256_digest(
                canonical_json_bytes(
                    [
                        DIFF_VERSION,
                        row.change_id,
                        row.path,
                        row.before.sha256,
                        row.after.sha256,
                        e.start,
                        e.end,
                        e.new_start,
                        e.new_end,
                    ]
                )
            ): e
            for e in changes
        }

    def descriptions(self):
        return [
            {
                "hunk_id": key,
                "before_start": edit.start + 1,
                "before_count": edit.end - edit.start,
                "after_start": edit.new_start + 1,
                "after_count": edit.new_end - edit.new_start,
                "excerpt": (
                    b"".join(self.before[edit.start : edit.end])[:160].decode("utf-8", "ignore")
                    + " → "
                    + b"".join(self.after[edit.new_start : edit.new_end])[:160].decode(
                        "utf-8", "ignore"
                    )
                ),
            }
            for key, edit in self.changes.items()
        ]

    def inverse(self, current: bytes, ids: tuple[str, ...]) -> bytes:
        if not ids or set(ids) - self.changes.keys():
            raise HunkConflict("修改块选择与历史版本不符")
        bom, now = text_lines(current)
        if self.bom != bom:
            raise HunkConflict("当前文件 BOM 已变化")
        drift = now != self.after
        if drift and not enabled("MERGE"):
            raise HunkConflict("保留后续编辑的合并已关闭")
        later = edits(self.after, now) if drift else []
        base_counts, current_counts = Counter(self.after), Counter(now)
        result = []
        cursor = 0
        for key, change in self.changes.items():
            if key not in ids:
                continue
            start, end = change.new_start, change.new_end
            offset = 0
            if drift:
                # Compare B→D and B→C on the same B coordinates. Insertion boundaries
                # are inclusive so independent ordering is never guessed.
                for edit in later:
                    if edit.end < start or (
                        edit.end == start and edit.start < edit.end and start < end
                    ):
                        offset += (edit.new_end - edit.new_start) - (edit.end - edit.start)
                    elif edit.start < end or (
                        edit.start == end and (start == end or edit.start == edit.end)
                    ):
                        raise HunkConflict("所选修改块与后续编辑重叠")
                left, right = max(0, start - 1), min(len(self.after), end + 1)
                anchors = self.after[left:start] + self.after[end:right]
                if (
                    not anchors
                    or any(base_counts[line] != 1 or current_counts[line] != 1 for line in anchors)
                    or left + offset < 0
                    or now[left + offset : right + offset] != self.after[left:right]
                ):
                    raise HunkConflict("修改块上下文重复或已变化，无法唯一定位")
            start, end = start + offset, end + offset
            if start < cursor:
                raise HunkConflict("修改块顺序无法确认")
            result.extend(now[cursor:start])
            result.extend(self.before[change.start : change.end])
            cursor = end
        result.extend(now[cursor:])
        if any(not line.endswith(b"\n") for line in result[:-1]):
            raise HunkConflict("恢复将改变相邻行的末尾换行边界")
        raw = (codecs.BOM_UTF8 if bom else b"") + b"".join(result)
        text_lines(raw)
        return raw
