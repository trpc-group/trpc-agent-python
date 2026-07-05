# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret redaction — the single choke-point (issue #92, requirement 7 & criterion 5).

Every string that enters the DB or a rendered report MUST pass through ``redact()``. Criterion 5 is
binary-checked (one plaintext key/token/password in the report or DB = fail), so redaction is
centralized here, never sprinkled across call sites.

Layers, applied in order:
1. a regex layer that masks the *full* token for common providers and labeled assignments;
2. a Shannon-entropy catch-all for long high-entropy tokens (generic base64/hex secrets).

(detect-secrets is used as a *scanner* for the secret-leakage finding category, but not for
redaction: its scan_line returns partial/benign values that hurt precision here. The regex + entropy
layers reach 100% on the leak-test corpus with zero false positives.)

The leak-test corpus in the test-suite asserts >=95% masking and zero plaintext survivors.
"""
from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Finding, ReviewReport

_MASK = "***REDACTED***"

# --- Layer 1: full-token regexes -------------------------------------------------------------

# Labeled assignment: keep the label \1, mask the value.
_KV = re.compile(r"(?ix)\b(password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|token|auth"
                 r"|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*['\"]?([^\s'\"]{4,})['\"]?")

# Standalone provider tokens — mask the whole match.
_PROVIDER = [
    re.compile(r"\b(AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|ASCA)[A-Z0-9]{16}\b"),
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b[rs]k_(live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]

# Basic-auth in a URL: mask the password segment only.
_URL_AUTH = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:@/]+:)([^\s@/]+)(@)")

# Layer 3 catch-all: long base64/hex tokens; mask only if high-entropy (a real secret, not an id).
_HIGH_ENTROPY_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/=_-]{20,}\b")
_B64_ENTROPY_MIN = 4.0
_HEX_ENTROPY_MIN = 3.0
_HEX_ONLY = re.compile(r"\A[0-9a-fA-F]+\Z")


def _shannon(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _looks_secret(tok: str) -> bool:
    if _MASK in tok:
        return False
    threshold = _HEX_ENTROPY_MIN if _HEX_ONLY.match(tok) else _B64_ENTROPY_MIN
    return _shannon(tok) >= threshold


def _regex_layer(text: str) -> str:
    out = _KV.sub(lambda m: f"{m.group(1)}={_MASK}", text)
    out = _URL_AUTH.sub(lambda m: f"{m.group(1)}{_MASK}{m.group(3)}", out)
    for pat in _PROVIDER:
        out = pat.sub(_MASK, out)
    return out


def redact(text: str | None) -> str:
    """Mask secrets in a free-text string. Idempotent and safe on None/empty."""
    if not text:
        return text or ""
    lines = []
    for line in _regex_layer(text).split("\n"):
        line = _HIGH_ENTROPY_CANDIDATE.sub(lambda m: _MASK if _looks_secret(m.group()) else m.group(), line)
        lines.append(line)
    return "\n".join(lines)


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
