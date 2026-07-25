"""Never persist raw credentials from a review input."""

from __future__ import annotations

import re


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?:Bearer\s+)[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"((?:password|token|api[_-]?key)\s*=\s*[\"'])[^\"']+([\"'])", re.IGNORECASE),
)


def redact(value: str) -> str:
    """Replace credential-like values before logging, reporting, or storing."""
    for pattern in _SECRET_PATTERNS:
        if pattern is _SECRET_PATTERNS[-1]:
            value = pattern.sub(r"\1[REDACTED]\2", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return value
