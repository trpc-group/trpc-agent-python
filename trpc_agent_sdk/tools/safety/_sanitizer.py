# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret-safe evidence handling."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_EVIDENCE_CHARS = 240
REDACTED_SECRET = "[REDACTED_SECRET]"
REDACTED_PRIVATE_KEY = "[REDACTED_PRIVATE_KEY]"
_OUTPUT_KEYS = ("formatted_output", "stdout", "stderr", "output")
_TRUNCATION_MARKER = "[TRUNCATED]"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|passwd|authorization|secret)"
    r"(\s*[=:]\s*)([\"'])(.*?)\3",
    re.DOTALL,
)
_JSON_SECRET_RE = re.compile(
    r"(?i)([\"'](?:api[_-]?key|token|password|passwd|authorization|secret)[\"']\s*:\s*)"
    r"([\"'])(.*?)\2",
    re.DOTALL,
)
_NAMED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|password|passwd|authorization|secret)"
    r"(\s*[=:]\s*)"
    r"[A-Za-z0-9_./+=-]{12,}\b", )
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_COMMON_TOKEN_RE = re.compile(r"\b(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b")
_URL_USERINFO_RE = re.compile(r"(?i)(https?://[^/\s:@]+:)[^@/\s]+@")


def _redact_quoted(match: re.Match) -> str:
    quote = match.group(3)
    return f"{match.group(1)}{match.group(2)}{quote}{REDACTED_SECRET}{quote}"


def _redact_json(match: re.Match) -> str:
    quote = match.group(2)
    return f"{match.group(1)}{quote}{REDACTED_SECRET}{quote}"


class SafetySanitizer:
    """Redact secrets before limiting evidence length."""

    def __init__(self, evidence_chars: int = DEFAULT_EVIDENCE_CHARS):
        if evidence_chars <= 0:
            raise ValueError("evidence_chars must be greater than zero")
        self._evidence_chars = evidence_chars

    def sanitize(self, value: object) -> tuple[str, bool]:
        """Return safe text and whether redaction occurred."""
        text = str(value)
        redacted = False
        for pattern, replacement in (
            (_PRIVATE_KEY_RE, REDACTED_PRIVATE_KEY),
            (_URL_USERINFO_RE, rf"\1{REDACTED_SECRET}@"),
            (_BEARER_RE, f"Bearer {REDACTED_SECRET}"),
            (_JSON_SECRET_RE, _redact_json),
            (_QUOTED_SECRET_RE, _redact_quoted),
            (_NAMED_SECRET_RE, rf"\1\2{REDACTED_SECRET}"),
            (_COMMON_TOKEN_RE, REDACTED_SECRET),
        ):
            text, count = pattern.subn(replacement, text)
            redacted = redacted or count > 0
        if len(text) > self._evidence_chars:
            text = text[:self._evidence_chars] + "..."
        return text, redacted


def truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate text at a valid UTF-8 boundary."""
    if max_bytes <= 0:
        return "", bool(value)
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True


def truncate_output(value: Any, max_bytes: int) -> Any:
    """Limit common Tool output fields without changing unrelated data."""
    if isinstance(value, str):
        limited, changed = truncate_text(value, max_bytes)
        if not changed or max_bytes <= 0:
            return limited
        marker = _truncation_marker(max_bytes)
        marker_bytes = len(marker.encode("utf-8"))
        content, _ = truncate_text(value, max(max_bytes - marker_bytes, 0))
        return content + marker
    if isinstance(value, list):
        result = list(value)
        remaining = max_bytes
        marker = _truncation_marker(max_bytes)
        marker_bytes = len(marker.encode("utf-8"))
        string_bytes = sum(len(item.encode("utf-8")) for item in result if isinstance(item, str))
        reserve_marker = max_bytes > 0 and string_bytes > max_bytes
        if reserve_marker:
            remaining -= marker_bytes
        truncated = False
        for index, item in enumerate(result):
            if not isinstance(item, str):
                continue
            limited, changed = truncate_text(item, max(remaining, 0))
            result[index] = limited
            remaining -= len(limited.encode("utf-8"))
            truncated = truncated or changed
        if truncated and reserve_marker:
            result.append(marker)
        return result
    if not isinstance(value, dict):
        return value
    result = dict(value)
    was_truncated = False
    remaining = max_bytes
    keys = list(_OUTPUT_KEYS)
    keys.extend(key for key in result if key not in _OUTPUT_KEYS)
    for key in keys:
        item = result.get(key)
        if not isinstance(item, str):
            continue
        limited, changed = truncate_text(item, max(remaining, 0))
        result[key] = limited
        remaining -= len(limited.encode("utf-8"))
        was_truncated = was_truncated or changed
    if was_truncated:
        result["truncated"] = True
    return result


def _truncation_marker(max_bytes: int) -> str:
    if max_bytes >= len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER
    if max_bytes >= 3:
        return "[T]"
    if max_bytes > 0:
        return "!"
    return ""
