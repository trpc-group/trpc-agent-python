# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret detection + redaction patterns. Single source of truth for
check_secrets.py (sandbox side) and review/redaction.py (host side)."""
import hashlib
import re

SECRET_PATTERNS = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9\-]{10,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]{4,})?")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}")),
    ("url_basic_auth", re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^@\s]+@")),
    ("sensitive_assign", re.compile(
        r"(?i)(password|passwd|secret|token|api_key|apikey|secret_token|db_password)"
        r"\s*[:=]\s*\\?[\"'][^\"'\\]{6,}\\?[\"']")),
]


def find_secrets(text):
    """Return the list of raw secret substrings found in *text*."""
    found = []
    for _, pattern in SECRET_PATTERNS:
        for m in pattern.finditer(text):
            found.append(m.group(0))
    return found


def _fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact(text):
    """Replace every secret match with ***REDACTED-<sha8>*** (stable per value)."""
    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: "***REDACTED-%s***" % _fingerprint(m.group(0)), text)
    return text
