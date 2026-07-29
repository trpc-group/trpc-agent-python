#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Tests for the shared secret detector and output redactor."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from examples.skills_code_review_agent.code_review.redaction import (
    contains_plaintext_secret,
    redact_data,
    redact_text,
    redact_transport_fields,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from lib.diff_parser import parse_unified_diff  # noqa: E402
from lib.secret_rules import (  # noqa: E402
    SECRET_PATTERN_SPECS,
    detect_change_set_secrets,
    detect_secrets,
)


def _synthetic_secret(prefix: str, suffix: str) -> str:
    """拼接测试专用凭据片段，避免把完整真实格式凭据写入源码。"""

    return prefix + suffix


REAL_FORMAT_SAMPLES = [
    ("aws_access_key", "AKIA1234567890ABCDEF"),
    ("aws_access_key", "ASIA1234567890ABCDEF"),
    ("github_token", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("github_token", "github_pat_11AAABBBCCCDDDEEEFFF_1234567890abcdef"),
    ("gitlab_token", "glpat-abcdefghijklmnopqrstuvwxyz123456"),
    (
        "slack_token",
        _synthetic_secret(
            "xoxb-123456789012-123456789012-",
            "abcdefghijklmnopqrstuvwx",
        ),
    ),
    ("slack_token", "xoxp-123456789012-123456789012-abcdefghijklmnopqrstuvwx"),
    ("openai_api_key", "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("openai_api_key", "sk-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("stripe_secret_key", _synthetic_secret("sk_live_", "abcdefghijklmnopqrstuvwxyz123456")),
    ("stripe_secret_key", "rk_test_abcdefghijklmnopqrstuvwxyz123456"),
    ("twilio_auth_token", "twilio_auth_token = '0123456789abcdef0123456789abcdef'"),
    ("sendgrid_api_key", "SG.abcdefghijklmnopqrstuvwxyz.0123456789ABCDEFGHIJKLMNO"),
    ("npm_token", "npm_abcdefghijklmnopqrstuvwxyz1234567890"),
    ("pypi_token", "pypi-abcdefghijklmnopqrstuvwxyz12345678901234567890"),
    ("google_api_key", "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
    ("google_api_key", "AIzaABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"),
    (
        "azure_storage_key",
        "DefaultEndpointsProtocol=https;AccountKey="
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuv0123456789+/==",
    ),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijklmnopqrstuvwxyz0123456789"),
    ("private_key", "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAw\n-----END RSA PRIVATE KEY-----"),
    ("database_url", "postgresql://reviewer:password123@db.example.test:5432/reviews"),
    ("database_url", "mysql+pymysql://admin:password123@db.example.test/reviews"),
    ("database_url", "amqp://fixture_user:fixture_password@queue.invalid:5672/reviews"),
    ("bearer_token", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"),
    ("password", "password = 'LongSyntheticPasswordValue123!'"),
    ("password", "db_password: \"LongSyntheticPasswordValue123!\""),
    ("token", "access_token = 'abcdefghijklmnopqrstuvwxyz0123456789'"),
    ("token", "api_token: \"abcdefghijklmnopqrstuvwxyz0123456789\""),
    ("secret", "client_secret = 'abcdefghijklmnopqrstuvwxyz0123456789'"),
    ("secret", "secret_key: \"abcdefghijklmnopqrstuvwxyz0123456789\""),
    ("high_entropy_secret", "credential = 'fD7$kL2!qP9@wX4#zM8^rT1&vB6*eN3'"),
    ("high_entropy_secret", "value = 'aV7!qP2@xL9#rT4$wM8^kD1&zB6*eN3'"),
    ("high_entropy_secret", "api_key = 'R9!wK3@pV7#xM2$zT8^qL4&dB6*eN1'"),
    ("huggingface_token", "hf_abcdefghijklmnopqrstuvwxyz0123456789AB"),
    ("terraform_token", "ATLAS-abcdefghijklmnopqrstuvwxyz123456"),
    ("digitalocean_token", "dop_v1_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("discord_token", "MTAwMDAwMDAwMDAwMDAwMDAw.Ghijkl.abcdefghijklmnopqrstuvwxyz"),
    ("mailgun_api_key", "key-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("square_access_token", "sq0atp-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("shopify_access_token", "shpat_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("linear_api_key", "lin_api_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("datadog_api_key", "DD_API_KEY=abcdefghijklmnopqrstuvwxyz0123456789"),
    ("new_relic_license_key", "NEW_RELIC_LICENSE_KEY=abcdefghijklmnopqrstuvwxyz0123456789"),
    ("sentry_dsn", "https://1234567890abcdef1234567890abcdef@sentry.example.test/42"),
    ("database_url", "redis://:password123@cache.example.test:6379/0"),
    ("database_url", "amqps://reviewer:password123@mq.example.test:5671/reviews"),
    ("token", "private_token = 'abcdefghijklmnopqrstuvwxyz0123456789'"),
    ("secret", "webhook_secret: 'abcdefghijklmnopqrstuvwxyz0123456789'"),
]


BENIGN_SAMPLES = [
    "password = 'REDACTED'",
    "token = '<your-token-here>'",
    "api_key = 'example-api-key'",
    "secret = 'changeme'",
    "# ghp_your_github_token_here",
    "Authorization: Bearer <token>",
    "url = 'https://db.example.test/reviews'",
    "version = '2026.07.25'",
    "description = 'Use sk-your-key in local documentation.'",
    "random_identifier = 'abcdefghijklmno'",
]


@pytest.mark.parametrize(("expected_type", "sample"), REAL_FORMAT_SAMPLES)
def test_detects_real_format_secret_corpus(
    expected_type: str,
    sample: str,
) -> None:
    matches = detect_secrets(sample)

    assert any(match.secret_type == expected_type for match in matches)


def test_secret_corpus_detection_rate_is_at_least_95_percent() -> None:
    detected = sum(bool(detect_secrets(sample)) for _, sample in REAL_FORMAT_SAMPLES)

    assert len(REAL_FORMAT_SAMPLES) >= 48
    assert detected / len(REAL_FORMAT_SAMPLES) >= 0.95


@pytest.mark.parametrize("sample", BENIGN_SAMPLES)
def test_benign_and_placeholder_samples_are_not_detected(sample: str) -> None:
    """验证普通良性值与文档占位值不会形成敏感信息 finding。"""

    assert detect_secrets(sample) == ()


@pytest.mark.parametrize("sample", ("token = 'token'", "token = 'api-key'", "token = 'your-api-key'"))
def test_assignment_style_placeholder_values_are_not_detected(sample: str) -> None:
    """验证带变量名的赋值占位符按值而非整段赋值表达式降噪。"""

    assert detect_secrets(sample) == ()


def test_secret_patterns_are_shared_by_detection_and_redaction() -> None:
    """验证检出和脱敏复用同一模式表，且脱敏后不存在原始密钥。"""

    raw = "token = 'abcdefghijklmnopqrstuvwxyz0123456789'"
    matches = detect_secrets(raw)
    redacted = redact_text(raw)

    assert len(SECRET_PATTERN_SPECS) >= 12
    assert matches
    assert raw not in redacted
    assert "[REDACTED:token]" in redacted
    assert not contains_plaintext_secret(redacted)


def test_redacts_every_transport_field_and_nested_output_data() -> None:
    """验证嵌套传输字段也会经过同一脱敏出口。"""

    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    fields = redact_transport_fields(
        recommendation=f"Revoke {secret}",
        reasons=[f"blocked {secret}"],
        error=f"failed with {secret}",
        stdout=f"stdout {secret}",
        stderr=f"stderr {secret}",
    )
    data = redact_data({"fields": fields, "nested": [secret, {"key": secret}]})

    assert not contains_plaintext_secret(data)
    assert all(secret not in str(value) for value in fields.values())
    assert "[REDACTED:github_token]" in str(data)


def test_change_set_scans_new_context_and_deleted_old_lines() -> None:
    """验证 ChangeSet 同时定位新侧、上下文与删除旧侧的真实行号。"""

    new_secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    old_secret = "AKIA1234567890ABCDEF"
    context_secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    diff = "\n".join(
        [
            "diff --git a/config.py b/config.py",
            "--- a/config.py",
            "+++ b/config.py",
            "@@ -7,3 +7,3 @@",
            f"-AWS_ACCESS_KEY_ID = '{old_secret}'",
            f"+GITHUB_TOKEN = '{new_secret}'",
            " context = 'unchanged'",
            f" CONTEXT_TOKEN = '{context_secret}'",
        ]
    )

    locations = detect_change_set_secrets(parse_unified_diff(diff))

    assert [(item.secret_type, item.line, item.line_side) for item in locations] == [
        ("github_token", 7, "new"),
        ("openai_api_key", 9, "new"),
        ("aws_access_key", 7, "old"),
    ]
    assert all(new_secret not in item.evidence for item in locations)
    assert all(old_secret not in item.evidence for item in locations)
    assert all(context_secret not in item.evidence for item in locations)
