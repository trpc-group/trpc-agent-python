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
    (re.compile(r"\brk_(?:live|test)_[A-Za-z0-9]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]{3,}(@)"), r"\1[REDACTED]\2"),
    (re.compile(r"(?i)\b(basic\s+)[A-Za-z0-9+/=]{12,}"), r"\1[REDACTED]"),
    (re.compile(r"(?im)^(\s*[+ -]?\s*[A-Za-z0-9_]*(?:api[_-]?key|token|secret|password)[A-Za-z0-9_]*\s*[:=]\s*)"
                r"(?P<value>[^\r\n#;,\\]{6,}(?:\\\r?\n\s*[+ -]?\s*[^\r\n#;,]{2,})*)"), r"\1[REDACTED]"),
    (re.compile(r"(?im)^(\s*[+ -]?\s*(?:passwd|pwd)\s*[:=]\s*)"
                r"(?P<value>[^\r\n#;,\\]{6,}(?:\\\r?\n\s*[+ -]?\s*[^\r\n#;,]{2,})*)"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|token|secret|password)[A-Za-z0-9_]*)\s*[:=]\s*"
            r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*')"
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)\b(passwd|pwd)\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*')"), r"\1=[REDACTED]"),
    (re.compile(
        r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|token|secret|password)[A-Za-z0-9_]*)\s*[:=]\s*['\"]?[^'\"\s,;]{6,}['\"]?"),
     r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]{6,}['\"]?"), r"\1=[REDACTED]"),
]

UUID_PATTERN = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
HEX_IDENTIFIER_PATTERN = re.compile(r"(?i)^[0-9a-f]{32,64}$")
BASE64_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9+/_-]{32,64}={0,2}$")
HYPHENATED_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9]{4,}(?:-[A-Za-z0-9]{4,}){2,}$")
BASE64_CONTEXT_PATTERN = re.compile(r"(?i)\b(base64|fixture|blob|encoded)\b")
SENSITIVE_LITERAL_CONTEXT_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key(?:[_-]?id)?|key[_-]?id|secret|token|password|passwd|pwd|"
    r"private[_-]?key|signing[_-]?key|session[_-]?key)")


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
        re.compile(r"\brk_(?:live|test)_[A-Za-z0-9]{16,}\b"),
        re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]{3,}@"),
        re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{12,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"super-secret-password"),
    ]
    return any(pattern.search(text) for pattern in direct_secret_patterns)


def _redact_high_entropy_literals(text: str) -> tuple[str, int]:
    pattern = re.compile(r"(['\"])(?P<value>[A-Za-z0-9_+/=-]{28,})\1")
    redaction_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal redaction_count
        value = match.group("value")
        context = text[max(0, match.start() - 80):match.start()]
        if _looks_like_high_entropy_secret(value, context=context):
            redaction_count += 1
            return f"{match.group(1)}[REDACTED]{match.group(1)}"
        return match.group(0)

    return pattern.sub(replace, text), redaction_count


def _looks_like_high_entropy_secret(value: str, *, context: str = "") -> bool:
    if len(value) < 28:
        return False
    sensitive_context = bool(SENSITIVE_LITERAL_CONTEXT_PATTERN.search(context))
    if value.lower().startswith(("http", "pytest", "example")) and not sensitive_context:
        return False
    if _looks_like_allowed_identifier(value, context=context) and not sensitive_context:
        return False
    alphabet = set(value)
    if len(alphabet) < 12:
        return False
    entropy = -sum((value.count(ch) / len(value)) * math.log2(value.count(ch) / len(value)) for ch in alphabet)
    if sensitive_context:
        return entropy >= 4.0
    return entropy >= 4.6


def _looks_like_allowed_identifier(value: str, *, context: str = "") -> bool:
    """Keep common non-secret identifiers readable in review evidence."""
    if UUID_PATTERN.fullmatch(value):
        return True
    if HEX_IDENTIFIER_PATTERN.fullmatch(value):
        return True
    if BASE64_IDENTIFIER_PATTERN.fullmatch(value):
        base64_value = value.rstrip("=")
        has_base64_specific_character = any(character in base64_value for character in "+/_-")
        has_base64_context = bool(BASE64_CONTEXT_PATTERN.search(context))
        has_known_secret_prefix = base64_value.startswith(("sk", "rk", "xox", "AIza"))
        if (has_base64_specific_character or "=" in value or has_base64_context) and not has_known_secret_prefix:
            return True
    if 28 <= len(value) <= 80 and HYPHENATED_IDENTIFIER_PATTERN.fullmatch(value):
        return True
    return False
