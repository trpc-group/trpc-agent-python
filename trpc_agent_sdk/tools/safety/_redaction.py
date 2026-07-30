#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Secret redaction used before evidence enters reports, logs, or tracing."""

from __future__ import annotations

import re
from typing import Iterable

_REDACTED = "[REDACTED]"
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|passwd|secret)\b(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def redact_text(text: str, *, secrets: Iterable[str] = (), max_chars: int = 240) -> tuple[str, bool]:
    """Redact known and shaped secrets, then bound retained evidence."""

    redacted = text
    changed = False
    for secret in sorted({value for value in secrets if value}, key=len, reverse=True):
        if secret in redacted:
            redacted = redacted.replace(secret, _REDACTED)
            changed = True
    for pattern in _SECRET_PATTERNS:
        if pattern.search(redacted):
            if pattern.groups >= 3:
                redacted = pattern.sub(r"\1\2" + _REDACTED, redacted)
            else:
                redacted = pattern.sub(_REDACTED, redacted)
            changed = True
    redacted = redacted.replace("\r", "\\r").replace("\n", "\\n")
    if len(redacted) > max_chars:
        redacted = f"{redacted[:max_chars - 1]}…"
        changed = True
    return redacted, changed
