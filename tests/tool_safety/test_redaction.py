"""Tests for redaction."""

from __future__ import annotations

import json

from trpc_agent_sdk.tools.safety._redaction import Redactor
from trpc_agent_sdk.tools.safety._models import ScriptLanguage


def test_env_value_redacted():
    redactor = Redactor(env_values=["super-secret-value"])
    out = redactor.redact("payload=super-secret-value")
    assert "super-secret-value" not in out
    assert "<REDACTED:env:" in out


def test_bearer_token_redacted():
    redactor = Redactor()
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
    out = redactor.redact(text)
    assert "Bearer eyJ" not in out


def test_api_key_slash_redacted():
    redactor = Redactor()
    text = "key=sk-1234567890abcdefghij"
    out = redactor.redact(text)
    assert "sk-1234567890abcdefghij" not in out


def test_private_key_block_redacted():
    redactor = Redactor()
    text = "-----BEGIN RSA PRIVATE KEY-----\nbody\n-----END RSA PRIVATE KEY-----"
    out = redactor.redact(text)
    assert "BEGIN RSA PRIVATE KEY" not in out or "body" not in out.split("REDACTED")[-1]


def test_password_assignment_redacted():
    redactor = Redactor()
    text = "password = 'letmein1234'"
    out = redactor.redact(text)
    assert "letmein1234" not in out


def test_truncate_long_text():
    redactor = Redactor(evidence_max_chars=20)
    out = redactor.truncate("abcdefghijklmnopqrstuvwxyz")
    assert len(out) <= 30
    assert out.startswith("abc")
    assert out.endswith("xyz")


def test_truncate_short_text_unchanged():
    redactor = Redactor(evidence_max_chars=100)
    out = redactor.truncate("abc")
    assert out == "abc"


def test_build_evidence_redacts_all_fields():
    redactor = Redactor(env_values=["sekret"])
    evidence = redactor.build_evidence(
        snippet="value=sekret password=hunter2",
        line=1,
        column=2,
        language=ScriptLanguage.PYTHON,
        extras={"raw": "value=sekret"},
    )
    serial = evidence.model_dump_json()
    assert "sekret" not in serial
    assert "hunter2" not in serial
    assert "<REDACTED:" in serial
