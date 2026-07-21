# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redaction helpers for safety findings.

Safety findings are routinely copied into logs, traces, and JSON reports.  Keep
the sanitization at the scanner boundary so custom rules cannot accidentally
turn those outputs into a second secret store.
"""

from __future__ import annotations

import re
from typing import Any

_MAX_EVIDENCE_LENGTH = 320

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_INCOMPLETE_PRIVATE_KEY_RE = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*", re.IGNORECASE | re.DOTALL)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|token|credential)"
    r"\b\s*(?:=|:)\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)", )
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|AKIA[A-Z0-9]{12,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b", )
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://[^/@\s:]+:)([^@/\s]+)(@)")
_SENSITIVE_DICT_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|authorization|cookie|credential|password|passwd|private[_-]?key|secret|token)"
    r"(?:$|[_-])")
_PLACEHOLDER_VALUES = {"", "changeme", "dummy", "example", "none", "null", "redacted", "test", "xxx"}


def _is_sensitive_dict_key(value: Any) -> bool:
    text = str(value)
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text).lower()
    return bool(_SENSITIVE_DICT_KEY_RE.search(snake_case))


def redact_text(value: str, *, max_length: int = _MAX_EVIDENCE_LENGTH) -> str:
    """Return a bounded representation with common secret formats removed."""

    text = str(value).replace("\x00", "")
    text = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _INCOMPLETE_PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]\3", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _COMMON_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)
    text = " ".join(text.split())
    if not text:
        text = "[REDACTED]"
    if len(text) > max_length:
        text = f"{text[:max_length - 3]}..."
    return text


def redact_value(value: Any) -> Any:
    """Recursively sanitize metadata while preserving JSON-friendly shapes."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            redact_text(str(key), max_length=80): "[REDACTED]" if _is_sensitive_dict_key(key) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(type(value).__name__)


def contains_secret_literal(value: str) -> bool:
    """Return whether a string resembles a credential or private key."""

    if (_PRIVATE_KEY_RE.search(value) or _INCOMPLETE_PRIVATE_KEY_RE.search(value) or _BEARER_RE.search(value)
            or _COMMON_TOKEN_RE.search(value)):
        return True
    for match in _SECRET_ASSIGNMENT_RE.finditer(value):
        secret_value = match.group(2).strip().strip("'\"").lower()
        if secret_value not in _PLACEHOLDER_VALUES and len(secret_value) >= 6:
            return True
    return False


def contains_private_key(value: str) -> bool:
    """Return whether a string contains a PEM private-key marker."""

    return bool(_PRIVATE_KEY_RE.search(value) or _INCOMPLETE_PRIVATE_KEY_RE.search(value))


__all__ = ["contains_private_key", "contains_secret_literal", "redact_text", "redact_value"]
