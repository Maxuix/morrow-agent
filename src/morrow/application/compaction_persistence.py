"""Durable encoding for the Pi-style compaction projection.

The operational store already has an immutable, range-validated context-checkpoint
repository.  Compaction entries use that repository with a distinct codec, so the
authoritative ConversationLog schema and migration history remain unchanged.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib
from collections.abc import Iterable

from morrow.core.compaction import COMPACTION_ENTRY_MAX_BYTES, CompactionEntry
from morrow.core.context import ContextCheckpoint, ContextCheckpointSection
from morrow.core.domain import DurableConversationRecord, canonical_json_bytes, sha256_digest

COMPACTION_CHECKPOINT_CODEC = "pi_compaction"
COMPACTION_CHECKPOINT_METHOD_VERSION = "v2"
_COMPACTION_SECTION_PREFIX = "pi_compaction_entry_"
_COMPACTION_ENCODING_PREFIX = "zlib-base64-v1:"
_COMPACTION_SECTION_BYTES = 7_000
_COMPACTION_CHECKPOINT_PAYLOAD_BYTES = 24 * 1024


def compaction_checkpoint_id(entry: CompactionEntry) -> str:
    """Derive a collision-resistant checkpoint ID without persisting another random ID."""

    suffix = entry.entry_id.removeprefix("cmp_")
    candidate = f"chk_cmp_{suffix}"
    if len(candidate) <= 128:
        return candidate
    return f"chk_cmp_{sha256_digest(entry.entry_id)[:48]}"


def _encoded_entry(entry: CompactionEntry) -> tuple[bytes, str]:
    raw = canonical_json_bytes(entry.model_dump(mode="json"))
    if len(raw) > COMPACTION_ENTRY_MAX_BYTES:
        raise ValueError("compaction entry exceeds its durable budget")
    compressed = zlib.compress(raw, level=9)
    encoded = _COMPACTION_ENCODING_PREFIX + base64.b64encode(compressed).decode("ascii")
    if len(encoded.encode("ascii")) > _COMPACTION_CHECKPOINT_PAYLOAD_BYTES:
        raise ValueError("compaction entry cannot fit the checkpoint budget")
    return raw, encoded


def checkpoint_for_compaction(
    workspace_id: str,
    entry: CompactionEntry,
    records: Iterable[DurableConversationRecord],
) -> ContextCheckpoint:
    """Build a range-validated checkpoint carrying one immutable compaction entry."""

    raw, encoded = _encoded_entry(entry)
    by_position = {record.conversation_position: record for record in records}
    source_start = by_position.get(entry.source_start_sequence)
    source_end = by_position.get(entry.source_end_sequence)
    if source_start is None or source_end is None:
        raise ValueError("compaction source range is not durably present")
    if source_end.kind == "message":
        payload_role = source_end.payload.get("role")
        if payload_role != "tool":
            raise ValueError("compaction source range must end at a closed ToolCycle")
    elif source_end.kind != "terminal":
        raise ValueError("compaction source range is not durably closed")
    chunks = tuple(
        encoded[offset : offset + _COMPACTION_SECTION_BYTES]
        for offset in range(0, len(encoded), _COMPACTION_SECTION_BYTES)
    )
    source_end_position = entry.source_end_sequence + 1
    sections = tuple(
        ContextCheckpointSection(
            kind=f"{_COMPACTION_SECTION_PREFIX}{index:04d}",
            content=chunk,
            source_start_position=entry.source_start_sequence,
            source_end_position=source_end_position,
        )
        for index, chunk in enumerate(chunks)
    )
    return ContextCheckpoint(
        checkpoint_id=compaction_checkpoint_id(entry),
        workspace_id=workspace_id,
        session_id=entry.session_id,
        task_run_id=entry.task_run_id,
        source_agent_run_id=entry.agent_run_id,
        codec=COMPACTION_CHECKPOINT_CODEC,
        method_version=COMPACTION_CHECKPOINT_METHOD_VERSION,
        source_start_record_id=source_start.record_id,
        source_start_position=entry.source_start_sequence,
        source_end_record_id=source_end.record_id,
        source_end_position=source_end_position,
        sections=sections,
        input_bytes=len(raw),
        output_bytes=len(encoded.encode("ascii")),
        request_estimate_chars=len(encoded),
        created_at=entry.created_at,
    )


def entry_from_compaction_checkpoint(checkpoint: ContextCheckpoint) -> CompactionEntry:
    """Decode and strictly validate one stored compaction checkpoint."""

    if (
        checkpoint.codec != COMPACTION_CHECKPOINT_CODEC
        or checkpoint.method_version != COMPACTION_CHECKPOINT_METHOD_VERSION
    ):
        raise ValueError("checkpoint is not a v2 Pi compaction entry")
    expected_prefix = _COMPACTION_SECTION_PREFIX
    indexed: list[tuple[int, str]] = []
    for section in checkpoint.sections:
        if not section.kind.startswith(expected_prefix):
            raise ValueError("compaction checkpoint contains an unexpected section")
        try:
            index = int(section.kind.removeprefix(expected_prefix))
        except ValueError as exc:
            raise ValueError("compaction checkpoint section index is invalid") from exc
        indexed.append((index, section.content))
    indexed.sort()
    if not indexed or [index for index, _content in indexed] != list(range(len(indexed))):
        raise ValueError("compaction checkpoint sections are incomplete")
    encoded = "".join(content for _index, content in indexed)
    if not encoded.startswith(_COMPACTION_ENCODING_PREFIX):
        raise ValueError("compaction checkpoint encoding is invalid")
    try:
        compressed = base64.b64decode(
            encoded.removeprefix(_COMPACTION_ENCODING_PREFIX), validate=True
        )
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, COMPACTION_ENTRY_MAX_BYTES + 1)
        raw += decompressor.flush()
        if (
            len(raw) > COMPACTION_ENTRY_MAX_BYTES
            or decompressor.unused_data
            or decompressor.unconsumed_tail
        ):
            raise ValueError("compaction checkpoint payload is oversized")
        entry = CompactionEntry.model_validate_json(raw, strict=True)
    except (binascii.Error, TypeError, ValueError, json.JSONDecodeError, zlib.error) as exc:
        raise ValueError("compaction checkpoint payload is invalid") from exc
    if (
        entry.session_id != checkpoint.session_id
        or entry.source_start_sequence != checkpoint.source_start_position
        or entry.source_end_sequence + 1 != checkpoint.source_end_position
    ):
        raise ValueError("compaction checkpoint boundary is inconsistent")
    return entry


def compaction_entries_from_checkpoints(
    checkpoints: Iterable[ContextCheckpoint],
) -> tuple[CompactionEntry, ...]:
    entries = [
        entry_from_compaction_checkpoint(checkpoint)
        for checkpoint in checkpoints
        if checkpoint.codec == COMPACTION_CHECKPOINT_CODEC
    ]
    entries.sort(key=lambda entry: (entry.source_end_sequence, entry.entry_id))
    return tuple(entries)


__all__ = [
    "COMPACTION_CHECKPOINT_CODEC",
    "COMPACTION_CHECKPOINT_METHOD_VERSION",
    "checkpoint_for_compaction",
    "compaction_checkpoint_id",
    "compaction_entries_from_checkpoints",
    "entry_from_compaction_checkpoint",
]
