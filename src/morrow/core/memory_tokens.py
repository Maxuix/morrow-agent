"""Pure, bounded lexical tokenization for Project Knowledge retrieval."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from morrow.core.memory_selection import MemorySearchTokenKind, MemorySearchWeightBand
from morrow.core.models import ProtocolModel

MEMORY_TOKEN_MAX_CHARS = 128
MEMORY_RECORD_TOKEN_LIMIT = 96
MEMORY_QUERY_TOKEN_LIMIT = 64
MEMORY_TOKEN_INPUT_MAX_CHARS = 8_192

_QUERY_KIND_RANK = {
    MemorySearchTokenKind.IDENTIFIER: 0,
    MemorySearchTokenKind.PATH: 1,
    MemorySearchTokenKind.WORD: 2,
    MemorySearchTokenKind.NUMBER: 3,
    MemorySearchTokenKind.CJK_BIGRAM: 4,
}

_STOP_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "please",
        "that",
        "the",
        "this",
        "to",
        "use",
        "using",
        "we",
        "what",
        "when",
        "why",
        "with",
        "you",
        "以及",
        "如何",
        "和",
        "在",
        "是",
        "的",
        "请",
    }
)
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+")
_PATH_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9._~-]+[/\\])+[A-Za-z0-9._~-]+")
_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*")
_WORD_TOKEN = re.compile(r"[A-Za-z]+")
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:v\d+(?:[._-]\d+)*|\d+(?:[._-]\d+)+|\d{2,})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENTIFIER_PARTS = re.compile(r"[._/\\\-\s]+")


class MemoryToken(ProtocolModel):
    """One normalized lexical token with a coarse, non-public weight band."""

    token_kind: MemorySearchTokenKind
    token: str = Field(min_length=1, max_length=MEMORY_TOKEN_MAX_CHARS)
    weight_band: MemorySearchWeightBand

    @field_validator("token")
    @classmethod
    def normalized_token(cls, value: str) -> str:
        normalized = value.casefold().strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("memory token must be one non-whitespace token")
        return normalized


_WEIGHT_RANK = {
    MemorySearchWeightBand.HIGH: 3,
    MemorySearchWeightBand.MEDIUM: 2,
    MemorySearchWeightBand.LOW: 1,
}


class _TokenCollector:
    def __init__(self, limit: int) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= MEMORY_RECORD_TOKEN_LIMIT:
            raise ValueError("memory token limit is invalid")
        self.limit = limit
        self.tokens: dict[tuple[MemorySearchTokenKind, str], MemoryToken] = {}

    def add(
        self,
        token_kind: MemorySearchTokenKind,
        value: str,
        weight_band: MemorySearchWeightBand,
    ) -> None:
        normalized = value.casefold().strip()
        if not normalized or len(normalized) > MEMORY_TOKEN_MAX_CHARS:
            return
        try:
            token = MemoryToken(
                token_kind=token_kind,
                token=normalized,
                weight_band=weight_band,
            )
        except ValueError:
            return
        key = (token.token_kind, token.token)
        existing = self.tokens.get(key)
        if existing is None or _WEIGHT_RANK[token.weight_band] > _WEIGHT_RANK[existing.weight_band]:
            self.tokens[key] = token

    def finish(self, *, prioritize_non_cjk: bool = False) -> tuple[MemoryToken, ...]:
        kind_rank = _QUERY_KIND_RANK if prioritize_non_cjk else None
        return tuple(
            sorted(
                self.tokens.values(),
                key=lambda token: (
                    -_WEIGHT_RANK[token.weight_band],
                    kind_rank.get(token.token_kind, 99) if kind_rank else token.token_kind.value,
                    token.token_kind.value,
                    token.token,
                ),
            )[: self.limit]
        )


def tokenize_memory_text(
    value: str,
    *,
    weight_band: MemorySearchWeightBand = MemorySearchWeightBand.LOW,
    limit: int = MEMORY_RECORD_TOKEN_LIMIT,
) -> tuple[MemoryToken, ...]:
    """Tokenize text without filesystem, network, locale, or extension behavior."""

    if not isinstance(value, str):
        raise TypeError("memory token input must be text")
    if len(value) > MEMORY_TOKEN_INPUT_MAX_CHARS:
        raise ValueError("memory token input exceeds its bounded text budget")
    collector = _TokenCollector(limit)
    _add_text(collector, value, weight_band)
    return collector.finish()


def tokenize_memory_record(
    *,
    semantic_key: str,
    category: str,
    statement: str,
    limit: int = MEMORY_RECORD_TOKEN_LIMIT,
) -> tuple[MemoryToken, ...]:
    """Tokenize the three bounded, authority-owned Project Knowledge fields."""

    for value in (semantic_key, category, statement):
        if not isinstance(value, str):
            raise TypeError("memory record token input must be text")
        if len(value) > MEMORY_TOKEN_INPUT_MAX_CHARS:
            raise ValueError("memory record token input exceeds its bounded text budget")
    collector = _TokenCollector(limit)
    _add_identifier(collector, semantic_key, MemorySearchWeightBand.HIGH)
    _add_text(collector, category, MemorySearchWeightBand.MEDIUM)
    _add_text(collector, statement, MemorySearchWeightBand.LOW)
    return collector.finish()


def tokenize_memory_query(
    value: str, *, limit: int = MEMORY_QUERY_TOKEN_LIMIT
) -> tuple[MemoryToken, ...]:
    """Tokenize a bounded foreground query using only stable lexical rules."""

    if not isinstance(value, str):
        raise TypeError("memory token input must be text")
    if len(value) > MEMORY_TOKEN_INPUT_MAX_CHARS:
        raise ValueError("memory token input exceeds its bounded text budget")
    collector = _TokenCollector(limit)
    _add_text(collector, value, MemorySearchWeightBand.MEDIUM)
    return collector.finish(prioritize_non_cjk=True)


def _add_text(collector: _TokenCollector, value: str, weight_band: MemorySearchWeightBand) -> None:
    for match in _CJK_RUN.finditer(value):
        _add_cjk_bigrams(collector, match.group(0), weight_band)
    for match in _PATH_TOKEN.finditer(value):
        normalized_path = match.group(0).replace("\\", "/")
        collector.add(MemorySearchTokenKind.PATH, normalized_path, weight_band)
        for part in _split_identifier(normalized_path):
            _add_word(collector, part, weight_band)
    for match in _IDENTIFIER_TOKEN.finditer(value):
        _add_identifier(collector, match.group(0), weight_band)
    for match in _NUMBER_TOKEN.finditer(value):
        number = match.group(0).casefold()
        if any(character.isdigit() for character in number):
            collector.add(MemorySearchTokenKind.NUMBER, number, weight_band)
    for match in _WORD_TOKEN.finditer(value):
        _add_word(collector, match.group(0), weight_band)


def _add_identifier(
    collector: _TokenCollector, value: str, weight_band: MemorySearchWeightBand
) -> None:
    normalized = value.casefold().strip()
    if not normalized:
        return
    parts = _split_identifier(value)
    is_compound = len(parts) > 1 or bool(_CAMEL_BOUNDARY.search(value))
    if is_compound:
        collector.add(MemorySearchTokenKind.IDENTIFIER, normalized, weight_band)
    for part in parts:
        _add_word(collector, part, weight_band)


def _add_word(collector: _TokenCollector, value: str, weight_band: MemorySearchWeightBand) -> None:
    normalized = value.casefold().strip()
    if normalized and not normalized.isdigit() and normalized not in _STOP_TOKENS:
        collector.add(MemorySearchTokenKind.WORD, normalized, weight_band)


def _add_cjk_bigrams(
    collector: _TokenCollector, value: str, weight_band: MemorySearchWeightBand
) -> None:
    normalized = value.casefold()
    for index in range(max(0, len(normalized) - 1)):
        collector.add(
            MemorySearchTokenKind.CJK_BIGRAM,
            normalized[index : index + 2],
            weight_band,
        )


def _split_identifier(value: str) -> tuple[str, ...]:
    camel_split = _CAMEL_BOUNDARY.sub(" ", value)
    return tuple(
        part.casefold()
        for part in _IDENTIFIER_PARTS.split(camel_split)
        if part and part.casefold() not in _STOP_TOKENS
    )


__all__ = [
    "MEMORY_QUERY_TOKEN_LIMIT",
    "MEMORY_RECORD_TOKEN_LIMIT",
    "MEMORY_TOKEN_INPUT_MAX_CHARS",
    "MEMORY_TOKEN_MAX_CHARS",
    "MemoryToken",
    "tokenize_memory_query",
    "tokenize_memory_record",
    "tokenize_memory_text",
]
