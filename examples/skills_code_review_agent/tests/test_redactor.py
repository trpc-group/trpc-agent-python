# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redaction acceptance: >= 95% detection over 20+ secret formats, allowlist intact."""

from __future__ import annotations

from review_agent.redactor import MASK, Redactor

# Fake tokens whose format is scanned by GitHub push protection are split
# across concatenations so this file never contains a full-format literal;
# the runtime values still match the real credential shapes.
_AWS_KEY = "AKIA" + "IOSFODNN7REALQ2Z"
_AWS_SECRET = "aBcDeFgHiJkLmNoPqRsTuVwXyZ" + "0123456789/+aB"
_SLACK_BOT = "xoxb-" + "123456789012-ABCDEFabcdef"
_STRIPE = "sk_live_" + "a1B2c3D4e5F6g7H8i9J0k1L2"
_GOOGLE = "AIzaSy" + "A1234567890abcdefghijklmnopqrstuv"
_OPENAI = "sk-proj-" + "abcdefghij1234567890ABCDEFGHIJ"
_ANTHROPIC = "sk-ant-" + "api03-abcdefghij1234567890"
_NPM = "npm_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
_PYPI = "pypi-" + "AgEIcHlwaS5vcmcCJDUwNjYxY"
_SENDGRID = "SG." + "a1B2c3D4e5F6g7H8i9J0.k1L2m3N4o5P6q7R8s9T0u1V2w3X4y5Z6a7B8c9D0"
_TWILIO = "SK" + "0123456789abcdef0123456789abcdef"

# (label, sample line, secret substring that must disappear)
SECRET_SAMPLES = [
    ("aws_key_id", f'key = "{_AWS_KEY}"', _AWS_KEY),
    ("aws_secret", f'aws_secret = "{_AWS_SECRET}"', _AWS_SECRET[:26]),
    ("github_pat_classic", 'token = "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"', "ghp_a1B2c3D4"),
    ("github_pat_fine", 'token = "github_pat_11AAAAAAA0aaaaaaaaaaaaaa"', "github_pat_11AAAAAAA0"),
    ("gitlab_pat", 'token = "glpat-AbCdEfGhIjKlMnOpQrSt"', "glpat-AbCdEfGh"),
    ("slack_bot", f'slack = "{_SLACK_BOT}"', _SLACK_BOT[:17]),
    ("stripe_live", f'stripe = "{_STRIPE}"', _STRIPE[:16]),
    ("google_api", f'g = "{_GOOGLE}"', _GOOGLE[:17]),
    ("openai", f'k = "{_OPENAI}"', _OPENAI[:18]),
    ("anthropic", f'k = "{_ANTHROPIC}"', _ANTHROPIC[:12]),
    ("npm", f'n = "{_NPM}"', _NPM[:12]),
    ("pypi", f'p = "{_PYPI}"', _PYPI[:20]),
    ("sendgrid", f'sg = "{_SENDGRID}"', _SENDGRID[:11]),
    ("twilio", f'tw = "{_TWILIO}"', _TWILIO[:18]),
    ("telegram", 't = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"', "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"),
    ("jwt", 'j = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4"',
     "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
    ("pem_block", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----",
     "MIIEpAIBAAKCAQEA7"),
    ("url_password", 'dsn = "postgres://svc:Sup3rS3cret@db.prod:5432/app"', "Sup3rS3cret"),
    ("azure", 'c = "DefaultEndpointsProtocol=https;AccountKey=abcdefghijklmnopqrstuvwxyz0123456789ABCD+/=="',
     "abcdefghijklmnopqrstuvwxyz0123456789ABCD"),
    ("basic_auth", "headers = {'Authorization': 'Basic dXNlcjpwYXNzd29yZA=='}", "dXNlcjpwYXNzd29yZA=="),
    ("bearer", "headers = {'Authorization': 'Bearer ya29.a0AfH6SMBx1234567890'}", "ya29.a0AfH6SMBx1234567890"),
    ("generic_password", 'password = "N0tAPlaceholder42"', "N0tAPlaceholder42"),
    ("generic_api_key", "api_key: 'q9w8e7r6t5y4u3i2o1p0'", "q9w8e7r6t5y4u3i2o1p0"),
]

ALLOWLIST_SAMPLES = [
    'key = "AKIAIOSFODNN7EXAMPLE"',
    'secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"',
    'password = "changeme-example"',
    'api_key = "your-api-key-here"',
    'token = "${GITHUB_TOKEN}"',
    'password = "xxxxxxxxxx"',
]


def test_detection_rate_at_least_95_percent():
    redactor = Redactor()
    hits = 0
    misses = []
    for label, sample, secret in SECRET_SAMPLES:
        result = redactor.redact(sample)
        if secret not in result.text and result.hit_count > 0:
            hits += 1
        else:
            misses.append(label)
    rate = hits / len(SECRET_SAMPLES)
    assert rate >= 0.95, f"detection {rate:.0%} < 95%, missed: {misses}"


def test_allowlist_untouched():
    redactor = Redactor()
    for sample in ALLOWLIST_SAMPLES:
        result = redactor.redact(sample)
        assert MASK not in result.text, f"allowlisted sample was redacted: {sample}"


def test_key_names_survive_value_masking():
    redactor = Redactor()
    result = redactor.redact('db_password = "S3cretValue99"')
    assert "db_password" in result.text
    assert "S3cretValue99" not in result.text


def test_redact_obj_recurses_into_nested_structures():
    redactor = Redactor()
    payload = {"a": ['token = "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"'], "b": {"c": "no secret"}}
    cleaned = redactor.redact_obj(payload)
    assert "ghp_a1B2c3D4" not in str(cleaned)
    assert cleaned["b"]["c"] == "no secret"
