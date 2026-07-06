"""Redaction helpers for report-safe evidence strings."""

from __future__ import annotations

import hashlib
import re


_NAMED_SECRET_RE = re.compile(
    r"(?P<prefix>\b(?:api[_-]?(?:key|token)|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd)\b"
    r"\s*[:=]\s*[\"']?)(?P<value>[A-Za-z0-9_./+=:@-]{8,})(?P<suffix>[\"']?)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+(?P<value>[A-Za-z0-9_./+=-]{12,})", re.IGNORECASE)
_OPENAI_STYLE_RE = re.compile(r"\b(?P<value>sk-[A-Za-z0-9]{12,})\b")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _replacement(value: str) -> str:
    return f"<redacted:{_fingerprint(value)}>"


def redact_text(text: str) -> str:
    """Redact likely API keys, tokens, secrets, and passwords."""

    if not text:
        return text

    def replace_named(match: re.Match[str]) -> str:
        value = match.group("value")
        return f"{match.group('prefix')}{_replacement(value)}{match.group('suffix')}"

    def replace_bearer(match: re.Match[str]) -> str:
        value = match.group("value")
        return f"Bearer {_replacement(value)}"

    def replace_openai_style(match: re.Match[str]) -> str:
        value = match.group("value")
        return _replacement(value)

    redacted = _NAMED_SECRET_RE.sub(replace_named, text)
    redacted = _BEARER_RE.sub(replace_bearer, redacted)
    redacted = _OPENAI_STYLE_RE.sub(replace_openai_style, redacted)
    return redacted
