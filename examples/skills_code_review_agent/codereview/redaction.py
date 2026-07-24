# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Host-side secret redaction.

Wraps the canonical pattern table shared with the sandbox rule
(``skills/code-review/scripts/lib/secret_patterns.py``, loaded through
:mod:`codereview.diff_parser`). Every string is scrubbed through this
redactor BEFORE it is persisted to the database or rendered into a report
— defense in depth on top of the in-sandbox evidence redaction.
"""

from __future__ import annotations

from typing import Any
from typing import Tuple

from .diff_parser import secret_patterns

REDACTED_PLACEHOLDER = secret_patterns.REDACTED_PLACEHOLDER


class SecretRedactor:
    """Detect and scrub secrets; counts every redacted span."""

    def __init__(self) -> None:
        self.redaction_count = 0

    def redact(self, text: str) -> Tuple[str, int]:
        """Return (redacted_text, span_count) and accumulate the counter."""
        if not text:
            return text, 0
        redacted, count = secret_patterns.redact_text(text, REDACTED_PLACEHOLDER)
        self.redaction_count += count
        return redacted, count

    def redact_str(self, text: str) -> str:
        return self.redact(text)[0]

    def contains_secret(self, text: str) -> bool:
        return secret_patterns.contains_secret(text or "")

    def redact_obj(self, obj: Any) -> Any:
        """Recursively redact every string inside dicts / lists / tuples."""
        if isinstance(obj, str):
            return self.redact_str(obj)
        if isinstance(obj, dict):
            return {key: self.redact_obj(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            redacted = [self.redact_obj(item) for item in obj]
            return redacted if isinstance(obj, list) else tuple(redacted)
        return obj
