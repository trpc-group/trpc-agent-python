"""Credential redaction applied before logs, reports, and database writes."""

from __future__ import annotations

import re
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)['\"]?[^\s,'\"]+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def redact_text(value: str) -> tuple[str, bool]:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", redacted)
    return redacted, redacted != value


def redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        output, changed = [], False
        for item in value:
            clean, item_changed = redact_value(item)
            output.append(clean)
            changed = changed or item_changed
        return output, changed
    if isinstance(value, dict):
        output, changed = {}, False
        for key, item in value.items():
            clean, item_changed = redact_value(item)
            output[key] = clean
            changed = changed or item_changed
        return output, changed
    return value, False
