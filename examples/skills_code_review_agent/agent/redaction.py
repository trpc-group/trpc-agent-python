"""Secret detection and redaction helpers."""

from __future__ import annotations

import json
import re
from dataclasses import is_dataclass
from dataclasses import replace
from typing import Any


REDACTION_TOKEN = "<REDACTED>"

SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
        r"id[_-]?token|token|client[_-]?secret|secret|password|passwd|pwd)\b"
        r"(\s*[:=]\s*)(['\"]?)([^'\"()\s,;#]{8,})(\3)"
        r"(?=$|[\s,;#])"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)(://[^:\s/@]{2,}):([^@\s/]{4,})@"),
]


def contains_secret(text: str) -> bool:
    """Return whether text appears to contain a secret value."""
    return any(pattern.search(text or "") for pattern in SECRET_PATTERNS)


def redact_text(text: str) -> tuple[str, int]:
    """Redact secret values from a string and return the number of replacements."""
    if not text:
        return text, 0
    redacted = text
    total = 0

    def repl_key_value(match: re.Match[str]) -> str:
        value = match.group(4)
        if REDACTION_TOKEN in value:
            return match.group(0)
        quote = match.group(3) or ""
        return f"{match.group(1)}{match.group(2)}{quote}{REDACTION_TOKEN}{quote}"

    redacted, count = SECRET_PATTERNS[0].subn(repl_key_value, redacted)
    total += count

    for pattern in SECRET_PATTERNS[1:]:
        if pattern.pattern.startswith("(?i)(://"):
            redacted, count = pattern.subn(r"\1:" + REDACTION_TOKEN + "@", redacted)
        elif "Bearer" in pattern.pattern:
            redacted, count = pattern.subn("Bearer " + REDACTION_TOKEN, redacted)
        else:
            redacted, count = pattern.subn(REDACTION_TOKEN, redacted)
        total += count

    return redacted, total


def redact_obj(value: Any) -> tuple[Any, int]:
    """Recursively redact strings inside a JSON-like object."""
    if value is None:
        return None, 0
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        total = 0
        out = []
        for item in value:
            redacted, count = redact_obj(item)
            total += count
            out.append(redacted)
        return out, total
    if isinstance(value, tuple):
        redacted, count = redact_obj(list(value))
        return tuple(redacted), count
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            redacted_key, key_count = redact_obj(key)
            redacted_item, item_count = redact_obj(item)
            total += key_count + item_count
            out[redacted_key] = redacted_item
        return out, total
    if is_dataclass(value):
        total = 0
        updates = {}
        for key, item in value.__dict__.items():
            redacted, count = redact_obj(item)
            total += count
            updates[key] = redacted
        return replace(value, **updates), total
    return value, 0


def redact_json_text(value: Any) -> tuple[str, int]:
    """Return redacted pretty JSON for a JSON-like value."""
    redacted, count = redact_obj(value)
    return json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True), count
