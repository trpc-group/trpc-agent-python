"""Tests for trpc_agent_sdk.tools.safety._redaction."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._models import (
    EVIDENCE_MAX_CHARS,
    Evidence,
    ScriptLanguage,
)
from trpc_agent_sdk.tools.safety._redaction import (
    Redactor,
    contains_secret_literal,
    evidence_was_redacted,
    make_default_redactor,
)


class TestRedactorBasics:

    def test_empty_text_passthrough(self):
        r = Redactor()
        assert r.redact("") == ""

    def test_no_secrets_no_change(self):
        r = Redactor()
        out = r.redact("just a normal string")
        assert out == "just a normal string"
        assert r.active is False

    def test_env_value_replaced(self):
        r = Redactor(env_values=["my-secret-token"])
        out = r.redact("Authorization: my-secret-token")
        assert "my-secret-token" not in out
        assert "<REDACTED:env:" in out
        assert r.active is True

    def test_env_value_longest_first(self):
        # If both "secret" and "secret-token" are env values, the longer
        # one is replaced first so the shorter substring cannot leak.
        r = Redactor(env_values=["secret", "secret-token"])
        out = r.redact("Authorization: secret-token")
        assert "secret-token" not in out
        assert "secret" not in out or "<REDACTED" in out

    def test_empty_env_values_ignored(self):
        r = Redactor(env_values=["", "real"])
        out = r.redact("real value")
        assert "real" not in out


class TestSecretPatterns:

    def test_sk_api_key(self):
        r = Redactor()
        out = r.redact("key=sk-" + "a" * 20)
        assert "<REDACTED:api_key_slash:" in out

    def test_bearer_token(self):
        r = Redactor()
        out = r.redact("Authorization: Bearer abcdef1234567890")
        assert "<REDACTED:bearer_token:" in out

    def test_aws_access_key(self):
        r = Redactor()
        out = r.redact("AWS_KEY=AKIA" + "A" * 16)
        assert "<REDACTED:aws_access_key:" in out

    def test_jwt_pattern(self):
        r = Redactor()
        # Three base64-ish segments, each long enough to satisfy {8,}.
        token = "eyJ" + "a" * 20 + "." + "eyJ" + "b" * 20 + "." + "c" * 20
        out = r.redact(token)
        assert "<REDACTED:jwt:" in out

    def test_github_token(self):
        r = Redactor()
        out = r.redact("ghp_" + "a" * 20)
        assert "<REDACTED:github_token:" in out

    def test_password_assignment(self):
        r = Redactor()
        out = r.redact('password = "supersecret123"')
        assert "<REDACTED:password:" in out

    def test_private_key_block(self):
        r = Redactor()
        body = ("-----BEGIN RSA PRIVATE KEY-----\n"
                "MIIBOgIBAAJBAKjQ4Z\n"
                "-----END RSA PRIVATE KEY-----")
        out = r.redact(body)
        assert "<REDACTED:private_key_block:" in out

    def test_hex_secret_32(self):
        r = Redactor()
        out = r.redact("token: " + "a" * 40)
        assert "<REDACTED" in out


class TestTruncate:

    def test_short_text_unchanged(self):
        r = Redactor()
        assert r.truncate("short") == "short"

    def test_long_text_truncated(self):
        r = Redactor()
        text = "x" * (EVIDENCE_MAX_CHARS + 100)
        out = r.truncate(text)
        assert len(out) <= EVIDENCE_MAX_CHARS + 5
        assert "…" in out

    def test_tiny_budget_short_circuit(self):
        r = Redactor(evidence_max_chars=5)
        assert r.truncate("abcdefgh") == "abcde"

    def test_zero_budget(self):
        r = Redactor(evidence_max_chars=0)
        assert r.truncate("anything") == ""


class TestBuildEvidence:

    def test_redacts_snippet(self):
        r = Redactor()
        ev = r.build_evidence(snippet="password=hunter2likevalue")
        assert "hunter2likevalue" not in ev.snippet

    def test_redacts_extras(self):
        r = Redactor()
        ev = r.build_evidence(
            snippet="ok",
            extras={"header": "Bearer someTokenString"},
        )
        assert "Bearer" not in ev.extras["header"] or "<REDACTED" in ev.extras["header"]

    def test_clamps_line_and_column(self):
        r = Redactor()
        ev = r.build_evidence(snippet="x", line=-5, column=-2)
        assert ev.line == 0
        assert ev.column == 0

    def test_language_propagates(self):
        r = Redactor()
        ev = r.build_evidence(snippet="x", language=ScriptLanguage.PYTHON)
        assert ev.language == ScriptLanguage.PYTHON


class TestContainsSecretLiteral:

    def test_positive(self):
        assert contains_secret_literal("sk-" + "a" * 20) is True
        assert contains_secret_literal("Bearer token12345678") is True

    def test_negative(self):
        assert contains_secret_literal("hello world") is False


class TestEvidenceWasRedacted:

    def test_redacted_snippet(self):
        ev = Evidence(snippet="<REDACTED:jwt:abcd>")
        assert evidence_was_redacted(ev) is True

    def test_redacted_extra(self):
        ev = Evidence(snippet="ok", extras={"x": "<REDACTED:env:abc>"})
        assert evidence_was_redacted(ev) is True

    def test_no_redaction(self):
        ev = Evidence(snippet="plain")
        assert evidence_was_redacted(Evidence(snippet="plain")) is False


def test_make_default_redactor():
    r = make_default_redactor(["abc"])
    assert isinstance(r, Redactor)
    assert r.redact("abc xyz").startswith("<REDACTED:env:")
