"""Conservative, non-reversible safety checks for learning payloads.

The scanner deliberately returns reason codes and never returns matched text.  It is a
domain helper: it does not persist evidence, redact files, or decide whether a candidate
is eligible for promotion.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class LearningSafetyCode(StrEnum):
    SECRET_MATERIAL = "secret_material"
    PROHIBITED_PERSONAL_DATA = "prohibited_personal_data"
    HIDDEN_UNICODE_CONTROL = "hidden_unicode_control"
    CAPABILITY_AUTHORIZATION = "capability_authorization"
    PROMPT_INJECTION = "prompt_injection"


class LearningSafetyFinding(BaseModel):
    """A sanitized finding suitable for bounded application decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: LearningSafetyCode


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|private[_ -]?key)\b"),
    re.compile(r"(?i)\b(?:password|passwd|cookie|credential|authorization)\b\s*[:=]"),
    re.compile(r"(?<![A-Za-z0-9])(?:sk|rk|ghp|github_pat)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
)
_PERSONAL_PATTERNS = (
    re.compile(r"(?i)\b(?:ssn|social security|passport|bank account|credit card)\b"),
    re.compile(r"(?:身份证|护照|银行卡|信用卡|病历|诊断|处方|工资|薪资|精确地址)"),
)
_AUTHORIZATION_PATTERNS = (
    re.compile(
        r"(?i)\b(?:allow|permit|authorize|approve|grant)\b.{0,48}\b(?:delete|deploy|publish|send|pay|execute|install)\b"
    ),
    re.compile(
        r"(?:允许|授权|批准|自动删除|自动部署|自动发布|自动发信|自动支付|自动执行|跳过确认)"
    ),
    re.compile(r"(?i)\b(?:sudo|rm\s+-rf|force[- ]push|disable safety|bypass approval)\b"),
)
_INJECTION_PATTERNS = (
    re.compile(r"(?i)\bignore\s+(?:all\s+)?previous\s+instructions\b"),
    re.compile(r"(?i)\b(?:system|developer)\s+message\s*[:：]"),
    re.compile(r"(?i)\bdo\s+not\s+tell\s+the\s+user\b"),
    re.compile(r"(?:忽略之前的指令|系统消息|开发者消息|不要告诉用户|绕过安全)"),
    re.compile(r"<\|(?:system|assistant|developer|im_start|im_end)\|>", re.IGNORECASE),
)

# Bidi overrides, isolates, zero-width characters, and word joiners.  Ordinary CJK
# punctuation and emoji are intentionally not included.
_HIDDEN_UNICODE = re.compile(
    "[\u061c\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)


def scan_learning_text(value: str) -> tuple[LearningSafetyFinding, ...]:
    """Return stable safety reasons without exposing matched content."""

    if not isinstance(value, str):
        raise TypeError("learning text must be a string")

    findings: list[LearningSafetyFinding] = []

    def add(code: LearningSafetyCode, patterns: tuple[re.Pattern[str], ...]) -> None:
        if any(pattern.search(value) for pattern in patterns):
            findings.append(LearningSafetyFinding(code=code))

    add(LearningSafetyCode.SECRET_MATERIAL, _SECRET_PATTERNS)
    add(LearningSafetyCode.PROHIBITED_PERSONAL_DATA, _PERSONAL_PATTERNS)
    add(LearningSafetyCode.CAPABILITY_AUTHORIZATION, _AUTHORIZATION_PATTERNS)
    add(LearningSafetyCode.PROMPT_INJECTION, _INJECTION_PATTERNS)
    if _HIDDEN_UNICODE.search(value):
        findings.append(LearningSafetyFinding(code=LearningSafetyCode.HIDDEN_UNICODE_CONTROL))
    return tuple(findings)


def normalize_learning_text(value: str, *, label: str, maximum: int) -> str:
    """Normalize and safety-check user-visible learning text."""

    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{label} exceeds the learning text budget")
    findings = scan_learning_text(normalized)
    if findings:
        codes = ", ".join(finding.code.value for finding in findings)
        raise ValueError(f"{label} contains prohibited learning content: {codes}")
    return normalized


def learning_safety_codes(value: str) -> tuple[LearningSafetyCode, ...]:
    """Expose only stable reason codes for digest-only blocked evidence."""

    return tuple(finding.code for finding in scan_learning_text(value))
