# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redaction helpers for safety reports and audit events."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

DEFAULT_MAX_EVIDENCE_CHARS = 200
REDACTION_MARKER = "[REDACTED]"
TRUNCATION_SUFFIX = "...[truncated]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)\b"
        r"\s*[:=]\s*['\"]?[^'\"\s,;]+"),
)


def contains_secret(value: Any) -> bool:
    """Return True when text contains a high-signal secret-like pattern."""

    text = str(value or "")
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _redact_assignment(match: re.Match[str]) -> str:
    lower = match.group(0).lower()
    if lower.startswith("bearer"):
        return f"Bearer {REDACTION_MARKER}"
    key_match = re.match(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)\b",
        match.group(0))
    if key_match:
        return f"{key_match.group(1)}={REDACTION_MARKER}"
    return REDACTION_MARKER


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(TRUNCATION_SUFFIX):
        return text[:max_chars]
    return f"{text[:max_chars - len(TRUNCATION_SUFFIX)]}{TRUNCATION_SUFFIX}"


def redact_text(value: Any, *,
                max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS) -> str:
    """Redact secret-like content and truncate the result."""

    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_redact_assignment, text)
    return _truncate(text, max_chars)


def redact_evidence(value: Any, *,
                    max_chars: int = DEFAULT_MAX_EVIDENCE_CHARS) -> str:
    """Redact a report evidence snippet."""

    return redact_text(value, max_chars=max_chars)


def redact_env(env: Mapping[str, Any] | None) -> dict[str, str]:
    """Return env keys with values removed for audit-safe reporting."""

    if not env:
        return {}
    return {str(key): REDACTION_MARKER for key in env}
