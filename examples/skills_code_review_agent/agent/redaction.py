# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Sensitive information redaction — detects and masks secrets in findings and reports."""

import re
import math
from typing import Any


SENSITIVE_PATTERNS = [
    # API Keys
    (re.compile(r'(?:api[_-]?key|apikey|api_token)\s*[:=]\s*["\']?([\w\-]{8,})', re.IGNORECASE), 'api_key'),
    # Passwords
    (re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\']?([^"\'\s]{3,})["\']?', re.IGNORECASE), 'password'),
    # Tokens
    (re.compile(r'(?:token|secret[_-]?key|auth_token)\s*[:=]\s*["\']?([\w\-\.]{8,})["\']?', re.IGNORECASE), 'token'),
    # GitHub tokens
    (re.compile(r'(?:gh[pousr]_)[a-zA-Z0-9]{20,}'), 'github_token'),
    # OpenAI keys
    (re.compile(r'sk-(?:proj-)?[a-zA-Z0-9]{20,}'), 'openai_key'),
    # AWS keys
    (re.compile(r'AKIA[0-9A-Z]{16}'), 'aws_key'),
    # JWT tokens
    (re.compile(r'eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}'), 'jwt'),
]

REDACTION_TEXT = '[REDACTED]'


def _shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    length = len(s)
    entropy = 0.0
    for count in freq.values():
        p = count / length
        entropy -= p * math.log2(p)
    return entropy


def redact_text(text: str) -> str:
    """Redact sensitive information from a string."""
    result = text

    for pattern, _name in SENSITIVE_PATTERNS:
        result = pattern.sub(
            lambda m: m.group(0).replace(m.group(1), REDACTION_TEXT) if m.lastindex else REDACTION_TEXT,
            result
        )

    # Entropy-based detection for high-entropy strings (likely keys/tokens)
    words = result.split()
    for i, word in enumerate(words):
        if len(word) > 16 and _shannon_entropy(word) > 3.5:
            # Skip URLs, paths, hex strings, UUIDs, and identifiers
            w = word.strip("'\",;")
            if not any(c in word for c in '/.{}[]<>'):
                if w.isdigit() or re.fullmatch(r'[0-9a-fA-F]+', w):
                    continue  # numeric or hex string
                if re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}', w):
                    continue  # UUID
                if '-' in word[1:]:
                    continue
                words[i] = REDACTION_TEXT
    result = ' '.join(words)

    return result


def redact_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Redact sensitive data from all finding fields."""
    sensitive_fields = ['evidence', 'recommendation', 'title', 'file']
    for f in findings:
        for field in sensitive_fields:
            if field in f and isinstance(f[field], str):
                f[field] = redact_text(f[field])
    return findings


def check_sensitive(text: str) -> list[dict[str, str]]:
    """Check text for sensitive information. Returns list of detections.

    Each detection has 'type' (category name) and 'value' (the full matched text).
    Uses finditer to always return the complete match string regardless of
    whether the pattern contains capture groups.
    """
    detections: list[dict[str, str]] = []
    for pattern, name in SENSITIVE_PATTERNS:
        for m in pattern.finditer(text):
            detections.append({
                'type': name,
                'value': m.group(),
            })
    return detections
