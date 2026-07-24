# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for secret detection and redaction (>=95% detection target)."""
SAMPLE_SECRETS = [
    'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"',
    'key = "sk-ant-api03-abcdefghijklmnopqrst"',
    "aws AKIAIOSFODNN7EXAMPLE",
    'token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"',
    "Authorization: Bearer abc123def456ghi789jkl",
    "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456",
    "-----BEGIN RSA PRIVATE KEY-----",
    'password = "hunter2secret"',
    'PASSWD: "topsecretvalue"',
    'secret = "do-not-leak-me"',
    'api_key="deadbeefcafe1234"',
    "https://user:p4ssw0rd@db.example.com/prod",
    'TOKEN = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"',
    'apikey: "0123456789abcdefffff"',
    "Bearer xoxb-1234567890-abcdefghij",
    'DB_PASSWORD = "prod-db-pass-2026"',
    'passwd = "another$ecret!"',
    "AKIA0123456789ABCDEF",
    'secret_token = "veryhiddenvalue1"',
    'ghs_abcdefghijklmnopqrstuvwxyz0123456789',
]


def test_detection_rate_at_least_95_percent():
    from review.redaction import contains_secret
    detected = sum(1 for s in SAMPLE_SECRETS if contains_secret(s))
    assert detected / len(SAMPLE_SECRETS) >= 0.95


def test_redaction_removes_secret_values():
    from review.redaction import redact_text
    for sample in SAMPLE_SECRETS:
        out = redact_text(sample)
        assert "***REDACTED-" in out or sample == out, sample
    assert "hunter2secret" not in redact_text('password = "hunter2secret"')
    assert "AKIAIOSFODNN7EXAMPLE" not in redact_text("aws AKIAIOSFODNN7EXAMPLE")


def test_redaction_is_stable_fingerprint():
    from review.redaction import redact_text
    a = redact_text("Bearer abc123def456ghi789jkl")
    b = redact_text("Bearer abc123def456ghi789jkl")
    assert a == b


def test_plain_text_untouched():
    from review.redaction import redact_text
    text = "def add(a, b):\n    return a + b"
    assert redact_text(text) == text
