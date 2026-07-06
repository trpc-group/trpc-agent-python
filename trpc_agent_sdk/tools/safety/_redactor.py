# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret redaction for safety scan reports.

Scans text for API keys, tokens, passwords, and private key material,
replacing them with ``[REDACTED_SECRET]``.
"""

from __future__ import annotations

import re

# Patterns that alone are strong enough to flag a secret.
_SECRET_VALUE_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{12,}|"
                              r"ghp_[A-Za-z0-9_]{12,}|"
                              r"xox[baprs]-[A-Za-z0-9-]{10,}|"
                              r"-----BEGIN [A-Z ]*PRIVATE KEY-----)")

# Name=value patterns that look like credentials.
_SECRET_NAME_RE = re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|private[_-]?key|credential)")
_SECRET_NV_RE = re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|private[_-]?key|credential)"
                           r"=([^ \t\n\r;&|]+)")

_REDACTED = "[REDACTED_SECRET]"


class Redactor:
    """Detects and redacts secrets from scan report fields."""

    def __init__(self) -> None:
        self.changed = False

    def redact(self, text: str) -> str:
        """Replace secrets in *text*; return redacted copy."""
        orig = text
        text = _SECRET_VALUE_RE.sub(_REDACTED, text)
        text = _SECRET_NV_RE.sub(r"\1=" + _REDACTED, text)
        if text != orig:
            self.changed = True
        return text

    def looks_sensitive(self, text: str) -> bool:
        """Return True if *text* appears to contain a secret."""
        if _SECRET_VALUE_RE.search(text):
            return True
        return bool(_SECRET_NAME_RE.search(text) and "=" in text)
