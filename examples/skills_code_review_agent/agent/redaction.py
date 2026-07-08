"""Secret redaction helpers used before reporting or persistence."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass


@dataclass(slots=True)
class RedactionResult:
    text: str
    count: int


SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1[REDACTED]"),
    (re.compile(
        r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|token|secret|password)[A-Za-z0-9_]*)\s*[:=]\s*['\"]?[^'\"\s,;]{6,}['\"]?"),
     r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]{6,}['\"]?"), r"\1=[REDACTED]"),
]


def redact_text(text: str | None) -> RedactionResult:
    if not text:
        return RedactionResult("", 0)
    out = text
    count = 0
    for pattern, replacement in SECRET_PATTERNS:
        out, n = pattern.subn(replacement, out)
        count += n
    out, entropy_count = _redact_high_entropy_literals(out)
    count += entropy_count
    return RedactionResult(out, count)


def contains_unredacted_secret(text: str | None) -> bool:
    if not text:
        return False
    direct_secret_patterns = [
        re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"super-secret-password"),
    ]
    return any(pattern.search(text) for pattern in direct_secret_patterns)


def _redact_high_entropy_literals(text: str) -> tuple[str, int]:
    pattern = re.compile(r"(['\"])(?P<value>[A-Za-z0-9_+/=-]{28,})\1")

    def replace(match: re.Match[str]) -> str:
        value = match.group("value")
        if _looks_like_high_entropy_secret(value):
            return f"{match.group(1)}[REDACTED]{match.group(1)}"
        return match.group(0)

    return pattern.subn(replace, text)


def _looks_like_high_entropy_secret(value: str) -> bool:
    if len(value) < 28:
        return False
    if value.lower().startswith(("http", "pytest", "example")):
        return False
    alphabet = set(value)
    if len(alphabet) < 12:
        return False
    entropy = -sum((value.count(ch) / len(value)) * math.log2(value.count(ch) / len(value)) for ch in alphabet)
    return entropy >= 4.2
