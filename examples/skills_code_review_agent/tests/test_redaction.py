"""Tests for redaction module."""

import pytest

from pipeline.redaction import redact, redact_finding_evidence, should_redact
from pipeline.types import Finding, FindingCategory, Severity


class TestRedact:
    """Sensitive data redaction tests."""

    def test_redact_openai_key(self):
        text = 'API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678stu"'
        result, count = redact(text)
        # Both the generic API_KEY pattern and the specific sk- pattern match
        # The key content is properly redacted
        assert "sk-abc123" not in result  # The actual key must not appear
        assert count >= 1

    def test_redact_github_token(self):
        text = "GITHUB_TOKEN=github_pat_11AZZJUYA0KF2ao68ScGLT_X8BHm0mZE27E8tkX4Sxo89agi"
        result, count = redact(text)
        assert "ghp_***" in result
        assert count >= 1

    def test_redact_password(self):
        text = 'password = "super_secret_db_password_12345"'
        result, count = redact(text)
        assert "***" in result
        assert count >= 1

    def test_redact_aws_key(self):
        text = "AWS_KEY = AKIA1234567890ABCDEF"
        result, count = redact(text)
        assert "AKIA***" in result
        assert count >= 1

    def test_redact_jwt(self):
        text = "token = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgN"
        result, count = redact(text)
        assert "JWT***" in result  # JWT replaced with JWT***
        assert count >= 1

    def test_no_redaction_needed(self):
        text = "logger.info('Processing started')"
        result, count = redact(text)
        assert result == text
        assert count == 0

    def test_redact_private_key_header(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nabc123\n-----END RSA PRIVATE KEY-----"
        result, count = redact(text)
        assert "***" in result
        assert count >= 1

    def test_multiple_redactions(self):
        # JWT token contains multiple segments that trigger patterns
        text = 'API = "sk-xxx123abc456def789ghi"; PWD = "secret123"'
        result, count = redact(text)
        assert count >= 2

    def test_redact_db_connection_string(self):
        text = "DATABASE_URL=mongodb://admin:password123@localhost:27017/db"
        result, count = redact(text)
        assert "***" in result
        assert count >= 1


class TestShouldRedact:
    """Quick redaction check."""

    def test_sensitive_detected(self):
        assert should_redact('API_KEY = "sk-abc123"')

    def test_safe_pass(self):
        assert not should_redact("print('hello')")


class TestRedactFindingEvidence:
    """Redaction in findings."""

    def test_evidence_redacted(self):
        f = Finding(
            severity=Severity.CRITICAL,
            category=FindingCategory.SECRET_INFO,
            file="config.py",
            line=3,
            title="API key found",
            evidence='API_KEY = "sk-abc123def456ghi789jkl012mno345"',
            recommendation="Use env var",
            confidence=0.95,
            source="test",
        )
        findings, count = redact_finding_evidence([f])
        assert count >= 1
        # Evidence must be redacted — no real key patterns remain
        assert "sk-abc" not in findings[0].evidence

    def test_no_secrets_untouched(self):
        f = Finding(
            severity=Severity.LOW,
            category=FindingCategory.MISSING_TESTS,
            file="test.py", line=1,
            title="Missing test",
            evidence="def foo(): pass",
            recommendation="Add test",
            confidence=0.5, source="test",
        )
        findings, count = redact_finding_evidence([f])
        assert count == 0
