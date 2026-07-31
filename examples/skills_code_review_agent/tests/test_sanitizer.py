# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for redacting review artifacts."""

from __future__ import annotations

from examples.skills_code_review_agent.agent import redact_mapping
from examples.skills_code_review_agent.agent import redact_text
from examples.skills_code_review_agent.tests.secret_samples import bearer_like_token
from examples.skills_code_review_agent.tests.secret_samples import db_password_like_value
from examples.skills_code_review_agent.tests.secret_samples import generic_password_value
from examples.skills_code_review_agent.tests.secret_samples import openai_like_token
from examples.skills_code_review_agent.tests.secret_samples import private_key_body_value


def test_redacts_common_secret_shapes():
    bearer = bearer_like_token()
    api_key = openai_like_token()
    text = (f"Authorization: Bearer {bearer_like_token()}\n"
            f"API_KEY = \"{openai_like_token()}\"\n"
            f"password={generic_password_value()}\n"
            f"postgres://user:{db_password_like_value()}@db.example/app\n")

    redacted = redact_text(text)

    assert bearer not in redacted
    assert api_key not in redacted
    assert generic_password_value() not in redacted
    assert db_password_like_value() not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_private_key_blocks():
    text = f"-----BEGIN PRIVATE KEY-----\n{private_key_body_value()}\n-----END PRIVATE KEY-----"

    assert redact_text(text) == "[REDACTED]"


def test_redacts_nested_json_like_payloads():
    payload = {
        "headers": [f"Bearer {bearer_like_token()}"],
        "config": {
            "password": f"password={generic_password_value()}",
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
