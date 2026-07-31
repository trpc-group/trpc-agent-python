# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for redacting review artifacts."""

from __future__ import annotations

from examples.skills_code_review_agent.agent import redact_mapping
from examples.skills_code_review_agent.agent import redact_text


def test_redacts_common_secret_shapes():
    text = ("Authorization: Bearer abcdefghijklmnop\n"
            "API_KEY = \"sk-abcdefghijklmnop\"\n"
            "password=supersecret\n"
            "postgres://user:plainpass@db.example/app\n")

    redacted = redact_text(text)

    assert "abcdefghijklmnop" not in redacted
    assert "supersecret" not in redacted
    assert "plainpass" not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_private_key_blocks():
    text = "-----BEGIN PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----"

    assert redact_text(text) == "[REDACTED]"


def test_redacts_nested_json_like_payloads():
    payload = {
        "headers": ["Bearer abcdefghijklmnop"],
        "config": {
            "password": "password=plainsecret",
            "safe": "visible",
        },
    }

    redacted = redact_mapping(payload)

    assert redacted["headers"] == ["Bearer [REDACTED]"]
    assert redacted["config"]["password"] == "password=[REDACTED]"
    assert redacted["config"]["safe"] == "visible"


def test_does_not_redact_skill_words_or_paths():
    text = "/tmp/skills_code_review_agent/skill_manifest.json skill_name"

    assert redact_text(text) == text
