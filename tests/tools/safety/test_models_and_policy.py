# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy and sanitizer tests."""

import pytest

from trpc_agent_sdk.tools.safety import SafetySanitizer
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy


def test_policy_loads_yaml(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 1\n"
        "allowed_domains: [api.example.com]\n"
        "allowed_commands: [python]\n"
        "forbidden_paths: [.env]\n"
        "max_timeout_seconds: 20\n"
        "max_output_bytes: 1000\n"
        "long_sleep_seconds: 5\n"
        "large_write_bytes: 2000\n"
        "max_concurrency: 4\n",
        encoding="utf-8",
    )

    policy = ToolSafetyPolicy.from_yaml(path)

    assert policy.allowed_domains == ["api.example.com"]
    assert policy.max_timeout_seconds == 20


@pytest.mark.parametrize("value", [0, -1])
def test_policy_rejects_non_positive_limits(value):
    with pytest.raises(ValueError, match="greater than zero"):
        ToolSafetyPolicy(max_timeout_seconds=value)


def test_policy_rejects_unknown_version():
    with pytest.raises(ValueError, match="unsupported policy version"):
        ToolSafetyPolicy(version=2)


def test_policy_rejects_extra_keys():
    with pytest.raises(ValueError):
        ToolSafetyPolicy.model_validate({"version": 1, "unknown": True})


def test_sanitizer_redacts_before_truncation():
    sanitizer = SafetySanitizer(evidence_chars=80)
    raw = "password=super-secret-value " + "x" * 200

    safe, redacted = sanitizer.sanitize(raw)

    assert redacted is True
    assert "super-secret-value" not in safe
    assert len(safe) <= 83


def test_sanitizer_redacts_private_key_and_bearer():
    sanitizer = SafetySanitizer()
    raw = ("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY----- "
           "Authorization: Bearer secret-token-value")

    safe, redacted = sanitizer.sanitize(raw)

    assert redacted is True
    assert "abc" not in safe
    assert "secret-token-value" not in safe


@pytest.mark.parametrize(
    "raw, secret",
    [
        ("echo password='top secret phrase'", "top secret phrase"),
        ('echo token="multi word token"', "multi word token"),
        ("curl https://user:password-value@evil.test", "password-value"),
    ],
)
def test_sanitizer_redacts_complete_secret_values(raw, secret):
    safe, redacted = SafetySanitizer().sanitize(raw)
    assert redacted is True
    assert secret not in safe


def test_policy_file_validation_error_does_not_echo_input(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 1\nmax_timeout_seconds: very-secret-invalid-value\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        ToolSafetyPolicy.from_yaml(path)

    assert "very-secret-invalid-value" not in str(error.value)


def test_policy_rejects_duplicate_fields(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 1\n"
        "allowed_commands: [rm]\n"
        "allowed_commands: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unable to load tool safety policy"):
        ToolSafetyPolicy.from_yaml(path)


def test_policy_rejects_empty_list_entries_and_non_mapping_yaml(tmp_path):
    with pytest.raises(ValueError, match="must not be empty"):
        ToolSafetyPolicy(allowed_domains=[" "])
    path = tmp_path / "policy.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        ToolSafetyPolicy.from_yaml(path)
