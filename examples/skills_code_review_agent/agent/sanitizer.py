# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redaction helpers for review findings and execution artifacts."""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "[REDACTED]"
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?P<key>[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd|pwd)[A-Za-z0-9_.-]*)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?!\[REDACTED\])"
    r"(?P<value>[^'\"\s,;)}\]]{4,})"
    r"(?P=quote)")
_URL_PASSWORD_RE = re.compile(r"(?P<prefix>[a-z][a-z0-9+.-]*://[^:\s/@]+:)(?P<password>[^@\s/]+)(?P<suffix>@)",
                              re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_TOKEN_LITERAL_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|pk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{8,}|gho_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[bp]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16})\b")


def redact_text(text: str) -> str:
    """Redact common secret shapes from a text payload."""
    redacted = _PRIVATE_KEY_RE.sub(_REDACTED, text)
    redacted = _BEARER_RE.sub(f"Bearer {_REDACTED}", redacted)
    redacted = _URL_PASSWORD_RE.sub(lambda match: f"{match.group('prefix')}{_REDACTED}{match.group('suffix')}",
                                    redacted)
    redacted = _JWT_RE.sub(_REDACTED, redacted)
    redacted = _ASSIGNMENT_RE.sub(lambda match: f"{match.group('key')}{match.group('sep')}{_REDACTED}", redacted)
    return _TOKEN_LITERAL_RE.sub(_REDACTED, redacted)


def redact_mapping(value: Any) -> Any:
    """Recursively redact strings inside JSON-like structures."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [redact_mapping(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_mapping(item) for key, item in value.items()}
    return value
