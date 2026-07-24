# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from trpc_agent_sdk.tools.safety import REDACTION_MARKER
from trpc_agent_sdk.tools.safety import contains_secret
from trpc_agent_sdk.tools.safety import redact_env
from trpc_agent_sdk.tools.safety import redact_evidence
from trpc_agent_sdk.tools.safety import redact_text


class TestRedaction:
    """Test safety redaction helpers."""

    def test_redact_evidence_truncates_long_text(self):
        evidence = "x" * 80

        redacted = redact_evidence(evidence, max_chars=30)

        assert len(redacted) == 30
        assert redacted.endswith("...[truncated]")

    def test_redact_text_replaces_secret_assignments(self):
        text = "print token=cleartext-value and password:another-cleartext"

        redacted = redact_text(text)

        assert REDACTION_MARKER in redacted
        assert "cleartext-value" not in redacted
        assert "another-cleartext" not in redacted

    def test_redact_text_replaces_bearer_value(self):
        text = "Authorization: Bearer abcdefghijklmnop"

        redacted = redact_text(text)

        assert redacted == f"Authorization: Bearer {REDACTION_MARKER}"

    def test_contains_secret_detects_high_signal_patterns(self):
        assert contains_secret("api_key=cleartext-value")
        assert contains_secret("Bearer abcdefghijklmnop")
        assert not contains_secret("ordinary text")

    def test_redact_env_never_returns_values(self):
        redacted = redact_env({
            "OPENAI_API_KEY": "cleartext-value",
            "SAFE_FLAG": "enabled",
        })

        assert redacted == {
            "OPENAI_API_KEY": REDACTION_MARKER,
            "SAFE_FLAG": REDACTION_MARKER,
        }
        assert "cleartext-value" not in str(redacted)
        assert "enabled" not in str(redacted)
