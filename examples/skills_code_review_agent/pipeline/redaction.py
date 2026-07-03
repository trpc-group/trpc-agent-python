# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Secret redaction — the single choke-point (issue #92, requirement 7 & criterion 5).

Every string that enters the DB or a rendered report MUST pass through ``redact()``. Centralizing
it here is the load-bearing decision: criterion 5 is binary-checked (one plaintext key/token/
password in the report or DB = fail), so redaction can never be sprinkled across call sites.

MVP: regex baseline covering the common shapes. Slice 4 hardens this with ``detect-secrets`` spans
and a leak-test corpus proving >=95% and zero plaintext survivors.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Finding, ReviewReport

_MASK = "***REDACTED***"

# Keeps a leading label group \1 and masks the secret value \2.
_KV = (r"(?ix)\b(password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|token|auth"
       r"|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*['\"]?([^\s'\"]{4,})['\"]?")

_STANDALONE = [
    ("aws_access_key_id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
]

_KV_RE = re.compile(_KV)


def redact(text: str | None) -> str:
    """Mask secrets in a free-text string. Idempotent and safe on None/empty."""
    if not text:
        return text or ""
    out = _KV_RE.sub(lambda m: f"{m.group(1)}={_MASK}", text)
    for _name, pat in _STANDALONE:
        out = pat.sub(_MASK, out)
    return out


def redact_finding(f: "Finding") -> "Finding":
    """Return a copy of the finding with secret-bearing fields masked."""
    return f.model_copy(update={
        "evidence": redact(f.evidence),
        "title": redact(f.title),
        "recommendation": redact(f.recommendation),
    })


def redact_report(report: "ReviewReport") -> "ReviewReport":
    """Mask every finding in a report (defense in depth — findings are already redacted on entry)."""
    return report.model_copy(
        update={
            "findings": [redact_finding(f) for f in report.findings],
            "human_review": [redact_finding(f) for f in report.human_review],
        })
